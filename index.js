import express from 'express';
import cors from 'cors';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';
import { makeWASocket, useMultiFileAuthState, DisconnectReason, makeCacheableSignalKeyStore, fetchLatestBaileysVersion, makeInMemoryStore, PHONENUMBER_MCC } from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import pino from 'pino';
import path from 'path';
import fs from 'fs';

// ─────────────── CONFIG ───────────────
const PORT = process.env.PORT || 3001;
const AUTH_DIR = path.join(process.cwd(), 'auth');
const SESSIONS_DIR = path.join(process.cwd(), 'sessions');
const MEDIA_DIR = path.join(process.cwd(), 'media');

if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });
if (!fs.existsSync(SESSIONS_DIR)) fs.mkdirSync(SESSIONS_DIR, { recursive: true });
if (!fs.existsSync(MEDIA_DIR)) fs.mkdirSync(MEDIA_DIR, { recursive: true });

// ─────────────── STATE ───────────────
const devices = {};          // deviceId → { sock, qr, status, phoneNumber, chatsSent, retryCount }
const messageStore = {};     // deviceId → { messages: [], contacts: [] }
const blastIntervals = {};   // deviceId → interval

// ── CHECKER state ──
let checkerSock = null;
let checkerStatus = 'unpaired';
let checkerQR = null;
const CHECKER_SESSION_DIR = path.join(process.cwd(), 'checker_session');
let requiredPPURL = null;
let requiredName = null;
const deviceCompliance = {};  // deviceId → { ppOk, nameOk, checkedAt }

// ─────────────── LOGGER ───────────────
const logger = pino({ level: 'info', transport: { target: 'pino-pretty', options: { colorize: true } } });

// ─────────────── EXPRESS ───────────────
const app = express();
app.use(cors());
app.use(express.json({ limit: '50mb' }));
const httpServer = createServer(app);

// ─────────────── WEBSOCKET ───────────────
const wss = new WebSocketServer({ server: httpServer });

function broadcast(data) {
  const msg = JSON.stringify(data);
  wss.clients.forEach(client => {
    if (client.readyState === 1) client.send(msg);
  });
}

wss.on('connection', (ws) => {
  logger.info('Frontend connected via WebSocket');
  ws.on('close', () => logger.info('Frontend disconnected'));
});

// ─────────────── HELPERS ───────────────
function getStatusFromWS(sock) {
  if (!sock || !sock.user) return 'pairing';
  if (sock.ws && sock.ws.readyState === 1) return 'connected';
  return 'disconnected';
}

function broadcastDeviceState() {
  const list = Object.entries(devices).map(([id, d]) => ({
    id,
    status: d.status,
    phoneNumber: d.phoneNumber,
    chatsSent: d.chatsSent || 0,
    isBlasting: !!blastIntervals[id],
    compliance: deviceCompliance[id] || null,
  }));
  broadcast({ type: 'devices', data: list });
}

// ─────────────── WHATSAPP SOCKET ───────────────
async function createWASocket(deviceId, retryCount = 0) {
  const sessionDir = path.join(SESSIONS_DIR, deviceId);
  if (!fs.existsSync(sessionDir)) fs.mkdirSync(sessionDir, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(sessionDir);

  const sock = makeWASocket({
    version: (await fetchLatestBaileysVersion()).version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger.child({ level: 'silent' })),
    },
    logger: logger.child({ level: 'silent' }),
    printQRInTerminal: false,
    browser: ['FortuneWS', 'Chrome', '1.0.0'],
    markOnlineOnConnect: true,
    syncFullHistory: false,
    retryRequestDelayMs: 500,
    maxRetries: 5,
  });

  // Store reference
  if (!devices[deviceId]) {
    devices[deviceId] = { sock, status: 'pairing', phoneNumber: null, chatsSent: 0, retryCount };
  } else {
    devices[deviceId].sock = sock;
  }

  // Handle connection events
  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr && !devices[deviceId]?.phoneNumber) {
      // Send QR to frontend
      devices[deviceId].status = 'pairing';
      devices[deviceId].qr = qr;
      broadcast({ type: 'qr', deviceId, qr });
      broadcastDeviceState();
    }

    if (connection === 'open') {
      const phoneNumber = sock.user?.id?.split(':')[0]?.replace(/@s\.whatsapp\.net$/, '') || null;
      const formatted = phoneNumber ? formatPhone(phoneNumber) : 'Unknown';
      devices[deviceId].status = 'connected';
      devices[deviceId].phoneNumber = formatted;
      devices[deviceId].retryCount = 0;
      devices[deviceId].qr = null;
      logger.info(`Device ${deviceId}: Connected as ${formatted}`);
      broadcast({ type: 'device_connected', deviceId, phoneNumber: formatted });
      broadcastDeviceState();
      
      // Auto-check compliance via checker
      if (checkerSock && checkerStatus === 'connected' && formatted !== 'Unknown') {
        setTimeout(() => runComplianceCheck(deviceId, formatted), 3000);
      }
    }

    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = code !== DisconnectReason.loggedOut;

      if (code === DisconnectReason.loggedOut) {
        // Kicked / logged out
        devices[deviceId].status = 'disconnected';
        delete devices[deviceId].sock;
        // Clear auth data
        try { fs.rmSync(sessionDir, { recursive: true }); } catch {}
        logger.warn(`Device ${deviceId}: Logged out, auth cleared`);
        broadcast({ type: 'device_disconnected', deviceId, reason: 'logged_out' });
      } else if (code === DisconnectReason.banned) {
        devices[deviceId].status = 'banned';
        logger.warn(`Device ${deviceId}: BANNED`);
        broadcast({ type: 'device_banned', deviceId });
      } else if (shouldReconnect) {
        devices[deviceId].status = 'connecting';
        logger.info(`Device ${deviceId}: Reconnecting... (code: ${code})`);
        broadcast({ type: 'device_disconnected', deviceId, reason: 'reconnecting' });
        // Reconnect
        setTimeout(() => createWASocket(deviceId, (devices[deviceId]?.retryCount || 0) + 1), 3000 + Math.random() * 5000);
      }
      broadcastDeviceState();
    }
  });

  // Handle credentials update
  sock.ev.on('creds.update', saveCreds);

  // Messages
  sock.ev.on('messages.upsert', async (m) => {
    // We don't process incoming messages for blast purposes
    // But we can log them if needed
  });

  return sock;
}

function formatPhone(num) {
  // Convert 6281234567890 → +62 812-3456-7890
  const cleaned = num.replace(/\D/g, '');
  if (cleaned.startsWith('62')) return `+62 ${cleaned.slice(2, 5)}-${cleaned.slice(5, 9)}-${cleaned.slice(9)}`;
  if (cleaned.startsWith('0')) return `+62 ${cleaned.slice(1, 4)}-${cleaned.slice(4, 8)}-${cleaned.slice(8)}`;
  return `+${cleaned}`;
}

function normalizePhoneForWA(phone) {
  let cleaned = phone.replace(/\D/g, '');
  if (cleaned.startsWith('0')) cleaned = '62' + cleaned.slice(1);
  if (!cleaned.startsWith('62')) cleaned = '62' + cleaned;
  return cleaned + '@s.whatsapp.net';
}

// ─────────────── CHECKER BOT ───────────────

async function createCheckerSocket() {
  if (!fs.existsSync(CHECKER_SESSION_DIR)) fs.mkdirSync(CHECKER_SESSION_DIR, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(CHECKER_SESSION_DIR);
  
  checkerSock = makeWASocket({
    version: (await fetchLatestBaileysVersion()).version,
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger.child({ level: 'silent' })) },
    logger: logger.child({ level: 'silent' }),
    printQRInTerminal: false,
    browser: ['FortuneWS-Checker', 'Chrome', '1.0.0'],
    markOnlineOnConnect: false,
    syncFullHistory: false,
  });

  checkerSock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      checkerStatus = 'pairing';
      checkerQR = qr;
      broadcast({ type: 'checker_qr', qr });
      logger.info('Checker: QR ready, scan dengan WhatsApp admin');
    }
    if (connection === 'open') {
      checkerStatus = 'connected';
      checkerQR = null;
      const num = checkerSock.user?.id?.split(':')[0]?.replace(/@s\.whatsapp\.net$/, '');
      logger.info(`Checker: Connected as ${num}`);
      broadcast({ type: 'checker_connected', phoneNumber: num });
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      checkerStatus = code === DisconnectReason.loggedOut ? 'unpaired' : 'disconnected';
      if (code === DisconnectReason.loggedOut) {
        try { fs.rmSync(CHECKER_SESSION_DIR, { recursive: true }); } catch {}
        checkerSock = null;
      }
      broadcast({ type: 'checker_disconnected', reason: code === DisconnectReason.loggedOut ? 'logged_out' : 'reconnecting' });
      if (code !== DisconnectReason.loggedOut) {
        setTimeout(() => createCheckerSocket(), 5000);
      }
    }
  });

  checkerSock.ev.on('creds.update', saveCreds);
  return checkerSock;
}

async function runComplianceCheck(deviceId, phoneNumber) {
  if (!checkerSock || checkerStatus !== 'connected') {
    logger.info(`Checker: Skip compliance check for ${deviceId} — checker not ready`);
    return;
  }

  try {
    const jid = normalizePhoneForWA(phoneNumber);
    logger.info(`Checker: Running compliance check for ${phoneNumber} (${jid})`);

    // Check name — get contact profile
    let ppOk = null;
    let nameOk = null;

    try {
      // Try to fetch profile picture URL
      const ppUrl = await checkerSock.profilePictureUrl(jid, 'image');
      ppOk = !!ppUrl;  // Has PP = OK (we can't compare image content easily)
      logger.info(`Checker: ${phoneNumber} PP URL: ${ppUrl ? 'found' : 'not found'}`);
    } catch (e) {
      // No PP or error
      ppOk = false;
      logger.info(`Checker: ${phoneNumber} PP: not found (${e.message?.slice(0,50)})`);
    }

    // Check name — get status/contact info
    try {
      const contact = await checkerSock.getContact(jid);
      const displayName = contact?.name || contact?.notify || contact?.verifiedName || '';
      if (requiredName) {
        nameOk = displayName.toLowerCase().includes(requiredName.toLowerCase());
      } else {
        nameOk = true;  // No required name set, always pass
      }
      logger.info(`Checker: ${phoneNumber} name="${displayName}" required="${requiredName}" match=${nameOk}`);
    } catch (e) {
      nameOk = false;
      logger.info(`Checker: ${phoneNumber} name check failed: ${e.message?.slice(0,50)}`);
    }

    // Save compliance result
    deviceCompliance[deviceId] = {
      ppOk: ppOk ?? false,
      nameOk: nameOk ?? false,
      checkedAt: new Date().toISOString(),
      phoneNumber,
    };

    broadcast({
      type: 'compliance_checked',
      deviceId,
      phoneNumber,
      ppOk: deviceCompliance[deviceId].ppOk,
      nameOk: deviceCompliance[deviceId].nameOk,
    });
    broadcastDeviceState();

    logger.info(`Checker: ${phoneNumber} compliance — PP:${deviceCompliance[deviceId].ppOk} Name:${deviceCompliance[deviceId].nameOk}`);
  } catch (e) {
    logger.error(`Checker: Compliance check failed for ${deviceId}: ${e.message}`);
  }
}

// ─────────────── API ROUTES ───────────────

// Health check
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', uptime: process.uptime() });
});

// Get all devices
app.get('/api/devices', (req, res) => {
  const list = Object.entries(devices).map(([id, d]) => ({
    id,
    status: d.status,
    phoneNumber: d.phoneNumber,
    chatsSent: d.chatsSent || 0,
    qr: d.qr || null,
    isBlasting: !!blastIntervals[id],
  }));
  res.json(list);
});

// Create a new device (pairing) – sends QR back via WebSocket
app.post('/api/devices', async (req, res) => {
  try {
    const deviceId = `dev_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    await createWASocket(deviceId);
    res.json({ deviceId, status: 'pairing' });
  } catch (err) {
    logger.error('Failed to create device:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// Remove device
app.delete('/api/devices/:deviceId', async (req, res) => {
  const { deviceId } = req.params;
  try {
    // Stop blast if running
    if (blastIntervals[deviceId]) {
      clearInterval(blastIntervals[deviceId]);
      delete blastIntervals[deviceId];
    }

    if (devices[deviceId]?.sock) {
      try { await devices[deviceId].sock.logout(); } catch {}
    }

    delete devices[deviceId];
    delete messageStore[deviceId];

    // Clean session files
    const sessionDir = path.join(SESSIONS_DIR, deviceId);
    try { fs.rmSync(sessionDir, { recursive: true }); } catch {}

    broadcastDeviceState();
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Start blast
app.post('/api/blast/start', (req, res) => {
  const { deviceId, targets, template, speed = 3 } = req.body;

  if (!deviceId || !targets || !Array.isArray(targets) || targets.length === 0) {
    return res.status(400).json({ error: 'deviceId and targets[] required' });
  }

  const dev = devices[deviceId];
  if (!dev || dev.status !== 'connected') {
    return res.status(400).json({ error: 'Device not connected' });
  }

  if (blastIntervals[deviceId]) {
    clearInterval(blastIntervals[deviceId]);
  }

  let index = 0;
  let sentCount = 0;
  let failedCount = 0;
  let banned = false;

  const sendNext = async () => {
    if (banned) return;
    if (index >= targets.length) {
      // All done
      clearInterval(blastIntervals[deviceId]);
      delete blastIntervals[deviceId];
      broadcast({ type: 'blast_complete', deviceId, sent: sentCount, failed: failedCount });
      broadcastDeviceState();
      return;
    }

    const target = targets[index];
    index++;

    try {
      const jid = normalizePhoneForWA(target);
      const msgContent = buildMessage(template);

      // Check if media
      if (template?.image) {
        // Send image + caption
        await dev.sock.sendMessage(jid, {
          image: { url: template.image },
          caption: template.text || '',
          ...(template.footer ? { footer: template.footer } : {}),
          ...(template.buttons?.length > 0 ? { 
            buttons: template.buttons.map(b => ({
              buttonId: b.type + '_' + Date.now(),
              buttonText: { displayText: b.label },
              type: b.type === 'url' ? 1 : (b.type === 'call' ? 2 : 3),
              ...(b.type === 'url' ? { url: b.value } : b.type === 'call' ? { phone: b.value } : {}),
            }))
          } : {}),
        });
      } else {
        // Send text only
        await dev.sock.sendMessage(jid, { text: template?.text || 'Halo, ada promo spesial dari FortuneWS!' });
      }

      sentCount++;
      dev.chatsSent = (dev.chatsSent || 0) + 1;

      broadcast({
        type: 'blast_progress',
        deviceId,
        current: index,
        total: targets.length,
        sent: sentCount,
        failed: failedCount,
        target,
      });
    } catch (err) {
      failedCount++;
      const errMsg = err?.message || '';

      // Detect ban / rate limit
      if (errMsg.includes('banned') || errMsg.includes('blocked') || err?.output?.statusCode === 401) {
        banned = true;
        dev.status = 'banned';
        clearInterval(blastIntervals[deviceId]);
        delete blastIntervals[deviceId];
        broadcast({ type: 'device_banned', deviceId, sent: sentCount, failed: failedCount });
        broadcastDeviceState();
        return;
      }

      if (errMsg.includes('rate-overlimit') || errMsg.includes('too many') || err?.output?.statusCode === 429) {
        dev.status = 'disconnected';
        clearInterval(blastIntervals[deviceId]);
        delete blastIntervals[deviceId];
        broadcast({ type: 'device_disconnected', deviceId, reason: 'rate_limited' });
        broadcastDeviceState();
        return;
      }

      broadcast({ 
        type: 'blast_error', 
        deviceId, 
        target, 
        error: errMsg.slice(0, 100),
        current: index,
        sent: sentCount,
        failed: failedCount,
      });
    }
  };

  // Send first immediately, then interval
  sendNext();
  blastIntervals[deviceId] = setInterval(sendNext, speed * 1000);

  broadcast({ type: 'blast_started', deviceId, total: targets.length, speed });
  broadcastDeviceState();
  res.json({ success: true, total: targets.length, speed });
});

// Stop blast
app.post('/api/blast/stop', (req, res) => {
  const { deviceId } = req.body;
  if (!deviceId) return res.status(400).json({ error: 'deviceId required' });

  if (blastIntervals[deviceId]) {
    clearInterval(blastIntervals[deviceId]);
    delete blastIntervals[deviceId];
    broadcast({ type: 'blast_stopped', deviceId });
    broadcastDeviceState();
  }

  res.json({ success: true });
});

// Helper: build WA message from template 
function buildMessage(template) {
  if (!template) return { text: 'Halo, ada promo spesial dari FortuneWS!' };
  return { text: template.text || 'Halo!', image: template.image || null };
}

// ─────────────── CHECKER API ───────────────

// Get checker status & QR
app.get('/api/checker/status', (req, res) => {
  res.json({ status: checkerStatus, qr: checkerQR, requiredName, requiredPPURL });
});

// Start / pair checker
app.post('/api/checker/start', async (req, res) => {
  try {
    if (checkerSock && checkerStatus === 'connected') {
      return res.json({ status: 'connected', phoneNumber: checkerSock.user?.id?.split(':')[0] });
    }
    await createCheckerSocket();
    res.json({ status: checkerStatus, qr: checkerQR });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Set required PP & Name for compliance
app.post('/api/checker/requirements', (req, res) => {
  const { ppURL, name } = req.body;
  if (ppURL !== undefined) requiredPPURL = ppURL;
  if (name !== undefined) requiredName = name;
  logger.info(`Checker requirements updated: name="${requiredName}" ppURL="${requiredPPURL?.slice(0,30)}..."`);
  res.json({ requiredName, requiredPPURL });
});

// Get all compliance results
app.get('/api/checker/compliance', (req, res) => {
  res.json(deviceCompliance);
});

// Manual re-check specific device
app.post('/api/checker/recheck/:deviceId', async (req, res) => {
  const { deviceId } = req.params;
  const dev = devices[deviceId];
  if (!dev || !dev.phoneNumber) return res.status(404).json({ error: 'Device not found or not connected' });
  await runComplianceCheck(deviceId, dev.phoneNumber);
  res.json(deviceCompliance[deviceId] || {});
});

// Re-check all devices
app.post('/api/checker/recheck-all', async (req, res) => {
  const results = [];
  for (const [id, d] of Object.entries(devices)) {
    if (d.phoneNumber && d.status === 'connected') {
      await runComplianceCheck(id, d.phoneNumber);
      results.push({ deviceId: id, ...deviceCompliance[id] });
      await new Promise(r => setTimeout(r, 2000)); // delay antar check
    }
  }
  res.json(results);
});

// ─────────────── START ───────────────
httpServer.listen(PORT, () => {
  logger.info(`🚀 FortuneWS Server running on http://localhost:${PORT}`);
  logger.info(`📱 WebSocket ready for frontend connections`);
});

// Graceful shutdown
process.on('SIGINT', async () => {
  logger.info('Shutting down...');
  for (const id of Object.keys(blastIntervals)) clearInterval(blastIntervals[id]);
  for (const id of Object.keys(devices)) {
    try { await devices[id]?.sock?.logout(); } catch {}
  }
  process.exit(0);
});

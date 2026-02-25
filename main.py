import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
from datetime import datetime
import asyncio

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
CATEGORY_ID = 1460318893120815216
ADMIN_ROLE_ID = 1474378018876293184
MAX_SLOT = 19
QRIS_IMAGE_URL = "https://cdn.discordapp.com/attachments/1474456065075843194/1475759449590464512/qr_ID1026487205647_24.02.26_177191871_1771918718954.jpg?ex=699ea797&is=699d5617&hm=facc39606d31c06ecbfc7096afd991932e5394c9c79f454c744a2b4378f8d553&"
DATA_FILE = "slots.json"
PROGRESS_FILE = "progress.json"
PANELS_FILE = "panels.json"
CHATSTANDBY_FILE = "chatstandby.json"

chatstandby = {}
crete_data = {}

intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
    
# -------
# load chat standby
# -------
def load_chatstandby():
    global chatstandby
    try:
        with open("chatstandby.json", "r") as f:
            chatstandby = json.load(f)
    except:
        chatstandby = {}

def save_chatstandby():
    with open("chatstandby.json", "w") as f:
        json.dump(chatstandby, f, indent=4)
# -------
# LOAD DATA
# --------
waiting_qris = {}

if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE, "r") as f:
        progress_data = json.load(f)
else:
    progress_data = {}

if os.path.exists(PANELS_FILE):
    with open(PANELS_FILE, "r") as f:
        panels = json.load(f)
else:
    panels = {}

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        slots_data = json.load(f)
        for embed_id, slots in slots_data.items():
            for slot, data in slots.items():
                if isinstance(data, dict) and "users" in data:
                    data["users"] = [(u[0], u[1], int(u[2])) for u in data["users"]]
else:
    slots_data = {}

def save_progress():
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress_data, f)

def save_panels():
    with open(PANELS_FILE, "w") as f:
        json.dump(panels, f)

def save_slots():
    with open(DATA_FILE, "w") as f:
        json.dump(slots_data, f)
        
def load_create():
    global slots_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            slots_data = json.load(f)
    else:
        slots_data = {}
        
def format_rupiah(amount: int):
    return "Rp {:,}".format(amount).replace(",", ".")

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

# -----------------------
# UTIL: Progress bar
# -----------------------
def progress_bar(current, total=MAX_SLOT, length=20):
    filled = int(length * current / (total if total > 0 else 1))
    return "█" * filled + "░" * (length - filled)

# -----------------------
# UPDATE PANEL EMBED
# -----------------------
async def update_panel(guild, embed_id):
    if embed_id not in slots_data:
        return
    for channel in guild.text_channels:
        try:
            async for message in channel.history(limit=50):
                if message.author != bot.user or not message.embeds:
                    continue
                embed = message.embeds[0]
                if embed.footer and embed.footer.text == embed_id:
                    new_embed = discord.Embed(
                        title=embed.title,
                        description="Klik tombol daftar untuk memesan slot",
                        color=discord.Color.green()
                    )
                    new_embed.set_footer(text=embed_id)
                    for slot_name, slot_data_obj in slots_data[embed_id].items():
                        users = slot_data_obj["users"]
                        count = len(users)
                        bar = progress_bar(count)
                        name = f"🍀 {slot_name}"
                        if count >= MAX_SLOT:
                            name += " 🔒"
                        new_embed.add_field(
                            name=name,
                            value=f"{bar} {count}/{MAX_SLOT}",
                            inline=False
                        )
                    await message.edit(embed=new_embed, view=MainView(embed_id))
                    return
        except:
            continue

async def update_progress_embed(channel):
    if not channel or str(channel.id) not in progress_data:
        return
    data = progress_data[str(channel.id)]
    try:
        msg = await channel.fetch_message(data["message_id"])
        current = data["current"]
        target = data["target"]
        today = today_str()
        daily = data.get("daily", {}).get(today, 0)
        
        bar = progress_bar(current, target, 20)
        embed = discord.Embed(
            title="🚀 Road to Kesuksesan Finansial",
            description=(
                f"{bar}\n"
                f"{format_rupiah(current)} / {format_rupiah(target)}\n\n"
                f"📅 Total Hari Ini: {format_rupiah(daily)}"
            ),
            color=discord.Color.green()
        )
        await msg.edit(embed=embed)
    except:
        pass

# -----------------------
# VIEWS
# -----------------------
class TicketView(discord.ui.View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Selesai Pesan", style=discord.ButtonStyle.green, custom_id="ticket_selesai")
    async def selesai(self, interaction: discord.Interaction, button):
        # Cek admin
        is_admin = False
        if ADMIN_ROLE_ID in [role.id for role in interaction.user.roles]:
            is_admin = True
        
        # Cek panel-specific admin role
        topic = interaction.channel.topic
        if topic:
            try:
                embed_id = topic.split("|")[0]
                panel = panels.get(embed_id)
                if panel and panel.get("admin_role") in [role.id for role in interaction.user.roles]:
                    is_admin = True
            except:
                pass

        if not is_admin:
            await interaction.response.send_message("❌ Hanya admin yang bisa klik tombol ini", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        topic = interaction.channel.topic
        if not topic:
            return
        try:
            embed_id, slot_name, user_id, username_rb, price = topic.split("|")
            price = int(price)
            user = await interaction.guild.fetch_member(int(user_id))
        except (ValueError, discord.NotFound):
            return

        slots_data[embed_id][slot_name]["users"].append(
            (user.name, username_rb, int(user_id))
        )
        save_slots()
        await update_panel(interaction.guild, embed_id)
        
        try:
            await user.send(f"✅ Kamu berhasil daftar di slot **{slot_name}**")
        except:
            pass

        # Update progress otomatis
        today = today_str()
        for ch_id in progress_data:
            progress_data[ch_id]["current"] += price
            if "daily" not in progress_data[ch_id]:
                progress_data[ch_id]["daily"] = {}
            progress_data[ch_id]["daily"][today] = progress_data[ch_id]["daily"].get(today, 0) + price
            
            channel = interaction.guild.get_channel(int(ch_id))
            if channel:
                await update_progress_embed(channel)
        
        save_progress()

        # ==== Kirim ke log channel ====
        panel = panels.get(embed_id)
        if panel and "log_channel" in panel:
            log_ch = interaction.guild.get_channel(panel["log_channel"])
            if log_ch:
                embed_log = discord.Embed(
                    title="📦 LOG PESANAN",
                    color=discord.Color.green()
                )
                embed_log.description = (
                    f"**Status :** Pesanan selesai\n"
                    f"**Action by :** {interaction.user.mention}\n"
                    f"**User :** {user.mention}\n"
                    f"**Produk :** {slot_name}\n"
                    f"**Harga :** {format_rupiah(price)}"
                )
                await log_ch.send(embed=embed_log)
        # =============================
        await interaction.channel.delete()

    @discord.ui.button(label="Batal Pesan", style=discord.ButtonStyle.red, custom_id="ticket_batal")
    async def batal(self, interaction: discord.Interaction, button):
        topic = interaction.channel.topic
        if not topic:
            await interaction.channel.delete()
            return
            
        try:
            parts = topic.split("|")
            # Cek format topic untuk membedakan /create standar vs custom panel
            if len(parts) == 5: # /create: embed_id|slot_name|user_id|username_rb|price
                embed_id, slot_name, user_id, username_rb, price = parts
            else: # OrderView style: panel_id|user_id|product|price
                embed_id, user_id, slot_name, price = parts
                
            user_id = int(user_id)
        except:
            await interaction.channel.delete()
            return

        # Izin: User pembuat ticket OR Admin
        is_owner = interaction.user.id == user_id
        is_admin = ADMIN_ROLE_ID in [role.id for role in interaction.user.roles]
        
        panel = panels.get(embed_id)
        if panel and panel.get("admin_role") in [role.id for role in interaction.user.roles]:
            is_admin = True

        if not (is_owner or is_admin):
            await interaction.response.send_message("❌ Hanya pembuat tiket atau Admin yang bisa membatalkan", ephemeral=True)
            return

        await interaction.channel.delete()
        
class OrderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # =========================
    # TOMBOL SELESAI PESAN
    # =========================
    @discord.ui.button(label="✅ Selesai Pesan", style=discord.ButtonStyle.green, custom_id="order_selesai")
    async def selesai(self, interaction: discord.Interaction, button):

        topic = interaction.channel.topic
        if not topic:
            return

        try:
            panel_id, user_id, product, price = topic.split("|")
            price = int(price)
        except:
            return

        panel = panels.get(panel_id)
        if not panel:
            return

        admin_role_id = panel["admin_role"]

        # Cek apakah user memiliki role admin yang ditentukan di panel
        user_role_ids = [r.id for r in interaction.user.roles]
        if admin_role_id not in user_role_ids and ADMIN_ROLE_ID not in user_role_ids:
            await interaction.response.send_message(
                f"❌ Hanya <@&{admin_role_id}> atau Admin Bot  yang bisa klik tombol ini",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # =========================
        # UPDATE PROGRESS
        # =========================
        progress_channel_id = str(panel["progress_channel"])

        if progress_channel_id in progress_data:

            progress_data[progress_channel_id]["current"] += price

            today = today_str()

            if "daily" not in progress_data[progress_channel_id]:
                progress_data[progress_channel_id]["daily"] = {}

            progress_data[progress_channel_id]["daily"][today] = \
                progress_data[progress_channel_id]["daily"].get(today, 0) + price

            save_progress()

            ch = interaction.guild.get_channel(int(progress_channel_id))

            if ch:
                await update_progress_embed(ch)

        # =========================
        # AMBIL USER
        # =========================
        try:
            user = await interaction.guild.fetch_member(int(user_id))
        except:
            user = None

        # =========================
        # DM USER
        # =========================
        if user:
            try:
                await user.send(f"✅ Pesanan {product} selesai")
            except:
                pass

        # =========================
        # KIRIM LOG SELESAI
        # =========================
        if user:
            await send_log_selesai(
                interaction.guild,
                panel_id,
                interaction.user,
                user,
                product,
                price
            )

        # =========================
        # HAPUS TICKET
        # =========================
        await interaction.channel.delete()


    # =========================
    # TOMBOL BATAL PESAN
    # =========================
    @discord.ui.button(label="❌ Batal Pesan", style=discord.ButtonStyle.red, custom_id="order_batal")
    async def batal(self, interaction: discord.Interaction, button):
        topic = interaction.channel.topic
        if not topic:
            await interaction.channel.delete()
            return

        try:
            panel_id, user_id, product, price = topic.split("|")
            user_id = int(user_id)
        except:
            await interaction.channel.delete()
            return

        # Izin: User pembuat ticket OR Admin
        is_owner = interaction.user.id == user_id
        is_admin = False
        if ADMIN_ROLE_ID in [role.id for role in interaction.user.roles]:
            is_admin = True
            
        panel = panels.get(panel_id)
        if panel and panel.get("admin_role") in [role.id for role in interaction.user.roles]:
            is_admin = True

        if not (is_owner or is_admin):
            await interaction.response.send_message("❌ Hanya pembuat tiket atau Admin yang bisa membatalkan", ephemeral=True)
            return

        await interaction.channel.delete()

    
async def send_log_selesai(guild, panel_id, admin, user, product, price):
    panel = panels.get(panel_id)
    if not panel or not panel.get("log_channel"):
        return
    
    log_channel = guild.get_channel(panel["log_channel"])
    if not log_channel:
        return

    embed = discord.Embed(
        title="📦 LOG PESANAN",
        color=discord.Color.green()
    )
    embed.description = (
        f"**Status :** Pesanan selesai\n"
        f"**Action by :** {admin.mention}\n"
        f"**User :** {user.mention}\n"
        f"**Produk :** {product}\n"
        f"**Harga :** {format_rupiah(price)}"
    )
    await log_channel.send(embed=embed)

class UsernameModal(discord.ui.Modal):
    def __init__(self, embed_id, slot_name):
        super().__init__(title="Isi Username Roblox")
        self.embed_id = embed_id
        self.slot_name = slot_name
        self.username = discord.ui.TextInput(label="Username Roblox")
        self.add_item(self.username)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        username_rb = self.username.value
        guild = interaction.guild
        category = guild.get_channel(CATEGORY_ID)
        if not category:
            await interaction.response.send_message("❌ Category not found", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.get_role(ADMIN_ROLE_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        ticket = await guild.create_text_channel(
            name=f"tiket-{user.name}",
            category=category,
            overwrites=overwrites
        )

        price = slots_data[self.embed_id][self.slot_name].get("price", 0)
        await ticket.edit(topic=f"{self.embed_id}|{self.slot_name}|{user.id}|{username_rb}|{price}")

        embed = discord.Embed(title="Tiket Pesanan", description="Admin akan memproses pesananmu", color=discord.Color.green())
        embed.add_field(name="Slot", value=self.slot_name)
        embed.add_field(name="User", value=user.mention)
        embed.add_field(name="Roblox", value=username_rb)
        await ticket.send(user.mention, embed=embed, view=TicketView(timeout=None))

        qris_embed = discord.Embed(title="📲 Pembayaran QRIS", description="Silahkan scan QRIS dibawah ini untuk membayar", color=discord.Color.green())
        qris_embed.set_image(url=QRIS_IMAGE_URL)
        await ticket.send(user.mention, embed=qris_embed)
        await interaction.response.send_message(f"✅ Tiket berhasil dibuat: {ticket.mention}", ephemeral=True)

class SelectSlot(discord.ui.Select):
    def __init__(self, embed_id, options):
        super().__init__(placeholder="Pilih slot", options=options)
        self.embed_id = embed_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(UsernameModal(self.embed_id, self.values[0]))

class SelectSlotView(discord.ui.View):
    def __init__(self, embed_id, options):
        super().__init__()
        self.add_item(SelectSlot(embed_id, options))

class MainView(discord.ui.View):
    def __init__(self, embed_id):
        super().__init__(timeout=None)
        self.embed_id = embed_id

    @discord.ui.button(label="🎯 Daftar", style=discord.ButtonStyle.green, custom_id="main_daftar_new")
    async def daftar(self, interaction: discord.Interaction, button):
        options = []
        for slot_name, data in slots_data[self.embed_id].items():
            if len(data["users"]) < MAX_SLOT:
                options.append(discord.SelectOption(label=slot_name, description=f"{len(data['users'])}/{MAX_SLOT} terisi"))
        if not options:
            await interaction.response.send_message("❌ Semua slot penuh", ephemeral=True)
            return
        await interaction.response.send_message("Pilih slot:", view=SelectSlotView(self.embed_id, options), ephemeral=True)

    @discord.ui.button(label="📊 Lihat Slot Terisi", style=discord.ButtonStyle.gray, custom_id="open_slot")
    async def lihat(self, interaction: discord.Interaction, button):
        options = [discord.SelectOption(label=s) for s in slots_data[self.embed_id]]
        await interaction.response.send_message("Pilih slot:", view=LihatSlotView(self.embed_id, options), ephemeral=True)

class LihatSlotSelect(discord.ui.Select):
    def __init__(self, embed_id, options):
        super().__init__(placeholder="Pilih slot", options=options)
        self.embed_id = embed_id

    async def callback(self, interaction: discord.Interaction):
        users = slots_data[self.embed_id][self.values[0]]["users"]
        text = f"**List {self.values[0]}**\n\n" + ("Kosong" if not users else "\n".join(f"{i}. <@{u[2]}> - {u[1]} ✅" for i, u in enumerate(users, 1)))
        await interaction.response.send_message(text, ephemeral=True)

class LihatSlotView(discord.ui.View):
    def __init__(self, embed_id, options):
        super().__init__()
        self.add_item(LihatSlotSelect(embed_id, options))

class ProductView(discord.ui.View):
    def __init__(self, panel_id):
        super().__init__(timeout=None)
        self.panel_id = panel_id

        panel = panels.get(panel_id)
        if not panel:
            return

        for product, price in panel["products"].items():
            button = discord.ui.Button(
                label=f"{product} • {format_rupiah(price)}",
                style=discord.ButtonStyle.green,
                custom_id=f"buy_{panel_id}_{product}"
            )
            button.callback = self.make_callback(product, price)
            self.add_item(button)

    def make_callback(self, product, price):
        async def callback(interaction: discord.Interaction):

            panel = panels.get(self.panel_id)
            if not panel:
                return

            category = interaction.guild.get_channel(panel["category"])
            admin_role = interaction.guild.get_role(panel["admin_role"])

            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                admin_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }

            ticket = await interaction.guild.create_text_channel(
                name=f"tiket-{interaction.user.name}",
                category=category,
                overwrites=overwrites
            )

            await ticket.edit(
                topic=f"{self.panel_id}|{interaction.user.id}|{product}|{price}"
            )

            embed = discord.Embed(
                title="Pembayaran",
                description=f"Produk: **{product}**\nHarga: **{format_rupiah(price)}**",
                color=discord.Color.green()
        )
 
            embed.set_image(url=QRIS_IMAGE_URL)
            
            await ticket.send(
                interaction.user.mention,
                embed=embed,
                view=OrderView()
            )
 
            await interaction.response.send_message(
                f"✅ Ticket dibuat: {ticket.mention}",
                ephemeral=True
            )
 
        return callback
 
# -----------------------
# COMMANDS
# -----------------------
 
 
# -----------------------
# COMMANDS
# -----------------------
@bot.tree.command(name="create")
@app_commands.describe(title="Judul embed", slot1="Slot 1", slot2="Slot 2", slot3="Slot 3", slot4="Slot 4", slot5="Slot 5")
async def create(interaction: discord.Interaction, title: str, slot1: str, slot2: str = None, slot3: str = None, slot4: str = None, slot5: str = None):
    if ADMIN_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ Tidak ada akses", ephemeral=True)
        return
    embed_id = str(interaction.id)
    slots_data[embed_id] = {}
    save_slots()
    embed = discord.Embed(title=title, description="Klik tombol daftar untuk memesan slot", color=discord.Color.green())
    embed.set_footer(text=embed_id)
    for slot in [slot1, slot2, slot3, slot4, slot5]:
        if slot and slot.strip():
            slots_data[embed_id][slot] = {"price": 0, "users": []}
            embed.add_field(name=f"🍀 {slot} (Waiting)", value=f"{progress_bar(0)} 0/{MAX_SLOT}", inline=False)
    await interaction.channel.send(embed=embed, view=MainView(embed_id))
    save_slots()
    await interaction.response.send_message("✅ Panel berhasil dibuat", ephemeral=True)
 
@tasks.loop(hours=24)
async def reset_daily():
    today = today_str()
    for ch_id in progress_data:
        progress_data[ch_id]["daily"] = {today: 0}
        ch = bot.get_channel(int(ch_id))
        if ch: await update_progress_embed(ch)
    save_progress()



@bot.event
async def on_ready():
    # Load persistent views
    for panel_id in panels:
        bot.add_view(ProductView(panel_id))
 
    bot.add_view(TicketView())
    bot.add_view(OrderView())
 
    await bot.tree.sync()
    load_create()
    for embed_id in slots_data:
        bot.add_view(MainView(embed_id))
        
    load_chatstandby()
    save_chatstandby()
    print(slots_data)
    print(f"✅ Bot siap: {bot.user}")
    reset_daily.start()
    
    
@bot.event
async def on_message(message):
    if message.author.bot:
        return
 
    if message.author.id in waiting_qris:
        if not message.attachments:
            return
 
        data = waiting_qris.pop(message.author.id)
        qris_url = message.attachments[0].url
 
        panels[data["panel_id"]] = {
            "title": data["title"],
            "channel": data["channel"],
            "category": data["category"],
            "admin_role": data["admin_role"],
            "log_channel": data["log_channel"],
            "progress_channel": data["progress_channel"],
            "products": data["products"],
            "qris": qris_url
        }
        save_panels()
 
        channel = bot.get_channel(data["channel"])
        embed = discord.Embed(
            title=data["title"],
            description="Silahkan pilih produk:",
            color=discord.Color.green()
        )
 
        await channel.send(
            embed=embed,
            view=ProductView(data["panel_id"])
        )
        await message.reply("✅ Panel berhasil dibuat")

    if message.author.bot:
        return

    ch_id = str(message.channel.id)

    if ch_id in chatstandby:

        data = chatstandby[ch_id]

        # hapus pesan standby lama
        if data.get("last_message_id"):
            try:
                old = await message.channel.fetch_message(data["last_message_id"])
                await old.delete()
            except:
                pass

        # kirim standby baru
        new_msg = await message.channel.send(data["message"])

        chatstandby[ch_id]["last_message_id"] = new_msg.id

        save_chatstandby()
            
    await bot.process_commands(message)

            
@bot.tree.command(name="takerole")
async def takerole(interaction: discord.Interaction, title: str, channel: discord.TextChannel, role: discord.Role):
    if ADMIN_ROLE_ID not in [r.id for r in interaction.user.roles]:
        await interaction.response.send_message("❌ No access", ephemeral=True)
        return
    embed = discord.Embed(title=title, description="Klik tombol dibawah untuk mendapatkan role", color=discord.Color.green())
    class TakeRoleView(discord.ui.View):
        def __init__(self, r): super().__init__(timeout=None); self.role = r
        @discord.ui.button(label="Ambil Role", style=discord.ButtonStyle.green)
        async def take(self, i, b):
            if self.role in i.user.roles: await i.response.send_message("Sudah punya", ephemeral=True)
            else: await i.user.add_roles(self.role); await i.response.send_message("Berhasil", ephemeral=True)
    await channel.send(embed=embed, view=TakeRoleView(role))
    await interaction.response.send_message("✅ Embed dibuat", ephemeral=True)
 
@bot.tree.command(name="roadto20jt")
async def roadto20jt(interaction: discord.Interaction, channel: discord.TextChannel, start: int = 0, target: int = 20000000):
    if ADMIN_ROLE_ID not in [r.id for r in interaction.user.roles]:
        await interaction.response.send_message("❌ No access", ephemeral=True)
        return
    progress_data[str(channel.id)] = {"current": start, "target": target, "daily": {today_str(): 0}}
    embed = discord.Embed(title="🚀 Road to Kesuksesan Finansial", color=discord.Color.green())
    msg = await channel.send(embed=embed)
    progress_data[str(channel.id)]["message_id"] = msg.id
    await update_progress_embed(channel)
    save_progress()
    await interaction.response.send_message("✅ Progress dibuat", ephemeral=True)
 
@bot.tree.command(name="tambah")
async def tambah(interaction: discord.Interaction, nominal: int, channel: discord.TextChannel):
    if ADMIN_ROLE_ID not in [r.id for r in interaction.user.roles]:
        await interaction.response.send_message("❌ No access", ephemeral=True)
        return
    ch_id = str(channel.id)
    if ch_id not in progress_data:
        await interaction.response.send_message("❌ Not found", ephemeral=True)
        return
    progress_data[ch_id]["current"] += nominal
    today = today_str()
    progress_data[ch_id]["daily"][today] = progress_data[ch_id]["daily"].get(today, 0) + nominal
    await update_progress_embed(channel)
    save_progress()
    await interaction.response.send_message(f"✅ Added {format_rupiah(nominal)}", ephemeral=True)
 
 
@bot.tree.command(name="custom-embed")
@app_commands.describe(
    title="Judul panel",
    channel="Channel panel",
    category="Category ticket",
    admin_role="Role admin",
    log_channel="Channel log pesanan",
    progress_channel="Channel progress",
    products="Format: Netflix|50000,Spotify|30000"
)
async def custom_embed(
    interaction: discord.Interaction,
    title: str,
    channel: discord.TextChannel,
    category: discord.CategoryChannel,
    admin_role: discord.Role,
    log_channel: discord.TextChannel,
    progress_channel: discord.TextChannel,
    products: str
):
    if ADMIN_ROLE_ID not in [r.id for r in interaction.user.roles]:
        await interaction.response.send_message("❌ Tidak ada akses", ephemeral=True)
        return
 
    panel_id = str(interaction.id)
 
    # Parse produk
    product_dict = {}
    try:
        for item in products.split(","):
            name, price = item.split("|")
            product_dict[name.strip()] = int(price.strip())
    except Exception as e:
        await interaction.response.send_message(f"❌ Format produk salah: {e}", ephemeral=True)
        return
 
    panels[panel_id] = {
        "title": title,
        "channel": channel.id,
        "category": category.id,
        "admin_role": admin_role.id,
        "progress_channel": progress_channel.id,
        "products": product_dict,
        "qris": QRIS_IMAGE_URL,
        "log_channel": log_channel.id
    }
    save_panels()
 
    # Kirim embed panel
    embed = discord.Embed(
        title=title,
        description="Klik tombol untuk membeli",
        color=discord.Color.green()
    )
    await channel.send(embed=embed, view=ProductView(panel_id))
 
    await interaction.response.send_message("✅ Panel berhasil dibuat", ephemeral=True)

 #chatscandby
@bot.tree.command(name="chatstanby", description="Aktifkan chat standby otomatis")
@app_commands.describe(
    channel="Channel tujuan",
    message="Pesan standby",
    thread="Thread opsional (untuk forum / thread tertentu)"
)
async def chatstanby(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str,
    thread: discord.Thread = None
):

    # cek admin role
    if ADMIN_ROLE_ID not in [r.id for r in interaction.user.roles]:
        await interaction.response.send_message(
            "❌ Admin only",
            ephemeral=True
        )
        return
        
    # kalau thread dipilih → pakai thread
        
        await interaction.response.defer(ephemeral=True)
    target = thread if thread else channel

    ch_id = str(target.id)

    chatstandby[ch_id] = {
        "message": message,
        "last_message_id": None
    }

    save_chatstandby()

    await interaction.response.send_message(
        f"✅ Chat standby aktif di {target.mention}",
        ephemeral=True
    )

async def keep_alive():
    while True:
        print("Bot masih hidup...")
        await asyncio.sleep(300)  # tiap 5 menit

bot.loop.create_task(keep_alive())

bot.run(TOKEN, reconnect=True)

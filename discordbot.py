# bot.py

#  
# https://discord.com/api/oauth2/authorize?client_id=1363106545449304144&permissions=2147485696&scope=bot%20applications.commands

import os, asyncio
import subprocess
from datetime import datetime
import discord
from discord.ext import commands
import aiomysql
from dotenv import load_dotenv
import io
from discord import File, Embed

# -------------------------------
# Logging setup
# -------------------------------
import logging
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

print("🔧  Starting bot …")

# -------------------------------
# Load secrets from .env
# -------------------------------
load_dotenv()
TOKEN    = os.getenv("DISCORD_TOKEN")
DB_PASS  = os.getenv("DB_PASS")
SOCKET   = "/var/run/mysqld/mysqld-bot.sock"

# -------------------------------
# Show diagnostic info
# -------------------------------
print("🔍 Trying to connect with socket:", SOCKET)
print("🔍 DB_PASS (first 5 chars):", DB_PASS[:5])
print("🔍 Socket exists:", os.path.exists(SOCKET))

# -------------------------------
# Bot setup
# -------------------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# -------------------------------
# Helper to see if user is admin
# -------------------------------
async def is_admin(discord_id: int) -> bool:
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT role FROM users WHERE discord_id = %s", (discord_id,))
            row = await cur.fetchone()
            return row and row[0].upper() == "ADMIN"

# -------------------------------
# On ready event
# -------------------------------
@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} (ID {bot.user.id})")

    # DB connection pool
    bot.db = await aiomysql.create_pool(
        user="discord_bot",
        password=DB_PASS,
        unix_socket=SOCKET,
        db="team_inventory",
        autocommit=True
    )

    print("🗄️   DB pool ready")

    # Sync slash commands
    synced = await bot.tree.sync()
    print(f"🔄  Synced {len(synced)} slash commands")

# -------------------------------
# Clean shutdown
# -------------------------------
@bot.event
async def on_close():
    bot.db.close()
    await bot.db.wait_closed()

# -------------------------------
# /stock — Show inventory
# -------------------------------
@bot.tree.command(description="Afficher l'inventaire (visible seulement par toi)")
async def stock(interaction: discord.Interaction):
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, item, size, quantity, prix FROM stock WHERE quantity > 0 ORDER BY item, size")
            rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("📦 Aucun article en stock.", ephemeral=True)
        return

    from collections import defaultdict
    grouped = defaultdict(list)
    for id, item, size, qty, prix in rows:
        grouped[item, prix].append((id, size, qty))

    lines = ["🛍️ **Inventaire de l'Équipe**"]
    for (item, prix), entries in grouped.items():
        lines.append(f"\n__**{item}**__ — {prix:.2f} $")
        for id, size, qty in entries:
            lines.append(f"`#{id}` Taille {size} — Qte: `{qty}`")

    message = "\n".join(lines)
    await interaction.response.send_message(message, ephemeral=True)

# -------------------------------
# /acheter — Buy an item
# -------------------------------
@bot.tree.command(description="Acheter un item")
async def acheter(interaction: discord.Interaction, id: int, quantité: int):
    user = interaction.user

    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            # Get item info
            await cur.execute("SELECT item, size, quantity, prix FROM stock WHERE id = %s", (id,))
            row = await cur.fetchone()

            if not row:
                await interaction.response.send_message("❌ Article introuvable.", ephemeral=True)
                return

            item, size, stock_qty, prix = row

            if quantité > stock_qty:
                await interaction.response.send_message(f"❌ Stock insuffisant: seulement {stock_qty} en inventaire.", ephemeral=True)
                return

            # Update quantity
            new_qty = stock_qty - quantité
            await cur.execute("UPDATE stock SET quantity = %s WHERE id = %s", (new_qty, id))

    total = round(prix * quantité, 2)
    await interaction.response.send_message(
        f"✅ Achat confirmé pour **{item}** ({size}) x{quantité} — Total: `{total:.2f} $`", ephemeral=True
    )

    # Get buyer email from users table
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email FROM users WHERE discord_id = %s", (user.id,))
            email_row = await cur.fetchone()

    user_email = email_row[0] if email_row else None
    username   = f"{user.name} (ID: {user.id})"
    now        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Compose message
    report = (
        f"Subject: Achat Item Siboire - Café William: {now}\n\n"
        f"Nouvel achat:\n"
        f"Par: {username}\n"
        f"Article: {item} ({size})\n"
        f"Quantité: {quantité}\n"
        f"Prix unitaire: {prix:.2f} $\n"
        f"Total: {total:.2f} $"
        f"\n\nSVP, Faire un transfert à maxime.t.turcotte@gmail.com ou 873-682-1983\nRéponse:achat\n\nMerci"
    )

    # Recipient list
    recipients = ["maxime.t.turcotte@gmail.com"]
    if user_email:
        recipients.append(user_email)

    # Send the email
    try:
        sendmail_cmd = ["/usr/sbin/sendmail", "-v", "-F", "Siboire - Café William"] + recipients
        subprocess.run(sendmail_cmd, input=report.encode(), check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Erreur d'envoi de l'email: {e}")

# -------------------------------
# /contact_table — Info coureur (table)
# -------------------------------
@bot.tree.command(description="💻 Voir les contacts dans un tableau (vue bureau)")
async def contact_table(interaction: discord.Interaction):
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT first_name, last_name, tel, email FROM users ORDER BY last_name, first_name"
            )
            rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("❌ Aucun contact trouvé.", ephemeral=True)
        return

    # Format as fixed-width table
    lines = [
        f"{'Prénom':<15} {'Nom':<20} {'Téléphone':<18} {'Email'}",
        f"{'-'*15} {'-'*20} {'-'*18} {'-'*30}"
    ]
    for first_name, last_name, tel, email in rows:
        lines.append(f"{first_name:<15} {last_name:<20} {tel or '-':<18} {email or '-'}")

    message = "```\n" + "\n".join(lines) + "\n```"
    await interaction.response.send_message(message, ephemeral=True)

# -------------------------------
# /contact — Info coureur (cell)
# -------------------------------
@bot.tree.command(description="📇 Voir les contacts de l'équipe (copie facile)")
async def contact(interaction: discord.Interaction):
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT first_name, last_name, tel, email FROM users ORDER BY last_name, first_name"
            )
            rows = await cur.fetchall()

    if not rows:
        await interaction.response.send_message("❌ Aucun contact trouvé.", ephemeral=True)
        return

    lines = ["📇 **Contacts de l'équipe:**"]
    for first, last, tel, email in rows:
        tel_display = f"`{tel}`" if tel else "_aucun numéro_"
        email_display = f"`{email}`" if email else "_aucun email_"
        lines.append(
            f"**{first} {last}**\n"
            f"{tel_display}\n"
            f"{email_display}\n"
        )

    await interaction.response.send_message("\n".join(lines), ephemeral=True)

# -------------------------------
# /recu — Enter a receipt with image
# -------------------------------
@bot.tree.command(name="recu", description="Ajouter un reçu avec image")
async def recu(
    interaction: discord.Interaction,
    amount: float,
    description: str,
    image: discord.Attachment,
):
    """Store a receipt record with its image in the DB."""
    # Download the attachment bytes
    img_bytes = await image.read()

    # Insert into DB (make sure your `factures` table has an `image_blob` BLOB column)
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO factures
                  (discord_id, amount, description, image_blob, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (interaction.user.id, amount, description, img_bytes)
            )

    await interaction.response.send_message(
        "✅ Reçu enregistré avec image !", ephemeral=True
    )


# -------------------------------
# /recu_info — Info sur tous les recus
# -------------------------------
@bot.tree.command(description="Voir tous tes reçus")
async def recu_info(interaction: discord.Interaction):
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT id, amount, description, created_at
                FROM factures
                WHERE discord_id = %s
                ORDER BY created_at DESC
            """, (interaction.user.id,))
            rows = await cur.fetchall()

            await cur.execute("""
                SELECT SUM(amount) FROM factures WHERE discord_id = %s
            """, (interaction.user.id,))
            total = await cur.fetchone()

    if not rows:
        await interaction.response.send_message("🧾 Aucun reçu trouvé.", ephemeral=True)
        return

    lines = [f"🧾 **Reçus de {interaction.user.display_name}**"]
    for fid, amount, desc, created in rows:
        lines.append(f"`#{fid}` {created:%Y-%m-%d} - {desc}: {amount:.2f} $")
    lines.append(f"\n**Total dû**: `{total[0]:.2f} $`")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


# -------------------------------
# /recu_enleve - Enleve un recu
# -------------------------------
@bot.tree.command(description="Supprimer un reçu")
async def recu_enleve(interaction: discord.Interaction, id: int):
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                DELETE FROM factures
                WHERE id = %s AND discord_id = %s
            """, (id, interaction.user.id))
            if cur.rowcount == 0:
                await interaction.response.send_message("❌ Reçu introuvable ou non autorisé.", ephemeral=True)
                return

    await interaction.response.send_message("🗑️ Reçu supprimée avec succès.", ephemeral=True)

# -------------------------------
# /recus_admin - Voir toutes les factures
# -------------------------------
@bot.tree.command(description="📋 Voir tous les reçus (admin seulement)")
async def recus_admin(interaction: discord.Interaction):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin seulement.", ephemeral=True)
        return

    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            # Fetch all users
            await cur.execute("SELECT discord_id, first_name, last_name FROM users ORDER BY last_name, first_name")
            users = await cur.fetchall()

            # Fetch all receipts
            await cur.execute("SELECT id, discord_id, amount, description, created_at FROM factures ORDER BY created_at ASC")
            all_receipts = await cur.fetchall()

    # Organize receipts by user
    receipt_map = {}
    for rid, uid, amt, desc, created in all_receipts:
        receipt_map.setdefault(uid, []).append((rid, amt, desc, created))

    lines = ["🧾 **Résumé des reçus par personne :**"]
    total_global = 0

    for discord_id, first, last in users:
        receipts = receipt_map.get(discord_id, [])
        total_user = sum(r[1] for r in receipts)
        total_global += total_user

        lines.append(f"\n👤 **{first} {last}** — Total: `{total_user:.2f} $`")

        if receipts:
            for rid, amt, desc, created in receipts:
                lines.append(f"  • `{created.strftime('%Y-%m-%d')}` - {desc}: `{amt:.2f} $`")
        else:
            lines.append("  _Aucun reçu._")

    lines.append(f"\n🧾 **Total général: `{total_global:.2f} $`**")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

# -------------------------------
# /update_tel - Update tel number
# -------------------------------
@bot.tree.command(description="📞 Met à jour ton numéro de téléphone")
async def update_tel(interaction: discord.Interaction, tel: str):
    discord_id = interaction.user.id

    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET tel = %s WHERE discord_id = %s", (tel, discord_id)
            )

    await interaction.response.send_message(
        f"✅ Ton numéro de téléphone a été mis à jour: `{tel}`", ephemeral=True
    )
# -------------------------------
# /update_mail - Update mail
# -------------------------------
@bot.tree.command(description="📧 Met à jour ton adresse email")
async def update_mail(interaction: discord.Interaction, mail: str):
    discord_id = interaction.user.id

    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET email = %s WHERE discord_id = %s", (mail, discord_id)
            )

    await interaction.response.send_message(
        f"✅ Ton adresse email a été mise à jour: `{mail}`", ephemeral=True
    )

class ValidationView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, receipts: list):
        super().__init__(timeout=None)
        self.interaction = interaction
        self.receipts = receipts
        self.index = 0
        self.message: discord.Message | None = None

    async def send_current(self):
        rec_id, user_id, amount, description, created = self.receipts[self.index]

        # fetch the image blob
        async with bot.db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT image_blob FROM factures WHERE id = %s", (rec_id,)
                )
                row = await cur.fetchone()
        img_bytes = row[0] if row else None

        # build embed
        embed = Embed(
            title=f"Reçu #{rec_id}",
            description=description,
            timestamp=created
        )
        embed.add_field(name="Montant", value=f"{amount:.2f} $")
        embed.add_field(name="Par", value=f"<@{user_id}>")

        files = []
        if img_bytes:
            fp = io.BytesIO(img_bytes)
            file = File(fp, filename=f"recu_{rec_id}.jpg")
            embed.set_image(url=f"attachment://recu_{rec_id}.jpg")
            files = [file]

        if not self.message:
            # first send
            self.message = await self.interaction.response.send_message(
                embed=embed, files=files, view=self, ephemeral=True
            )
        else:
            # edit existing
            await self.message.edit(embed=embed, attachments=files, view=self)

    @discord.ui.button(label="Accepter", style=discord.ButtonStyle.success)
    async def accept(self, button: discord.ui.Button, interaction: discord.Interaction):
        rec_id = self.receipts[self.index][0]
        async with bot.db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE factures SET state = 'accepted' WHERE id = %s", (rec_id,)
                )
        await interaction.response.defer()
        self.index += 1
        if self.index < len(self.receipts):
            await self.send_current()
        else:
            await interaction.followup.send("✅ Tous les reçus ont été traités.", ephemeral=True)
            self.stop()

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger)
    async def refuse(self, button: discord.ui.Button, interaction: discord.Interaction):
        rec_id = self.receipts[self.index][0]
        async with bot.db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE factures SET state = 'refused' WHERE id = %s", (rec_id,)
                )
        await interaction.response.defer()
        self.index += 1
        if self.index < len(self.receipts):
            await self.send_current()
        else:
            await interaction.followup.send("✅ Tous les reçus ont été traités.", ephemeral=True)
            self.stop()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        self.index += 1
        if self.index < len(self.receipts):
            await self.send_current()
        else:
            await interaction.followup.send("✅ Validation terminée.", ephemeral=True)
            self.stop()

    @discord.ui.button(label="End", style=discord.ButtonStyle.secondary)
    async def end(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_message("❌ Validation interrompue.", ephemeral=True)
        self.stop()


@bot.tree.command(name="validation", description="Valider les reçus en attente (admin uniquement)")
async def validation(interaction: discord.Interaction):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin seulement.", ephemeral=True)
        return

    # fetch all pending receipts
    async with bot.db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, discord_id, amount, description, created_at "
                "FROM factures WHERE state = 'pending' ORDER BY created_at ASC"
            )
            pending = await cur.fetchall()

    if not pending:
        await interaction.response.send_message("✅ Il n'y a pas de reçus en attente.", ephemeral=True)
        return

    view = ValidationView(interaction, pending)
    await view.send_current()



# -------------------------------
# Main entry point
# -------------------------------
async def main():
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

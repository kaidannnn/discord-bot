import discord
from discord.ext import commands
from discord.utils import get
import asyncio
from PIL import Image, ImageDraw, ImageFont
import os
import random
import json

# --- CONFIGURATION ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("TOKEN")
GUILD_ID = 1224387427674620006  # Remplace par l'ID de ton serveur
logs_channel_id = 1374160850654466118  # ID du salon de logs
xp_file = "xp.json"
banned_words = ["insulte1", "insulte2", "insulte3"]

# Variables globales
welcome_background = None

# --- XP SYSTEME ---
def charger_xp():
    if os.path.exists(xp_file):
        with open(xp_file, "r") as f:
            return json.load(f)
    return {}

def sauvegarder_xp(data):
    with open(xp_file, "w") as f:
        json.dump(data, f)

xp_data = charger_xp()

async def verifier_role_niveau(member, niveau):
    roles_niveaux = {
        5: "🥉 Bronze",
        10: "🥈 Argent",
        15: "🥇 Or",
        20: "💎 Diamant"
    }

    if niveau in roles_niveaux:
        role_name = roles_niveaux[niveau]
        role = discord.utils.get(member.guild.roles, name=role_name)
        if not role:
            role = await member.guild.create_role(name=role_name, color=discord.Color.random())
        if role not in member.roles:
            await member.add_roles(role)
            return role_name
    return None

def ajouter_xp(user_id):
    if str(user_id) not in xp_data:
        xp_data[str(user_id)] = {"xp": 0, "level": 1}
    xp_data[str(user_id)]["xp"] += random.randint(5, 15)
    niveau = xp_data[str(user_id)]["level"]
    if xp_data[str(user_id)]["xp"] >= niveau * 100:
        xp_data[str(user_id)]["level"] += 1
        return True
    return False

def error_handler(func):
    async def wrapper(*args, **kwargs):
        try:
            await func(*args, **kwargs)
        except Exception as e:
            await args[0].send(f"❌ Une erreur est survenue : {str(e)}")
    return wrapper

# --- EVENEMENTS DISCORD ---
@bot.event
async def on_ready():
    print(f"{bot.user} est prêt et connecté !")
    await bot.change_presence(activity=discord.Game(name="!help pour voir les commandes"))

# Supprime la commande help par défaut
bot.remove_command('help')

class HelpView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=60)
        self.ctx = ctx

    @discord.ui.button(label="Commandes Générales", style=discord.ButtonStyle.primary, emoji="👥")
    async def general_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👥 Commandes Générales",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Utilitaires",
            value="""
            `!ping` - Vérifier si le bot est en ligne
            `!niveau [@membre]` - Voir le niveau d'un membre
            `!top` - Voir le classement des membres
            `!bienvenue [@membre]` - Simuler un message de bienvenue
            """,
            inline=False
        )
        embed.add_field(
            name="Tickets",
            value="""
            `!ticket` - Ouvrir un nouveau ticket de support
            `!fermer` - Fermer un ticket (dans le salon du ticket)
            """,
            inline=False
        )
        embed.add_field(
            name="Fun",
            value="""
            `!pile_face` - Jouer à pile ou face
            `!dé [faces]` - Lancer un dé
            `!sondage <question>` - Créer un sondage
            `!embed <#couleur> <message>` - Créer un message embed coloré
            """,
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Commandes Modération", style=discord.ButtonStyle.danger, emoji="🛡️")
    async def mod_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.ctx.author.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Vous n'avez pas accès aux commandes de modération !", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛡️ Commandes de Modération",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Gestion des membres",
            value="""
            `!mute @membre` - Rendre muet un membre
            `!unmute @membre` - Rendre la parole à un membre
            `!kick @membre [raison]` - Expulser un membre
            `!ban @membre [raison]` - Bannir un membre
            """,
            inline=False
        )
        embed.add_field(
            name="Configuration",
            value="""
            `!set_welcome` - Définir l'image de bienvenue
            `!setup_ticket` - Configurer le système de tickets
            """,
            inline=False
        )
        await interaction.response.edit_message(embed=embed, view=self)

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="📚 Menu d'Aide",
        description="Cliquez sur un bouton ci-dessous pour voir les commandes de chaque catégorie.",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Demandé par {ctx.author.name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    view = HelpView(ctx)
    await ctx.send(embed=embed, view=view)

spam_check = {}

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Filtrage d’insultes
    if any(mot in message.content.lower() for mot in banned_words):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, ce mot est interdit ! 🚫")
        return

    # Anti-spam
    author_id = str(message.author.id)
    current_time = message.created_at.timestamp()

    if author_id not in spam_check:
        spam_check[author_id] = {
            "last_message_time": current_time,
            "messages": [current_time]
        }
    else:
        spam_check[author_id]["messages"].append(current_time)

        # Garder seulement les messages des 5 dernières secondes
        spam_check[author_id]["messages"] = [t for t in spam_check[author_id]["messages"] if current_time - t <= 5]

        # Si plus de 10 messages en 5 secondes
        if len(spam_check[author_id]["messages"]) > 10:
            await message.channel.send(f"{message.author.mention}, attention à ne pas spam ! ⚠️")
            del spam_check[author_id]  # Réinitialise pour éviter le spam continu

    # XP
    leveled_up = ajouter_xp(message.author.id)
    sauvegarder_xp(xp_data)
    if leveled_up:
        nouveau_niveau = xp_data[str(message.author.id)]['level']
        embed = discord.Embed(
            title="⭐ Niveau Supérieur !",
            description=f"{message.author.mention} passe au **niveau {nouveau_niveau}** !",
            color=discord.Color.green()
        )
        await message.channel.send(embed=embed)

        # Vérifier les récompenses de rôle
        nouveau_role = await verifier_role_niveau(message.author, nouveau_niveau)
        if nouveau_role:
            await message.channel.send(f"🎁 {message.author.mention} a débloqué le rôle **{nouveau_role}** !")

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    # Création image de bienvenue
    if welcome_background:
        img = Image.open(welcome_background).copy()
        img = img.resize((600, 300))
    else:
        img = Image.new("RGB", (600, 300), color=(40, 44, 52))

    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((30, 120), f"Bienvenue {member.name} sur le serveur !", font=font, fill=(255, 255, 255))
    path = f"{member.id}_welcome.png"
    img.save(path)

    # Envoi dans le salon #général
    salon = get(member.guild.text_channels, name="général")
    if salon:
        await salon.send(content=f"🎉 Bienvenue {member.mention} !", file=discord.File(path))
    os.remove(path)

# --- SYSTÈME DE MUSIQUE ---
from discord import FFmpegPCMAudio
import yt_dlp

# Configuration yt-dlp
ydl_opts = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'no_warnings': True,
    'quiet': True
}

queues = {}

@bot.command()
async def join(ctx):
    """Rejoint le salon vocal"""
    if ctx.author.voice is None:
        await ctx.send("❌ Vous devez être dans un salon vocal !")
        return
    
    voice_channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await voice_channel.connect()
    else:
        await ctx.voice_client.move_to(voice_channel)
    await ctx.send(f"✅ Connecté à {voice_channel.name}")

@bot.command()
async def leave(ctx):
    """Quitte le salon vocal"""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Au revoir !")

@bot.command()
@error_handler
async def play(ctx, *, query):
    """Joue une musique depuis YouTube (URL ou recherche)"""
    if ctx.author.voice is None:
        await ctx.send("❌ Vous devez être dans un salon vocal !")
        return

    if ctx.voice_client is None:
        await ctx.author.voice.channel.connect()

    # Configuration youtube_dl pour la recherche
    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }
    
    ydl_search_opts = {
        'format': 'bestaudio/best',
        'default_search': 'ytsearch',
        'noplaylist': True,
        'quiet': True
    }

    with yt_dlp.YoutubeDL(ydl_search_opts) as ydl:
        try:
            # Recherche la vidéo
            info = ydl.extract_info(query, download=False)
            
            # Si c'est une recherche, prendre le premier résultat
            if 'entries' in info:
                info = info['entries'][0]
            
            url = info['url']
            titre = info['title']
            
            ctx.voice_client.stop()
            ctx.voice_client.play(FFmpegPCMAudio(url, **FFMPEG_OPTIONS))

            embed = discord.Embed(
                title="🎵 Lecture en cours",
                description=f"**{titre}**",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send("❌ Erreur lors de la lecture de la musique")

@bot.command()
async def pause(ctx):
    """Met en pause la musique"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Musique en pause")

@bot.command()
async def resume(ctx):
    """Reprend la lecture"""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Lecture reprise")

@bot.command()
async def stop(ctx):
    """Arrête la musique"""
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send("⏹️ Musique arrêtée")

@bot.command()
async def loop(ctx):
    """Boucle la musique en cours"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.loop = not ctx.voice_client.loop
        state = "activé" if ctx.voice_client.loop else "désactivé"
        await ctx.send(f"🔁 Boucle de musique {state} !")
    else:
        await ctx.send("❌ Aucune musique ne joue actuellement.")

# --- CLASH OF CLANS ---
import coc

# Initialisation du client CoC
try:
    coc_client = coc.Client()
    coc_client.set_credentials(
        email=os.getenv("COC_EMAIL"),
        password=os.getenv("COC_PASSWORD")
    )
except Exception as e:
    print(f"Erreur d'initialisation du client CoC: {e}")
    coc_client = None

@bot.group(name="coc")
async def coc_group(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("❌ Commande invalide. Utilisez `!help coc` pour voir les commandes disponibles.")

@coc_group.command(name="profil")
async def coc_profile(ctx, tag: str):
    """Affiche les stats d'un joueur CoC"""
    try:
        # Nettoyer le tag
        if tag.startswith('#'):
            tag = tag[1:]
        
        player = await coc_client.get_player(f'#{tag}')
        
        embed = discord.Embed(
            title=f"👑 Profil de {player.name}",
            color=discord.Color.gold()
        )
        
        embed.add_field(name="Niveau HDV", value=player.town_hall, inline=True)
        embed.add_field(name="Niveau", value=player.exp_level, inline=True)
        embed.add_field(name="Trophées", value=player.trophies, inline=True)
        embed.add_field(name="Clan", value=player.clan.name if player.clan else "Aucun", inline=True)
        embed.add_field(name="Rôle", value=player.role if player.clan else "N/A", inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send("❌ Erreur: Impossible de trouver ce joueur. Vérifiez le tag.")

@coc_group.command(name="clan")
async def coc_clan(ctx, tag: str):
    """Affiche les infos d'un clan CoC"""
    try:
        if tag.startswith('#'):
            tag = tag[1:]
            
        clan = await coc_client.get_clan(f'#{tag}')
        
        embed = discord.Embed(
            title=f"🛡️ Clan: {clan.name}",
            description=clan.description,
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Niveau", value=clan.level, inline=True)
        embed.add_field(name="Points", value=clan.points, inline=True)
        embed.add_field(name="Membres", value=f"{clan.member_count}/50", inline=True)
        embed.add_field(name="Type", value=clan.type, inline=True)
        embed.add_field(name="Trophées Guerre", value=clan.war_league, inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send("❌ Erreur: Impossible de trouver ce clan. Vérifiez le tag.")

# --- COMMANDES ---
@bot.command()
async def ping(ctx):
    embed = discord.Embed(
        title="🏓 Pong !",
        description=f"Latence: {round(bot.latency * 1000)}ms",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)
    await ctx.message.add_reaction("🏓")

@bot.command()
async def niveau(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    data = xp_data.get(str(membre.id), {"xp": 0, "level": 1})
    embed = discord.Embed(
        title=f"📊 Niveau de {membre.name}",
        color=discord.Color.purple()
    )
    embed.add_field(name="Niveau", value=f"**{data['level']}**", inline=True)
    embed.add_field(name="XP", value=f"**{data['xp']}**", inline=True)
    embed.set_thumbnail(url=membre.avatar.url if membre.avatar else None)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def set_welcome(ctx):
    global welcome_background
    if not ctx.message.attachments:
        await ctx.send("❌ Merci d'attacher une image à votre message !")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
        await ctx.send("❌ Le fichier doit être une image (PNG, JPG) !")
        return

    await attachment.save("welcome_bg.png")
    welcome_background = "welcome_bg.png"
    await ctx.send("✅ L'image de bienvenue a été mise à jour !")

@bot.command()
async def bienvenue(ctx, membre: discord.Member = None):
    membre = membre or ctx.author
    # Création image de bienvenue
    img = Image.new("RGB", (600, 300), color=(40, 44, 52))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((30, 120), f"Bienvenue {membre.name} sur le serveur !", font=font, fill=(255, 255, 255))
    path = f"{membre.id}_welcome.png"
    img.save(path)

    # Envoi dans le salon actuel
    await ctx.send(content=f"🎉 Bienvenue {membre.mention} !", file=discord.File(path))
    os.remove(path)

# --- SYSTÈME DE TICKETS ---
async def create_ticket_from_reaction(guild, user):
    # Vérifier si un ticket existe déjà
    existing_ticket = discord.utils.get(guild.text_channels, name=f'ticket-{user.id}')
    if existing_ticket:
        return None

    # Créer une catégorie "Tickets" si elle n'existe pas
    category = discord.utils.get(guild.categories, name='Tickets')
    if not category:
        category = await guild.create_category('Tickets')

    # Créer le salon ticket
    ticket_channel = await guild.create_text_channel(
        f'ticket-{user.id}',
        category=category,
        overwrites={
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
    )

    embed = discord.Embed(
        title="🎫 Nouveau Ticket",
        description="Un membre du staff vous répondra dès que possible.\nPour fermer le ticket, utilisez `!fermer`",
        color=discord.Color.blue()
    )
    await ticket_channel.send(embed=embed)
    return ticket_channel

@bot.command()
async def setup_ticket(ctx):
    """Crée le message pour les tickets avec réaction"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Vous n'avez pas la permission d'utiliser cette commande !")
        return

    embed = discord.Embed(
        title="🎫 Créer un Ticket",
        description="Réagissez avec 🎟️ pour créer un ticket",
        color=discord.Color.blue()
    )
    message = await ctx.send(embed=embed)
    await message.add_reaction("🎟️")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member.bot:
        return

    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    # Vérifier si c'est une réaction sur le message de ticket
    if message.author == bot.user and "Créer un Ticket" in message.embeds[0].title:
        if str(payload.emoji) == "🎟️":
            # Supprimer la réaction de l'utilisateur
            await message.remove_reaction(payload.emoji, payload.member)

            # Créer le ticket
            ticket_channel = await create_ticket_from_reaction(payload.member.guild, payload.member)
            if ticket_channel:
                try:
                    await payload.member.send(f"✅ Votre ticket a été créé : {ticket_channel.mention}")
                except:
                    pass

@bot.command()
async def ticket(ctx):
    # Vérifier si un ticket existe déjà
    existing_ticket = discord.utils.get(ctx.guild.text_channels, name=f'ticket-{ctx.author.id}')
    if existing_ticket:
        await ctx.send("❌ Vous avez déjà un ticket ouvert !")
        return

    # Créer une catégorie "Tickets" si elle n'existe pas
    category = discord.utils.get(ctx.guild.categories, name='Tickets')
    if not category:
        category = await ctx.guild.create_category('Tickets')

    # Créer le salon ticket
    ticket_channel = await ctx.guild.create_text_channel(
        f'ticket-{ctx.author.id}',
        category=category,
        overwrites={
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
    )

    embed = discord.Embed(
        title="🎫 Nouveau Ticket",
        description="Un membre du staff vous répondra dès que possible.\nPour fermer le ticket, utilisez `!fermer`",
        color=discord.Color.blue()
    )
    await ticket_channel.send(embed=embed)
    await ctx.send(f"✅ Votre ticket a été créé : {ticket_channel.mention}")

@bot.command()
async def embed(ctx, couleur: str, *, message: str):
    """Crée un embed avec une couleur personnalisée
    Usage: !embed #ff0000 Mon message"""
    try:
        # Convertir la couleur hex en int
        couleur = int(couleur.strip('#'), 16)

        embed = discord.Embed(
            description=message,
            color=couleur
        )
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)

        await ctx.send(embed=embed)
        await ctx.message.delete()
    except ValueError:
        await ctx.send("❌ Format de couleur invalide ! Utilisez un code hexadécimal (ex: #ff0000)")

@bot.command()
async def pile_face(ctx):
    resultat = random.choice(["🪙 Pile", "💫 Face"])
    embed = discord.Embed(
        title="Pile ou Face",
        description=f"Le résultat est... **{resultat}** !",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

@bot.command()
async def dé(ctx, faces: int = 6):
    if faces < 2:
        await ctx.send("❌ Le dé doit avoir au moins 2 faces !")
        return
    resultat = random.randint(1, faces)
    embed = discord.Embed(
        title=f"Lancer de dé à {faces} faces",
        description=f"🎲 Le résultat est... **{resultat}** !",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@bot.command()
async def top(ctx):
    # Trier les joueurs par XP
    classement = sorted(xp_data.items(), key=lambda x: x[1]['xp'], reverse=True)

    embed = discord.Embed(
        title="🏆 Classement des membres",
        color=discord.Color.gold()
    )

    # Afficher le top 10
    for i, (user_id, data) in enumerate(classement[:10], 1):
        user = ctx.guild.get_member(int(user_id))
        if user:
            embed.add_field(
                name=f"{i}. {user.name}",
                value=f"Niveau {data['level']} - {data['xp']} XP",
                inline=False
            )

    await ctx.send(embed=embed)

@bot.command()
async def sondage(ctx, *, question):
    embed = discord.Embed(
        title="📊 Sondage",
        description=question,
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Sondage créé par {ctx.author.name}")
    message = await ctx.send(embed=embed)
    await message.add_reaction("👍")
    await message.add_reaction("👎")

@bot.command()
async def fermer(ctx):
    if not ctx.channel.name.startswith('ticket-'):
        await ctx.send("❌ Cette commande ne peut être utilisée que dans un ticket !")
        return

    await ctx.send("🔒 Le ticket va être fermé dans 5 secondes...")
    await asyncio.sleep(5)
    await ctx.channel.delete()
    await log_action(ctx.guild, f"Ticket fermé par {ctx.author}")

# --- COMMANDES DE MODÉRATION ---
@bot.command()
@commands.has_permissions(manage_messages=True)
async def mute(ctx, membre: discord.Member):
    role = get(ctx.guild.roles, name="Muted")
    if not role:
        role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(role, speak=False, send_messages=False)
    await membre.add_roles(role)
    await ctx.send(f"🔇 {membre.mention} a été mute.")
    await log_action(ctx.guild, f"{ctx.author} a mute {membre}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def unmute(ctx, membre: discord.Member):
    role = get(ctx.guild.roles, name="Muted")
    if role:
        await membre.remove_roles(role)
        await ctx.send(f"🔊 {membre.mention} a été unmute.")
        await log_action(ctx.guild, f"{ctx.author} a unmute {membre}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, membre: discord.Member, *, raison="Aucune raison"):
    await membre.kick(reason=raison)
    await ctx.send(f"👢 {membre.mention} a été expulsé.")
    await log_action(ctx.guild, f"{ctx.author} a kick {membre} : {raison}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, membre: discord.Member, *, raison="Aucune raison"):
    await membre.ban(reason=raison)
    await ctx.send(f"🔨 {membre.mention} a été banni.")
    await log_action(ctx.guild, f"{ctx.author} a banni {membre} : {raison}")

# --- LOGS ---
async def log_action(guild, message):
    salon = guild.get_channel(logs_channel_id)
    if salon:
        await salon.send(f"📝 **LOG :** {message}")

bot.run(TOKEN)
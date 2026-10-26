import time
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,Modal,reply,card,staff,safe,Confirm
from core.api import tag

class ClanModal(Modal,title='Configurer le clan'):
    value=discord.ui.TextInput(label='Tag du clan',placeholder='#...',max_length=16)
    async def on_submit(self,i):
        await staff(i); await i.response.defer(ephemeral=True)
        t=tag(str(self.value)); c=await i.client.api.clan(t)
        await i.client.db.set(i.guild_id,'clan',t)
        await i.client.db.log(i.guild_id,i.user.id,'config_clan',t)
        await reply(i,'Clan configuré',safe(c['name'])+' · '+t)

class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,key):
        self.key=key
        super().__init__(placeholder='Choisir le salon',channel_types=[discord.ChannelType.text],min_values=1,max_values=1)
    async def callback(self,i):
        await staff(i)
        channel=i.guild.get_channel(self.values[0].id)
        if not channel: raise ValueError('Salon introuvable.')
        perms=channel.permissions_for(i.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links): raise ValueError('Autorise Voir le salon, Envoyer des messages et Intégrer des liens pour Kai-Bot.')
        await i.client.db.set(i.guild_id,self.key,channel.id)
        await reply(i,'Salon enregistré',channel.mention)

class RoleSelect(discord.ui.RoleSelect):
    def __init__(self): super().__init__(placeholder='Rôle autorisé à gérer Kai-Bot',min_values=1,max_values=1)
    async def callback(self,i):
        if not i.user.guild_permissions.manage_guild: raise ValueError('Seul un administrateur disposant de Gérer le serveur peut déléguer le rôle staff.')
        role=self.values[0]
        if role.is_default(): raise ValueError('Le rôle @everyone ne peut pas être staff.')
        await i.client.db.set(i.guild_id,'staff_role',role.id)
        await reply(i,'Rôle staff enregistré',role.mention)

class AdminView(View):
    async def interaction_check(self,i):
        if not await super().interaction_check(i): return False
        await staff(i); return True
    @discord.ui.button(label='1 · Clan',style=discord.ButtonStyle.primary)
    async def clan(self,i,b): await i.response.send_modal(ClanModal())
    @discord.ui.button(label='2 · Salon guerres',style=discord.ButtonStyle.primary)
    async def war(self,i,b):
        v=View(i.user.id); v.add_item(ChannelSelect('war_channel'))
        await reply(i,'Salon de suivi','Rappel H−1 et résultat final des guerres suivies.',view=v)
    @discord.ui.button(label='3 · Salon staff',style=discord.ButtonStyle.primary)
    async def staffchannel(self,i,b):
        v=View(i.user.id); v.add_item(ChannelSelect('staff_channel'))
        await reply(i,'Salon staff','Suggestions et diagnostics. Choisis un salon privé.',view=v)
    @discord.ui.button(label='Rôle staff')
    async def role(self,i,b):
        v=View(i.user.id); v.add_item(RoleSelect())
        await reply(i,'Déléguer la gestion','Un administrateur doit choisir le rôle.',view=v)
    @discord.ui.button(label='Suivi LDC : activer/désactiver')
    async def cwl(self,i,b):
        value=not await i.client.db.get(i.guild_id,'track_cwl',False)
        await i.client.db.set(i.guild_id,'track_cwl',value)
        await reply(i,'Suivi LDC','Activé' if value else 'Désactivé')
    @discord.ui.button(label='Diagnostic')
    async def diagnostic(self,i,b): await i.client.get_cog('Admin').diagnose(i)
    @discord.ui.button(label='Sauvegarde',style=discord.ButtonStyle.success)
    async def backup(self,i,b): await i.client.get_cog('Backups').manual(i)

class AnnouncementModal(Modal,title='Préparer une annonce'):
    heading=discord.ui.TextInput(label='Titre',max_length=150)
    body=discord.ui.TextInput(label='Message',style=discord.TextStyle.paragraph,max_length=3000)
    async def on_submit(self,i):
        await staff(i)
        title=str(self.heading); body=str(self.body); channel=i.channel
        async def send(j):
            await staff(j)
            await channel.send(embed=card(title,body))
            await j.client.db.log(j.guild_id,j.user.id,'announce',str(channel.id))
            await reply(j,'Annonce publiée',channel.mention)
        await i.response.send_message(embed=card(title,body),view=Confirm(i.user.id,send),ephemeral=True)

class Admin(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def home(self,i):
        await staff(i)
        await reply(i,'ESPACE STAFF','Configure le clan puis les salons.\n\nLa sauvegarde complète est réservée au propriétaire du bot. Les droits sont vérifiés à chaque clic.',view=AdminView(i.user.id))
    async def diagnose(self,i):
        await staff(i); await i.response.defer(ephemeral=True)
        t=await self.bot.db.get(i.guild_id,'clan')
        api='Non testé : aucun clan configuré'
        if t:
            try: await self.bot.api.clan(t); api='Connexion réussie'
            except ValueError as exc: api=str(exc)
        await reply(i,'DIAGNOSTIC',f'**Modules** : {len(self.bot.cogs)} chargés\n**Clash** : {api}\n**Base de données** : connectée\n**Sauvegarde externe** : {await self.bot.db.get(0,"last_remote_backup","aucune réussite enregistrée")}\n\nLa présence de fichiers ne confirme pas un déploiement actif.')
    @app_commands.command(name='config',description='Assistant de configuration du bot')
    @app_commands.default_permissions(manage_guild=True)
    async def config(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='admin_config',description='Ouvrir les réglages staff')
    @app_commands.default_permissions(manage_guild=True)
    async def config_alias(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='announce',description='Prévisualiser puis publier une annonce dans ce salon')
    @app_commands.default_permissions(manage_guild=True)
    async def announce(self,i:discord.Interaction):
        await staff(i); await i.response.send_modal(AnnouncementModal())
    @app_commands.command(name='diagnostic',description='Vérifier la configuration et les services')
    @app_commands.default_permissions(manage_guild=True)
    async def diagnostic(self,i:discord.Interaction): await self.diagnose(i)
    @commands.command(name='syncguild')
    @commands.is_owner()
    @commands.guild_only()
    async def syncguild(self,ctx):
        self.bot.tree.copy_global_to(guild=ctx.guild)
        result=await self.bot.tree.sync(guild=ctx.guild)
        await ctx.send(embed=card('Synchronisation',f'{len(result)} commandes disponibles.'))

async def setup(bot): await bot.add_cog(Admin(bot))

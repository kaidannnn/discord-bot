import discord
from discord import app_commands
from discord.ext import commands
from core.api import tag
from core.ui import View, Modal, card, reply, Confirm

class LinkModal(Modal,title='Lier mon village'):
    player=discord.ui.TextInput(label='Tag du joueur',placeholder='#LGGY2J0JY',max_length=16)
    token=discord.ui.TextInput(label='Jeton API du jeu (pas Supercell ID)',placeholder='Paramètres → Plus de paramètres → Jeton API',max_length=100)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        t=tag(str(self.player))
        if not await i.client.api.verify(t,str(self.token)): raise ValueError('Le jeton ne correspond pas à ce village. Recopie le jeton API dans les paramètres du jeu.')
        p=await i.client.api.player(t)
        await i.client.db.link(i.guild_id,i.user.id,t)
        await reply(i,'Village lié',f'**{discord.utils.escape_markdown(p["name"])}** · {t}\nTu peux maintenant ouvrir ton profil. Le jeton de vérification n’est pas conservé.')

class AccountSelect(discord.ui.Select):
    def __init__(self,rows,action):
        self.action=action
        super().__init__(placeholder='Choisir le compte',options=[discord.SelectOption(label=r['tag']+(' · principal' if r['main'] else ''),value=r['tag']) for r in rows[:25]])
    async def callback(self,i):
        t=self.values[0]
        if self.action=='profile': return await i.client.get_cog('Clash').player_panel(i,t)
        if self.action=='main':
            await i.client.db.main(i.guild_id,i.user.id,t)
            return await reply(i,'Compte principal modifié',t)
        async def remove(j):
            await j.client.db.unlink(j.guild_id,j.user.id,t)
            await reply(j,'Compte délié',t)
        await reply(i,'Délier ce compte ?',t,view=Confirm(i.user.id,remove))

class AccountsView(View):
    @discord.ui.button(label='Lier un village',style=discord.ButtonStyle.success)
    async def link(self,i,b): await i.response.send_modal(LinkModal())
    async def choose(self,i,action):
        rows=await i.client.db.rows('SELECT * FROM links WHERE guild=? AND user=? ORDER BY main DESC,tag',(i.guild_id,i.user.id))
        if not rows: return await reply(i,'Aucun compte lié','Clique sur Lier un village.')
        v=View(i.user.id); v.add_item(AccountSelect(rows,action))
        await reply(i,'Mes villages','Choisis le compte concerné.',view=v)
    @discord.ui.button(label='Mon profil',style=discord.ButtonStyle.primary)
    async def profile(self,i,b): await self.choose(i,'profile')
    @discord.ui.button(label='Compte principal')
    async def main(self,i,b): await self.choose(i,'main')
    @discord.ui.button(label='Délier',style=discord.ButtonStyle.danger)
    async def unlink(self,i,b): await self.choose(i,'unlink')

class Accounts(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def home(self,i):
        rows=await self.bot.db.rows('SELECT * FROM links WHERE guild=? AND user=? ORDER BY main DESC,tag',(i.guild_id,i.user.id))
        text='\n'.join(('★ ' if r['main'] else '• ')+r['tag'] for r in rows) or 'Aucun village lié. Commence avec le bouton vert.'
        await reply(i,'MON COMPTE',text+'\n\nLa liaison utilise le jeton API du jeu, jamais ton mot de passe ou ton code Supercell ID.',view=AccountsView(i.user.id))
    @app_commands.command(name='link',description='Lier ton compte Clash avec vérification')
    @app_commands.checks.cooldown(2,60,key=lambda i:i.user.id)
    async def link(self,i:discord.Interaction): await i.response.send_modal(LinkModal())
    @app_commands.command(name='comptes',description='Gérer tes villages et ton compte principal')
    async def accounts(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='unlink',description='Délier un de tes comptes avec confirmation')
    async def unlink(self,i:discord.Interaction): await AccountsView(i.user.id).choose(i,'unlink')
    @app_commands.command(name='profile',description='Consulter ton profil Clash ou celui d’un membre')
    async def profile(self,i:discord.Interaction,membre:discord.Member|None=None):
        user=membre or i.user
        rows=await self.bot.db.rows('SELECT tag FROM links WHERE guild=? AND user=? ORDER BY main DESC,tag LIMIT 1',(i.guild_id,user.id))
        if not rows: return await reply(i,'Compte manquant','Ce membre doit d’abord utiliser /link.')
        await self.bot.get_cog('Clash').player_panel(i,rows[0]['tag'])

async def setup(bot): await bot.add_cog(Accounts(bot))

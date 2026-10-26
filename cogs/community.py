import time
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,Modal,reply,card,safe,staff,show_pages,Confirm

class SuggestionModal(Modal,title='Proposer une idée'):
    title_input=discord.ui.TextInput(label='Titre',max_length=100)
    body=discord.ui.TextInput(label='Ton idée concrète',style=discord.TextStyle.paragraph,max_length=1800)
    def __init__(self,category): super().__init__(); self.category=category
    async def on_submit(self,i):
        now=int(time.time())
        async with i.client.db.transaction() as c:
            async with c.execute('SELECT 1 FROM suggestions WHERE guild=? AND user=? AND created>?',(i.guild_id,i.user.id,now-60)) as q:
                if await q.fetchone(): raise ValueError('Patiente une minute entre deux suggestions.')
            cur=await c.execute('INSERT INTO suggestions(guild,user,category,title,body,created) VALUES(?,?,?,?,?,?)',(i.guild_id,i.user.id,self.category,str(self.title_input),str(self.body),now))
            sid=cur.lastrowid
        await i.response.defer(ephemeral=True)
        channel=i.guild.get_channel(await i.client.db.get(i.guild_id,'staff_channel',0))
        notified=False
        if channel:
            try:
                await channel.send(embed=card(f'Suggestion #{sid} · {safe(str(self.title_input))}',f'Catégorie : {self.category}\nAuteur : <@{i.user.id}>\n\n{safe(str(self.body))}'))
                notified=True
            except discord.HTTPException: pass
        await reply(i,'Suggestion enregistrée',f'Référence **#{sid}**. '+('Le staff a été prévenu.' if notified else 'Elle reste consultable dans la liste staff ; la notification n’a pas été envoyée.'))

class CategorySelect(discord.ui.Select):
    def __init__(self): super().__init__(placeholder='Choisir la catégorie',options=[discord.SelectOption(label=x,value=x) for x in ['Clan','E-sport','Cup','Discord','Autre']])
    async def callback(self,i): await i.response.send_modal(SuggestionModal(self.values[0]))

class StatusModal(Modal,title='Traiter une suggestion'):
    number=discord.ui.TextInput(label='Numéro de suggestion',max_length=12)
    status=discord.ui.TextInput(label='Statut',placeholder='Reçue / En étude / Acceptée / Refusée / Terminée',max_length=30)
    async def on_submit(self,i):
        await staff(i)
        try: sid=int(str(self.number))
        except ValueError: raise ValueError('Numéro invalide.')
        value=str(self.status).strip()
        if value not in ['Reçue','En étude','Acceptée','Refusée','Terminée']: raise ValueError('Utilise un des statuts proposés dans le champ.')
        rows=await i.client.db.rows('SELECT id FROM suggestions WHERE id=? AND guild=?',(sid,i.guild_id))
        if not rows: raise ValueError('Suggestion introuvable sur ce serveur.')
        await i.client.db.execute('UPDATE suggestions SET status=? WHERE id=? AND guild=?',(value,sid,i.guild_id))
        await reply(i,'Statut mis à jour',f'#{sid} · {value}')

class DMModal(Modal,title='Préparer un message privé'):
    title_input=discord.ui.TextInput(label='Titre',max_length=100)
    body=discord.ui.TextInput(label='Message',style=discord.TextStyle.paragraph,max_length=2500)
    def __init__(self,target): super().__init__(); self.target=target
    async def on_submit(self,i):
        await staff(i)
        e=card(str(self.title_input),str(self.body))
        async def send(j):
            await staff(j)
            try: await self.target.send(embed=e)
            except discord.Forbidden: return await reply(j,'Message non envoyé','Cette personne n’accepte pas les MP du bot.')
            await j.client.db.log(j.guild_id,j.user.id,'dm',str(self.target.id))
            await reply(j,'Message envoyé',f'Destinataire : {safe(self.target.display_name)}')
        await i.response.send_message(content=f'Destinataire : {self.target.mention}',embed=e,view=Confirm(i.user.id,send),ephemeral=True)

class CommunityView(View):
    @discord.ui.button(label='Suggestion',style=discord.ButtonStyle.primary)
    async def suggestion(self,i,b):
        v=View(i.user.id); v.add_item(CategorySelect()); await reply(i,'Une idée pour KAI ?','Choisis la catégorie.',view=v)
    @discord.ui.button(label='Mes suggestions')
    async def mine(self,i,b): await i.client.get_cog('Community').my_suggestions(i)
    @discord.ui.button(label='Niveaux')
    async def levels(self,i,b): await i.client.get_cog('Community').levels_panel(i)
    @discord.ui.button(label='Ouvrir un ticket')
    async def ticket(self,i,b): await i.client.get_cog('Tickets').open_ticket(i)
    @discord.ui.button(label='Suggestions staff',row=1)
    async def review(self,i,b):
        await staff(i)
        rows=await i.client.db.rows('SELECT * FROM suggestions WHERE guild=? ORDER BY id DESC LIMIT 50',(i.guild_id,))
        await show_pages(i,'Suggestions staff',[f'**#{r["id"]} · {safe(r["title"])}** · {r["status"]}\n<@{r["user"]}> · {safe(r["body"])[:220]}' for r in rows])
    @discord.ui.button(label='Changer un statut (staff)',row=1)
    async def status(self,i,b):
        await staff(i); await i.response.send_modal(StatusModal())

    @discord.ui.button(label='Rôles & anniversaire',row=2)
    async def roles(self,i,b): await i.client.get_cog('Roles').home(i)

    @discord.ui.button(label='Préférences MP',row=2)
    async def notifications(self,i,b): await i.client.get_cog('Messaging').home(i)

class Community(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def home(self,i): await reply(i,'COMMUNAUTÉ','Tes idées, ta progression et un accès direct au staff.',view=CommunityView(i.user.id))
    async def my_suggestions(self,i):
        rows=await self.bot.db.rows('SELECT * FROM suggestions WHERE guild=? AND user=? ORDER BY id DESC LIMIT 20',(i.guild_id,i.user.id))
        await show_pages(i,'Mes suggestions',[f'**#{r["id"]} · {safe(r["title"])}**\n{r["status"]}' for r in rows])
    async def levels_panel(self,i):
        rows=await self.bot.db.rows('SELECT * FROM xp WHERE guild=? ORDER BY points DESC LIMIT 50',(i.guild_id,))
        await show_pages(i,'Niveaux · activité Discord',[f'{n}. <@{r["user"]}> · Niveau {int((r["points"]/100)**0.5)} · {r["points"]} XP' for n,r in enumerate(rows,1)])
    @commands.Cog.listener()
    async def on_message(self,m):
        if not m.guild or m.author.bot or m.webhook_id: return
        now=int(time.time())
        async with self.bot.db.transaction() as c:
            await c.execute('INSERT OR IGNORE INTO xp(guild,user) VALUES(?,?)',(m.guild.id,m.author.id))
            await c.execute('UPDATE xp SET points=points+5,last=? WHERE guild=? AND user=? AND last<=?',(now,m.guild.id,m.author.id,now-10))
    @app_commands.command(name='suggestion',description='Envoyer une suggestion guidée au staff')
    async def suggestion(self,i:discord.Interaction): await CommunityView(i.user.id).suggestion.callback(i)
    @app_commands.command(name='mes_suggestions',description='Voir le suivi de tes suggestions')
    async def mine(self,i:discord.Interaction): await self.my_suggestions(i)
    @app_commands.command(name='niveau',description='Voir le classement des niveaux Discord')
    async def levels(self,i:discord.Interaction): await self.levels_panel(i)
    @app_commands.command(name='mp',description='Préparer et confirmer un message privé à un membre (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def dm(self,i:discord.Interaction,membre:discord.Member):
        await staff(i)
        if membre.bot: raise ValueError('Choisis une personne, pas un bot.')
        await i.response.send_modal(DMModal(membre))

async def setup(bot): await bot.add_cog(Community(bot))

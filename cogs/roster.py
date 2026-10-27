import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,reply,staff,show_pages,safe

class RosterSelect(discord.ui.Select):
    def __init__(self,rows,remove=False):
        self.remove=remove
        super().__init__(placeholder='Choisir ton village',options=[discord.SelectOption(label=r['tag'],value=r['tag']) for r in rows[:25]])
    async def callback(self,i):
        t=self.values[0]
        if self.remove:
            await i.client.db.execute('DELETE FROM roster WHERE guild=? AND user=? AND tag=?',(i.guild_id,i.user.id,t))
            return await reply(i,'Inscription retirée',t)
        rows=await i.client.db.rows('SELECT 1 FROM links WHERE guild=? AND user=? AND tag=?',(i.guild_id,i.user.id,t))
        if not rows: raise ValueError('Le village doit être lié à ton compte.')
        await i.client.db.execute('INSERT OR IGNORE INTO roster(guild,user,tag) VALUES(?,?,?)',(i.guild_id,i.user.id,t))
        await reply(i,'Candidature enregistrée','Le staff peut maintenant valider ton inscription dans /roster.')

class ReviewSelect(discord.ui.Select):
    def __init__(self,rows):
        super().__init__(placeholder='Valider un joueur',options=[discord.SelectOption(label=r['tag'],value=r['tag']) for r in rows[:25]])
    async def callback(self,i):
        await staff(i)
        await i.client.db.execute("UPDATE roster SET role='Validé' WHERE guild=? AND tag=?",(i.guild_id,self.values[0]))
        await reply(i,'Joueur validé',self.values[0])

class RosterView(View):
    async def choose(self,i,remove):
        rows=await i.client.db.rows('SELECT tag FROM links WHERE guild=? AND user=?',(i.guild_id,i.user.id))
        if not rows: raise ValueError('Lie d’abord un village avec /link.')
        v=View(i.user.id); v.add_item(RosterSelect(rows,remove))
        await reply(i,'Mon inscription','Choisis ton village.',view=v)
    @discord.ui.button(label='Proposer mon compte',style=discord.ButtonStyle.success)
    async def join(self,i,b): await self.choose(i,False)
    @discord.ui.button(label='Retirer mon compte')
    async def leave(self,i,b): await self.choose(i,True)
    @discord.ui.button(label='Voir le roster',style=discord.ButtonStyle.primary)
    async def list(self,i,b):
        rows=await i.client.db.rows('SELECT * FROM roster WHERE guild=? ORDER BY role,tag',(i.guild_id,))
        await show_pages(i,'ROSTER',[f'<@{r["user"]}> · **{r["tag"]}** · {r["role"]}' for r in rows])
    @discord.ui.button(label='Valider (staff)')
    async def review(self,i,b):
        await staff(i)
        rows=await i.client.db.rows("SELECT tag FROM roster WHERE guild=? AND role<>'Validé' LIMIT 25",(i.guild_id,))
        if not rows: return await reply(i,'Tout est à jour','Aucune candidature en attente.')
        v=View(i.user.id); v.add_item(ReviewSelect(rows)); await reply(i,'Validation staff','Choisis le compte à valider.',view=v)

class Roster(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def home(self,i): await reply(i,'ÉQUIPE · ROSTER','Propose un village lié et vérifié. Le staff valide les candidatures.\nLes noms Discord restent associés aux tags Clash.',view=RosterView(i.user.id))
    @app_commands.command(name='roster',description='Gérer les candidatures et consulter l’équipe')
    async def roster(self,i:discord.Interaction): await self.home(i)

async def setup(bot): await bot.add_cog(Roster(bot))

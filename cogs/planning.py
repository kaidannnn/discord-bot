import time
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,Modal,reply,card,safe,staff,show_pages,Confirm

class EventModal(Modal,title='Créer un événement'):
    name=discord.ui.TextInput(label='Nom',placeholder='Entraînement e-sport',max_length=100)
    date=discord.ui.TextInput(label='Date et heure de Paris',placeholder='2026-11-15 20:30',max_length=16)
    capacity=discord.ui.TextInput(label='Places disponibles',default='5',max_length=3)
    async def on_submit(self,i):
        await staff(i)
        try:
            naive=datetime.strptime(str(self.date),'%Y-%m-%d %H:%M')
            local=naive.replace(tzinfo=ZoneInfo('Europe/Paris'))
            # Reject nonexistent and ambiguous local times at DST transitions.
            if local.astimezone(timezone.utc).astimezone(local.tzinfo).replace(tzinfo=None)!=naive or local.utcoffset()!=local.replace(fold=1).utcoffset():
                raise ValueError
            starts=int(local.timestamp()); capacity=int(str(self.capacity))
        except ValueError: raise ValueError('Date invalide ou ambiguë. Utilise AAAA-MM-JJ HH:MM (heure de Paris).')
        if starts<=time.time() or not 1<=capacity<=100: raise ValueError('Choisis une date future et entre 1 et 100 places.')
        eid=await i.client.db.execute('INSERT INTO events(guild,title,starts,capacity,creator) VALUES(?,?,?,?,?)',(i.guild_id,str(self.name),starts,capacity,i.user.id))
        await reply(i,'Événement créé',f'**{safe(str(self.name))}** · <t:{starts}:F> · #{eid}')

class EventView(View):
    def __init__(self,user,event): super().__init__(user); self.event=event
    async def join(self,i,status):
        await i.client.db.rsvp(i.guild_id,self.event,i.user.id,status)
        await reply(i,'Réponse enregistrée',{'present':'Présent','reserve':'En réserve','absent':'Absent'}[status])
    @discord.ui.button(label='Présent',style=discord.ButtonStyle.success)
    async def yes(self,i,b): await self.join(i,'present')
    @discord.ui.button(label='Réserve')
    async def reserve(self,i,b): await self.join(i,'reserve')
    @discord.ui.button(label='Absent',style=discord.ButtonStyle.danger)
    async def no(self,i,b): await self.join(i,'absent')
    @discord.ui.button(label='Participants')
    async def participants(self,i,b):
        rows=await i.client.db.rows('SELECT a.* FROM attendance a JOIN events e ON e.id=a.event WHERE a.event=? AND e.guild=?',(self.event,i.guild_id))
        await show_pages(i,'Participants',[f'<@{r["user"]}> · {r["status"]}' for r in rows])
    @discord.ui.button(label='Supprimer (staff)',row=1)
    async def delete(self,i,b):
        await staff(i)
        async def remove(j):
            await staff(j)
            await j.client.db.execute('DELETE FROM events WHERE id=? AND guild=?',(self.event,j.guild_id))
            await reply(j,'Événement supprimé')
        await reply(i,'Supprimer cet événement ?','Les inscriptions seront supprimées.',view=Confirm(i.user.id,remove))

class EventsSelect(discord.ui.Select):
    def __init__(self,events):
        self.events={str(e['id']):e for e in events}
        super().__init__(placeholder='Choisir un événement',options=[discord.SelectOption(label=e['title'][:100],value=str(e['id']),description=datetime.fromtimestamp(e['starts'],ZoneInfo('Europe/Paris')).strftime('%d/%m · %H:%M Paris')) for e in events])
    async def callback(self,i):
        e=self.events[self.values[0]]
        await reply(i,safe(e['title']),f'<t:{e["starts"]}:F> · {e["capacity"]} places\nChoisis ta disponibilité.',view=EventView(i.user.id,e['id']))

class PlanningView(View):
    @discord.ui.button(label='Créer un événement (staff)',style=discord.ButtonStyle.primary)
    async def create(self,i,b):
        await staff(i); await i.response.send_modal(EventModal())

class Planning(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def home(self,i):
        rows=await self.bot.db.rows('SELECT * FROM events WHERE guild=? AND starts>? ORDER BY starts LIMIT 25',(i.guild_id,int(time.time())))
        v=PlanningView(i.user.id)
        if rows: v.add_item(EventsSelect(rows))
        await reply(i,'PLANNING','Choisis un événement et indique ta présence.' if rows else 'Aucun événement prévu. Le staff peut en créer un.',view=v)
    @app_commands.command(name='planning',description='Voir les événements et s’inscrire en un clic')
    async def planning(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='planning_add',description='Créer un événement guidé (staff)')
    async def add(self,i:discord.Interaction):
        await staff(i); await i.response.send_modal(EventModal())

async def setup(bot): await bot.add_cog(Planning(bot))

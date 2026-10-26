import asyncio
import time
import logging
import discord
from discord import app_commands
from discord.ext import commands,tasks
from core.ui import View,Modal,reply,card,staff,Confirm,show_pages

SCHEMA='''
CREATE TABLE IF NOT EXISTS dm_optin(guild INTEGER,user INTEGER,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS dm_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER,creator INTEGER,role INTEGER,title TEXT,body TEXT,created INTEGER,cancelled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dm_queue(job INTEGER REFERENCES dm_jobs(id),user INTEGER,state TEXT DEFAULT 'pending',PRIMARY KEY(job,user));
'''

class ConsentView(View):
    @discord.ui.button(label='Recevoir les annonces en MP',style=discord.ButtonStyle.success)
    async def yes(self,i,b):
        await i.client.db.execute('INSERT OR IGNORE INTO dm_optin VALUES(?,?)',(i.guild_id,i.user.id))
        await reply(i,'Préférence enregistrée','Tu recevras les campagnes staff qui ciblent tes rôles. Tu peux te désinscrire ici à tout moment.')
    @discord.ui.button(label='Me désinscrire',style=discord.ButtonStyle.secondary)
    async def no(self,i,b):
        await i.client.db.execute('DELETE FROM dm_optin WHERE guild=? AND user=?',(i.guild_id,i.user.id))
        await reply(i,'Désinscription enregistrée','Les futurs envois en attente seront ignorés pour toi.')

class CampaignModal(Modal,title='Annonce privée à un rôle'):
    heading=discord.ui.TextInput(label='Titre',max_length=100)
    body=discord.ui.TextInput(label='Message',style=discord.TextStyle.paragraph,max_length=2500)
    def __init__(self,role): super().__init__(); self.role=role
    async def on_submit(self,i):
        await staff(i); await i.response.defer(ephemeral=True)
        if self.role.is_default(): raise ValueError('Choisis un rôle ciblé, pas @everyone.')
        rows=await i.client.db.rows('SELECT user FROM dm_optin WHERE guild=?',(i.guild_id,))
        members=[]
        for r in rows:
            try: m=i.guild.get_member(r['user']) or await i.guild.fetch_member(r['user'])
            except discord.NotFound: continue
            if not m.bot and self.role in m.roles: members.append(m.id)
        if not members: raise ValueError('Aucun membre de ce rôle n’a activé les MP dans /notifications.')
        if len(members)>100: raise ValueError('Limite de 100 destinataires par campagne. Choisis un rôle plus précis.')
        heading=str(self.heading); body=str(self.body)
        async def enqueue(j):
            await staff(j)
            async with j.client.db.transaction() as c:
                async with c.execute('SELECT 1 FROM dm_jobs WHERE guild=? AND created>?',(j.guild_id,int(time.time())-300)) as q:
                    if await q.fetchone(): raise ValueError('Patiente 5 minutes entre deux campagnes.')
                cur=await c.execute('INSERT INTO dm_jobs(guild,creator,role,title,body,created) VALUES(?,?,?,?,?,?)',(j.guild_id,j.user.id,self.role.id,heading,body,int(time.time())))
                jid=cur.lastrowid
                await c.executemany('INSERT INTO dm_queue(job,user) VALUES(?,?)',[(jid,uid) for uid in members])
            await reply(j,'Campagne créée',f'#{jid} · {len(members)} destinataires maximum. Envoi progressif ; /mp_statut permet de suivre et /mp_annuler d’arrêter les messages encore en attente.')
        await i.followup.send(content=f'Aperçu · {len(members)} destinataires volontaires · {self.role.mention}',embed=card(heading,body),view=Confirm(i.user.id,enqueue),ephemeral=True)

class Messaging(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self):
        async with self.bot.db.lock: await self.bot.db.conn.executescript(SCHEMA)
        # Never automatically replay an ambiguous send after a crash.
        await self.bot.db.execute("UPDATE dm_queue SET state='unknown' WHERE state='sending'")
        if not self.bot.offline: self.worker.start()
    def cog_unload(self): self.worker.cancel()
    @app_commands.command(name='notifications',description='Choisir si tu souhaites recevoir les campagnes staff en MP')
    async def notifications(self,i:discord.Interaction): await self.home(i)
    async def home(self,i):
        active=bool(await self.bot.db.rows('SELECT 1 FROM dm_optin WHERE guild=? AND user=?',(i.guild_id,i.user.id)))
        await reply(i,'MES NOTIFICATIONS','Annonces privées : **'+('activées' if active else 'désactivées')+'**.\nTon choix concerne les campagnes par rôle de ce serveur.',view=ConsentView(i.user.id))
    @app_commands.command(name='mp_role',description='Préparer une campagne privée pour les membres volontaires d’un rôle')
    @app_commands.default_permissions(manage_guild=True)
    async def campaign(self,i:discord.Interaction,role:discord.Role):
        await staff(i); await i.response.send_modal(CampaignModal(role))
    @app_commands.command(name='mp_statut',description='Suivre les campagnes de messages privés (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def status(self,i:discord.Interaction):
        await staff(i)
        jobs=await self.bot.db.rows('SELECT * FROM dm_jobs WHERE guild=? ORDER BY id DESC LIMIT 20',(i.guild_id,))
        lines=[]
        for job in jobs:
            states=await self.bot.db.rows('SELECT state,COUNT(*) AS n FROM dm_queue WHERE job=? GROUP BY state',(job['id'],))
            lines.append(f'**#{job["id"]}**'+(' · Annulée' if job['cancelled'] else '')+'\n'+', '.join(f'{r["state"]}: {r["n"]}' for r in states))
        await show_pages(i,'Campagnes MP',lines)
    @app_commands.command(name='mp_annuler',description='Annuler les messages non encore envoyés d’une campagne')
    @app_commands.default_permissions(manage_guild=True)
    async def cancel(self,i:discord.Interaction,campagne:int):
        await staff(i)
        if not await self.bot.db.rows('SELECT id FROM dm_jobs WHERE id=? AND guild=?',(campagne,i.guild_id)): raise ValueError('Campagne introuvable sur ce serveur.')
        await self.bot.db.execute('UPDATE dm_jobs SET cancelled=1 WHERE id=? AND guild=?',(campagne,i.guild_id))
        await reply(i,'Campagne annulée','Les messages encore en attente ne seront pas envoyés. Un message déjà en cours d’envoi peut aboutir.')
    @tasks.loop(seconds=2)
    async def worker(self):
        try: await self.deliver_one()
        except Exception as exc: logging.getLogger('kai').error('File MP : %s',type(exc).__name__)
    @worker.before_loop
    async def before(self): await self.bot.wait_until_ready()
    async def deliver_one(self):
        rows=await self.bot.db.rows("SELECT q.user,j.* FROM dm_queue q JOIN dm_jobs j ON j.id=q.job WHERE q.state='pending' AND j.cancelled=0 ORDER BY j.id,q.user LIMIT 1")
        if not rows: return
        job=rows[0]; uid=job['user']; guild=self.bot.get_guild(job['guild'])
        state='skipped'
        if guild and await self.bot.db.rows('SELECT 1 FROM dm_optin WHERE guild=? AND user=?',(guild.id,uid)):
            try:
                member=guild.get_member(uid) or await guild.fetch_member(uid)
                if any(r.id==job['role'] for r in member.roles) and not member.bot:
                    await self.bot.db.execute("UPDATE dm_queue SET state='sending' WHERE job=? AND user=?",(job['id'],uid))
                    await member.send(embed=card(job['title'],job['body']+'\n\nPour ne plus recevoir ces annonces : /notifications sur le serveur.'))
                    state='sent'
            except discord.Forbidden: state='blocked'
            except discord.NotFound: state='left'
            except discord.HTTPException: state='failed'
        await self.bot.db.execute('UPDATE dm_queue SET state=? WHERE job=? AND user=?',(state,job['id'],uid))

async def setup(bot): await bot.add_cog(Messaging(bot))

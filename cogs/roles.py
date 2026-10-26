import logging
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import commands,tasks
from core.ui import View,Modal,reply,staff,card,show_pages

SCHEMA='''
CREATE TABLE IF NOT EXISTS th_roles(guild INTEGER, th INTEGER, role INTEGER, PRIMARY KEY(guild,th));
CREATE TABLE IF NOT EXISTS birthdays(guild INTEGER,user INTEGER,day INTEGER,month INTEGER,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS birthday_awards(guild INTEGER,user INTEGER,year INTEGER,role INTEGER,expires INTEGER,removed INTEGER DEFAULT 0,PRIMARY KEY(guild,user,year));
'''

def elevated(role):
    return any(getattr(role.permissions,x) for x in ('administrator','manage_guild','manage_roles','manage_channels','ban_members','kick_members','manage_webhooks','mention_everyone'))

def safe_role(i,role):
    if role.is_default() or role.managed or role>=i.guild.me.top_role: raise ValueError('Choisis un rôle classique situé sous le rôle de Kai-Bot.')
    if i.user.id!=i.guild.owner_id and role>=i.user.top_role: raise ValueError('Le rôle doit être placé sous ton propre rôle.')
    p=role.permissions
    if any(getattr(p,x) for x in ('administrator','manage_guild','manage_roles','manage_channels','ban_members','kick_members','manage_webhooks','mention_everyone')):
        raise ValueError('Choisis un rôle décoratif sans permissions de gestion.')

class BirthdayModal(Modal,title='Mon anniversaire (facultatif)'):
    date=discord.ui.TextInput(label='Jour et mois uniquement',placeholder='15/08',max_length=5)
    async def on_submit(self,i):
        try: value=datetime.strptime(str(self.date),'%d/%m')
        except ValueError:
            if str(self.date)=='29/02': value=datetime(2000,2,29)
            else: raise ValueError('Utilise JJ/MM, par exemple 15/08.')
        await i.client.db.execute('INSERT INTO birthdays VALUES(?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET day=excluded.day,month=excluded.month',(i.guild_id,i.user.id,value.day,value.month))
        await reply(i,'Anniversaire enregistré','Jour/mois uniquement. Une récompense de 300 XP maximum par année et un rôle temporaire si le staff l’a configuré. Tu peux supprimer cette information à tout moment.')

class RolesView(View):
    @discord.ui.button(label='Actualiser mon rôle HDV',style=discord.ButtonStyle.primary)
    async def sync(self,i,b):
        await i.response.defer(ephemeral=True)
        result=await i.client.get_cog('Roles').sync_member(i.guild,i.user.id)
        await reply(i,'Rôle HDV',result)
    @discord.ui.button(label='Mon anniversaire')
    async def birthday(self,i,b): await i.response.send_modal(BirthdayModal())
    @discord.ui.button(label='Supprimer mon anniversaire')
    async def delete(self,i,b):
        await i.client.db.execute('DELETE FROM birthdays WHERE guild=? AND user=?',(i.guild_id,i.user.id))
        await reply(i,'Anniversaire supprimé','Aucun futur anniversaire ne sera traité. Un rôle déjà attribué expire à la fin de sa journée.')

class Roles(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self):
        async with self.bot.db.lock: await self.bot.db.conn.executescript(SCHEMA)
        if not self.bot.offline: self.tick.start()
    def cog_unload(self): self.tick.cancel()
    @app_commands.command(name='roles',description='Actualiser ton rôle HDV et gérer ton anniversaire')
    async def roles(self,i:discord.Interaction): await self.home(i)
    async def home(self,i): await reply(i,'RÔLES & ANNIVERSAIRE','Le rôle HDV correspond à ton compte principal vérifié. L’anniversaire est facultatif ; seule la date jour/mois est conservée.',view=RolesView(i.user.id))
    @app_commands.command(name='role_hdv',description='Associer un rôle décoratif à un HDV (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def role_hdv(self,i:discord.Interaction,hdv:app_commands.Range[int,1,30],role:discord.Role):
        await staff(i); safe_role(i,role)
        await self.bot.db.execute('INSERT INTO th_roles VALUES(?,?,?) ON CONFLICT(guild,th) DO UPDATE SET role=excluded.role',(i.guild_id,hdv,role.id))
        await reply(i,'Rôle HDV enregistré',f'HDV {hdv} → {role.mention}. Actualisation toutes les 30 minutes ou depuis /roles. Si tu remplaces un ancien rôle, retire manuellement l’ancien rôle devenu non géré.')
    @app_commands.command(name='role_anniversaire',description='Configurer le rôle temporaire anniversaire (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def role_birthday(self,i:discord.Interaction,role:discord.Role):
        await staff(i); safe_role(i,role)
        await self.bot.db.set(i.guild_id,'birthday_role',role.id)
        await reply(i,'Rôle anniversaire enregistré',role.mention+' · retiré après la journée anniversaire (heure de Paris).')
    async def sync_member(self,guild,uid):
        links=await self.bot.db.rows('SELECT tag FROM links WHERE guild=? AND user=? AND main=1',(guild.id,uid))
        if not links: return 'Lie ton compte principal avec /link.'
        mappings=await self.bot.db.rows('SELECT * FROM th_roles WHERE guild=?',(guild.id,))
        if not mappings: return 'Le staff n’a pas encore configuré les rôles HDV.'
        p=await self.bot.api.player(links[0]['tag'])
        target=next((guild.get_role(r['role']) for r in mappings if r['th']==p.get('townHallLevel')),None)
        if not target: return 'Aucun rôle configuré pour cet HDV.'
        if target.managed or target>=guild.me.top_role or elevated(target): raise ValueError('Rôle non attribuable : vérifier la hiérarchie Discord.')
        member=guild.get_member(uid) or await guild.fetch_member(uid)
        if target not in member.roles: await member.add_roles(target,reason='Compte Clash principal vérifié')
        remove=[r for r in member.roles if r!=target and r.id in {x['role'] for x in mappings} and r<guild.me.top_role and not r.managed]
        if remove: await member.remove_roles(*remove,reason='Mise à jour HDV vérifié')
        return f'HDV {p.get("townHallLevel")} → {target.mention}'
    @tasks.loop(minutes=30)
    async def tick(self):
        now=datetime.now(ZoneInfo('Europe/Paris'))
        for guild in self.bot.guilds:
            links=await self.bot.db.rows('SELECT DISTINCT user FROM links WHERE guild=?',(guild.id,))
            for r in links:
                try: await self.sync_member(guild,r['user'])
                except (ValueError,discord.HTTPException): continue
            try: await self.birthdays(guild,now)
            except Exception as exc: logging.getLogger('kai').warning('Anniversaires %s : %s',guild.id,type(exc).__name__)
    @tick.before_loop
    async def before(self): await self.bot.wait_until_ready()
    async def birthdays(self,guild,now):
        rows=await self.bot.db.rows('SELECT user FROM birthdays WHERE guild=? AND day=? AND month=?',(guild.id,now.day,now.month))
        rid=await self.bot.db.get(guild.id,'birthday_role',0)
        expiry=int((now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).timestamp())
        for r in rows:
            uid=r['user']
            async with self.bot.db.transaction() as c:
                cur=await c.execute('INSERT OR IGNORE INTO birthday_awards(guild,user,year,role,expires) VALUES(?,?,?,?,?)',(guild.id,uid,now.year,rid,expiry))
                fresh=cur.rowcount>0
                if fresh: await c.execute('INSERT INTO xp(guild,user,points,last) VALUES(?,?,300,0) ON CONFLICT(guild,user) DO UPDATE SET points=points+300',(guild.id,uid))
            awards=await self.bot.db.rows('SELECT role,expires FROM birthday_awards WHERE guild=? AND user=? AND year=?',(guild.id,uid,now.year))
            award=awards[0]
            role=guild.get_role(award['role']) if award['expires']>int(now.timestamp()) else None
            if role and role<guild.me.top_role and not role.managed and not elevated(role):
                try:
                    member=guild.get_member(uid) or await guild.fetch_member(uid)
                    if role not in member.roles: await member.add_roles(role,reason='Anniversaire déclaré')
                except discord.HTTPException: pass
        expired=await self.bot.db.rows('SELECT * FROM birthday_awards WHERE guild=? AND expires<=? AND removed=0',(guild.id,int(now.timestamp())))
        for r in expired:
            role=guild.get_role(r['role'])
            try:
                if role:
                    member=guild.get_member(r['user']) or await guild.fetch_member(r['user'])
                    if role in member.roles: await member.remove_roles(role,reason='Fin de journée anniversaire')
                await self.bot.db.execute('UPDATE birthday_awards SET removed=1 WHERE guild=? AND user=? AND year=?',(guild.id,r['user'],r['year']))
            except discord.NotFound:
                await self.bot.db.execute('UPDATE birthday_awards SET removed=1 WHERE guild=? AND user=? AND year=?',(guild.id,r['user'],r['year']))
            except discord.HTTPException: pass

async def setup(bot): await bot.add_cog(Roles(bot))

import asyncio
import json
import logging
import time
from datetime import datetime,timezone
import discord
from discord import app_commands
from discord.ext import commands,tasks
from core.api import encoded,APIError,tag
from core.ui import View,reply,card,safe,show_pages,clan_tag

def timestamp(value):
    return int(datetime.strptime(value,'%Y%m%dT%H%M%S.%fZ').replace(tzinfo=timezone.utc).timestamp())

def remaining(w):
    limit=w.get('attacksPerMember',1)
    return [(m,max(0,limit-len(m.get('attacks',[])))) for m in w.get('clan',{}).get('members',[]) if len(m.get('attacks',[]))<limit]

def reminder_stage(w,now):
    if w.get('state')=='warEnded': return 'end'
    if w.get('state')!='inWar': return None
    seconds=timestamp(w['endTime'])-now
    if seconds<=0: return None
    return next((str(m) for m in (5,15,30,60,120,720) if seconds<=m*60),None)

def war_card(w):
    if w.get('state')=='notInWar': return card('GUERRE','Aucune guerre en cours.')
    a=w.get('clan',{}); b=w.get('opponent',{})
    e=card('GUERRE · '+safe(a.get('name','Clan')),f'Contre **{safe(b.get("name","Adversaire"))}**\nÉtat : {w.get("state","inconnu")} · {w.get("teamSize",0)}v{w.get("teamSize",0)}')
    for side in (a,b): e.add_field(name=safe(side.get('name','Clan')),value=f'⭐ **{side.get("stars",0)}** · {side.get("destructionPercentage",0):.2f}%\n⚔️ {side.get("attacks",0)} attaques')
    if w.get('endTime'): e.add_field(name='Fin',value=f'<t:{timestamp(w["endTime"])}:R>')
    return e

class WarView(View):
    def __init__(self,user,w): super().__init__(user); self.w=w
    @discord.ui.button(label='Attaques restantes',style=discord.ButtonStyle.primary)
    async def remaining(self,i,b):
        await show_pages(i,'Attaques restantes',[f'**{safe(m["name"])}** · {m["tag"]} · {n} restante(s)' for m,n in remaining(self.w)])
    @discord.ui.button(label='Attaques réalisées')
    async def attacks(self,i,b):
        opponents={m['tag']:m for m in self.w.get('opponent',{}).get('members',[])}
        lines=[]
        for m in self.w.get('clan',{}).get('members',[]):
            for a in m.get('attacks',[]):
                target=opponents.get(a['defenderTag'],{})
                lines.append(f'**{safe(m["name"])}** → {safe(target.get("name",a["defenderTag"]))}\n⭐ {a.get("stars",0)} · {a.get("destructionPercentage",0)}% · {a.get("duration",0)} s')
        await show_pages(i,'Attaques',lines)
    @discord.ui.button(label='Composition')
    async def lineup(self,i,b):
        await show_pages(i,'Composition',[f'{m.get("mapPosition",0)} · **{safe(m["name"])}** · HDV {m.get("townhallLevel",0)} · {m["tag"]}' for m in sorted(self.w.get('clan',{}).get('members',[]),key=lambda x:x.get('mapPosition',0))])
    @discord.ui.button(label='Bases ouvertes')
    async def open(self,i,b):
        await show_pages(i,'Bases non triplées',[f'{m.get("mapPosition",0)} · {safe(m["name"])} · {m.get("bestOpponentAttack",{}).get("stars",0)} ⭐' for m in self.w.get('opponent',{}).get('members',[]) if m.get('bestOpponentAttack',{}).get('stars',0)<3])

    @discord.ui.button(label='Ligue de guerres',row=1,style=discord.ButtonStyle.primary)
    async def cwl(self,i,b):
        cog=i.client.get_cog('Wars')
        await cog.cwl.callback(cog,i,self.w.get('clan',{}).get('tag'))
    @discord.ui.button(label='Historique',row=1)
    async def history(self,i,b):
        cog=i.client.get_cog('Wars')
        await cog.warlog.callback(cog,i)
    @discord.ui.button(label='Performances',row=1)
    async def performance(self,i,b): await i.client.get_cog('Statistics').ranking(i)

class CWLSelect(discord.ui.Select):
    def __init__(self,wars):
        self.wars=wars
        super().__init__(placeholder='Choisir la journée LDC',options=[discord.SelectOption(label=f'Jour {n+1} · {w.get("state")}',value=str(n)) for n,w in enumerate(wars)])
    async def callback(self,i):
        w=self.wars[int(self.values[0])]
        await i.response.send_message(embed=war_card(w),view=WarView(i.user.id,w),ephemeral=True)

class Wars(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self):
        if not self.bot.offline: self.monitor.start()
    def cog_unload(self): self.monitor.cancel()
    async def home(self,i,t=None):
        await i.response.defer(ephemeral=True)
        w=await self.bot.api.war(t or await clan_tag(i))
        await i.followup.send(embed=war_card(w),view=WarView(i.user.id,w),ephemeral=True)
    @app_commands.command(name='war',description='Suivre la guerre avec des boutons')
    async def war(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='war_live',description='Consulter les dernières données de guerre')
    async def war_live(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='remaining',description='Voir les attaques restantes')
    async def left(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        w=await self.bot.api.war(await clan_tag(i))
        await show_pages(i,'Attaques restantes',[f'{safe(m["name"])} · {n}' for m,n in remaining(w)])
    @app_commands.command(name='warlog',description='Consulter le journal des guerres')
    async def warlog(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        data=await self.bot.api.request('/clans/'+encoded(await clan_tag(i))+'/warlog?limit=25')
        await show_pages(i,'Historique GDC',[f'**{x.get("result","—")}** · {safe(x.get("opponent",{}).get("name","?"))}\n{x.get("clan",{}).get("stars",0)}–{x.get("opponent",{}).get("stars",0)} ⭐ · {x.get("endTime","")}' for x in data.get('items',[])])
    async def cwl_wars(self,t):
        group=await self.bot.api.request('/clans/'+encoded(t)+'/currentwar/leaguegroup',ttl=60)
        result=[]
        for r in group.get('rounds',[]):
            tags=[x for x in r.get('warTags',[]) if x!='#0']
            for wt in tags:
                w=await self.bot.api.request('/clanwarleagues/wars/'+encoded(wt),ttl=30)
                if w.get('clan',{}).get('tag')==t: result.append(w); break
                if w.get('opponent',{}).get('tag')==t:
                    w['clan'],w['opponent']=w['opponent'],w['clan']; result.append(w); break
        return result
    @app_commands.command(name='cwl',description='Choisir et suivre une journée LDC')
    async def cwl(self,i:discord.Interaction,clan:str|None=None):
        await i.response.defer(ephemeral=True)
        ws=await self.cwl_wars(tag(clan) if clan else await clan_tag(i))
        if not ws: return await reply(i,'LDC','Aucune journée disponible.')
        v=View(i.user.id); v.add_item(CWLSelect(ws))
        await reply(i,'LIGUE DE GUERRES','Choisis la journée à consulter.',view=v)
    @app_commands.command(name='missed',description='Attaques manquées des guerres suivies et terminées')
    async def missed(self,i:discord.Interaction):
        rows=await self.bot.db.rows('SELECT payload FROM wars WHERE guild=? ORDER BY updated DESC LIMIT 20',(i.guild_id,))
        lines=[]
        for r in rows:
            w=json.loads(r['payload'])
            if w.get('state')=='warEnded': lines.extend(f'{safe(m["name"])} · {n} manquée(s) · {w.get("endTime")}' for m,n in remaining(w))
        await show_pages(i,'Attaques manquées · depuis installation',lines)
    @tasks.loop(minutes=2)
    async def monitor(self):
        for guild in self.bot.guilds:
            try: await self.poll_guild(guild)
            except Exception as exc: logging.getLogger('kai').warning('Suivi clan %s : %s',guild.id,type(exc).__name__)
    @monitor.before_loop
    async def before(self): await self.bot.wait_until_ready()
    async def poll_guild(self,guild):
        configurations={r['tag']:r for r in await self.bot.db.rows('SELECT * FROM followed_clans WHERE guild=?',(guild.id,))}
        default=await self.bot.db.get(guild.id,'clan')
        if default and default not in configurations:
            configurations[default]={'channel':await self.bot.db.get(guild.id,'war_channel',0),'cwl':await self.bot.db.get(guild.id,'track_cwl',False)}
        for t,cfg in configurations.items():
            try: await self.poll_clan(guild,t,cfg)
            except Exception as exc: logging.getLogger('kai').warning('Suivi %s : %s',t,type(exc).__name__)
    async def poll_clan(self,guild,t,cfg):
        wars=[await self.bot.api.war(t)]
        if cfg['cwl']:
            try: wars+=await self.cwl_wars(t)
            except APIError: pass
        for w in wars:
            if w.get('state')=='notInWar' or not w.get('preparationStartTime'): continue
            key=t+':'+w['preparationStartTime']+':'+w.get('opponent',{}).get('tag','')
            await self.bot.db.execute('INSERT INTO wars VALUES(?,?,?,?,?) ON CONFLICT(guild,war_key) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',(guild.id,key,'war',json.dumps(w),int(time.time())))
            channel=guild.get_channel(cfg['channel'])
            if not channel: continue
            mode=reminder_stage(w,time.time())
            if not mode: continue
            notice=key+':'+mode
            if await self.bot.db.rows('SELECT 1 FROM notices WHERE guild=? AND key=?',(guild.id,notice)): continue
            e=war_card(w)
            if mode!='end': e.add_field(name=f'Attaques restantes · rappel {mode} min',value='\n'.join(f'{safe(m["name"])} : {n}' for m,n in remaining(w))[:1024] or 'Toutes les attaques sont faites.',inline=False)
            await channel.send(embed=e)
            await self.bot.db.execute('INSERT OR IGNORE INTO notices VALUES(?,?,?)',(guild.id,notice,int(time.time())))
        now=int(time.time())
        last=await self.bot.db.get(guild.id,'snapshot_ts:'+t,0)
        if now-last>=1800:
            c=await self.bot.api.clan(t)
            async with self.bot.db.transaction() as conn:
                for m in c.get('memberList',[]): await conn.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)',(guild.id,m['tag'],now,json.dumps(m)))
                await conn.execute('DELETE FROM snapshots WHERE guild=? AND ts<?',(guild.id,now-90*86400))
            await self.bot.db.set(guild.id,'snapshot_ts:'+t,now)

async def setup(bot): await bot.add_cog(Wars(bot))

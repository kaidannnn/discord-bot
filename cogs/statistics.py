import json
from datetime import datetime,timezone
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,reply,card,show_pages,safe,staff,clan_tag

class Statistics(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def ranking(self,i):
        rows=await self.bot.db.rows('SELECT payload FROM wars WHERE guild=?',(i.guild_id,))
        since=await self.bot.db.get(i.guild_id,'season_start',0)
        from cogs.wars import timestamp
        stats={}
        for row in rows:
            w=json.loads(row['payload'])
            if w.get('state')!='warEnded' or timestamp(w['endTime'])<since: continue
            for m in w.get('clan',{}).get('members',[]):
                item=stats.setdefault(m['tag'],{'name':m['name'],'attacks':0,'stars':0,'pct':0,'perfect':0})
                for a in m.get('attacks',[]):
                    item['attacks']+=1; item['stars']+=a.get('stars',0); item['pct']+=a.get('destructionPercentage',0); item['perfect']+=a.get('stars',0)==3
        values=sorted(stats.items(),key=lambda kv:(kv[1]['stars'],kv[1]['pct']),reverse=True)
        lines=[f'**{n}. {safe(x["name"])}** · {t}\n{x["stars"]} ⭐ · {x["attacks"]} attaques · {x["perfect"]} triples · {x["pct"]/max(x["attacks"],1):.1f}% moyen' for n,(t,x) in enumerate(values,1)]
        await show_pages(i,'Performance · guerres archivées depuis le suivi',lines)
    @app_commands.command(name='mvp',description='Classement des performances des guerres suivies')
    async def mvp(self,i:discord.Interaction): await self.ranking(i)
    @app_commands.command(name='season_stats',description='Statistiques de la saison locale en cours')
    async def season_stats(self,i:discord.Interaction): await self.ranking(i)
    @app_commands.command(name='season_start',description='Ouvrir une nouvelle période statistique (staff)')
    async def season_start(self,i:discord.Interaction):
        await staff(i)
        from core.ui import Confirm
        async def start(j):
            await staff(j)
            await self.bot.db.set(j.guild_id,'season_start',int(datetime.now(timezone.utc).timestamp()))
            await reply(j,'Nouvelle période ouverte','Les anciennes guerres restent conservées ; le classement utilise désormais la nouvelle période.')
        await reply(i,'Commencer une nouvelle période ?','Cela change le début du classement local.',view=Confirm(i.user.id,start))
    @app_commands.command(name='legend_results',description='Voir les statistiques Légende officielles de ton compte')
    async def legend(self,i:discord.Interaction):
        rows=await self.bot.db.rows('SELECT tag FROM links WHERE guild=? AND user=? ORDER BY main DESC LIMIT 1',(i.guild_id,i.user.id))
        if not rows: raise ValueError('Lie ton village avec /link.')
        await i.response.defer(ephemeral=True)
        p=await self.bot.api.player(rows[0]['tag']); stats=p.get('legendStatistics',{})
        lines=[]
        for key,label in [('currentSeason','Saison en cours'),('previousSeason','Saison précédente'),('bestSeason','Meilleure saison')]:
            s=stats.get(key)
            if s: lines.append(f'**{label}** · {s.get("id","—")}\nTrophées : {s.get("trophies","—")} · Rang : {s.get("rank","—")}')
        await reply(i,'LÉGENDE · '+safe(p['name']),('\n\n'.join(lines) or 'Aucune saison Légende disponible.')+'\n\nLes détails quotidiens attaque/défense ne sont pas disponibles dans ces données API.')
    @app_commands.command(name='lastseen',description='Expliquer et consulter les observations d’activité disponibles')
    async def lastseen(self,i:discord.Interaction):
        await reply(i,'Activité Clash','L’API ne fournit pas l’heure de dernière connexion. /activity affiche uniquement les variations réellement observées depuis l’installation du bot.')

async def setup(bot): await bot.add_cog(Statistics(bot))

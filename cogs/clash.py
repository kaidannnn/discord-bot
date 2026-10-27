import csv
import io
import json
import time
import discord
from discord import app_commands
from discord.ext import commands
from core.api import encoded, tag
from core.ui import View, Modal, reply, card, safe, show_pages, clan_tag, staff, Pages, pages

class PlayerLookup(Modal,title='Chercher un joueur'):
    value=discord.ui.TextInput(label='Tag Clash',placeholder='#LGGY2J0JY',max_length=16)
    async def on_submit(self,i): await i.client.get_cog('Clash').player_panel(i,tag(str(self.value)))

class PlayerSections(discord.ui.Select):
    def __init__(self,p):
        self.player=p
        super().__init__(placeholder='Explorer le profil',options=[discord.SelectOption(label=l,value=v) for l,v in [('Héros','heroes'),('Troupes','troops'),('Sorts','spells'),('Équipements','heroEquipment'),('Succès','achievements'),('Légende','legendStatistics')]])
    async def callback(self,i):
        key=self.values[0]; p=self.player
        if key=='legendStatistics':
            d=p.get(key,{})
            text='\n'.join(f'**{safe(k)}** : {safe(v)}' for k,v in d.items()) or 'Aucune statistique Légende publiée pour ce joueur.'
            await reply(i,'Légende · '+safe(p['name']),text+'\n\nLes attaques/défenses quotidiennes détaillées ne sont pas fournies ici. Les variations de trophées observées ne permettent pas de les reconstituer exactement.')
        else:
            await show_pages(i,safe(p['name']),[f'**{safe(x["name"])}** · {x.get("level",x.get("value",0))}/{x.get("maxLevel",x.get("target",0))}'+(' · ouvriers' if x.get('village')=='builderBase' else '') for x in p.get(key,[])])

class ClanChoice(discord.ui.Select):
    def __init__(self,rows):
        super().__init__(placeholder='Choisir un clan suivi',options=[discord.SelectOption(label=r['tag'],value=r['tag']) for r in rows[:25]])
    async def callback(self,i): await i.client.get_cog('Clash').home(i,self.values[0])

class ClanView(View):
    def __init__(self,user,t=None): super().__init__(user); self.tag=t
    @discord.ui.button(label='Membres',style=discord.ButtonStyle.primary)
    async def members(self,i,b): await i.client.get_cog('Clash').members_panel(i,t=self.tag)
    @discord.ui.button(label='Dons')
    async def donations(self,i,b): await i.client.get_cog('Clash').members_panel(i,'donations',self.tag)
    @discord.ui.button(label='Trophées')
    async def trophies(self,i,b): await i.client.get_cog('Clash').members_panel(i,'trophies',self.tag)
    @discord.ui.button(label='Raids')
    async def raids(self,i,b): await i.client.get_cog('Clash').raids_panel(i,self.tag)
    @discord.ui.button(label='Chercher un joueur')
    async def player(self,i,b): await i.response.send_modal(PlayerLookup())

    @discord.ui.button(label='Bases du clan',row=1)
    async def bases(self,i,b): await i.client.get_cog('Resources').home(i,'base')
    @discord.ui.button(label='Armées & conseils',row=1)
    async def armies(self,i,b): await i.client.get_cog('Resources').home(i,'army')

    @discord.ui.button(label='Guerre du clan',row=1,style=discord.ButtonStyle.primary)
    async def war(self,i,b): await i.client.get_cog('Wars').home(i,self.tag)
    @discord.ui.button(label='Changer de clan',row=1)
    async def clans(self,i,b): await i.client.get_cog('Clash').choose_clan(i)

class Clash(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def home(self,i,t=None):
        await i.response.defer(ephemeral=True)
        t=t or await clan_tag(i)
        p=await self.bot.api.clan(t)
        e=card(safe(p['name']),f'{p["tag"]} · Niveau {p.get("clanLevel",0)}\n\n{safe(p.get("description",""))}')
        for name,value in [('Membres',f'{p.get("members",0)}/50'),('Trophées',p.get('clanPoints',0)),('Guerres gagnées',p.get('warWins',0)),('Ligue LDC',p.get('warLeague',{}).get('name','—'))]: e.add_field(name=name,value=str(value))
        if p.get('badgeUrls',{}).get('medium'): e.set_thumbnail(url=p['badgeUrls']['medium'])
        await i.followup.send(embed=e,view=ClanView(i.user.id,t),ephemeral=True)
    async def player_panel(self,i,t):
        if not i.response.is_done(): await i.response.defer(ephemeral=True)
        p=await self.bot.api.player(t)
        e=card(safe(p['name']),f'{p["tag"]} · HDV {p.get("townHallLevel",0)}\n{safe(p.get("clan",{}).get("name","Sans clan"))}')
        for label,key in [('Trophées','trophies'),('Record','bestTrophies'),('Étoiles GDC','warStars'),('Dons','donations'),('Dons reçus','donationsReceived'),('Niveau','expLevel')]: e.add_field(name=label,value=str(p.get(key,0)))
        v=View(i.user.id); v.add_item(PlayerSections(p)); v.add_item(discord.ui.Button(label='Ouvrir dans Clash',url='https://link.clashofclans.com/fr?action=OpenPlayerProfile&tag='+encoded(t)))
        await i.followup.send(embed=e,view=v,ephemeral=True)
    async def members_panel(self,i,sort='clanRank',t=None):
        await i.response.defer(ephemeral=True)
        p=await self.bot.api.clan(t or await clan_tag(i))
        members=sorted(p.get('memberList',[]),key=lambda m:m.get(sort,0),reverse=sort!='clanRank')
        await show_pages(i,'CLAN · '+sort,[f'**{n}. {safe(m["name"])}** · {m["tag"]}\nHDV {m.get("townHallLevel","?")} · 🏆 {m.get("trophies",0)} · Dons {m.get("donations",0)} / reçus {m.get("donationsReceived",0)}' for n,m in enumerate(members,1)])
    async def raids_panel(self,i,t=None):
        await i.response.defer(ephemeral=True)
        data=await self.bot.api.request('/clans/'+encoded(t or await clan_tag(i))+'/capitalraidseasons?limit=3')
        lines=[]
        for r in data.get('items',[]):
            lines.append(f'**{r.get("startTime")} · {r.get("state")}**\nOr : {r.get("capitalTotalLoot",0)} · Attaques : {r.get("totalAttacks",0)}')
            lines.extend(f'{safe(m["name"])} · {m.get("attacks",0)}/{m.get("attackLimit",0)+m.get("bonusAttackLimit",0)} · Or {m.get("capitalResourcesLooted",0)}' for m in r.get('members',[]))
        await show_pages(i,'RAIDS',lines)
    async def choose_clan(self,i):
        rows=await self.bot.db.rows('SELECT tag FROM followed_clans WHERE guild=? ORDER BY tag',(i.guild_id,))
        default=await self.bot.db.get(i.guild_id,'clan')
        if default and default not in {r['tag'] for r in rows}: rows.insert(0,{'tag':default})
        if not rows: raise ValueError('Le staff doit configurer un clan avec /config ou /clan_suivi.')
        v=View(i.user.id); v.add_item(ClanChoice(rows))
        await reply(i,'CLANS SUIVIS','Choisis le clan à consulter.',view=v)
    @app_commands.command(name='clans',description='Choisir parmi les clans suivis du serveur')
    async def clans(self,i:discord.Interaction): await self.choose_clan(i)
    @app_commands.command(name='clan_suivi',description='Ajouter ou mettre à jour un clan suivi et son salon (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def follow(self,i:discord.Interaction,clan:str,salon:discord.TextChannel,ldc:bool=False):
        await staff(i); await i.response.defer(ephemeral=True)
        t=tag(clan); p=await self.bot.api.clan(t)
        perms=salon.permissions_for(i.guild.me)
        if not(perms.view_channel and perms.send_messages and perms.embed_links): raise ValueError('Kai-Bot doit pouvoir voir ce salon et y envoyer des embeds.')
        rows=await self.bot.db.rows('SELECT tag FROM followed_clans WHERE guild=?',(i.guild_id,))
        if len(rows)>=24 and t not in {r['tag'] for r in rows}: raise ValueError('Limite de 24 clans suivis par serveur.')
        await self.bot.db.execute('INSERT INTO followed_clans VALUES(?,?,?,?) ON CONFLICT(guild,tag) DO UPDATE SET channel=excluded.channel,cwl=excluded.cwl',(i.guild_id,t,salon.id,int(ldc)))
        await reply(i,'Clan suivi',safe(p['name'])+' → '+salon.mention)
    @app_commands.command(name='clan_retirer',description='Arrêter le suivi d’un clan supplémentaire (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def unfollow(self,i:discord.Interaction,clan:str):
        await staff(i)
        await self.bot.db.execute('DELETE FROM followed_clans WHERE guild=? AND tag=?',(i.guild_id,tag(clan)))
        await reply(i,'Suivi supplémentaire retiré','Les historiques sont conservés. Le clan principal reste suivi s’il est configuré dans /config.')
    @app_commands.command(name='clan',description='Voir le tableau de bord du clan')
    async def clan(self,i:discord.Interaction): await self.home(i)
    @app_commands.command(name='player',description='Chercher un joueur Clash')
    async def player(self,i:discord.Interaction): await i.response.send_modal(PlayerLookup())
    @app_commands.command(name='members',description='Voir les membres du clan')
    async def members(self,i:discord.Interaction): await self.members_panel(i)
    @app_commands.command(name='donations',description='Classement des dons du clan')
    async def donations(self,i:discord.Interaction): await self.members_panel(i,'donations')
    @app_commands.command(name='classement',description='Classement trophées du clan')
    async def ranking(self,i:discord.Interaction): await self.members_panel(i,'trophies')
    @app_commands.command(name='raids',description='Résultats des derniers raids de capitale')
    async def raids(self,i:discord.Interaction): await self.raids_panel(i)
    @app_commands.command(name='activity',description='Variations observées depuis le début du suivi')
    async def activity(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        p=await self.bot.api.clan(await clan_tag(i)); lines=[]
        for m in p.get('memberList',[]):
            rows=await self.bot.db.rows('SELECT * FROM snapshots WHERE guild=? AND tag=? ORDER BY ts LIMIT 1',(i.guild_id,m['tag']))
            if not rows: lines.append(f'{safe(m["name"])} · suivi en attente'); continue
            old=json.loads(rows[0]['payload'])
            lines.append(f'{safe(m["name"])} · trophées {m.get("trophies",0)-old.get("trophies",0):+} depuis <t:{rows[0]["ts"]}:R>')
        await show_pages(i,'Activité observée · pas une dernière connexion',lines)
    @app_commands.command(name='export',description='Exporter les membres du clan en CSV (staff)')
    async def export(self,i:discord.Interaction):
        await staff(i); await i.response.defer(ephemeral=True)
        p=await self.bot.api.clan(await clan_tag(i))
        out=io.StringIO(); w=csv.writer(out); keys=['name','tag','townHallLevel','trophies','donations','donationsReceived']
        w.writerow(keys)
        for m in p.get('memberList',[]):
            row=[m.get(k,'') for k in keys]
            w.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row])
        await reply(i,'Export du clan','Fichier CSV compatible Excel.',file=discord.File(io.BytesIO(out.getvalue().encode('utf-8-sig')),filename='clan.csv'))

async def setup(bot): await bot.add_cog(Clash(bot))

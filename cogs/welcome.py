import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,card,reply,staff,show_pages

SCHEMA='CREATE TABLE IF NOT EXISTS invite_joins(guild INTEGER,user INTEGER,inviter INTEGER,confidence TEXT,PRIMARY KEY(guild,user));'

def attribution(before,after):
    if before is None: return None
    changed=[data for code,data in after.items() if data['uses']-before.get(code,{'uses':data['uses']})['uses']>0]
    if len(changed)==1:
        item=changed[0]
        old=next((before[c]['uses'] for c,v in after.items() if v is item and c in before),item['uses'])
        if item['uses']-old==1: return item['inviter']
    return None

class Welcome(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.cache={}; self.lock=asyncio.Lock()
    async def cog_load(self): await self.bot.db.execute(SCHEMA)
    async def snapshot(self,guild):
        invites=await guild.invites()
        return {x.code:{'uses':x.uses or 0,'inviter':x.inviter.id if x.inviter else None} for x in invites}
    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds:
            if await self.bot.db.get(g.id,'track_invites',False):
                try: self.cache[g.id]=await self.snapshot(g)
                except discord.HTTPException: self.cache[g.id]=None
    @commands.Cog.listener()
    async def on_invite_create(self,invite):
        if invite.guild and invite.guild.id in self.cache and self.cache[invite.guild.id] is not None:
            self.cache[invite.guild.id][invite.code]={'uses':invite.uses or 0,'inviter':invite.inviter.id if invite.inviter else None}
    @commands.Cog.listener()
    async def on_member_join(self,member):
        if member.bot: return
        guild=member.guild
        if await self.bot.db.get(guild.id,'track_invites',False):
            async with self.lock:
                try:
                    after=await self.snapshot(guild)
                    inviter=attribution(self.cache.get(guild.id),after)
                    self.cache[guild.id]=after
                    await self.bot.db.execute('INSERT OR IGNORE INTO invite_joins VALUES(?,?,?,?)',(guild.id,member.id,inviter,'inferred' if inviter else 'unknown'))
                except discord.HTTPException: self.cache[guild.id]=None
        channel=guild.get_channel(await self.bot.db.get(guild.id,'welcome_channel',0))
        if channel:
            from cogs.navigation import Home
            try: await channel.send(embed=card('BIENVENUE CHEZ KAI',f'{member.mention}, ouvre **Mon compte** pour lier ton village.\nPuis découvre le clan, les guerres et le planning.'),view=Home(self.bot))
            except discord.HTTPException: logging.getLogger('kai').warning('Salon bienvenue inaccessible: %s',guild.id)
    @app_commands.command(name='bienvenue',description='Configurer le salon d’accueil des nouveaux membres (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def welcome(self,i:discord.Interaction,salon:discord.TextChannel):
        await staff(i)
        if not self.bot.intents.members: raise ValueError('Active MEMBERS_INTENT=true et Server Members Intent dans le portail Discord, puis redémarre.')
        p=salon.permissions_for(i.guild.me)
        if not(p.view_channel and p.send_messages and p.embed_links): raise ValueError('Kai-Bot doit pouvoir voir et écrire dans ce salon.')
        await self.bot.db.set(i.guild_id,'welcome_channel',salon.id)
        await reply(i,'Accueil activé',salon.mention)
    @app_commands.command(name='invites_activer',description='Activer ou désactiver le suivi indicatif des invitations')
    @app_commands.default_permissions(manage_guild=True)
    async def enable(self,i:discord.Interaction,activer:bool):
        await staff(i)
        if activer and not self.bot.intents.members: raise ValueError('Active l’intent membres dans le portail et dans .env avant ce suivi.')
        await i.response.defer(ephemeral=True)
        if activer: self.cache[i.guild_id]=await self.snapshot(i.guild)
        else: self.cache.pop(i.guild_id,None)
        await self.bot.db.set(i.guild_id,'track_invites',activer)
        await reply(i,'Suivi des invitations','Activé. Attribution indicative à partir des compteurs ; certains arrivants restent inconnus.' if activer else 'Désactivé.')
    @app_commands.command(name='invites',description='Classement indicatif des invitations observées')
    async def invites(self,i:discord.Interaction):
        rows=await self.bot.db.rows('SELECT inviter,COUNT(*) AS n FROM invite_joins WHERE guild=? AND inviter IS NOT NULL GROUP BY inviter ORDER BY n DESC',(i.guild_id,))
        await show_pages(i,'Invitations observées · attribution indicative',[f'<@{r["inviter"]}> · {r["n"]} arrivée(s)' for r in rows])

async def setup(bot): await bot.add_cog(Welcome(bot))

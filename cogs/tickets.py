import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,reply,card,staff,Confirm

class Tickets(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.lock=asyncio.Lock()
    async def open_ticket(self,i):
        await i.response.defer(ephemeral=True)
        async with self.lock:
            old=await self.bot.db.rows("SELECT channel FROM tickets WHERE guild=? AND user=? AND status='open'",(i.guild_id,i.user.id))
            for row in old:
                channel=i.guild.get_channel(row['channel'])
                if channel: return await reply(i,'Ticket existant',channel.mention)
            rid=await self.bot.db.get(i.guild_id,'staff_role',0)
            role=i.guild.get_role(rid)
            if not role: raise ValueError('Un administrateur doit configurer un rôle staff dans /config avant d’ouvrir des tickets.')
            overwrites={i.guild.default_role:discord.PermissionOverwrite(view_channel=False),i.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),role:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),i.guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True,read_message_history=True)}
            channel=await i.guild.create_text_channel(f'ticket-{i.user.id}',overwrites=overwrites,reason='Ticket demandé par le membre')
            try:
                await self.bot.db.execute('INSERT INTO tickets(guild,user,channel) VALUES(?,?,?)',(i.guild_id,i.user.id,channel.id))
                await channel.send(embed=card('TON ESPACE AVEC LE STAFF','Explique ta demande ici.\nPour fermer : /ticket_fermer. Le salon sera archivé en lecture seule.'))
            except Exception:
                await channel.delete(reason='Annulation création incomplète'); raise
        await reply(i,'Ticket ouvert',channel.mention)
    @app_commands.command(name='ticket',description='Ouvrir un salon privé avec le staff')
    @app_commands.checks.cooldown(1,120,key=lambda i:(i.guild_id,i.user.id))
    async def ticket(self,i:discord.Interaction): await self.open_ticket(i)
    @app_commands.command(name='ticket_fermer',description='Fermer le ticket actuel sans effacer les messages')
    async def close(self,i:discord.Interaction):
        rows=await self.bot.db.rows("SELECT * FROM tickets WHERE guild=? AND channel=? AND status='open'",(i.guild_id,i.channel_id))
        if not rows: raise ValueError('Ce salon n’est pas un ticket ouvert.')
        row=rows[0]
        if i.user.id!=row['user']: await staff(i)
        async def close(j):
            if j.user.id!=row['user']: await staff(j)
            owner=j.guild.get_member(row['user'])
            if owner is None:
                try: owner=await j.guild.fetch_member(row['user'])
                except discord.NotFound: owner=None
            if owner: await j.channel.set_permissions(owner,view_channel=True,send_messages=False,read_message_history=True)
            await self.bot.db.execute("UPDATE tickets SET status='closed' WHERE id=? AND guild=?",(row['id'],j.guild_id))
            await reply(j,'Ticket fermé','Le salon est conservé. Le staff peut l’archiver ou le supprimer manuellement.')
        await reply(i,'Fermer ce ticket ?','Les messages seront conservés.',view=Confirm(i.user.id,close))

async def setup(bot): await bot.add_cog(Tickets(bot))

import argparse
import asyncio
import logging
import os
import signal
import json
import time
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from core.db import Database
from core.api import ClashAPI
from core.ui import card, error

EXTENSIONS=['navigation','admin','accounts','clash','wars','planning','roster','community','tickets','statistics','roles','messaging','resources','welcome','backups']

class Tree(app_commands.CommandTree):
    async def interaction_check(self,i):
        if not i.guild:
            await i.response.send_message(embed=card('Serveur requis','Utilise Kai-Bot dans ton serveur Discord.'),ephemeral=True)
            return False
        return True
    async def on_error(self,i,exc):
        if isinstance(exc,app_commands.CommandOnCooldown): exc=ValueError(f'Patiente {exc.retry_after:.0f} secondes puis réessaie.')
        elif isinstance(exc,app_commands.MissingPermissions): exc=ValueError('Permission Gérer le serveur requise.')
        await error(i,getattr(exc,'original',exc))

class KaiBot(commands.Bot):
    def __init__(self,*,sync=False,offline=False):
        intents=discord.Intents.default()
        intents.members=os.getenv('MEMBERS_INTENT','false').lower()=='true'
        intents.message_content=os.getenv('MESSAGE_CONTENT_INTENT','false').lower()=='true'
        owner=int(os.getenv('OWNER_ID') or 0) or None
        super().__init__(command_prefix='!',intents=intents,tree_cls=Tree,owner_id=owner,help_command=None,allowed_mentions=discord.AllowedMentions.none())
        self.db=Database(os.getenv('DATABASE_PATH','data/kai_bot.db'))
        self.api=ClashAPI(os.getenv('COC_API_TOKEN',''))
        self.sync_requested=sync
        self.offline=offline

    async def setup_hook(self):
        await self.db.open(); await self.api.open()
        for name in EXTENSIONS: await self.load_extension('cogs.'+name)
        if os.getenv('ENABLE_CUP','false').lower()=='true': await self.load_extension('cogs.cup')
        if self.sync_requested:
            gid=int(os.getenv('GUILD_ID') or 0)
            guild=discord.Object(id=gid) if gid else None
            if guild: self.tree.copy_global_to(guild=guild)
            synced=await self.tree.sync(guild=guild)
            logging.info('%s commandes synchronisées',len(synced))

    async def on_ready(self):
        await self.change_presence(activity=discord.Game('/menu • Kai-Bot'))
        logging.info('Kai-Bot connecté. %s serveurs.',len(self.guilds))
        if os.getenv('KAI_HEALTH_FILE'):
            Path(os.environ['KAI_HEALTH_FILE']).write_text(json.dumps({'release':os.getenv('KAI_RELEASE'), 'ready_at':time.time()}))

    async def close(self):
        for ext in list(self.extensions): await self.unload_extension(ext)
        await self.api.close(); await self.db.close()
        await super().close()

async def run(sync=False):
    token=os.getenv('DISCORD_TOKEN','').strip()
    if not token: raise SystemExit('DISCORD_TOKEN absent. Remplis .env sur ton hébergeur, jamais sur GitHub.')
    async with KaiBot(sync=sync) as bot:
        loop=asyncio.get_running_loop()
        for s in (signal.SIGTERM,signal.SIGINT):
            try: loop.add_signal_handler(s,lambda: asyncio.create_task(bot.close()))
            except NotImplementedError: pass
        await bot.start(token)

if __name__=='__main__':
    load_dotenv()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    parser=argparse.ArgumentParser(); parser.add_argument('--sync',action='store_true')
    asyncio.run(run(parser.parse_args().sync))

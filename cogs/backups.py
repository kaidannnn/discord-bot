import asyncio
import base64
import io
import json
import logging
import os
import re
import tempfile
from datetime import datetime,timezone
from pathlib import Path
import aiohttp
import discord
from cryptography.fernet import Fernet
from discord import app_commands
from discord.ext import commands,tasks
from core.ui import reply

class Backups(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.lock=asyncio.Lock()
    async def cog_load(self):
        if not self.bot.offline: self.hourly.start()
    def cog_unload(self): self.hourly.cancel()
    async def snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'kai_bot.db'
            await self.bot.db.backup(p)
            if p.stat().st_size>20*1024*1024: raise ValueError('Sauvegarde supérieure à 20 Mo : configure un stockage adapté avant de poursuivre.')
            return await asyncio.to_thread(p.read_bytes)
    async def manual(self,i):
        if not await self.bot.is_owner(i.user): raise ValueError('Seul le propriétaire du bot peut exporter la base complète.')
        await i.response.defer(ephemeral=True)
        async with self.lock: data=await self.snapshot()
        if len(data)>i.guild.filesize_limit: raise ValueError('Sauvegarde trop volumineuse pour Discord. Utilise la sauvegarde externe.')
        await reply(i,'Sauvegarde complète','Télécharge ce fichier et conserve-le hors de Katabump. Il contient les données de tous les serveurs gérés par le bot, mais pas tes clés API.',file=discord.File(io.BytesIO(data),filename='kai_bot.db'))
    @app_commands.command(name='backup',description='Télécharger une sauvegarde cohérente (propriétaire)')
    async def backup(self,i:discord.Interaction): await self.manual(i)
    @app_commands.command(name='backup_externe',description='Tester la sauvegarde chiffrée externe (propriétaire)')
    async def remote_command(self,i:discord.Interaction):
        if not await self.bot.is_owner(i.user): raise ValueError('Action réservée au propriétaire.')
        await i.response.defer(ephemeral=True)
        await self.remote()
        await reply(i,'Sauvegarde externe réussie','Copie chiffrée enregistrée dans le dépôt privé configuré.')
    @tasks.loop(hours=1)
    async def hourly(self):
        if not os.getenv('BACKUP_REPOSITORY'): return
        try: await self.remote()
        except Exception as exc:
            await self.bot.db.set(0,'backup_error',type(exc).__name__)
            logging.getLogger('kai').error('Sauvegarde externe échouée : %s',type(exc).__name__)
    @hourly.before_loop
    async def before(self): await self.bot.wait_until_ready()
    async def remote(self):
        repo=os.getenv('BACKUP_REPOSITORY',''); token=os.getenv('BACKUP_GITHUB_TOKEN',''); key=os.getenv('BACKUP_ENCRYPTION_KEY','')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not token or not key:
            raise ValueError('Renseigne BACKUP_REPOSITORY, BACKUP_GITHUB_TOKEN et BACKUP_ENCRYPTION_KEY dans .env.')
        try: cipher=Fernet(key.encode())
        except Exception: raise ValueError('Clé de chiffrement invalide. Génère-la avec scripts/backup_key.py.')
        async with self.lock:
            async with aiohttp.ClientSession(headers={'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},timeout=aiohttp.ClientTimeout(total=60)) as session:
                root='https://api.github.com/repos/'+repo
                async with session.get(root) as r:
                    if r.status!=200: raise ValueError('Dépôt de sauvegarde inaccessible. Vérifie le token et le nom du dépôt.')
                    metadata=await r.json()
                if not metadata.get('private'): raise ValueError('Sauvegarde refusée : le dépôt de données doit être PRIVÉ.')
                data=cipher.encrypt(await self.snapshot())
                now=datetime.now(timezone.utc)
                # 24 logical hourly slots. Git history retains earlier encrypted versions.
                url=root+f'/contents/backups/hour-{now.hour:02}.db.fernet'
                payload={'message':'Kai-Bot encrypted backup '+now.isoformat(),'content':base64.b64encode(data).decode()}
                async with session.get(url) as r:
                    if r.status==200: payload['sha']=(await r.json())['sha']
                    elif r.status!=404: raise ValueError(f'Lecture sauvegarde refusée ({r.status}).')
                async with session.put(url,json=payload) as r:
                    if r.status not in (200,201): raise ValueError(f'Écriture sauvegarde refusée ({r.status}). Réessaie après vérification des accès.')
                await self.bot.db.set(0,'last_remote_backup',now.isoformat())
                await self.bot.db.set(0,'backup_error',None)

async def setup(bot): await bot.add_cog(Backups(bot))

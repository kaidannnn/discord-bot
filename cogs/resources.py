import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View,Modal,reply,card,safe,staff

SCHEMA='CREATE TABLE IF NOT EXISTS resources(id INTEGER PRIMARY KEY AUTOINCREMENT,guild INTEGER,kind TEXT,title TEXT,th INTEGER,url TEXT,notes TEXT,creator INTEGER);'

def validate_link(value,kind):
    parsed=urllib.parse.urlsplit(value.strip())
    if parsed.scheme!='https' or parsed.hostname!='link.clashofclans.com' or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Utilise un lien HTTPS officiel link.clashofclans.com.')
    query=urllib.parse.parse_qs(parsed.query)
    action='OpenLayout' if kind=='base' else 'CopyArmy'
    if query.get('action')!=[action]: raise ValueError(f'Ce lien doit correspondre à l’action {action}.')
    if kind=='base' and not query.get('id'): raise ValueError('Le lien de base ne contient pas d’identifiant.')
    if kind=='army' and not query.get('army'): raise ValueError('Le lien ne contient pas de composition.')
    return value.strip()

class ResourceModal(Modal,title='Ajouter une ressource Clash'):
    name=discord.ui.TextInput(label='Nom',max_length=100)
    th=discord.ui.TextInput(label='Niveau HDV',default='18',max_length=2)
    url=discord.ui.TextInput(label='Lien de copie officiel Clash',max_length=512)
    notes=discord.ui.TextInput(label='Conseils / CDC / contexte',style=discord.TextStyle.paragraph,max_length=1500,required=False)
    def __init__(self,kind): super().__init__(); self.kind=kind
    async def on_submit(self,i):
        await staff(i)
        try: th=int(str(self.th))
        except ValueError: raise ValueError('Indique un niveau HDV numérique.')
        if not 1<=th<=30: raise ValueError('Niveau HDV hors plage.')
        url=validate_link(str(self.url),self.kind)
        rid=await i.client.db.execute('INSERT INTO resources(guild,kind,title,th,url,notes,creator) VALUES(?,?,?,?,?,?,?)',(i.guild_id,self.kind,str(self.name),th,url,str(self.notes),i.user.id))
        await reply(i,'Ressource ajoutée',f'#{rid} · {safe(str(self.name))}')

class ResourceSelect(discord.ui.Select):
    def __init__(self,rows):
        self.rows={str(r['id']):r for r in rows}
        super().__init__(placeholder='Choisir une ressource',options=[discord.SelectOption(label=r['title'][:100],description=f'HDV {r["th"]} · #{r["id"]}',value=str(r['id'])) for r in rows])
    async def callback(self,i):
        r=self.rows[self.values[0]]
        v=View(i.user.id); v.add_item(discord.ui.Button(label='Copier dans Clash',url=r['url']))
        await reply(i,safe(r['title']),f'HDV {r["th"]}\n\n{safe(r["notes"])}\n\nRessource ajoutée par le staff. La disponibilité du lien dépend de Clash.',view=v)

class Catalogue(View):
    def __init__(self,user,kind,rows):
        super().__init__(user); self.kind=kind; self.rows=rows; self.index=0; self.render()
    def render(self):
        for item in list(self.children):
            if isinstance(item,ResourceSelect): self.remove_item(item)
        selected=self.rows[self.index:self.index+25]
        if selected: self.add_item(ResourceSelect(selected))
    @discord.ui.button(label='Page suivante')
    async def next(self,i,b):
        self.index=self.index+25 if self.index+25<len(self.rows) else 0
        self.render(); await i.response.edit_message(view=self)
    @discord.ui.button(label='Ajouter (staff)',style=discord.ButtonStyle.primary)
    async def add(self,i,b):
        await staff(i); await i.response.send_modal(ResourceModal(self.kind))

class Resources(commands.Cog):
    def __init__(self,bot): self.bot=bot
    async def cog_load(self): await self.bot.db.execute(SCHEMA)
    async def home(self,i,kind,th=None):
        sql='SELECT * FROM resources WHERE guild=? AND kind=?'; params=[i.guild_id,kind]
        if th: sql+=' AND th=?'; params.append(th)
        rows=await self.bot.db.rows(sql+' ORDER BY id DESC LIMIT 500',params)
        await reply(i,'BASES' if kind=='base' else 'ARMÉES','Choisis une ressource. Le staff ajoute ses liens officiels et conseils.' if rows else 'Catalogue vide : le staff peut ajouter les premiers liens.',view=Catalogue(i.user.id,kind,rows))
    @app_commands.command(name='bases',description='Parcourir les bases et copier un village')
    async def bases(self,i:discord.Interaction,hdv:app_commands.Range[int,1,30]|None=None): await self.home(i,'base',hdv)
    @app_commands.command(name='army',description='Parcourir les armées et leurs conseils')
    async def army(self,i:discord.Interaction,hdv:app_commands.Range[int,1,30]|None=None): await self.home(i,'army',hdv)
    @app_commands.command(name='ressource_supprimer',description='Retirer un lien du catalogue (staff)')
    @app_commands.default_permissions(manage_guild=True)
    async def remove(self,i:discord.Interaction,numero:int):
        await staff(i)
        from core.ui import Confirm
        async def remove(j):
            await staff(j)
            await self.bot.db.execute('DELETE FROM resources WHERE guild=? AND id=?',(j.guild_id,numero))
            await reply(j,'Ressource retirée',f'#{numero}')
        await reply(i,'Retirer cette ressource ?',f'#{numero}',view=Confirm(i.user.id,remove))

async def setup(bot): await bot.add_cog(Resources(bot))

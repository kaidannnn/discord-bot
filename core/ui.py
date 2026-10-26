import logging
import discord

COLOR=0x527DFF

def safe(value): return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))

def card(title, description='', *, color=COLOR):
    e=discord.Embed(title=str(title)[:256],description=str(description)[:4000],color=color)
    e.set_footer(text='Kai-Bot')
    return e

async def reply(i, title, description='', **kwargs):
    kwargs.setdefault('ephemeral',True)
    kwargs['embed']=card(title,description)
    if i.response.is_done(): return await i.followup.send(**kwargs)
    return await i.response.send_message(**kwargs)

async def error(i, exc):
    if isinstance(exc,ValueError): message=str(exc)
    elif isinstance(exc,discord.Forbidden): message='Permission Discord manquante. Le staff doit vérifier les droits du bot dans ce salon.'
    else:
        logging.getLogger('kai').error('Interaction failed: %s',type(exc).__name__,exc_info=exc)
        message='Une erreur est survenue. Relance /menu ; le détail technique est dans les logs.'
    await reply(i,'Action impossible',message)

class View(discord.ui.View):
    def __init__(self,user=None,*,timeout=180):
        super().__init__(timeout=timeout)
        self.user=user
    async def interaction_check(self,i):
        if not i.guild:
            await reply(i,'Serveur requis','Utilise ce panneau dans ton serveur Discord.'); return False
        if self.user and i.user.id!=self.user:
            await reply(i,'Ton espace personnel','Ouvre /menu pour utiliser tes propres commandes.'); return False
        return True
    async def on_error(self,i,exc,item): await error(i,exc)

class Modal(discord.ui.Modal):
    async def on_error(self,i,exc): await error(i,exc)

class Pages(View):
    def __init__(self,user,pages):
        super().__init__(user)
        self.pages=pages or [card('Aucun résultat')]
        self.index=0
    @discord.ui.button(label='Précédent',style=discord.ButtonStyle.secondary)
    async def previous(self,i,b):
        self.index=(self.index-1)%len(self.pages)
        await i.response.edit_message(embed=self.pages[self.index],view=self)
    @discord.ui.button(label='Suivant',style=discord.ButtonStyle.primary)
    async def next(self,i,b):
        self.index=(self.index+1)%len(self.pages)
        await i.response.edit_message(embed=self.pages[self.index],view=self)

def pages(title,lines,size=10):
    lines=list(lines)
    return [card(f'{title} · {n//size+1}', '\n'.join(lines[n:n+size])) for n in range(0,len(lines),size)] or [card(title,'Aucune donnée disponible.')]

async def show_pages(i,title,lines):
    p=pages(title,lines)
    send=i.followup.send if i.response.is_done() else i.response.send_message
    await send(embed=p[0],view=Pages(i.user.id,p),ephemeral=True)

async def staff(i):
    if not i.guild: raise ValueError('Commande réservée au serveur.')
    rid=await i.client.db.get(i.guild_id,'staff_role',0)
    if i.user.guild_permissions.manage_guild or await i.client.is_owner(i.user) or any(r.id==rid for r in i.user.roles): return True
    raise ValueError('Cette action est réservée au staff configuré.')

async def clan_tag(i):
    t=await i.client.db.get(i.guild_id,'clan')
    if not t: raise ValueError('Le clan n’est pas encore configuré. Un administrateur doit lancer /config.')
    return t

class Confirm(View):
    def __init__(self,user,action):
        super().__init__(user,timeout=90); self.action=action; self.used=False
    @discord.ui.button(label='Confirmer',style=discord.ButtonStyle.success)
    async def yes(self,i,b):
        if self.used: return await reply(i,'Déjà traité')
        self.used=True
        for c in self.children: c.disabled=True
        await i.response.edit_message(view=self)
        await self.action(i)
    @discord.ui.button(label='Annuler',style=discord.ButtonStyle.secondary)
    async def no(self,i,b):
        self.used=True
        await i.response.edit_message(embed=card('Action annulée'),view=None)

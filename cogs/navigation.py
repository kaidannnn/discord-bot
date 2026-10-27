import discord
from discord import app_commands
from discord.ext import commands
from core.ui import View, card, reply, staff

class Home(View):
    def __init__(self,bot):
        super().__init__(timeout=None); self.bot=bot
    @discord.ui.button(label='Mon compte',emoji='👤',custom_id='kai:home:account',style=discord.ButtonStyle.primary)
    async def account(self,i,b): await self.bot.get_cog('Accounts').home(i)
    @discord.ui.button(label='Mon clan',emoji='🛡️',custom_id='kai:home:clan',style=discord.ButtonStyle.primary)
    async def clan(self,i,b): await self.bot.get_cog('Clash').home(i)
    @discord.ui.button(label='Guerres',emoji='⚔️',custom_id='kai:home:war',style=discord.ButtonStyle.primary)
    async def war(self,i,b): await self.bot.get_cog('Wars').home(i)
    @discord.ui.button(label='Planning',emoji='📅',custom_id='kai:home:planning',row=1)
    async def planning(self,i,b): await self.bot.get_cog('Planning').home(i)
    @discord.ui.button(label='Équipe',emoji='🏆',custom_id='kai:home:roster',row=1)
    async def roster(self,i,b): await self.bot.get_cog('Roster').home(i)
    @discord.ui.button(label='Communauté',emoji='💬',custom_id='kai:home:community',row=1)
    async def community(self,i,b): await self.bot.get_cog('Community').home(i)
    @discord.ui.button(label='Staff',emoji='⚙️',custom_id='kai:home:staff',row=2)
    async def admin(self,i,b):
        await staff(i); await self.bot.get_cog('Admin').home(i)
    @discord.ui.button(label='Aide',custom_id='kai:home:help',row=2)
    async def help(self,i,b):
        await reply(i,'Trois étapes pour démarrer','**1. Mon compte** → lie ton village avec le jeton API du jeu.\n**2. Mon clan** → consulte les membres, dons et statistiques.\n**3. Guerres / Planning** → suis les attaques et inscris-toi.\n\nChaque espace est privé pour toi. Le panneau d’accueil reste utilisable après redémarrage. Si un sous-menu expire après 3 minutes, relance /menu.\nBesoin d’aide humaine ? Communauté → Ticket.')

    @discord.ui.button(label='KAI CUP',emoji='🏅',custom_id='kai:home:cup',row=2)
    async def cup(self,i,b):
        await reply(i,'KAI CUP','Consulte ton inscription, ton équipe et le tableau. Pour t’inscrire, utilise le panneau Cup publié par le staff.',view=CupHome(i.user.id))

class CupHome(View):
    async def invoke(self,i,name):
        cog=i.client.get_cog('KaiCup')
        if not cog: return await reply(i,'Cup inactive','Le propriétaire doit activer ENABLE_CUP puis synchroniser les commandes.')
        command=getattr(cog,name)
        await command.callback(cog,i)
    @discord.ui.button(label='Mon inscription',style=discord.ButtonStyle.primary)
    async def me(self,i,b): await self.invoke(i,'cup_me')
    @discord.ui.button(label='Mon équipe')
    async def team(self,i,b): await self.invoke(i,'cup_team')
    @discord.ui.button(label='Tableau du tournoi')
    async def bracket(self,i,b): await self.invoke(i,'cup_bracket')
    @discord.ui.button(label='État des inscriptions')
    async def status(self,i,b): await self.invoke(i,'cup_status')

class Navigation(commands.Cog):
    def __init__(self,bot): self.bot=bot; self.view=Home(bot)
    async def cog_load(self): self.bot.add_view(self.view)
    @app_commands.command(name='menu',description='Ouvrir le centre de commande guidé Kai-Bot')
    async def menu(self,i:discord.Interaction):
        await i.response.send_message(embed=card('KAI · CENTRE DE COMMANDE','Ton clan. Ton équipe. Tes objectifs.\n\nChoisis un espace pour commencer : le bot te guide à chaque étape.'),view=Home(self.bot),ephemeral=True)
    @app_commands.command(name='guide',description='Comprendre le parcours Kai-Bot')
    async def guide(self,i:discord.Interaction): await self.view.help.callback(i)
    @app_commands.command(name='panneau',description='Installer un accueil permanent dans ce salon')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self,i:discord.Interaction):
        await i.response.send_message(embed=card('KAI · BIENVENUE','Une seule porte d’entrée pour ton expérience Clash.\nChoisis ton espace ci-dessous.'),view=Home(self.bot))

async def setup(bot): await bot.add_cog(Navigation(bot))

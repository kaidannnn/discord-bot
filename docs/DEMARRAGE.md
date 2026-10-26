# Installer Kai-Bot — parcours simple

## 1. Discord

Dans le [Developer Portal](https://discord.com/developers/applications), ouvre ton application de bot ou crée-en une appelée **Kai-Bot**. Le nom visible de l’application est un réglage Discord : le code ne le renomme pas tout seul.

Dans **Bot**, récupère le token et conserve-le dans le fichier `.env` sur l’hébergeur uniquement. Ne le colle ni dans ChatGPT ni sur GitHub.

Invite le bot avec les scopes `bot` et `applications.commands`. Permissions utiles : Voir les salons, Envoyer des messages, Intégrer des liens, Joindre des fichiers, Lire l’historique. Ajoute Gérer les salons pour les tickets et la Cup, Gérer les rôles pour les rôles HDV/anniversaire. Le suivi optionnel d’invitations utilise la lecture des invitations et nécessite Gérer le serveur côté bot. Le bot n’a pas besoin du droit Administrateur.

Le socle fonctionne sans les intents privilégiés. `!syncguild` exige **Message Content Intent**, à activer dans le portail et avec `MESSAGE_CONTENT_INTENT=true`. L’alternative `python main.py --sync` n’en a pas besoin. Pour utiliser la Cup et retrouver les membres dans le cache Discord, active **Server Members Intent** et `MEMBERS_INTENT=true`.

## 2. Clash of Clans

Sur [developer.clashofclans.com](https://developer.clashofclans.com/), crée une clé correspondant à **l’IP publique de sortie de ton serveur Katabump**. L’adresse affichée pour le SFTP n’est pas forcément l’IP de sortie : demande cette IP au support si nécessaire.

Conserve cette clé dans `COC_API_TOKEN`. Cette clé du bot est différente du petit jeton API personnel que chaque joueur utilise avec `/link`.

## 3. Fichier privé `.env`

Copie `.env.example` vers `.env`, sur le serveur :

```dotenv
DISCORD_TOKEN=ton_token_discord
COC_API_TOKEN=ta_cle_clash
OWNER_ID=ton_identifiant_discord
GUILD_ID=identifiant_de_ton_serveur_discord
DATABASE_PATH=data/kai_bot.db
MEMBERS_INTENT=false
MESSAGE_CONTENT_INTENT=false
ENABLE_CUP=false
```

Les valeurs ci-dessus sont des exemples, pas des accès fonctionnels. Pour copier un identifiant Discord : activer le mode développeur dans les paramètres Discord, puis Copier l’identifiant sur le profil/serveur.

## 4. Démarrage manuel initial

Sur Katabump, choisis Python **3.12**, envoie les fichiers du projet, et place `main.py` et `requirements.txt` à la racine. Le fichier de démarrage est `main.py`.

Une fois les dépendances installées, lance **une fois** `python main.py --sync` pour enregistrer les commandes. Si le panneau n’accepte pas d’argument, crée temporairement `first_start.py` avec :

```python
import asyncio
from dotenv import load_dotenv
from main import run
load_dotenv()
asyncio.run(run(sync=True))
```

Choisis ce fichier au premier démarrage. Une fois les commandes disponibles, repasse sur `main.py`. Pas de synchronisation automatique à chaque reconnexion.

Sur Discord : `/config` → clan → salon guerres → salon staff → rôle staff. Puis `/panneau` pour installer le menu partagé. Teste `/link` avec ton propre village puis un profil, une guerre et une inscription au planning.

## 5. Contrôles réels indispensables

- `/diagnostic` confirme la connexion Clash et les modules chargés.
- Deux membres ne peuvent pas contrôler le même panneau personnel.
- Un membre ordinaire ne peut pas modifier la configuration.
- Le ticket est invisible à @everyone.
- Un redémarrage conserve les comptes, événements et le panneau permanent.
- `/backup` produit un fichier téléchargeable ; la restauration est testée sur une copie.

Aucun de ces essais connectés n’a été effectué par l’assistant à la livraison : aucun accès Discord/Katabump n’était configuré dans cet environnement.

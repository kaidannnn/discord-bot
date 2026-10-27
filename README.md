# Kai-Bot

Bot Discord Clash of Clans en français, avec un accueil guidé, des panneaux privés, des boutons, des listes et des formulaires. Signature **Kai-Bot** sous les réponses. Python 3.12.

> Reconstruction du 26 octobre 2026. Code et tests hors ligne fournis. La connexion réelle Discord/Clash/Katabump doit encore être validée avec les accès du propriétaire. Le code ancien reste dans l’historique GitHub.

## Par où commencer ?

1. Lis [le guide de démarrage](docs/DEMARRAGE.md).
2. Pour conserver le code et déployer automatiquement : [GitHub → Katabump](docs/DEPLOIEMENT.md).
3. Pour protéger les comptes liés, réglages et inscriptions : [sauvegarde et restauration](docs/SAUVEGARDES.md).
4. Pour savoir précisément ce qui est présent : [fonctionnalités et limites](docs/FONCTIONNALITES.md).
5. Pour le tournoi historique : [module KAI CUP](docs/CUP.md).

## Utilisation Discord

- Joueurs : **`/menu`**. Un point d’entrée pour tout le bot.
- Administrateur : **`/config`**, puis clan, salons et rôle staff.
- Accueil partagé durable : **`/panneau`** dans le salon choisi.
- Première installation : démarrer une fois avec **`python main.py --sync`**.

Les menus personnels expirent après 3 minutes. L’accueil partagé est persistant, y compris après un redémarrage. Les comptes sont vérifiés avec le jeton API du jeu ; aucun mot de passe Supercell ni code de connexion n’est demandé.

## Architecture

| Module | Responsabilité |
|---|---|
| `navigation` | Accueil permanent et navigation |
| `admin` | Configuration guidée, diagnostic, annonces avec confirmation |
| `accounts` | Liaison vérifiée, multicompte, compte principal, déliaison |
| `clash` | Profil, héros, troupes, sorts, équipements, membres, dons, raids, CSV |
| `wars` | GDC, LDC, attaques restantes/réalisées, bases ouvertes, rappels de 12 h à 5 min, fin, multi-clans |
| `statistics` | Classements des guerres archivées, périodes locales, Légende officielle |
| `planning` | Événements, inscriptions atomiques, réserve et absences |
| `roster` | Candidatures depuis les comptes vérifiés, validation staff |
| `community` | Suggestions avec suivi, XP, MP individuels prévisualisés |
| `tickets` | Salons privés et fermeture conservant les messages |
| `roles` | Rôles HDV vérifiés, anniversaire facultatif et récompense annuelle |
| `messaging` | Campagnes MP à un rôle, aperçu, file persistante et annulation |
| `resources` | Catalogue de bases et armées avec liens de copie |
| `welcome` | Accueil, suivi indicatif des invitations et classement |
| `backups` | Snapshot SQLite, export propriétaire, sauvegarde chiffrée externe |
| `cup` | Module historique récupéré, optionnel, tournoi 3→4→5 et playoffs |

Le nouveau socle partage un client HTTP Clash avec cache, limite de concurrence, délais et reprises sur erreurs temporaires. SQLite utilise WAL, des transactions et une séparation par serveur. Le module Cup historique conserve sa couche d’accès propre.

## Développement

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q core cogs scripts main.py bootstrap.py
```

Lancer les tests depuis la racine du projet. Ils ne nécessitent ni token Discord ni token Clash. Ne jamais versionner `.env`, les fichiers de base de données ou les clés de sauvegarde.

## Sources techniques

- [Documentation discord.py](https://discordpy.readthedocs.io/en/stable/)
- [API officielle Clash of Clans](https://developer.clashofclans.com/)
- [Déploiement Python Katabump](https://docs.katabump.com/guides/python/)
- [Gestion du serveur Katabump](https://docs.katabump.com/hosting/manage-your-server/)

La documentation Katabump décrit l’envoi par SFTP et le choix du fichier de démarrage. Le superviseur et le déploiement automatiques fournis ici sont notre propre intégration : leur compatibilité doit être testée sur le serveur réel.

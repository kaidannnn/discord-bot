# Protéger les données de Kai-Bot

GitHub protège le code. La base SQLite contient les comptes liés, réglages, suggestions, événements, XP, tickets, historiques et tables Cup. **Elle doit être sauvegardée séparément.** Le token de vérification d’un village n’est pas conservé.

## Copie ponctuelle

`/backup` est réservé au propriétaire du bot (`OWNER_ID`). Le fichier est transmis en réponse privée. Télécharge-le sur ton ordinateur ou un stockage personnel. Cette base contient les données de tous les serveurs du bot : ne la publie pas.

## Copie automatique hors Katabump

1. Crée sur GitHub un **autre dépôt PRIVÉ**, par exemple `kai-bot-data`. Ne mets jamais les sauvegardes dans le dépôt public du code.
2. Crée un token GitHub à permissions fines, limité à ce dépôt, avec **Contents: Read and write** et Metadata: Read.
3. Sur un ordinateur de confiance, exécute `python scripts/backup_key.py`. Conserve la clé obtenue dans un gestionnaire de mots de passe, **hors Katabump**. Sans elle, la sauvegarde chiffrée est inutilisable.
4. Dans `.env` sur Katabump, ajoute :

```dotenv
BACKUP_REPOSITORY=ton_pseudo/ton_depot_prive
BACKUP_GITHUB_TOKEN=token_limite_au_depot_de_donnees
BACKUP_ENCRYPTION_KEY=cle_generee
```

5. Redémarre, puis lance `/backup_externe`. Une confirmation doit indiquer une écriture réussie. `/diagnostic` affiche ensuite la date de la dernière sauvegarde réussie.

Le bot vérifie que le dépôt est privé **avant** de transférer la sauvegarde. Le contenu est chiffré avec Fernet. Il réalise un snapshot SQLite cohérent, puis écrit dans un des 24 fichiers horaires. L’historique Git conserve aussi les anciennes versions chiffrées : cela peut faire grossir le dépôt à long terme. Cette solution est adaptée à une petite base ; le code refuse une base brute de plus de 20 Mo. Prévoir un stockage objet avec rétention pour un gros bot.

Le cycle est horaire. Une suppression soudaine peut faire perdre les modifications depuis la dernière sauvegarde réussie. Aucun dispositif ne garantit « zéro perte pour toujours » : conserve aussi tes accès et teste la restauration.

## Restaurer

1. Arrête le bot.
2. Télécharge une sauvegarde `.db.fernet` depuis le dépôt privé.
3. Dans un terminal privé, définis la variable d’environnement `BACKUP_ENCRYPTION_KEY` avec ta clé. Ne l’écris pas dans une commande partagée ni dans le dépôt.
4. Lance :

```bash
python scripts/restore.py sauvegarde.db.fernet kai_bot_restored.db
```

Le script vérifie le déchiffrement, l’intégrité SQLite et refuse d’écraser un fichier existant. Transfère ensuite la base restaurée vers `data/kai_bot.db` sur le serveur **arrêté**, après avoir conservé une copie de l’ancienne. En mode superviseur, le chemin de la base est fixé à ce dossier partagé.

5. Remets `.env`, les clés Discord/Clash et la configuration de sauvegarde ; puis démarre.
6. Vérifie les comptes liés, un événement et la dernière date de sauvegarde.

Le bot sauvegarde uniquement sa base, pas les messages Discord ni les fichiers hébergés par d’autres services. Les secrets sont à conserver dans ton gestionnaire de mots de passe. Les copies locales avant déploiement disparaissent si tout le serveur est supprimé : elles complètent, mais ne remplacent pas la copie externe.

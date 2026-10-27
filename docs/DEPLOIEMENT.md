# GitHub → Katabump : mises à jour automatiques

## Ce que fait cette intégration

Une modification fusionnée dans `main` déclenche les tests GitHub Actions. S’ils réussissent, le workflow envoie une **nouvelle version dans son propre dossier** par SFTP. Le fichier `CURRENT` indique la version à lancer. `bootstrap.py`, le petit superviseur exécuté sur Katabump, surveille ce fichier, arrête proprement l’ancienne version puis démarre la nouvelle.

Si la nouvelle version ne parvient pas à se connecter à Discord dans le délai prévu, le superviseur retente la dernière version reconnue fonctionnelle. Il conserve au moins les trois dernières versions de code, trois copies locales avant déploiement et les dépendances séparées. Les dépendances anciennes ne sont pas automatiquement supprimées : surveiller le quota disque Katabump après plusieurs changements de dépendances.

**État à la livraison : scripts prêts, connexion Katabump non configurée et non testée.** Le dépôt GitHub existant est public. Il doit contenir uniquement le code, jamais les secrets ni la base.

## Configuration à faire une seule fois

### A. Sur Katabump

1. Récupère les informations **SFTP** depuis la rubrique Settings/Paramètres : hôte, port, utilisateur, mot de passe et racine distante.
2. Vérifie la clé hôte SSH auprès du panneau ou du support de Katabump. La valeur `known_hosts` doit être celle du serveur attendu ; ne désactive pas la vérification de clé.
3. Envoie **`bootstrap.py`**, **`requirements.txt`** et ton fichier privé **`.env`** à la racine du serveur (généralement `/home/container`).
4. Choisis **Python 3.12**, puis **PY FILE = `bootstrap.py`**.
5. Crée un fichier vide nommé **`SYNC_ONCE`** à la racine. Le superviseur le retirera après la première connexion réussie : il sert à enregistrer les commandes Discord une seule fois.
6. La base restera à `/home/container/data/kai_bot.db`, hors des dossiers de versions. Si tu démarres avec une ancienne base, ne l’écrase pas : seule la nouvelle base Kai-Bot est compatible sans migration dédiée.

### B. Sur GitHub

Dans `kaidannnn/discord-bot` → **Settings → Environments**, crée un environnement **`katabump`**.

Ajoute les **secrets** suivants dans cet environnement :

| Nom | Valeur à saisir en privé |
|---|---|
| `SFTP_HOST` | Hôte SFTP du serveur |
| `SFTP_PORT` | Port SFTP, souvent 2022 ; confirmer dans le panneau |
| `SFTP_USERNAME` | Identifiant SFTP |
| `SFTP_PASSWORD` | Mot de passe SFTP |
| `SFTP_KNOWN_HOSTS` | Ligne complète de clé hôte vérifiée, avec `[hôte]:port` si nécessaire |

Dans **Settings → Secrets and variables → Actions → Variables**, ajoute :

| Variable | Valeur |
|---|---|
| `KATABUMP_DEPLOY_ENABLED` | `true` une fois prêt à activer |
| `SFTP_ROOT` | `/home/container` ou la racine indiquée par le panneau |

Les tokens Discord et Clash restent **dans `.env` sur Katabump**. GitHub Actions n’en a pas besoin.

### C. Premier envoi

1. Vérifie/fusionne la reconstruction dans `main`.
2. Ouvre **Actions → Kai-Bot tests and deploy → Run workflow**, sur `main`.
3. Attends la réussite des tests puis de l’envoi SFTP.
4. Démarre le serveur Katabump. Vérifie le message « Kai-Bot: version connectée ».
5. Sur Discord, teste `/menu` et `/diagnostic`.

La réussite du workflow confirme le transfert, **pas à elle seule la connexion du bot**. Celle-ci est confirmée par le superviseur et Discord.

## Ensuite

Les modifications dans une branche sont testées. Les modifications fusionnées dans **main** sont déployées quand `KATABUMP_DEPLOY_ENABLED=true`. Il n’est plus nécessaire de recopier les cogs un par un. Pour de nouvelles commandes slash, recrée `SYNC_ONCE` avant le prochain redémarrage/déploiement.

## Retour arrière manuel

Dans le gestionnaire de fichiers, regarde les dossiers `releases/`. Mets le SHA de la version voulue dans le fichier `CURRENT`. Le superviseur changera de version. La base est conservée ; une migration destructive future nécessitera une restauration de sauvegarde distincte. La version actuelle crée des tables sans supprimer les anciennes.

## Si Katabump est supprimé

Le dépôt GitHub conserve le code. Recrée le serveur, réinstalle le superviseur et `.env`, restaure la dernière sauvegarde de données, puis relance le déploiement. Les identifiants SFTP et l’IP autorisée dans la clé Clash devront être mis à jour.

## Limites à connaître

- SFTP doit prendre en charge `posix-rename@openssh.com` pour basculer atomiquement `CURRENT`. Sinon le déploiement échoue sans basculer ; adaptation à prévoir avec le support.
- Le serveur doit pouvoir lancer Python, installer les dépendances avec pip et accéder à Discord, Clash et GitHub.
- Les changements de `bootstrap.py` exigent une mise à jour manuelle du fichier racine, car le superviseur ne se remplace pas lui-même.
- GitHub peut imposer une autorisation de workflows sur la connexion utilisée pour publier le fichier CI. Si ce fichier manque dans GitHub, l’envoi automatique n’est pas actif.

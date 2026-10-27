# Validation — 27 octobre 2026

- Python 3.12.14, discord.py 2.6.4, aiosqlite 0.21.0.
- Compilation de tous les modules Python réussie.
- Suite complète : **76 tests réussis + 6 sous-tests réussis** (26,05 s).
- Puis correction ciblée de l’attribution du rôle anniversaire après modification de la date ; **11 tests du module de fonctionnalités réussis**, incluant une nouvelle régression. Total actuel : 77 tests collectés.
- Chargement hors ligne du bot et de la Cup optionnelle ; commandes sérialisables et accueil persistant vérifiés.

## Ce que couvrent les tests

Propriété des comptes liés, sélection du principal, concurrence, capacité d’événement, isolation par serveur, rollback SQLite, snapshot et restauration, validation de tags/liens, contrôle d’accès aux panneaux, cache HTTP et réessais, refus de sauvegarder dans un dépôt public, chiffrement et accusé de sauvegarde externe simulée, pointeurs de déploiement, contrôle de santé, nettoyage conservateur des versions, retour effectif à la version précédente simulé, MP volontaires/anti-doublons/reprise d’envoi ambigu, anniversaire une fois par an, attribution indicative des invitations, échéances de rappels et transitions Cup récupérées.

## Ce qui reste à vérifier en conditions réelles

Connexion Discord et Clash, permissions des salons/rôles, IP autorisée Clash, interaction tactile dans Discord, suivi d’une guerre réelle, envoi SFTP Katabump avec clé hôte vérifiée, activation du superviseur et écriture dans le dépôt privé des sauvegardes. Aucun de ces essais connectés n’est présenté comme effectué.

Un avertissement de dépréciation `audioop` provient de discord.py sous Python 3.12 ; il n’empêche pas ces tests. Le projet cible Python 3.12.

## Reprise après incident — 27 octobre

Suite complète relancée : **83 tests et 6 sous-tests réussis** (12,02 s). La sauvegarde précède désormais l’arrêt de la version active. Une erreur disque, de permission ou SQLite diffère la mise à jour et laisse le bot actif ; une nouvelle tentative est prévue après 30 secondes. Six cas de régression couvrent ces erreurs avec et sans processus actif. Les essais connectés restent à effectuer.

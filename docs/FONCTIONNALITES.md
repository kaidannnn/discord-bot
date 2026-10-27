# Inventaire réel — reconstruction Kai-Bot

## Disponible dans le code

| Espace | Parcours et fonctions |
|---|---|
| Accueil | `/menu`, `/guide`, panneau partagé permanent `/panneau` |
| Comptes | `/link` vérifié, jusqu’à 25 villages, sélection du principal, `/unlink` confirmé, `/profile`, `/comptes` |
| Clash | `/clan`, `/player`, `/members`, `/donations`, `/classement` trophées, `/raids`, `/export` CSV staff ; héros, troupes, sorts, équipements, succès via le profil |
| Guerres | `/war`, `/war_live`, attaques restantes/réalisées, composition, bases non triplées, `/warlog`, `/cwl`, `/missed` depuis le début du suivi |
| Automatique | Interrogation GDC toutes les 2 minutes, LDC optionnelles, rappels à 12 h, 2 h, 1 h, 30 min, 15 min et 5 min, puis résultat, historique local ; snapshots membres toutes les 30 minutes, conservation 90 jours |
| Performance | `/mvp`, `/season_stats`, `/season_start`, `/activity`, `/legend_results`, explication honnête via `/lastseen` |
| Équipe | `/roster` : candidatures depuis un compte lié, vue Discord + tag, validation staff |
| Planning | `/planning`, `/planning_add`, date Paris contrôlée, présence/réserve/absence, capacité protégée contre inscriptions concurrentes |
| Communauté | Suggestions catégorisées, suivi personnel et traitement staff, XP 5 par message avec délai 10 secondes, classement de niveaux, MP individuel avec aperçu et confirmation |
| Tickets | Création d’un salon privé, un ticket ouvert par personne, fermeture en lecture seule pour l’auteur |
| Staff | Configuration guidée, diagnostic, annonce avec aperçu, permission vérifiée à chaque action, synchro propriétaire explicite |
| Sauvegarde | Export SQLite cohérent, sauvegarde externe horaire chiffrée vers un dépôt GitHub privé, script de restauration vérifiant l’intégrité |
| Rôles | Rôles HDV automatiques depuis le compte principal vérifié ; anniversaire facultatif, 300 XP annuels maximum, rôle temporaire |
| Accueil | Bienvenue avec menu, suivi indicatif des invitations avec classement (intents et permissions nécessaires) |
| Campagnes MP | Prévisualisation, confirmation, volontariat des destinataires, file persistante, suivi, annulation, pas de renvoi automatique d’un envoi ambigu |
| Ressources | Catalogues de bases et armées, filtres HDV, liens officiels de copie validés, notes staff |
| Multi-clans | Jusqu’à 24 clans supplémentaires, choix interactif, salon de suivi et LDC configurables par clan |
| Déploiement | Tests CI, transfert SFTP par versions, pointeur atomique, superviseur, sauvegarde locale avant mise à jour et retour à la dernière version connectée |
| Cup optionnelle | 25 sous-commandes historiques, panneau d’inscription et menu joueur, mécanismes 3→4→5 conservés |

## Ce qui n’est pas encore rétabli à l’identique

La reconstruction n’est pas une restauration bit à bit de l’ancien serveur. Les anciens liens Discord, scores XP, événements et historiques sont perdus tant qu’aucune sauvegarde de leur base n’est retrouvée.

Restent à porter ou à affiner : missions/quêtes avancées et récompenses personnalisées, rappels de raids/JDC, visualisation chronologique détaillée des performances et unification complète de l’administration Cup. Certaines anciennes commandes sont regroupées dans les menus au lieu de conserver tous les anciens alias.

L’API officielle utilisée ne fournit pas ici une dernière connexion exacte, les points JDC individuels en direct, ni le détail quotidien exact des attaques/défenses Légende. Le bot ne fabrique pas ces données. Les statistiques locales commencent à l’installation et peuvent manquer des guerres non observées pendant une coupure.

Le rappel de guerre est mémorisé après envoi. Un arrêt brutal entre l’envoi Discord et son enregistrement SQLite peut entraîner un doublon au redémarrage ; il ne s’agit pas d’une garantie « exactly once » entre deux services.

## Mise en production

Le code est contrôlé hors ligne. La mise en production, l’identité visible de l’application Discord, les tokens, le dépôt privé des sauvegardes, les secrets GitHub Actions et les accès SFTP restent à configurer et à tester sur les comptes réels.

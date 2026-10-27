# Kai-Bot — journal de reprise

Date : 26 octobre 2026. Nom exact demandé : **Kai-Bot**, sous toutes les réponses. L’utilisateur demande un bot intégral reconstruit, guidé, interactif, multi-cogs, et une liaison GitHub→Katabump avec protection contre la suppression de l’hébergeur.

## Réalisé dans cette session

- Retrouvé le pack ancien V4 et le module Cup lot 94 ; identifié le dépôt GitHub existant `kaidannnn/discord-bot` (public).
- Conservé le point de départ GitHub `93acf429eb15841dc5d9ce1bce5e13eb8ac3950a` ; branche de travail `kai-bot-rebuild`.
- Nouveau socle multi-cogs, menus, formulaires, comptes vérifiés, clan, guerres/LDC, roster, planning, suggestions, XP, tickets, sauvegardes et statistiques.
- Cup récupérée, activable séparément, avec ses tests de logique conservés.
- Déploiement via GitHub Actions/SFTP et superviseur avec rollback ; aucune connexion Katabump active à ce stade.
- Sauvegardes chiffrées horaires vers un dépôt privé distinct ; restauration vérifiée hors ligne.
- Guides débutant, déploiement, sauvegardes, Cup et inventaire explicite des fonctions restantes.

## Points d’attention pour la suite

1. Lire le résultat final des tests dans `VALIDATION.md` ; ne pas réutiliser les anciens rapports de test des packs de récupération.
2. Consulter `docs/FONCTIONNALITES.md` : le socle utilisable n’implémente pas encore à l’identique toutes les anciennes fonctionnalités. Rôles HDV, MP par rôle avec file durable, anniversaires, ressources, bienvenue/invitations et multi-clans ont été ajoutés. Prioriser les essais connectés et l’unification staff/Cup, puis les missions et les rappels de raids.
3. Tester les erreurs réseau/permissions et les workflows au-delà de la simple compilation. Les essais connectés nécessitent les accès du propriétaire.
4. Ne jamais demander les tokens dans le chat ni les versionner. Ne jamais mettre la base dans le dépôt public.
5. Le déploiement requiert les secrets SFTP GitHub et `.env` sur Katabump ; l’assistant n’a pas ces accès et ne doit pas prétendre que c’est connecté.
6. Le nom de l’application Discord doit être réglé par le propriétaire dans le Developer Portal ; la signature des réponses est réglée dans le code.
7. L’automatisation « Avancement de Kai-Bot » est programmée pour deux points horaires. À chaque exécution, reprendre les fichiers réellement disponibles, avancer sur un point testable, sauvegarder les changements et rendre compte sans inventer du travail continu entre les exécutions.

## Définition de terminé

Socle testé + code durable sur GitHub + version téléchargeable + documentation claire. Déploiement actif et sauvegarde externe réussie ne peuvent être déclarés que sur preuves des services réels. La restauration complète des fonctionnalités historiques demeure un chantier jusqu’à clôture de l’inventaire.

## Correction du 27 octobre 2026

Après autorisation explicite de publication sur le dépôt public, correction du superviseur : sauvegarde avant arrêt, erreurs de sauvegarde gérées, version active conservée et nouvelle tentative différée. Six tests ajoutés ; suite complète : 83 tests et 6 sous-tests réussis. Aucun secret ni donnée de joueur ajouté.

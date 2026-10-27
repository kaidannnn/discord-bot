# KAI CUP — module historique récupéré

Le code Cup provient du pack `KAI_CUP_lot_94_2026-10-11.zip` retrouvé parmi les fichiers du projet. Ses mécanismes de tournoi et ses tests ont été conservés. La reconstruction adapte le chemin de la base, la signature Kai-Bot, le timeout HTTP et le chargement hors ligne.

## Activation

1. Active `ENABLE_CUP=true` dans `.env` et, pour les fonctions qui s’appuient sur la liste de membres, `MEMBERS_INTENT=true` ainsi que l’intent correspondant dans Discord.
2. Redémarre puis synchronise les commandes une seule fois.
3. Lance `/cup setup` avec les paramètres souhaités sur un serveur de test.
4. Installe le panneau d’inscription. Les joueurs utilisent ce panneau ; le bouton **KAI CUP** de `/menu` donne accès à leur inscription, leur équipe, le tableau et l’état des inscriptions.

## Périmètre conservé

- Places clan et externes, inscriptions et liste d’attente.
- Tirage d’équipes, capitaines, salons privés, liaison aux clans Cup.
- Déroulement 3→4→5, résultats, recrutement adverse et verrouillages.
- Check-in, compositions, bracket, phases suivantes et MVP.
- Diagnostic, export et suivi live à 45 secondes.

Le format historique cible **72 joueurs, 24 équipes, HDV 18**. Il ne s’agit pas d’un moteur universel pour tout format. Certains titres historiques affichent encore KAI CUP 2026 ; la signature du bot est bien Kai-Bot. Le module est désactivé par défaut pour éviter de démarrer les boucles et la gestion des salons avant configuration.

## Limites de l’intégration

Le module conserve ses propres connexions SQLite/HTTP et une partie de ses commandes staff à paramètres ; son interface n’est pas encore aussi unifiée que celle du nouveau socle. Les boutons joueurs essentiels sont accessibles depuis `/menu`. Le registre des inscriptions Cup historique est distinct des comptes vérifiés du nouveau `/link` : une migration complète vers un registre unique reste à réaliser.

Les tests récupérés valident des transitions et contrôles de cohérence hors ligne ; ils ne valident pas les permissions réelles de ton serveur, l’API live ou une soirée complète en conditions réelles. Organise un tournoi de test avant un événement public. `/cup reset_draw` est réservé aux tirages de test : ne l’utilise pas pour nettoyer un tournoi réel.

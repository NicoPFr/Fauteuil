# Connecter ton compte Google à Fauteuil

La connexion sert à lire tes abonnements YouTube et à proposer leurs dernières vidéos. Le flux de recommandations personnalisé de YouTube n’est pas exposé à l’API publique.

## Première connexion

1. Dans Fauteuil, clique sur **Connecter Google**, puis **Ouvrir YouTube Data API**. Dans Google Cloud, crée ou sélectionne un projet et clique sur **Activer**.
2. Depuis Fauteuil, clique sur **Configurer Fauteuil**. Donne le nom **Fauteuil** à l’application, indique ton adresse de contact et choisis **Externe**. Dans **Audience**, ajoute ensuite ton adresse Google à la liste des utilisateurs test.
3. Clique sur **Créer un client Google**, choisis **Application de bureau**, puis télécharge le fichier **JSON** proposé par Google.
4. Reviens dans Fauteuil, clique sur **Importer le fichier Google et se connecter** et choisis le fichier JSON. Autorise l’accès en lecture seule à YouTube dans la fenêtre Google.

Le secret client est conservé dans le trousseau macOS. Le jeton de renouvellement est également dans le trousseau. Le flux d’autorisation utilise PKCE et redirige vers `127.0.0.1` sur le Mac.

## Sources officielles

- [OAuth pour applications de bureau](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Authentification YouTube Data API](https://developers.google.com/youtube/data/guides/authentication)
- [Abonnements YouTube](https://developers.google.com/youtube/v3/docs/subscriptions/list)
- [Limites de l’API sur les recommandations personnalisées](https://developers.google.com/youtube/data/revision_history)

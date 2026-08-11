# Stockage des Données - Configuration Render

## Problème Identifié

Les données (comptes, demandes, validations...) disparaissaient sur Render à
chaque redémarrage / redéploiement du service. Deux causes cumulées :

1. **Stockage éphémère** : le plan gratuit Render ne fournit pas de disque
   persistant. La base SQLite était donc écrite dans le système de fichiers
   du conteneur, qui est entièrement recréé à chaque redéploiement et à
   chaque réveil du service après une mise en veille (inactivité).
2. **Réinitialisation automatique destructive** : au démarrage, l'application
   comptait les utilisateurs et, s'il y en avait moins de 5, supprimait
   **toutes les tables** puis recréait uniquement les 5 comptes de démo. Le
   moindre redémarrage avec une base incomplète (ou vidée par la cause n°1)
   effaçait donc irrémédiablement toutes les données réelles.

## Solution Implémentée

### 1. Base de données PostgreSQL gérée par Render

`render.yaml` déclare maintenant une base PostgreSQL gratuite Render
(`app-mmse-db`) et transmet automatiquement son URL de connexion à
l'application via la variable d'environnement `DATABASE_URL` :

```yaml
databases:
  - name: app-mmse-db
    plan: free
    databaseName: suivi_heures
    user: suivi_heures_user

services:
  - type: web
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: app-mmse-db
          property: connectionString
```

PostgreSQL est un service géré indépendant du conteneur web : les données
survivent aux redéploiements, aux redémarrages et aux mises en veille du
service gratuit.

`suivi-heures-ot/database.py` utilise `DATABASE_URL` dès qu'elle est définie
(sinon il utilise SQLite localement pour le développement) — aucun autre
fichier n'a besoin d'être modifié.

### 2. Suppression de la logique de réinitialisation destructive

`main.py` ne fait plus jamais de `DROP` automatique. Au démarrage :

- `ensure_schema()` crée les tables manquantes et ajoute les colonnes
  manquantes, sans jamais toucher aux données existantes.
- Les 5 comptes de démonstration ne sont créés qu'une seule fois, et
  uniquement si la base est **totalement vide** (0 utilisateur). Dès qu'un
  seul compte existe, cette création est ignorée.

## Limite connue : les pièces jointes

PostgreSQL résout la persistance des données métier (comptes, demandes,
validations...). Les **fichiers téléversés** (pièces jointes) restent en
revanche stockés sur le disque du conteneur, qui reste éphémère sur le plan
gratuit Render : ils peuvent être perdus lors d'un redéploiement. Si les
pièces jointes doivent elles aussi être durables, il faudra soit passer à un
plan Render avec disque persistant, soit les stocker dans un service de
stockage objet externe (S3 ou équivalent).

## Vérification

1. Créer une demande via l'interface.
2. Vérifier qu'elle s'affiche dans la liste.
3. Redéployer ou redémarrer le service depuis le tableau de bord Render.
4. Vérifier que la demande est toujours présente.

## Développement local

En local, sans `DATABASE_URL` défini, l'application continue d'utiliser un
fichier SQLite (`suivi_heures.db`) dans le répertoire du projet — aucune
configuration supplémentaire n'est nécessaire pour développer.

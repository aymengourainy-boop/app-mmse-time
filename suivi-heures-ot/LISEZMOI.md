# Suivi des heures & OT — Démarrage

## Installation (une seule fois)

```
pip install -r requirements.txt
```

## Créer les données de test (une seule fois)

```
python seed.py
```

Cela crée le fichier `suivi_heures.db` (base SQLite) avec 5 comptes :

| Rôle        | Matricule | Mot de passe |
|-------------|-----------|--------------|
| Technicien        | TECH001   | tech123      |
| Technicien SHIFT  | TECHS001  | shift123     |
| Superviseur       | SUP001    | sup123       |
| Superviseur SHIFT | SUPS001   | supshift123  |
| Admin             | ADMIN001  | admin123     |

## Lancer l'application

Assurez-vous d'être dans le dossier `suivi-heures-ot/suivi-heures-ot` puis lancez :

```
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

ou, si vous préférez démarrer avec Python :

```
python main.py
```

Puis ouvrez : **http://127.0.0.1:8000**

Le même serveur sert à la fois la page HTML (`static/index.html`) et l'API
(`/api/...`) — un seul port, pas de configuration CORS à gérer.

## Déploiement Microsoft Azure (App Service Linux)

Variables d'environnement recommandées :

```
APP_DATA_DIR=/home/site/data
PORT=8000
```

Commande de démarrage :

```
python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Notes :
- la base SQLite et les uploads sont stockés dans `APP_DATA_DIR`
- si vous utilisez PostgreSQL sur Azure, définissez simplement `DATABASE_URL`
- pensez à lancer `python seed.py` une première fois pour créer les comptes de base

## Calcul des heures supplémentaires

La règle des heures supplémentaires peut être paramétrée par département dans la table
`regles_heures_supplementaires`. Si une règle spécifique au département est disponible,
elle est utilisée pour déterminer le seuil des heures normales journalières.

## Passer en PostgreSQL plus tard

Dans `database.py`, changez uniquement la ligne `DATABASE_URL`, ou définissez
la variable d'environnement avant de lancer le serveur :

```
export DATABASE_URL="postgresql+psycopg2://utilisateur:motdepasse@localhost:5432/suivi_heures"
uvicorn main:app --reload
```

Il faudra aussi installer le pilote PostgreSQL : `pip install psycopg2-binary`.
Aucun autre fichier n'a besoin d'être modifié.

## Export des heures approuvées / rejetées

Un script d'export est disponible dans `export_heures.py`.

Exemples :

```
python export_heures.py --statut approuvee --format csv --output heures_approuvees.csv
python export_heures.py --statut rejetee --format txt --output heures_rejetees.txt
```

Le fichier CSV peut être ouvert dans Excel. Le fichier texte contient un rapport lisible avec un bloc pour chaque demande.

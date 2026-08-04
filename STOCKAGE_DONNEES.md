# Stockage des Donnees - Configuration Render

## Probleme Identifie et Resolu

**PROBLEME:** Les donnees envoyees par les techniciens sur Render n'etaient pas stockees apres le redemarrage de l'application.

**CAUSE:** La base de donnees SQLite etait stockee dans le conteneur ephemere (`/app/`) au lieu du disque persistant (`/var/data`). Lors d'un redemarrage ou d'un redeploi, tous les fichiers du conteneur etaient supprimes, y compris la base de donnees.

## Solution Implementee

### 1. **Modification de database.py**

La fonction `_get_app_data_dir()` detecte automatiquement l'environnement et utilise le bon chemin:

```python
# Sur Render: /var/data (disque persistant)
# Sur Azure: /home/site/data
# En developpement: ./ (repertoire courant)
```

**Ordre de priorite:**
1. Si `DATABASE_URL` est defini → utiliser le repertoire courant (PostgreSQL)
2. Si `RENDER=true` → `/var/data` (disque persistant Render)
3. Si `WEBSITE_SITE_NAME` existe → `/home/site/data` (Azure)
4. Si `APP_DATA_DIR` est defini → utiliser cette valeur
5. Sinon → `./` (developpement local)

### 2. **Modification de render.yaml**

Ajout de la variable d'environnement `RENDER=true` pour s'assurer que la detection fonctionne correctement:

```yaml
envVars:
  - key: APP_DATA_DIR
    value: /var/data
  - key: RENDER
    value: "true"
```

## Verification

Pour verifier que la base de donnees est correctement stockee sur Render:

1. **Aller dans le tableau de bord Render**
2. **Ouvrir votre service app-mmse-time**
3. **Aller dans l'onglet Disks**
4. **Verifier que le disque "app-mmse-data" est monte a /var/data**

## Donnees Persistantes

### Avant cette correction:
```
Redemarrage Render
  ↓
Conteneur ephemere detruit
  ↓
Base de donnees disparue ❌
```

### Apres cette correction:
```
Redemarrage Render
  ↓
Conteneur ephemere detruit
  ↓
Disque persistant /var/data presereve
  ↓
Base de donnees restauree ✓
```

## Fichiers Qui Persistent

Tous les fichiers stockes dans `/var/data`:
- `suivi_heures.db` (la base de donnees SQLite)
- `uploads/` (les fichiers joints)
- Tout autre fichier stocke par l'application

## Developpement Local

En developpement local, la base de donnees est stockee dans le repertoire courant du projet:
```
C:\Users\...\suivi-heures-ot.worktrees\verifier-donnees-stockage-bdd\suivi_heures.db
```

## Test de Redemarrage

Pour tester que les donnees persistent sur Render:

1. **Envoyer une demande via l'interface**
2. **Verifier qu'elle s'affiche dans la liste**
3. **Redemarrer le service depuis le tableau de bord Render** (Reboot)
4. **Verifier que la demande est toujours presente** ✓

## Prochaines Etapes Recommandees

1. **Optionnel:** Augmenter la taille du disque si vos donnees depassent 1 GB
   ```yaml
   disk:
     sizeGB: 10  # Au lieu de 1
   ```

2. **Optionnel:** Mettre en place des sauvegardes regulieres
   - Exporter les donnees vers PostgreSQL pour plus de securite
   - Ou ajouter une sauvegarde automatique du fichier SQLite

3. **Recommande:** Monitorer la taille du disque
   - Ajouter des logs pour verifier l'utilisation de l'espace disque

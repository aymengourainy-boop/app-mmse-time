#!/bin/bash
# Script de démarrage pour Render
# Initialise la base de données puis démarre l'application

set -e

echo "[STARTUP] PWD: $(pwd)"
echo "[STARTUP] LS:"
ls -la

echo "[STARTUP] Initialisation de la base de donnees..."
cd suivi-heures-ot
python init_db.py

echo "[STARTUP] Demarrage de l'application..."
python main.py


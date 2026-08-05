#!/bin/bash
# Script de démarrage pour Render
# Initialise la base de données puis démarre l'application

cd suivi-heures-ot
echo "[STARTUP] Initialisation de la base de donnees..."
python init_db.py

if [ $? -ne 0 ]; then
  echo "[STARTUP] Erreur lors de l'initialisation!"
  exit 1
fi

echo "[STARTUP] Demarrage de l'application..."
python main.py

#!/bin/bash
# Script de démarrage pour Render
# Démarre l'application avec Uvicorn en bindant le port correctement

set -e

echo "[STARTUP] Démarrage de l'application avec Uvicorn..."
cd suivi-heures-ot

# Utiliser la variable PORT de Render, sinon 8000 par défaut
PORT=${PORT:-8000}
echo "[STARTUP] Binding to port $PORT"

uvicorn main:app --host 0.0.0.0 --port $PORT

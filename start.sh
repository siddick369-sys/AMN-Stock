#!/usr/bin/env bash
# start.sh — Script de démarrage pour Render (Threading local).

# Lancement de Gunicorn au premier plan (bloquant)
echo "==> Démarrage de Gunicorn (Threading local)..."
gunicorn amn_stock.wsgi:application \
  --workers 2 \
  --threads 4 \
  --timeout 120 \
  --bind 0.0.0.0:$PORT \
  --access-logfile - \
  --error-logfile -

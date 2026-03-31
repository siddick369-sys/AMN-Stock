#!/usr/bin/env bash
# start.sh — Script de démarrage pour Render (Threading local).

# IMPORTANT : 1 seul worker Gunicorn obligatoire avec LocMemCache + daemon threads.
# LocMemCache est par-processus : si workers > 1, le thread qui écrit le résultat IA
# et le worker qui répond au poll HTTP sont des processus différents → cache miss → "erreur".
# 4 threads suffisent largement pour les appels I/O-bound (Groq, WhatsApp, emails).

# Lancement de Gunicorn au premier plan (bloquant)
echo "==> Démarrage de Gunicorn (1 worker / 4 threads)..."
gunicorn amn_stock.wsgi:application \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --bind 0.0.0.0:$PORT \
  --access-logfile - \
  --error-logfile -

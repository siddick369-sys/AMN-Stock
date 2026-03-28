#!/usr/bin/env bash
# start.sh — Lance Gunicorn + Celery Worker + Celery Beat dans le même service.
# Utile pour rester sur le plan FREE de Render.

# 1. Lancer Celery Worker en arrière-plan
echo "==> Démarrage de Celery Worker..."
celery -A amn_stock worker --loglevel=info --concurrency=1 --max-tasks-per-child=10 &

# 2. Lancer Celery Beat en arrière-plan
echo "==> Démarrage de Celery Beat..."
celery -A amn_stock beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler &

# 3. Lancer Gunicorn au premier plan (bloquant)
echo "==> Démarrage de Gunicorn..."
gunicorn amn_stock.wsgi:application \
  --workers 2 \
  --threads 2 \
  --timeout 120 \
  --bind 0.0.0.0:$PORT \
  --access-logfile - \
  --error-logfile -

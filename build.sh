#!/usr/bin/env bash
# build.sh — Script de build exécuté par Render à chaque déploiement.
# Ce script remplace toutes les commandes qu'on lancerait normalement via le
# shell (non disponible sur le plan Free de Render).
# Chaque opération est idempotente : relancer le build ne crée pas de doublons.
set -o errexit   # quitter immédiatement en cas d'erreur

# ── 1. Dépendances Python ────────────────────────────────────────────────────
echo "==> [1/5] Installation des dépendances Python..."
pip install --upgrade pip
pip install -r requirements.txt
chmod +x start.sh

# ── 2. Fichiers statiques ────────────────────────────────────────────────────
echo "==> [2/5] Collecte des fichiers statiques (WhiteNoise)..."
python manage.py collectstatic --no-input

# ── 3. Migrations base de données ────────────────────────────────────────────
# Applique toutes les migrations en attente (idempotent).
# Lors du tout premier déploiement, crée les tables Django + Celery Beat/Results.
echo "==> [3/5] Application des migrations de base de données..."
python manage.py migrate --no-input

# ── 4. Tâches planifiées Celery Beat ─────────────────────────────────────────
# Enregistre les PeriodicTask en base (DatabaseScheduler).
# get_or_create garantit l'idempotence : un re-déploiement ne crée pas de doublons.
echo "==> [4/5] Initialisation des tâches planifiées Celery Beat..."
python manage.py shell -c "
from django_celery_beat.models import PeriodicTask, CrontabSchedule
import json

# --- Tâche 1 : Vérification du stock critique toutes les 30 minutes ---
sched_30min, _ = CrontabSchedule.objects.get_or_create(
    minute='*/30', hour='*',
    day_of_week='*', day_of_month='*', month_of_year='*',
    defaults={'timezone': 'Africa/Abidjan'},
)
task1, created1 = PeriodicTask.objects.get_or_create(
    name='check-low-stock-every-30min',
    defaults=dict(
        task='stock.tasks.check_low_stock',
        crontab=sched_30min,
        args=json.dumps([]),
        enabled=True,
        description='Vérifie le stock critique et envoie des alertes.',
    ),
)
print(f'  check_low_stock : {\"créée\" if created1 else \"déjà présente\"}')

print('Tâches Celery Beat configurées.')
"

# ── 5. Vérification finale ────────────────────────────────────────────────────
# Note : manage.py check --database default est omis volontairement.
# NeonDB free tier peut se mettre en pause entre les étapes du build,
# provoquant un timeout. Le succès de migrate (step 3) suffit à valider
# la connexion à la base de données.
echo "==> [5/5] Vérification de la configuration Django (sans connexion DB)..."
python manage.py check

echo ""
echo "======================================================"
echo "  Build AMN-Stock terminé avec succès."
echo "  Migrations   : OK"
echo "  Static files : OK"
echo "  Celery Beat  : OK"
echo "======================================================"

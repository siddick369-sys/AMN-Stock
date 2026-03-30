#!/usr/bin/env bash
# build.sh — Script de build exécuté par Render à chaque déploiement.
# Chaque opération est idempotente.

set -o errexit   # quitter immédiatement en cas d'erreur

# ── 1. Dépendances Python ────────────────────────────────────────────────────
echo "==> [1/4] Installation des dépendances Python..."
pip install --upgrade pip
pip install -r requirements.txt
chmod +x start.sh

# ── 2. Fichiers statiques ────────────────────────────────────────────────────
echo "==> [2/4] Collecte des fichiers statiques (WhiteNoise)..."
python manage.py collectstatic --no-input

# ── 3. Migrations base de données ────────────────────────────────────────────
echo "==> [3/4] Application des migrations de base de données..."
python manage.py migrate --no-input

# ── 4. Vérification finale ────────────────────────────────────────────────────
echo "==> [4/4] Vérification de la configuration Django..."
python manage.py check

echo ""
echo "======================================================"
echo "  Build AMN-Stock terminé avec succès."
echo "  (Migration Celery -> Threading appliquée)"
echo "======================================================"

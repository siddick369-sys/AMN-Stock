#!/usr/bin/env bash
# build.sh — Script de build exécuté par Render à chaque déploiement.
# Chaque opération est idempotente.

set -o errexit   # quitter immédiatement en cas d'erreur

# ── 0. Dépendances système pour WeasyPrint ───────────────────────────────────
# WeasyPrint utilise Pango/Cairo/GLib pour le rendu PDF (Ubuntu/Debian).
# Ces librairies sont déjà présentes sur Render mais on s'assure qu'elles
# sont bien installées. || true évite d'échouer si déjà présentes.
echo "==> [0/5] Dépendances système WeasyPrint (Pango, Cairo, Harfbuzz)..."
apt-get install -y --no-install-recommends \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libharfbuzz0b \
  libcairo2 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  fonts-liberation \
  fonts-dejavu-core \
  2>/dev/null || true

# ── 1. Dépendances Python ────────────────────────────────────────────────────
echo "==> [1/5] Installation des dépendances Python..."
pip install --upgrade pip
pip install -r requirements.txt
chmod +x start.sh

# ── 2. Fichiers statiques ────────────────────────────────────────────────────
echo "==> [2/5] Collecte des fichiers statiques (WhiteNoise)..."
python manage.py collectstatic --no-input

# ── 3. Migrations base de données ────────────────────────────────────────────
echo "==> [3/5] Application des migrations de base de données..."
python manage.py migrate --no-input

# ── 4. Vérification finale ────────────────────────────────────────────────────
echo "==> [4/5] Vérification de la configuration Django..."
python manage.py check

echo ""
echo "======================================================"
echo "  Build AMN-Stock terminé avec succès."
echo "======================================================"

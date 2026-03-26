#!/usr/bin/env bash
# build.sh — Script de build exécuté par Render à chaque déploiement
set -o errexit   # quitter immédiatement en cas d'erreur

echo "==> Installation des dépendances Python..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Collecte des fichiers statiques..."
python manage.py collectstatic --no-input

echo "==> Application des migrations de base de données..."
python manage.py migrate --no-input

echo "==> Build terminé avec succès."

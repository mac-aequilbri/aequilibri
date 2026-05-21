#!/usr/bin/env bash
# Render build script — runs once at each deploy before the web process starts.
set -o errexit

echo "==> Installing Python dependencies"
pip install -r requirements.txt

echo "==> Collecting static files"
python manage.py collectstatic --no-input

echo "==> Applying database migrations"
python manage.py migrate --no-input

echo "==> Loading saved roof corrections"
python manage.py loaddata fixtures/corrections.json

echo "==> Build complete"

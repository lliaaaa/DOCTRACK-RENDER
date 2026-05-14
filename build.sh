#!/usr/bin/env bash
set -e

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Running database migrations..."
flask --app run db upgrade

echo "==> Build complete."

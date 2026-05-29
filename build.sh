#!/usr/bin/env bash
# build.sh — Render build script for DocTrack
# Runs on every deploy before the start command.
set -e

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Running database migrations..."
python -m flask --app run db upgrade

echo "==> Build complete."

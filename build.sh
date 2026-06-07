#!/usr/bin/env bash
# build.sh — Render build script for DocTrack
set -e

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Build complete."
echo "==> Note: db.create_all() and seeding run automatically on app startup."
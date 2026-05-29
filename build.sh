#!/usr/bin/env bash
# build.sh — Render build script for DocTrack
set -e

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Running database migrations..."
# If the DB is brand new and db.create_all() hasn't run yet,
# flask db upgrade will create tables via migration.
# If the DB already has tables (created by db.create_all()),
# the migration checks for column/table existence before altering — safe.
flask db upgrade || {
  echo "==> Migration failed — attempting to stamp head (tables already exist via db.create_all)..."
  flask db stamp head
  echo "==> Stamped. Schema is managed by db.create_all()."
}

echo "==> Build complete."

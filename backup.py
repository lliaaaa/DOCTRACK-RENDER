"""
backup.py  —  DocTrack Database Backup Utility
-----------------------------------------------
For PostgreSQL (Render deployment): uses pg_dump to export a .sql file.
For local SQLite fallback: copies the .db file directly.

Usage:
    python backup.py

Schedule via cron (daily at 2 AM):
    0 2 * * * cd /path/to/doctrack && python backup.py >> logs/backup.log 2>&1
"""

import os
import subprocess
import shutil
from datetime import datetime


BACKUP_DIR  = os.path.join(os.path.dirname(__file__), "backups")
MAX_BACKUPS = 10   # keep the last N backups


def run_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    db_url = os.environ.get("DATABASE_URL", "")

    if db_url and "postgres" in db_url:
        _backup_postgres(db_url, timestamp)
    else:
        _backup_sqlite(timestamp)

    _rotate_old_backups()


def _backup_postgres(db_url: str, timestamp: str):
    """Dump PostgreSQL database to a .sql file using pg_dump."""
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    out_file = os.path.join(BACKUP_DIR, f"doctrack_{timestamp}.sql")
    try:
        subprocess.run(
            ["pg_dump", db_url, "-f", out_file, "--no-password"],
            check=True, capture_output=True, text=True
        )
        size_kb = os.path.getsize(out_file) // 1024
        print(f"[{timestamp}] PostgreSQL backup OK → {out_file} ({size_kb} KB)")
    except subprocess.CalledProcessError as e:
        print(f"[{timestamp}] pg_dump FAILED: {e.stderr}")
    except FileNotFoundError:
        print(f"[{timestamp}] pg_dump not found — install postgresql-client")


def _backup_sqlite(timestamp: str):
    """Copy the SQLite .db file."""
    db_file = os.path.join(os.path.dirname(__file__), "doctrack.db")
    if not os.path.exists(db_file):
        print(f"[{timestamp}] SQLite file not found: {db_file}")
        return
    out_file = os.path.join(BACKUP_DIR, f"doctrack_{timestamp}.db")
    shutil.copy2(db_file, out_file)
    size_kb = os.path.getsize(out_file) // 1024
    print(f"[{timestamp}] SQLite backup OK → {out_file} ({size_kb} KB)")


def _rotate_old_backups():
    """Keep only the last MAX_BACKUPS files."""
    files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith("doctrack_")],
        reverse=True
    )
    for old in files[MAX_BACKUPS:]:
        os.remove(os.path.join(BACKUP_DIR, old))
        print(f"Removed old backup: {old}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_backup()

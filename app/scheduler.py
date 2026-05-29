"""
scheduler.py
------------
Background task scheduler for DocTrack.
Uses APScheduler to run jobs automatically inside the Flask app —
no manual commands, no cron, no separate process needed.

Jobs registered here:
  1. daily_backup     — runs every day at 2:00 AM, backs up the database
  2. cleanup_old_logs — runs every Sunday, trims AuditLog older than 1 year

Call init_scheduler(app) inside create_app() after db.init_app(app).
"""

import os
import logging
import shutil
import subprocess
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("doctrack.scheduler")

_scheduler = None   # module-level singleton


def init_scheduler(app):
    """
    Start the APScheduler background scheduler and register all jobs.
    Safe to call multiple times — only starts once.
    Called from create_app() after all extensions are initialized.
    """
    global _scheduler
    if _scheduler is not None:
        return  # already started (e.g. reloader fork)

    # Don't run scheduler in the Werkzeug reloader child process
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" and app.debug:
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Manila")

    # ── Job 1: Daily database backup at 2:00 AM ────────────────────────────
    _scheduler.add_job(
        func=lambda: _run_backup(app),
        trigger=CronTrigger(hour=2, minute=0),
        id="daily_backup",
        name="Daily DB Backup",
        replace_existing=True,
        misfire_grace_time=3600,   # run even if missed by up to 1 hour
    )

    # ── Job 2: Weekly AuditLog cleanup every Sunday at 3:00 AM ────────────
    _scheduler.add_job(
        func=lambda: _cleanup_old_audit_logs(app),
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="cleanup_audit_logs",
        name="Weekly AuditLog Cleanup",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # ── Job 3: Hourly expired temp-admin revocation ──────────────────────
    _scheduler.add_job(
        func=lambda: _revoke_expired_temp_admins(app),
        trigger=CronTrigger(minute=0),   # every hour on the hour
        id="revoke_expired_temp_admins",
        name="Revoke Expired Temp Admins",
        replace_existing=True,
        misfire_grace_time=600,
    )

    _scheduler.start()
    logger.info("DocTrack scheduler started. Jobs: daily_backup, cleanup_audit_logs, revoke_expired_temp_admins")


def _run_backup(app):
    """
    Back up the database automatically.
    - PostgreSQL (Render): pg_dump → .sql file in /backups
    - SQLite (local dev):  copy .db file to /backups
    Keeps the last 10 backups; older ones are deleted automatically.
    """
    with app.app_context():
        backup_dir  = os.path.join(app.root_path, "..", "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_url      = app.config.get("SQLALCHEMY_DATABASE_URI", "")

        try:
            if "postgresql" in db_url or "postgres" in db_url:
                _backup_postgres(db_url, backup_dir, timestamp)
            else:
                _backup_sqlite(app, backup_dir, timestamp)
            _rotate_backups(backup_dir)
        except Exception as e:
            logger.error(f"Backup failed: {e}")


def _backup_postgres(db_url: str, backup_dir: str, timestamp: str):
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    out_file = os.path.join(backup_dir, f"doctrack_{timestamp}.sql")
    subprocess.run(
        ["pg_dump", db_url, "-f", out_file, "--no-password"],
        check=True, capture_output=True, text=True
    )
    size_kb = os.path.getsize(out_file) // 1024
    logger.info(f"PostgreSQL backup OK → {out_file} ({size_kb} KB)")


def _backup_sqlite(app, backup_dir: str, timestamp: str):
    db_file  = os.path.join(app.root_path, "..", "doctrack.db")
    if not os.path.exists(db_file):
        logger.warning(f"SQLite file not found: {db_file}")
        return
    out_file = os.path.join(backup_dir, f"doctrack_{timestamp}.db")
    shutil.copy2(db_file, out_file)
    size_kb  = os.path.getsize(out_file) // 1024
    logger.info(f"SQLite backup OK → {out_file} ({size_kb} KB)")


def _rotate_backups(backup_dir: str, keep: int = 10):
    """Delete oldest backups, keeping only the last `keep` files."""
    files = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith("doctrack_")],
        reverse=True
    )
    for old in files[keep:]:
        os.remove(os.path.join(backup_dir, old))
        logger.info(f"Removed old backup: {old}")


def _cleanup_old_audit_logs(app):
    """
    Delete AuditLog entries older than 1 year to prevent unbounded table growth.
    Keeps the most recent 365 days of audit history.
    """
    with app.app_context():
        from .models import AuditLog, db
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        try:
            deleted = AuditLog.query.filter(AuditLog.timestamp < cutoff).delete()
            db.session.commit()
            logger.info(f"AuditLog cleanup: removed {deleted} entries older than {cutoff.date()}")
        except Exception as e:
            db.session.rollback()
            logger.error(f"AuditLog cleanup failed: {e}")


def _revoke_expired_temp_admins(app):
    """
    Fix #5 — Auto-revoke temp-admin grants whose expiry timestamp has passed.
    Runs every hour. Logs each revocation so it appears in the audit trail.
    """
    with app.app_context():
        from .models import Account, db, log_audit, AuditAction
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        expired = Account.query.filter(
            Account.is_temp_admin == True,
            Account.temp_admin_expires_at != None,
            Account.temp_admin_expires_at <= now,
        ).all()
        for account in expired:
            account.revoke_temp_admin()
            logger.info(f"Temp admin revoked for account_id={account.account_id} ({account.username})")
        if expired:
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.error(f"Temp admin revocation commit failed: {e}")

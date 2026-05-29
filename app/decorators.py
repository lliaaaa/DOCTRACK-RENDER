from functools import wraps
from flask import redirect, url_for, flash, request
from flask_login import current_user


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))

            # ── Fix #5: auto-revoke expired temp-admin grants ─────────────
            if current_user.is_temp_admin and current_user.is_temp_admin_expired:
                from . import db
                current_user.revoke_temp_admin()
                db.session.commit()
                flash("Your temporary admin access has expired and has been revoked.", "warning")
                return redirect(url_for("main.dashboard"))

            # ── Fix #6: force password change before accessing any page ───
            if current_user.must_change_password:
                change_pw_url = url_for("auth.force_change_password")
                if request.endpoint != "auth.force_change_password":
                    flash("You must change your password before continuing.", "warning")
                    return redirect(change_pw_url)

            if current_user.role not in roles:
                flash("You are not authorized to access this page.", "danger")
                return redirect(url_for("main.dashboard"))
            return f(*args, **kwargs)
        return wrapped
    return decorator

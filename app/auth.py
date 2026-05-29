from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from .models import User, Account, log_audit, AuditAction
from . import db, limiter

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])   # brute-force protection
def login():
    """
    Authenticate a user by email and password.
    Rate-limited to 10 POST attempts per minute per IP.
    All login attempts (success and failure) are recorded in AuditLog.
    """
    if request.method == "POST":
        email    = request.form["email"].lower().strip()
        password = request.form["password"]

        user_profile = User.query.filter_by(email=email).first()
        if not user_profile or not user_profile.account:
            log_audit(AuditAction.LOGIN_FAILED,
                      details=f"No account found for email: {email}")
            flash("No account found with this email.", "danger")
            return redirect(url_for("auth.login"))

        account = user_profile.account

        if account.is_deactivated:
            log_audit(AuditAction.LOGIN_FAILED,
                      details=f"Login attempt on deactivated account: {email}")
            flash("This account has been deactivated. Contact admin.", "danger")
            return redirect(url_for("auth.login"))

        if not account.check_password(password):
            log_audit(AuditAction.LOGIN_FAILED,
                      details=f"Wrong password for: {email}")
            flash("Incorrect password.", "danger")
            return redirect(url_for("auth.login"))

        login_user(account)
        log_audit(AuditAction.LOGIN,
                  details=f"Logged in from {request.remote_addr}")
        return redirect(url_for("main.dashboard"))

    return render_template("login.html")


@bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    """Allow a logged-in user to change their own password."""
    current_password = request.form.get("current_password")
    new_password     = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    if not current_user.check_password(current_password):
        flash("Current password is incorrect.", "danger")
        return redirect(request.referrer)

    if new_password != confirm_password:
        flash("New passwords do not match.", "warning")
        return redirect(request.referrer)

    if len(new_password) < 6:
        flash("Password must be at least 6 characters long.", "warning")
        return redirect(request.referrer)

    current_user.set_password(new_password)
    db.session.commit()
    log_audit(AuditAction.UPDATE_SETTING, details="Password changed")
    flash("Password updated successfully.", "success")
    return redirect(request.referrer)


@bp.route("/logout")
@login_required
def logout():
    """Log out the current user and record the event."""
    log_audit(AuditAction.LOGOUT, details="User logged out")
    logout_user()
    flash("Logged out successfully.", "info")
    return redirect(url_for("main.home"))

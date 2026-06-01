import logging
import logging.handlers
import os
from flask import Flask, render_template, request
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache

from .models import (db, Account, User, Department, DocumentStatus, DocumentType,
                     Document, Transaction, generate_document_code, AuditLog)
from config import Config

migrate      = Migrate()
login_manager = LoginManager()
csrf         = CSRFProtect()
limiter      = Limiter(key_func=get_remote_address)
cache        = Cache()


def create_app():
    # Load .env for local development
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    app = Flask(__name__)
    app.config.from_object(Config)

    # Flask-Caching: SimpleCache is fast and needs no extra infra
    app.config.setdefault('CACHE_TYPE', 'SimpleCache')
    app.config.setdefault('CACHE_DEFAULT_TIMEOUT', 30)

    # ── Init extensions ──────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    cache.init_app(app)

    login_manager.login_view    = "auth.login"
    login_manager.login_message = "You must login first"
    login_manager.login_message_category = "warning"

    # ── Rotating file logger ─────────────────────────────────────────────
    _setup_logger(app)

    # ── Blueprints ───────────────────────────────────────────────────────
    from .auth   import bp as auth_bp
    from .routes import bp as main_bp
    from .routes_api import api_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    # ── App-level error handlers ─────────────────────────────────────────
    @app.errorhandler(403)
    def forbidden(e):
        app.logger.warning(f"403 | {request.url} | IP:{request.remote_addr}")
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()   # prevent broken transactions from locking the DB
        app.logger.error(
            f"500 | {request.url} | IP:{request.remote_addr} | {e}"
        )
        return render_template("500.html"), 500

    # Rate-limit error page
    @app.errorhandler(429)
    def too_many_requests(e):
        return render_template("429.html"), 429

    with app.app_context():
        db.create_all()
        # Stamp Alembic to 'head' after db.create_all() so that flask db upgrade
        # during build knows all tables are already created and skips safely.
        # This prevents "column already exists" / "table already exists" errors
        # on Render when build runs flask db upgrade AFTER the app has already
        # called db.create_all() on a previous deploy.
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                result = conn.execute(
                    text("SELECT COUNT(*) FROM alembic_version")
                ).scalar()
                if result == 0:
                    conn.execute(text(
                        "INSERT INTO alembic_version (version_num) VALUES ('001_initial')"
                    ))
                    conn.commit()
        except Exception:
            pass  # alembic_version table doesn't exist yet — flask db upgrade will handle it

        try:
            _seed_data()
        except Exception as e:
            app.logger.warning(f"Seed skipped on startup: {e}")

    # ── Background scheduler (backup + cleanup) ──────────────────────────
    # Starts automatically with the app — no manual commands needed.
    # Daily backup at 2 AM PH time, AuditLog cleanup every Sunday 3 AM.
    from .scheduler import init_scheduler
    init_scheduler(app)

    return app


# ── Logger setup ─────────────────────────────────────────────────────────────

def _setup_logger(app):
    """Attach a rotating file handler to the app logger."""
    log_dir = os.path.join(app.root_path, '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'doctrack.log')
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=5
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
    ))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


# ── Seed data ────────────────────────────────────────────────────────────────

def _seed_data():
    from datetime import datetime, timezone, timedelta

    # Each entry: (display_name, short_code)
    departments = [
        ("Association of Barangay Captains (ABC) Office", "ABC"),
        ("Accounting Office", "ACCTG"),
        ("Assessor's Office", "ASSESS"),
        ("Budget Office", "BUDGET"),
        ("Bureau of Fire Protection (BFP)", "BFP"),
        ("Commission on Elections (COMELEC) Office", "COMELEC"),
        ("Department of the Interior and Local Government (DILG) Office", "DILG"),
        ("Human Resource Management Office (HRMO)", "HRMO"),
        ("Library Office", "LIB"),
        ("Mayor's Office (MO)", "MO"),
        ("Municipal Agriculture Office (MAO)", "MAO"),
        ("Municipal Civil Registrar Office (MCRO)", "MCRO"),
        ("Municipal Disaster Risk Reduction and Management Office (MDRRMO)", "MDRRMO"),
        ("Municipal Engineering Office (MEO)", "MEO"),
        ("Municipal Environment and Natural Resources Office (MENRO)", "MENRO"),
        ("Municipal Health Office/Rural Health Unit (MHO/RHU)", "MHO"),
        ("Municipal Planning and Development Office (MPDO)", "MPDO"),
        ("Municipal Social Welfare and Development Office (MSWDO)", "MSWDO"),
        ("Office for Senior Citizens Affairs (OSCA)", "OSCA"),
        ("Philippine National Police (PNP)", "PNP"),
        ("Public Employment Service Office (PESO)", "PESO"),
        ("Sangguniang Bayan (SB) Office", "SBO"),
        ("Tourism Office", "TOUR"),
        ("Treasurer's Office", "TREAS"),
        ("Vice Mayor's Office", "VMO"),
        ("Pantawid Pamilyang Pilipino Program (4Ps) Office", "4PS"),
    ]

    for dept_name, dept_code in departments:
        if not Department.query.filter_by(department_name=dept_name).first():
            db.session.add(Department(department_name=dept_name, department_code=dept_code))
    db.session.flush()

    DEFAULT_PASSWORD = "PASSWORD123"

    for dept_name, dept_code in departments:
        dept = Department.query.filter_by(department_name=dept_name).first()
        if not dept:
            continue
        slug = dept_code.lower().replace("/", "").replace("(", "").replace(")", "")

        # Admin account
        admin_email = f"{slug}.admin@lgu-unisan.com"
        if not User.query.filter_by(email=admin_email).first():
            user = User(first_name=dept_name, last_name="Admin",
                        email=admin_email, department_id=dept.department_id)
            db.session.add(user)
            db.session.flush()
            account = Account(user_id=user.user_id, username=admin_email,
                              role="admin", status="active",
                              must_change_password=True)
            account.set_password(DEFAULT_PASSWORD)
            db.session.add(account)

        # Staff account
        staff_email = f"{slug}.staff@lgu-unisan.com"
        if not User.query.filter_by(email=staff_email).first():
            user = User(first_name=dept_name, last_name="Staff",
                        email=staff_email, department_id=dept.department_id)
            db.session.add(user)
            db.session.flush()
            account = Account(user_id=user.user_id, username=staff_email,
                              role="staff", status="active",
                              must_change_password=True)
            account.set_password(DEFAULT_PASSWORD)
            db.session.add(account)

    db.session.flush()

    for name in ["SVP", "Bidding"]:
        if not DocumentType.query.filter_by(type_name=name).first():
            db.session.add(DocumentType(type_name=name))

    workflow_statuses = [
        "Request for PR", "Request for PO",
        "For Signature BAC Members - BAC Office",
        "For Signature of Mayor", "Request for OBR",
        "For Accounting Staff Validation", "For Processing",
        "With Checked", "Closed",
    ]
    for name in workflow_statuses:
        if not DocumentStatus.query.filter_by(name=name).first():
            db.session.add(DocumentStatus(name=name))

    db.session.commit()

    if Document.query.count() < 5:
        _seed_sample_documents()


def _seed_sample_documents():
    from datetime import datetime, timezone, timedelta

    svp_dt = DocumentType.query.filter_by(type_name="SVP").first()
    bid_dt = DocumentType.query.filter_by(type_name="Bidding").first()
    if not svp_dt or not bid_dt:
        return

    STATUS_TO_DEPT = {
        "Pending Release": "Accounting Office",
        "Request for PR": "Budget Office",
        "Request for PO": "Budget Office",
        "For Signature BAC Members - BAC Office": "BAC Office",
        "For Signature of Mayor": "Office of the Mayor",
        "Request for OBR": "Budget Office",
        "For Accounting Staff Validation": "Accounting Office",
        "For Processing": "Accounting Office",
        "With Checked": "Accounting Office",
        "Closed": "Accounting Office",
        "Assigned": "Accounting Office",
    }

    def get_dept(name):
        return Department.query.filter_by(department_name=name).first()

    now = datetime.now(timezone.utc)
    admin_user = User.query.first()
    implementing = "Accounting Office"

    svp_samples = [
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF OLD PTV AMBULANCE FOR THE MONTH OF MARCH 2026",
         "Reimbursement of Diesel", 3500.00, "Closed"),
        ("EVENTS AND SEMINARS - MUNICIPAL SPORTS FEST 2025",
         "Events and Seminars", 15000.00, "Closed"),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES FOR FIESTA CELEBRATION APRIL 2026",
         "Reimbursement of Tarpaulin", 1200.00, "With Checked"),
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF BACKHOE FOR THE PERIOD OF MARCH 10, 12, 2026",
         "Reimbursement of Diesel", 4800.00, "For Processing"),
        ("EVENTS AND SEMINARS - LGU ORIENTATION WORKSHOP JANUARY 2026",
         "Events and Seminars", 22000.00, "For Signature of Mayor"),
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF DUMP TRUCK FOR THE MONTH OF FEBRUARY 2026",
         "Reimbursement of Diesel", 2700.00, "Request for OBR"),
        ("EVENTS AND SEMINARS - YEAR-END ASSESSMENT DECEMBER 2025",
         "Events and Seminars", 18500.00, "Request for PO"),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES FOR ENVIRONMENT DAY MAY 2026",
         "Reimbursement of Tarpaulin", 980.00, "Request for PR"),
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF PATROL VEHICLE FOR THE MONTH OF APRIL 2026",
         "Reimbursement of Diesel", 3200.00, "Closed"),
        ("EVENTS AND SEMINARS - DISASTER RISK TRAINING Q1 2026",
         "Events and Seminars", 30000.00, "For Accounting Staff Validation"),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES FOR ELECTION AWARENESS DRIVE MARCH 2026",
         "Reimbursement of Tarpaulin", 750.00, "Closed"),
        ("EVENTS AND SEMINARS - MUNICIPAL BUDGET FORUM FEBRUARY 2026",
         "Events and Seminars", 12500.00, "For Signature BAC Members - BAC Office"),
    ]

    for i, (title, subcat, amount, status) in enumerate(svp_samples):
        code = f"DOCSEED{i+1:05d}"
        if Document.query.filter_by(document_code=code).first():
            continue
        days_ago = 30 - (i * 2)
        doc_date = now - timedelta(days=days_ago)
        dept_name = STATUS_TO_DEPT.get(status, implementing)
        dept = get_dept(dept_name) or get_dept(implementing)
        doc = Document(
            document_code=code, title=title,
            document_type_id=svp_dt.document_type_id,
            sub_category=subcat,
            created_by=admin_user.user_id if admin_user else None,
            datetime=doc_date, status=status,
            priority=["Normal", "Urgent", "Normal", "Routine"][i % 4],
            current_department_id=dept.department_id,
            implementing_office=implementing,
            amount=amount, arrived_at=doc_date, updated_at=doc_date,
            received_by="",
        )
        db.session.add(doc)
        db.session.flush()
        db.session.add(Transaction(
            document_id=doc.document_id, transaction_type="create",
            origin=implementing, destination=dept_name,
            action_by_name="System Seed", status=status, datetime=doc_date,
        ))

    bid_samples = [
        ("SUPPLY OF OFFICE SUPPLIES Q1 2026", 26000.00, "Closed", "Accounting Office"),
        ("PROCUREMENT OF ROAD REPAIR MATERIALS PHASE 1", 185000.00,
         "For Signature BAC Members - BAC Office", "Engineering"),
        ("IT EQUIPMENT FOR MUNICIPAL OFFICES 2026", 98000.00, "Closed", "BAC Office"),
        ("CONSTRUCTION OF MULTI-PURPOSE HALL PHASE 1", 500000.00, "Request for PO", "Engineering"),
        ("SUPPLY OF MEDICAL SUPPLIES MHO 2026", 45000.00, "For Signature of Mayor", "Municipal Health Office"),
        ("LANDSCAPING AND MAINTENANCE TOWN PLAZA 2026", 32000.00, "Closed", "Accounting Office"),
    ]

    for i, (title, amount, status, impl_office) in enumerate(bid_samples):
        code = f"DOCSEED{len(svp_samples)+i+1:05d}"
        if Document.query.filter_by(document_code=code).first():
            continue
        days_ago = 60 - (i * 5)
        doc_date = now - timedelta(days=days_ago)
        dept_name = STATUS_TO_DEPT.get(status, impl_office)
        dept = get_dept(dept_name) or get_dept(impl_office)
        doc = Document(
            document_code=code, title=title,
            document_type_id=bid_dt.document_type_id,
            created_by=admin_user.user_id if admin_user else None,
            datetime=doc_date, status=status,
            priority=["Normal", "Urgent"][i % 2],
            current_department_id=dept.department_id,
            implementing_office=impl_office,
            amount=amount, arrived_at=doc_date, updated_at=doc_date,
            received_by="",
        )
        db.session.add(doc)
        db.session.flush()
        db.session.add(Transaction(
            document_id=doc.document_id, transaction_type="create",
            origin=impl_office, destination=dept_name,
            action_by_name="System Seed", status=status, datetime=doc_date,
        ))

    db.session.commit()


@login_manager.user_loader
def load_user(user_id):
    return Account.query.get(int(user_id))

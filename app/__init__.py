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

    # 26 LGU Unisan offices: (full display name, short username prefix)
    OFFICES = [
        ("Association of Barangay Captains (ABC) Office",                         "abc"),
        ("Accounting Office",                                                      "acctg"),
        ("Assessor's Office",                                                     "assess"),
        ("Budget Office",                                                          "budget"),
        ("Bureau of Fire Protection (BFP)",                                        "bfp"),
        ("Commission on Elections (COMELEC) Office",                               "comelec"),
        ("Department of the Interior and Local Government (DILG) Office",          "dilg"),
        ("Human Resource Management Office (HRMO)",                                "hrmo"),
        ("Library Office",                                                         "lib"),
        ("Mayor's Office (MO)",                                                   "mo"),
        ("Municipal Agriculture Office (MAO)",                                     "mao"),
        ("Municipal Civil Registrar Office (MCRO)",                                "mcro"),
        ("Municipal Disaster Risk Reduction and Management Office (MDRRMO)",       "mdrrmo"),
        ("Municipal Engineering Office (MEO)",                                     "meo"),
        ("Municipal Environment and Natural Resources Office (MENRO)",             "menro"),
        ("Municipal Health Office/Rural Health Unit (MHO/RHU)",                   "mho"),
        ("Municipal Planning and Development Office (MPDO)",                       "mpdo"),
        ("Municipal Social Welfare and Development Office (MSWDO)",                "mswdo"),
        ("Office for Senior Citizens Affairs (OSCA)",                              "osca"),
        ("Philippine National Police (PNP)",                                       "pnp"),
        ("Public Employment Service Office (PESO)",                                "peso"),
        ("Sangguniang Bayan (SB) Office",                                          "sbo"),
        ("Tourism Office",                                                         "tour"),
        ("Treasurer's Office",                                                    "treas"),
        ("Vice Mayor's Office",                                                   "vmo"),
        ("Pantawid Pamilyang Pilipino Program (4Ps) Office",                       "4ps"),
        ("Bids and Awards Committee (BAC) Office",                                 "bac"),
    ]

    DEFAULT_PASSWORD = "PASSWORD123"

    # Seed departments
    for dept_name, _ in OFFICES:
        if not Department.query.filter_by(department_name=dept_name).first():
            code = dept_name.split("(")[1].rstrip(")") if "(" in dept_name else dept_name[:6].upper()
            db.session.add(Department(department_name=dept_name, department_code=code))
    db.session.flush()

    # Seed one admin + one staff per office
    for dept_name, prefix in OFFICES:
        dept = Department.query.filter_by(department_name=dept_name).first()
        if not dept:
            continue

        for role, suffix, label in [("admin", "admin", "Admin"), ("staff", "staff", "Staff")]:
            username = f"{prefix}.{suffix}"
            email    = f"{prefix}.{suffix}@lgu-unisan.com"

            if not User.query.filter_by(email=email).first():
                user = User(
                    first_name=dept_name,
                    last_name=label,
                    email=email,
                    department_id=dept.department_id,
                )
                db.session.add(user)
                db.session.flush()
                account = Account(
                    user_id=user.user_id,
                    username=username,
                    role=role,
                    status="active",
                    must_change_password=True,
                )
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
        _seed_demo_documents()


def _seed_demo_documents():
    """Seed realistic demo documents with full transaction trails for system demo."""
    from datetime import datetime, timezone, timedelta

    svp_dt = DocumentType.query.filter_by(type_name="SVP").first()
    bid_dt = DocumentType.query.filter_by(type_name="Bidding").first()
    if not svp_dt or not bid_dt:
        return

    def get_dept(name):
        return Department.query.filter(Department.department_name.ilike(f"%{name}%")).first()

    def now_minus(days=0):
        return datetime.now(timezone.utc) - timedelta(days=days)

    WORKFLOW = [
        "Request for PR", "Request for PO",
        "For Signature BAC Members - BAC Office",
        "For Signature of Mayor", "Request for OBR",
        "For Accounting Staff Validation", "For Processing",
        "With Checked", "Closed",
    ]

    STATUS_DEPT = {
        "Request for PR":                         "Budget Office",
        "Request for PO":                         "Budget Office",
        "For Signature BAC Members - BAC Office": "Bids and Awards Committee (BAC) Office",
        "For Signature of Mayor":                 "Mayor's Office (MO)",
        "Request for OBR":                        "Budget Office",
        "For Accounting Staff Validation":        "Accounting Office",
        "For Processing":                         "Accounting Office",
        "With Checked":                           "Accounting Office",
        "Closed":                                 "Accounting Office",
    }

    STEP_REMARKS = {
        "Request for PR": "PR prepared and forwarded to Budget Office.",
        "Request for PO": "PO prepared. Awaiting BAC Members signature.",
        "For Signature BAC Members - BAC Office": "Reviewed and signed by BAC Members. Forwarded to Mayor.",
        "For Signature of Mayor": "Approved and signed by the Municipal Mayor.",
        "Request for OBR": "OBR prepared and submitted to Accounting.",
        "For Accounting Staff Validation": "Validated by Accounting Staff. No discrepancies found.",
        "For Processing": "Currently being processed by Accounting Office.",
        "With Checked": "Checked and verified. Awaiting final release.",
        "Closed": "Document fully processed and released.",
    }

    ACTORS = [
        "Maria Santos", "Juan dela Cruz", "Ana Reyes",
        "Roberto Gomez", "Liza Bautista", "Carlos Mendoza",
        "Gloria Fernandez", "Eduardo Villanueva",
    ]

    acctg_dept = get_dept("Accounting")
    budget_dept = get_dept("Budget")
    bac_dept    = get_dept("Bids and Awards")
    mayor_dept  = get_dept("Mayor's Office")

    admin_user = User.query.first()
    creator_id = admin_user.user_id if admin_user else None

    def make_code(n, days_ago):
        fake_dt = datetime.now() - timedelta(days=days_ago)
        ts = fake_dt.strftime("%m%d%Y%H%M%S")
        return f"DOC{ts}{n:03d}"

    def build_trail(doc, status, impl_name, base_dt):
        target_idx = WORKFLOW.index(status) if status in WORKFLOW else 0
        for i in range(target_idx + 1):
            step = WORKFLOW[i]
            prev = WORKFLOW[i - 1] if i > 0 else None
            origin = STATUS_DEPT.get(prev, impl_name) if prev else impl_name
            destination = STATUS_DEPT.get(step, impl_name)
            step_dt = base_dt + timedelta(days=i * 2, hours=i)
            txn_type = "create" if i == 0 else "release"
            actor = ACTORS[i % len(ACTORS)]
            db.session.add(Transaction(
                document_id=doc.document_id,
                transaction_type=txn_type,
                origin=origin,
                destination=destination,
                action_by_name=actor,
                status=step,
                datetime=step_dt,
                remarks=STEP_REMARKS.get(step) if i > 0 else "Document submitted for processing.",
            ))

    def add_doc(code, title, doc_type_id, subcat, amount, status, priority, impl_name, impl_dept, days_ago):
        if Document.query.filter_by(document_code=code).first():
            return
        base_dt = now_minus(days=days_ago)
        step_idx = WORKFLOW.index(status) if status in WORKFLOW else 0
        curr_dept_name = STATUS_DEPT.get(status, impl_name)
        curr_dept = get_dept(curr_dept_name) or impl_dept or acctg_dept
        doc = Document(
            document_code=code, title=title,
            document_type_id=doc_type_id,
            sub_category=subcat,
            created_by=creator_id,
            datetime=base_dt, status=status, priority=priority,
            current_department_id=curr_dept.department_id if curr_dept else None,
            implementing_office=impl_name,
            amount=amount,
            arrived_at=base_dt,
            updated_at=base_dt + timedelta(days=step_idx * 2),
            received_by="",
        )
        db.session.add(doc)
        db.session.flush()
        build_trail(doc, status, impl_name, base_dt)

    # ── SVP Documents (13 docs, various stages) ──────────────────────────────
    SVP = [
        ("REIMBURSEMENT OF DIESEL EXPENSES — DUMP TRUCK OFFICIAL USE MARCH 2026",
         "Reimbursement of Diesel", 4800.00, "Closed", "Normal",
         "Municipal Engineering Office (MEO)", "Engineering", 55),
        ("REIMBURSEMENT OF DIESEL EXPENSES — PATROL VEHICLE FEBRUARY 2026",
         "Reimbursement of Diesel", 3200.00, "Closed", "Normal",
         "Mayor's Office (MO)", "Mayor", 48),
        ("EVENTS AND SEMINARS — LGU YEAR-END ASSESSMENT DECEMBER 2025",
         "Events and Seminars", 22000.00, "Closed", "Normal",
         "Human Resource Management Office (HRMO)", "Human Resource", 70),
        ("EVENTS AND SEMINARS — MUNICIPAL BUDGET PLANNING FORUM JANUARY 2026",
         "Events and Seminars", 18500.00, "Closed", "Normal",
         "Budget Office", "Budget", 60),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES — PALARONG UNISAN SPORTS FEST APRIL 2026",
         "Reimbursement of Tarpaulin", 1850.00, "Closed", "Normal",
         "Mayor's Office (MO)", "Mayor", 40),
        ("REIMBURSEMENT OF DIESEL EXPENSES — BACKHOE OFFICIAL USE MARCH 10–14, 2026",
         "Reimbursement of Diesel", 6500.00, "With Checked", "Urgent",
         "Municipal Engineering Office (MEO)", "Engineering", 18),
        ("EVENTS AND SEMINARS — DISASTER RISK REDUCTION TRAINING Q1 2026",
         "Events and Seminars", 30000.00, "For Processing", "Normal",
         "Municipal Disaster Risk Reduction and Management Office (MDRRMO)", "Disaster", 14),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES — ENVIRONMENT AWARENESS MONTH MAY 2026",
         "Reimbursement of Tarpaulin", 980.00, "For Accounting Staff Validation", "Normal",
         "Municipal Environment and Natural Resources Office (MENRO)", "Accounting", 10),
        ("EVENTS AND SEMINARS — SOCIAL WELFARE OUTREACH PROGRAM MARCH 2026",
         "Events and Seminars", 15000.00, "For Signature of Mayor", "Urgent",
         "Municipal Social Welfare and Development Office (MSWDO)", "Mayor", 7),
        ("REIMBURSEMENT OF DIESEL EXPENSES — AMBULANCE OFFICIAL USE APRIL 2026",
         "Reimbursement of Diesel", 3500.00, "Request for OBR", "Normal",
         "Mayor's Office (MO)", "Budget", 5),
        ("EVENTS AND SEMINARS — HRMO CAPABILITY BUILDING SEMINAR MAY 2026",
         "Events and Seminars", 28000.00, "For Signature BAC Members - BAC Office", "Normal",
         "Human Resource Management Office (HRMO)", "Bids and Awards", 3),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES — FIESTA CELEBRATION UNISAN 2026",
         "Reimbursement of Tarpaulin", 2200.00, "Request for PO", "Routine",
         "Mayor's Office (MO)", "Budget", 2),
        ("REIMBURSEMENT OF DIESEL EXPENSES — DUMP TRUCK MAY 2026",
         "Reimbursement of Diesel", 5100.00, "Request for PR", "Normal",
         "Municipal Engineering Office (MEO)", "Budget", 1),
    ]

    for i, (title, subcat, amount, status, priority, impl_name, dept_hint, days_ago) in enumerate(SVP):
        add_doc(make_code(i+1, days_ago), title, svp_dt.document_type_id, subcat,
                amount, status, priority, impl_name, get_dept(dept_hint), days_ago)

    # ── Bidding Documents (10 docs, various stages) ───────────────────────────
    BID = [
        ("PROCUREMENT OF OFFICE SUPPLIES AND MATERIALS FOR ALL DEPARTMENTS Q1 2026",
         55000.00, "Closed", "Normal",
         "Bids and Awards Committee (BAC) Office", "Bids and Awards", 80),
        ("SUPPLY AND DELIVERY OF IT EQUIPMENT — MUNICIPAL OFFICES 2026",
         98000.00, "Closed", "Normal",
         "Bids and Awards Committee (BAC) Office", "Bids and Awards", 65),
        ("LANDSCAPING AND MAINTENANCE OF UNISAN MUNICIPAL PLAZA 2026",
         32000.00, "Closed", "Normal",
         "Accounting Office", "Accounting", 50),
        ("PROCUREMENT OF MEDICAL AND DENTAL SUPPLIES — MHO/RHU 2026",
         45000.00, "With Checked", "Urgent",
         "Municipal Health Office/Rural Health Unit (MHO/RHU)", "Accounting", 22),
        ("CONSTRUCTION OF MULTI-PURPOSE COVERED COURT — BARANGAY VILLA REYES",
         500000.00, "For Signature of Mayor", "Urgent",
         "Municipal Engineering Office (MEO)", "Mayor", 12),
        ("REPAIR AND REHABILITATION OF FARM-TO-MARKET ROAD — BRGY. SAN ISIDRO PHASE 1",
         185000.00, "For Signature BAC Members - BAC Office", "Normal",
         "Municipal Engineering Office (MEO)", "Bids and Awards", 8),
        ("SUPPLY OF AGRICULTURAL INPUTS AND SEEDLINGS — MAO PROGRAM 2026",
         38000.00, "For Processing", "Normal",
         "Municipal Agriculture Office (MAO)", "Accounting", 6),
        ("PROCUREMENT OF DISASTER RESPONSE EQUIPMENT — MDRRMO 2026",
         120000.00, "Request for OBR", "Urgent",
         "Municipal Disaster Risk Reduction and Management Office (MDRRMO)", "Budget", 4),
        ("SUPPLY OF JANITORIAL AND SANITATION SUPPLIES — ALL OFFICES Q2 2026",
         18500.00, "Request for PO", "Routine",
         "Accounting Office", "Budget", 2),
        ("PROCUREMENT OF COMMUNICATION EQUIPMENT — MAYOR'S OFFICE 2026",
         75000.00, "Request for PR", "Normal",
         "Mayor's Office (MO)", "Budget", 1),
    ]

    for i, (title, amount, status, priority, impl_name, dept_hint, days_ago) in enumerate(BID):
        add_doc(make_code(i+50, days_ago), title, bid_dt.document_type_id, None,
                amount, status, priority, impl_name, get_dept(dept_hint), days_ago)

    db.session.commit()


@login_manager.user_loader
def load_user(user_id):
    return Account.query.get(int(user_id))

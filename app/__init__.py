import os
from flask import Flask
from flask_migrate import Migrate
from flask_login import LoginManager

from .models import db, Account, User, Department, DocumentStatus, DocumentType, Document, Transaction, generate_document_code
from config import Config

migrate = Migrate()
login_manager = LoginManager()


def create_app():
    # Load .env for local development (python-dotenv, safe to call even if file absent)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "You must login first"
    login_manager.login_message_category = "warning"

    from .auth import bp as auth_bp
    from .routes import bp as main_bp
    from .routes_api import api_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    with app.app_context():
        # db.create_all() is NOT called here — use `flask db upgrade` via migrations.
        # We only seed reference data (departments, users, doc types, statuses).
        _seed_data()

    return app


def _seed_data():
    """Seed departments, admin accounts, document types, and statuses."""
    from datetime import datetime, timezone, timedelta
    import random

    department_names = [
        "ABC Office",
        "Accounting Office",
        "Agriculture Office",
        "Assessors Office",
        "BAC Office",
        "Budget Office",
        "COMELEC Office",
        "Engineering",
        "Human Resources Office",
        "Library Office",
        "Office of the Mayor",
        "MENRO Office",
        "MDRRMO Office",
        "MPDC Office",
        "Municipal Health Office",
        "Treasurer Office",
        "Vice Mayor Office",
    ]

    # --- Departments ---
    for name in department_names:
        if not Department.query.filter_by(department_name=name).first():
            code = "".join(w[0] for w in name.split())[:6].upper()
            db.session.add(Department(department_name=name, department_code=code))
    db.session.flush()

    # --- Admin users (one per department) ---
    for dept_name in department_names:
        dept = Department.query.filter_by(department_name=dept_name).first()
        if not dept:
            continue
        email = f"{dept_name.lower().replace(' ', '').replace('/', '')}@site.com"
        if not User.query.filter_by(email=email).first():
            user = User(
                first_name=dept_name,
                last_name="Admin",
                email=email,
                department_id=dept.department_id,
            )
            db.session.add(user)
            db.session.flush()
            account = Account(
                user_id=user.user_id,
                username=email,
                role="admin",
                status="active",
            )
            account.set_password("123")
            db.session.add(account)

    db.session.flush()

    # --- Document types: only SVP and Bidding ---
    for name in ["SVP", "Bidding"]:
        if not DocumentType.query.filter_by(type_name=name).first():
            db.session.add(DocumentType(type_name=name))

    # --- Document statuses (in workflow order) ---
    workflow_statuses = [
        "Request for PR",
        "Request for PO",
        "For Signature BAC Members - BAC Office",
        "For Signature of Mayor",
        "Request for OBR",
        "For Accounting Staff Validation",
        "For Processing",
        "With Checked",
        "Closed",
    ]
    for name in workflow_statuses:
        if not DocumentStatus.query.filter_by(name=name).first():
            db.session.add(DocumentStatus(name=name))

    db.session.commit()

    # --- Sample documents for reports (seed only if DB is fresh) ---
    if Document.query.count() < 5:
        _seed_sample_documents()


def _seed_sample_documents():
    """Plant sample documents so reports show meaningful visuals.

    Each document is placed in the CORRECT department based on its status,
    matching the SVP_WORKFLOW routing table. received_by is always empty
    (open/unassigned in that office). Documents only appear in Open Documents
    of the office that currently owns them at that workflow stage.
    """
    from datetime import datetime, timezone, timedelta

    svp_dt = DocumentType.query.filter_by(type_name="SVP").first()
    bid_dt = DocumentType.query.filter_by(type_name="Bidding").first()
    if not svp_dt or not bid_dt:
        return

    # Map SVP workflow status → correct department
    STATUS_TO_DEPT = {
        "Pending Release":                          "Accounting Office",   # freshly created, still with creator
        "Request for PR":                           "Budget Office",
        "Request for PO":                           "Budget Office",
        "For Signature BAC Members - BAC Office":   "BAC Office",
        "For Signature of Mayor":                   "Office of the Mayor",
        "Request for OBR":                          "Budget Office",
        "For Accounting Staff Validation":          "Accounting Office",
        "For Processing":                           "Accounting Office",
        "With Checked":                             "Accounting Office",
        "Closed":                                   "Accounting Office",
        # Bidding (non-SVP) — stays in implementing office
        "Assigned":                                 "Accounting Office",
    }

    def get_dept(name):
        return Department.query.filter_by(department_name=name).first()

    now = datetime.now(timezone.utc)
    admin_user = User.query.first()
    implementing = "Accounting Office"

    # --- SVP sample documents ---
    # (title, subcat, amount, status)
    svp_samples = [
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF OLD PTV AMBULANCE FOR THE MONTH OF MARCH 2026",
            "Reimbursement of Diesel",   3500.00, "Closed"),
        ("EVENTS AND SEMINARS - MUNICIPAL SPORTS FEST 2025",
            "Events and Seminars",      15000.00, "Closed"),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES FOR FIESTA CELEBRATION APRIL 2026",
            "Reimbursement of Tarpaulin", 1200.00, "With Checked"),
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF BACKHOE FOR THE PERIOD OF MARCH 10, 12, 2026",
            "Reimbursement of Diesel",   4800.00, "For Processing"),
        ("EVENTS AND SEMINARS - LGU ORIENTATION WORKSHOP JANUARY 2026",
            "Events and Seminars",      22000.00, "For Signature of Mayor"),
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF DUMP TRUCK FOR THE MONTH OF FEBRUARY 2026",
            "Reimbursement of Diesel",   2700.00, "Request for OBR"),
        ("EVENTS AND SEMINARS - YEAR-END ASSESSMENT DECEMBER 2025",
            "Events and Seminars",      18500.00, "Request for PO"),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES FOR ENVIRONMENT DAY MAY 2026",
            "Reimbursement of Tarpaulin",  980.00, "Request for PR"),
        ("REIMBURSEMENT OF DIESEL EXPENSES FOR OFFICIAL USE OF PATROL VEHICLE FOR THE MONTH OF APRIL 2026",
            "Reimbursement of Diesel",   3200.00, "Closed"),
        ("EVENTS AND SEMINARS - DISASTER RISK TRAINING Q1 2026",
            "Events and Seminars",      30000.00, "For Accounting Staff Validation"),
        ("REIMBURSEMENT OF TARPAULIN EXPENSES FOR ELECTION AWARENESS DRIVE MARCH 2026",
            "Reimbursement of Tarpaulin",  750.00, "Closed"),
        ("EVENTS AND SEMINARS - MUNICIPAL BUDGET FORUM FEBRUARY 2026",
            "Events and Seminars",      12500.00, "For Signature BAC Members - BAC Office"),
    ]

    for i, (title, subcat, amount, status) in enumerate(svp_samples):
        code = f"DOCSEED{i+1:05d}"
        if Document.query.filter_by(document_code=code).first():
            continue
        days_ago = 30 - (i * 2)
        doc_date = now - timedelta(days=days_ago)
        # Place document in the correct office for its status
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
            received_by="",   # unassigned — open in that office
        )
        db.session.add(doc)
        db.session.flush()
        # Create transaction
        db.session.add(Transaction(
            document_id=doc.document_id, transaction_type="create",
            origin=implementing, destination=dept_name,
            action_by_name="System Seed", status=status,
            datetime=doc_date,
        ))

    # --- Bidding sample documents ---
    bid_samples = [
        ("SUPPLY OF OFFICE SUPPLIES Q1 2026",                           26000.00,  "Closed",        "Accounting Office"),
        ("PROCUREMENT OF ROAD REPAIR MATERIALS PHASE 1",               185000.00,  "For Signature BAC Members - BAC Office", "Engineering"),
        ("IT EQUIPMENT FOR MUNICIPAL OFFICES 2026",                     98000.00,  "Closed",        "BAC Office"),
        ("CONSTRUCTION OF MULTI-PURPOSE HALL PHASE 1",                 500000.00,  "Request for PO", "Engineering"),
        ("SUPPLY OF MEDICAL SUPPLIES MHO 2026",                         45000.00,  "For Signature of Mayor", "Municipal Health Office"),
        ("LANDSCAPING AND MAINTENANCE TOWN PLAZA 2026",                 32000.00,  "Closed",        "Accounting Office"),
    ]

    for i, (title, amount, status, impl_office) in enumerate(bid_samples):
        code = f"DOCSEED{len(svp_samples)+i+1:05d}"
        if Document.query.filter_by(document_code=code).first():
            continue
        days_ago = 60 - (i * 5)
        doc_date = now - timedelta(days=days_ago)
        # For Bidding: doc stays in implementing office unless it has a specific routed status
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
            action_by_name="System Seed", status=status,
            datetime=doc_date,
        ))

    db.session.commit()


@login_manager.user_loader
def load_user(user_id):
    return Account.query.get(int(user_id))

from datetime import datetime, timezone
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

TRANSACTION_CATEGORIES = ["Simple", "Complex", "Highly Technical"]
# Default SLA in minutes (Citizen's Charter RA 11032 standards)
DEFAULT_SLA = {
    "Simple":           4320,    # 3 working days
    "Complex":          14400,   # 7 working days
    "Highly Technical": 43200,   # 20 working days
}


class Department(db.Model):
    __tablename__ = "departments"
    department_id   = db.Column(db.Integer, primary_key=True)
    department_name = db.Column(db.String(100), nullable=False)
    department_code = db.Column(db.String(50),  nullable=True)
    users       = db.relationship('User', back_populates='department_rel', lazy='dynamic')
    assignments = db.relationship('DepartmentAssignment', back_populates='department')
    @property
    def id(self):   return self.department_id
    @property
    def name(self): return self.department_name
    def __str__(self): return self.department_name


class User(db.Model):
    __tablename__ = "users"
    user_id       = db.Column(db.Integer, primary_key=True)
    first_name    = db.Column(db.String(75),  nullable=False)
    last_name     = db.Column(db.String(75),  nullable=False)
    email         = db.Column(db.String(255), unique=True, nullable=False)
    phone         = db.Column(db.String(20),  nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.department_id'), nullable=True)
    department_rel       = db.relationship('Department', back_populates='users')
    account              = db.relationship('Account', back_populates='user', uselist=False)
    assignments          = db.relationship('DepartmentAssignment', back_populates='user')
    handled_transactions = db.relationship('Transaction', back_populates='handler', foreign_keys='Transaction.handled_by')
    @property
    def id(self):        return self.user_id
    @property
    def full_name(self): return f"{self.first_name} {self.last_name}".strip()
    @property
    def department(self): return self.department_rel.department_name if self.department_rel else None
    def __str__(self): return self.full_name


class Account(db.Model, UserMixin):
    __tablename__ = "accounts"
    account_id    = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False, unique=True)
    username      = db.Column(db.String(255), unique=True, nullable=False)
    password      = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(50),  default='user', nullable=False)
    status        = db.Column(db.String(20),  default='active', nullable=False)
    is_temp_admin = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', back_populates='account')
    def get_id(self):               return str(self.account_id)
    def set_password(self, pw):     self.password = generate_password_hash(pw)
    def check_password(self, pw):   return check_password_hash(self.password, pw)
    @property
    def id(self):          return self.account_id
    @property
    def full_name(self):   return self.user.full_name if self.user else ''
    @property
    def email(self):       return self.user.email if self.user else ''
    @property
    def department(self):  return self.user.department if self.user else None
    @property
    def department_id(self): return self.user.department_id if self.user else None
    @property
    def is_deactivated(self): return self.status == 'inactive'
    @is_deactivated.setter
    def is_deactivated(self, v): self.status = 'inactive' if v else 'active'


class DepartmentAssignment(db.Model):
    __tablename__ = "department_assignments"
    assignment_id = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.user_id'),             nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.department_id'), nullable=False)
    assigned_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user       = db.relationship('User',       back_populates='assignments')
    department = db.relationship('Department', back_populates='assignments')
    @property
    def id(self): return self.assignment_id


class DocumentType(db.Model):
    __tablename__ = 'document_type'
    document_type_id     = db.Column(db.Integer, primary_key=True)
    type_name            = db.Column(db.String(150), unique=True, nullable=False)
    description          = db.Column(db.Text, nullable=True)
    transaction_category = db.Column(db.String(30), default='Simple', nullable=False)
    sla_minutes          = db.Column(db.Integer, default=4320, nullable=False)
    documents       = db.relationship('Document', back_populates='doc_type_rel')
    charter_configs = db.relationship('CitizenCharterConfig', back_populates='doc_type',
                                      cascade='all, delete-orphan')
    @property
    def id(self):   return self.document_type_id
    @property
    def name(self): return self.type_name


class CitizenCharterConfig(db.Model):
    """Per-document-type (optionally per-department) SLA configuration."""
    __tablename__ = 'citizen_charter_config'
    config_id          = db.Column(db.Integer, primary_key=True)
    doc_type_id        = db.Column(db.Integer, db.ForeignKey('document_type.document_type_id'), nullable=False)
    department_id      = db.Column(db.Integer, db.ForeignKey('departments.department_id'), nullable=True)
    category           = db.Column(db.String(30), default='Simple', nullable=False)
    sla_minutes        = db.Column(db.Integer, default=4320, nullable=False)
    responsible_person = db.Column(db.String(150), nullable=True)
    doc_type   = db.relationship('DocumentType', back_populates='charter_configs')
    department = db.relationship('Department')
    @property
    def id(self): return self.config_id


class DocumentStatus(db.Model):
    __tablename__ = 'document_status'
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)


def generate_document_code():
    """DOC{MM}{DD}{YYYY}{HH}{mm}{ss}{NNN} — seq 001-999 wraps."""
    now = datetime.now()
    ts  = now.strftime("%m%d%Y%H%M%S")
    last_doc = Document.query.order_by(Document.document_id.desc()).first()
    seq = 1
    if last_doc and last_doc.document_code and len(last_doc.document_code) >= 3:
        try:
            last_seq = int(last_doc.document_code[-3:])
            seq = 1 if last_seq >= 999 else last_seq + 1
        except (ValueError, IndexError):
            seq = 1
    return f"DOC{ts}{seq:03d}"


class Document(db.Model):
    __tablename__ = 'documents'
    document_id      = db.Column(db.Integer, primary_key=True)
    document_code    = db.Column(db.String(50), unique=True, nullable=False)
    title            = db.Column(db.String(255), nullable=False)
    document_type_id = db.Column(db.Integer, db.ForeignKey('document_type.document_type_id'), nullable=False)
    created_by       = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=True)
    datetime         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status           = db.Column(db.String(100), nullable=False, default='Pending')
    priority              = db.Column(db.String(20),  default="Normal", nullable=False)
    action_taken          = db.Column(db.String(100), nullable=True)
    received_by           = db.Column(db.String(150), nullable=True)
    current_department_id = db.Column(db.Integer, db.ForeignKey('departments.department_id'), nullable=True)
    implementing_office   = db.Column(db.String(100), nullable=True)
    remarks               = db.Column(db.Text,  nullable=True)
    amount                = db.Column(db.Float, nullable=True)
    sub_category     = db.Column(db.String(100), nullable=True)  # SVP subcategory
    # SLA / duration tracking
    arrived_at      = db.Column(db.DateTime, nullable=True)   # when doc arrived at current dept
    workflow_step   = db.Column(db.String(100), nullable=True) # SVP step
    updated_at = db.Column(db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))
    doc_type_rel     = db.relationship('DocumentType', back_populates='documents')
    creator          = db.relationship('User', foreign_keys=[created_by])
    current_dept_rel = db.relationship('Department', foreign_keys=[current_department_id])
    transactions     = db.relationship('Transaction', back_populates='document',
                                       order_by='Transaction.datetime', cascade='all, delete-orphan')
    @property
    def id(self):         return self.document_id
    @property
    def doc_type(self):   return self.doc_type_rel.type_name if self.doc_type_rel else ''
    @property
    def department(self): return self.current_dept_rel.department_name if self.current_dept_rel else None
    @property
    def released_by(self): return self.creator.full_name if self.creator else ''
    @property
    def date_received(self): return self.datetime.date() if self.datetime else None
    @property
    def created_at(self):  return self.datetime
    @property
    def history(self):     return self.transactions

    def sla_info(self):
        """Return dict: {sla_minutes, elapsed_minutes, pct, tier, tier_label, hours_left}"""
        sla = DEFAULT_SLA.get("Simple")
        if self.doc_type_rel:
            sla = self.doc_type_rel.sla_minutes or sla
        ref = self.arrived_at or self.datetime
        if not ref:
            return dict(sla_minutes=sla, elapsed_minutes=0, pct=0,
                        tier="blue", tier_label="On Track", hours_left=round(sla/60,1))
        now = datetime.now(timezone.utc)
        ref_utc = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
        elapsed = int((now - ref_utc).total_seconds() / 60)
        pct = min(int(elapsed / sla * 100), 100) if sla else 100
        if pct < 25:
            tier, label = "blue",   "On Track"
        elif pct < 50:
            tier, label = "green",  "In Progress"
        elif pct < 75:
            tier, label = "yellow", "Urgent"
        else:
            tier, label = "red",    "Critical / Overdue"
        hours_left = max(round((sla - elapsed) / 60, 1), 0)
        return dict(sla_minutes=sla, elapsed_minutes=elapsed, pct=pct,
                    tier=tier, tier_label=label, hours_left=hours_left)


class Transaction(db.Model):
    __tablename__ = 'transactions'
    transaction_id   = db.Column(db.Integer, primary_key=True)
    document_id      = db.Column(db.Integer, db.ForeignKey('documents.document_id'), nullable=False)
    department_id    = db.Column(db.Integer, db.ForeignKey('departments.department_id'), nullable=True)
    transaction_type = db.Column(db.String(50),  nullable=False)
    origin           = db.Column(db.String(100), nullable=True)
    destination      = db.Column(db.String(100), nullable=True)
    handled_by       = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=True)
    action_by_name   = db.Column(db.String(150), nullable=True)
    datetime         = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    remarks          = db.Column(db.Text,  nullable=True)
    status           = db.Column(db.String(100), nullable=True)
    document = db.relationship('Document', back_populates='transactions')
    dept_rel = db.relationship('Department', foreign_keys=[department_id])
    handler  = db.relationship('User', back_populates='handled_transactions', foreign_keys=[handled_by])
    @property
    def id(self):           return self.transaction_id
    @property
    def record_id(self):    return self.document_id
    @property
    def record(self):       return self.document
    @property
    def action_type(self):  return self.transaction_type
    @property
    def from_department(self): return self.origin
    @property
    def to_department(self):   return self.destination
    @property
    def action_by(self):
        if self.action_by_name: return self.action_by_name
        return self.handler.full_name if self.handler else None
    @property
    def timestamp(self): return self.datetime


class SVPWorkflowStep(db.Model):
    __tablename__ = 'svp_workflow_steps'
    id            = db.Column(db.Integer, primary_key=True)
    step_order    = db.Column(db.Integer, nullable=False)
    status_name   = db.Column(db.String(100), nullable=False)
    department_name = db.Column(db.String(100), nullable=True)  # None = requesting office (Closed)

    def to_dict(self):
        return {
            'id': self.id,
            'step_order': self.step_order,
            'status_name': self.status_name,
            'department_name': self.department_name or ''
        }

import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from flask import Blueprint, Response, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import aliased

from . import db
from .models import (Document, Department, Transaction, User, Account,
                     DocumentType, DocumentStatus, CitizenCharterConfig,
                     DEFAULT_SLA, TRANSACTION_CATEGORIES, generate_document_code)
from .decorators import role_required

bp = Blueprint("main", __name__)

COMPLETED_STATUSES = {"Closed", "With Checked and Closed"}

# ---------------------------------------------------------------------------
# SVP Automated Routing
# ---------------------------------------------------------------------------
SVP_TYPE_NAME = "SVP"
SVP_WORKFLOW = [
    ("Request for PR",                          "Budget Office"),
    ("Request for PO",                          "Budget Office"),
    ("For Signature BAC Members - BAC Office",  "BAC Office"),
    ("For Signature of Mayor",                  "Office of the Mayor"),
    ("Request for OBR",                         "Budget Office"),
    ("For Accounting Staff Validation",         "Accounting Office"),
    ("For Processing",                          "Accounting Office"),
    ("With Checked",                            "Accounting Office"),
    ("Closed",                                  None),  # returns to requesting office
]

# Initial status for a newly created SVP document (before first release)
SVP_INITIAL_STATUS = "Pending Release"

SVP_SUBCATEGORIES = [
    "Reimbursement of Diesel",
    "Events and Seminars",
    "Reimbursement of Tarpaulin",
]
SVP_STATUS_ORDER = [s for s, _ in SVP_WORKFLOW]

def is_svp_doc(record) -> bool:
    return bool(record.doc_type_rel and record.doc_type_rel.type_name == SVP_TYPE_NAME)

def get_svp_next_step(current_status: str):
    # If doc is in initial placeholder state, first step is index 0
    if current_status == SVP_INITIAL_STATUS:
        return SVP_WORKFLOW[0]
    try: idx = SVP_STATUS_ORDER.index(current_status)
    except ValueError: return None
    if idx + 1 >= len(SVP_WORKFLOW): return None
    return SVP_WORKFLOW[idx + 1]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_dept_id(dept_name: str):
    if not dept_name: return None
    dept = Department.query.filter_by(department_name=dept_name).first()
    return dept.department_id if dept else None

def get_dept_users(dept_name: str, active_only: bool = False):
    dept = Department.query.filter_by(department_name=dept_name).first()
    if not dept: return []
    q = Account.query.join(User).filter(User.department_id == dept.department_id)
    if active_only: q = q.filter(Account.status == 'active')
    return q.all()

def get_next_status(current_status_name: str) -> str:
    current = DocumentStatus.query.filter_by(name=current_status_name).first()
    if current:
        nxt = (DocumentStatus.query.filter(DocumentStatus.id > current.id)
               .order_by(DocumentStatus.id.asc()).first())
        if nxt: return nxt.name
    return "With Checked and Closed"

def make_transaction(document_id, transaction_type, origin, destination,
                     status, remarks=None, handled_by_user_id=None, action_by_name=None):
    dept_id = get_dept_id(destination or origin)
    return Transaction(
        document_id=document_id, department_id=dept_id,
        transaction_type=transaction_type, origin=origin, destination=destination,
        handled_by=handled_by_user_id, action_by_name=action_by_name,
        status=status, remarks=remarks, datetime=datetime.now(timezone.utc),
    )

def visible_documents(department: str):
    dept_id = get_dept_id(department)
    processed_doc_ids = (db.session.query(Transaction.document_id).filter(
        (Transaction.origin == department) | (Transaction.destination == department)
    ).subquery())
    pending_transfer_ids = (db.session.query(Transaction.document_id).filter(
        Transaction.transaction_type == "transfer",
        Transaction.destination == department,
        ~Transaction.document_id.in_(
            db.session.query(Transaction.document_id).filter(
                Transaction.transaction_type == "received",
                Transaction.destination == department))
    ).subquery())
    return Document.query.filter(or_(
        Document.current_department_id == dept_id,
        Document.document_id.in_(processed_doc_ids),
        Document.document_id.in_(pending_transfer_ids),
    ))

def get_sla_tier_color(tier: str) -> str:
    return {"blue": "#2196F3", "green": "#4CAF50", "yellow": "#FF9800", "red": "#F44336"}.get(tier, "#9E9E9E")

# ---------------------------------------------------------------------------
# Core routes
# ---------------------------------------------------------------------------

@bp.route("/")
def home():
    return render_template("portal.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    records_q = visible_documents(current_user.department)
    all_recs  = records_q.order_by(Document.datetime.desc()).all()

    stats = {
        "total_documents": len(all_recs),
        "closed":     sum(1 for r in all_recs if r.status == "Closed"),
        "completed":  sum(1 for r in all_recs if r.status == "With Checked and Closed"),
        "in_process": sum(1 for r in all_recs if r.status not in COMPLETED_STATUSES),
    }

    # SLA tiers for active docs
    active = [r for r in all_recs if r.status not in COMPLETED_STATUSES]
    tier_counts = defaultdict(int)
    for r in active:
        tier_counts[r.sla_info()["tier"]] += 1

    status_data = (records_q.with_entities(Document.status, func.count(Document.document_id))
                   .group_by(Document.status).all())
    records = all_recs[:5]

    # Overdue / critical docs for notification badge
    critical = [r for r in active if r.sla_info()["tier"] == "red"]

    # Pending incoming transfers
    pending_incoming = (Transaction.query
        .filter_by(transaction_type="transfer", destination=current_user.department)
        .filter(~Transaction.document_id.in_(
            db.session.query(Transaction.document_id)
            .filter_by(transaction_type="received", destination=current_user.department)
        )).count())

    return render_template("dashboard.html", stats=stats, charts_combined=status_data,
                           records=records, tier_counts=tier_counts,
                           critical_docs=critical, pending_incoming=pending_incoming)


@bp.route("/api/sla/<int:record_id>")
@login_required
def sla_status(record_id):
    record = db.session.get(Document, record_id)
    if not record: return jsonify(error="not found"), 404
    info = record.sla_info()
    info["color"] = get_sla_tier_color(info["tier"])
    return jsonify(info)


@bp.route("/api/notifications")
@login_required
def notifications():
    dept = current_user.department
    dept_id = get_dept_id(dept)
    active = (Document.query
              .filter(Document.current_department_id == dept_id)
              .filter(Document.status.notin_(list(COMPLETED_STATUSES))).all())
    alerts = []
    for r in active:
        info = r.sla_info()
        if info["tier"] in ("yellow", "red"):
            alerts.append({
                "id": r.document_id, "code": r.document_code, "title": r.title,
                "tier": info["tier"], "tier_label": info["tier_label"],
                "pct": info["pct"], "elapsed_minutes": info["elapsed_minutes"],
                "hours_left": info["hours_left"],
            })
    # Pending incoming transfers
    pending_count = (Transaction.query
        .filter_by(transaction_type="transfer", destination=dept)
        .filter(~Transaction.document_id.in_(
            db.session.query(Transaction.document_id)
            .filter_by(transaction_type="received", destination=dept)
        )).count())
    return jsonify(alerts=alerts, pending_incoming=pending_count,
                   total_urgent=len(alerts))


@bp.route("/documents")
@login_required
def documents():
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    dept_id = get_dept_id(current_user.department)

    # Docs currently sitting in pending-transfer limbo for this office
    # (transferred here but not yet formally received — shown in Incoming instead)
    pending_incoming_ids = (db.session.query(Transaction.document_id).filter(
        Transaction.transaction_type == "transfer",
        Transaction.destination == current_user.department,
        ~Transaction.document_id.in_(
            db.session.query(Transaction.document_id).filter(
                Transaction.transaction_type == "received",
                Transaction.destination == current_user.department))
    ).subquery())

    # Open Documents = docs in this office with NO assigned person AND
    # status is either the initial placeholder (SVP_INITIAL_STATUS) or
    # not a mid-workflow SVP status (i.e. Bidding/other docs freshly created).
    # Mid-workflow SVP statuses (Request for PR, For Processing, etc.) belong
    # to specific offices in the workflow — they appear in Receive/Assigned, not Open.
    records_q = Document.query.filter(
        Document.current_department_id == dept_id,
        Document.status.notin_(list(COMPLETED_STATUSES)),
        Document.status.notin_([s for s in SVP_STATUS_ORDER]),  # exclude all workflow statuses
        or_(Document.received_by == None, Document.received_by == ""),
        ~Document.document_id.in_(pending_incoming_ids)
    )
    if q:
        records_q = records_q.filter(
            or_(Document.document_code.ilike(f"%{q}%"), Document.title.ilike(f"%{q}%")))
    if status_filter == "closed":
        records_q = Document.query.filter(
            Document.current_department_id == dept_id,
            Document.status == "Closed")
        if q:
            records_q = records_q.filter(
                or_(Document.document_code.ilike(f"%{q}%"), Document.title.ilike(f"%{q}%")))
    elif status_filter == "completed":
        records_q = Document.query.filter(
            Document.current_department_id == dept_id,
            Document.status == "With Checked and Closed")
        if q:
            records_q = records_q.filter(
                or_(Document.document_code.ilike(f"%{q}%"), Document.title.ilike(f"%{q}%")))
    records = records_q.order_by(Document.datetime.desc()).all()
    return render_template("documents.html", records=records, q=q, status_filter=status_filter)


@bp.route("/documents/<int:record_id>")
@login_required
def document_detail(record_id):
    record = visible_documents(current_user.department).filter(
        Document.document_id == record_id).first()
    if not record: abort(404)
    departments = Department.query.all()
    document_statuses = DocumentStatus.query.all()
    sla = record.sla_info()
    sla["color"] = get_sla_tier_color(sla["tier"])
    dept_users = get_dept_users(current_user.department, active_only=True)
    return render_template("document_detail.html", record=record,
                           departments=departments, document_statuses=document_statuses,
                           sla=sla, dept_users=dept_users)


@bp.route("/documents/edit/<int:record_id>", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    if request.method == "POST":
        record.title = request.form.get("title", record.title)
        new_type_name = request.form.get("doc_type", "")
        if new_type_name:
            dt = DocumentType.query.filter_by(type_name=new_type_name).first()
            if dt: record.document_type_id = dt.document_type_id
        record.sub_category = request.form.get("sub_category", "").strip() or None
        record.implementing_office = request.form.get("implementing_office", record.implementing_office)
        amount = request.form.get("amount")
        record.amount = float(amount) if amount else None
        record.received_by = request.form.get("received_by", record.received_by)
        record.status  = request.form.get("status", record.status)
        record.remarks = request.form.get("remarks", record.remarks)
        date_str = request.form.get("date_received", "").strip()
        if date_str:
            try: record.datetime = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError: pass
        record.updated_at = datetime.now(timezone.utc)
        db.session.add(make_transaction(
            document_id=record.document_id, transaction_type="edit",
            origin=current_user.department, destination=record.department,
            action_by_name=current_user.full_name,
            handled_by_user_id=current_user.user.user_id, status=record.status))
        db.session.commit()
        flash("Document updated successfully.", "success")
        return redirect(url_for("main.document_detail", record_id=record.document_id))
    departments    = Department.query.all()
    document_types = DocumentType.query.all()
    document_statuses = DocumentStatus.query.all()
    return render_template("document_edit.html", record=record, departments=departments,
                           document_types=document_types, document_statuses=document_statuses,
                           svp_subcategories=SVP_SUBCATEGORIES)


@bp.route("/documents/delete/<int:record_id>", methods=["POST"])
@login_required
@role_required("admin")
def delete_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    db.session.delete(record)
    db.session.commit()
    flash("Document deleted.", "info")
    return redirect(url_for("main.documents"))


@bp.route("/documents/close/<int:record_id>", methods=["POST"])
@login_required
@role_required("admin")
def close_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    if record.status in COMPLETED_STATUSES:
        return jsonify(success=False, message="Document is already closed.")
    data = request.get_json() or {}
    remarks = data.get("remarks", "").strip()
    # Admin can close at any stage — reason is required
    if not remarks:
        return jsonify(success=False, message="Please provide a reason for early closure.")
    prev_status = record.status
    record.status     = "Closed"
    record.updated_at = datetime.now(timezone.utc)
    db.session.add(make_transaction(
        document_id=record.document_id, transaction_type="close",
        origin=current_user.department, destination=record.department,
        action_by_name=current_user.full_name,
        handled_by_user_id=current_user.user.user_id, status="Closed",
        remarks=f"[Closed at stage: {prev_status}] {remarks}"))
    db.session.commit()
    return jsonify(success=True, message="Document has been closed.")


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@bp.route("/users")
@login_required
@role_required("admin")
def users():
    dept_accounts = get_dept_users(current_user.department)
    return render_template("users.html", users=dept_accounts)


@bp.route("/users/add", methods=["POST"])
@login_required
@role_required("admin")
def add_user():
    email = request.form.get("email", "").strip().lower()
    if User.query.filter_by(email=email).first():
        flash("User already exists.", "warning")
        return redirect(url_for("main.users"))
    full_name = request.form.get("full_name", "").strip()
    parts = full_name.split(" ", 1)
    dept  = Department.query.filter_by(department_name=current_user.department).first()
    new_user = User(first_name=parts[0], last_name=parts[1] if len(parts)>1 else "-",
                    email=email, department_id=dept.department_id if dept else None)
    db.session.add(new_user); db.session.flush()
    account = Account(user_id=new_user.user_id, username=email,
                      role=request.form.get("role","user"), status="active")
    account.set_password(request.form["password"])
    db.session.add(account); db.session.commit()
    flash("User added successfully.", "success")
    return redirect(url_for("main.users"))


@bp.route("/admin/users/edit/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def edit_user(id):
    account = db.session.get(Account, id) or abort(404)
    user = account.user
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    if not full_name:
        flash("Full name is required.", "danger")
        return redirect(url_for("main.users"))
    parts = full_name.split(" ", 1)
    user.first_name = parts[0]; user.last_name = parts[1] if len(parts)>1 else "-"
    if email and email != user.email:
        if not User.query.filter_by(email=email).first():
            user.email = email; account.username = email
        else:
            flash("Email already in use.", "warning")
            return redirect(url_for("main.users"))
    new_password = request.form.get("new_password", "").strip()
    if new_password:
        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return redirect(url_for("main.users"))
        account.set_password(new_password)
    give_admin = request.form.get("give_admin_access") == "on"
    account.role = "admin" if give_admin else "user"
    account.is_temp_admin = give_admin
    db.session.commit()
    flash(f"User {full_name} updated.", "success")
    return redirect(url_for("main.users"))


@bp.route("/admin/users/toggle/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def toggle_user(id):
    account = db.session.get(Account, id) or abort(404)
    if account.role == "admin":
        flash("Cannot deactivate admin accounts.", "warning")
        return redirect(url_for("main.users"))
    account.is_deactivated = not account.is_deactivated
    db.session.commit()
    return redirect(url_for("main.users"))


@bp.route("/admin/users/delete/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def delete_user(id):
    account = db.session.get(Account, id) or abort(404)
    if account.role == "admin":
        flash("Cannot delete admin accounts.", "danger")
        return redirect(url_for("main.users"))
    user = account.user
    db.session.delete(account); db.session.delete(user); db.session.commit()
    flash(f"User {user.full_name} deleted.", "success")
    return redirect(url_for("main.users"))


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

@bp.route("/add_document", methods=["GET", "POST"])
@login_required
def add_document():
    dept_accounts   = get_dept_users(current_user.department)
    document_type   = DocumentType.query.all()
    document_status = DocumentStatus.query.all()
    departments     = Department.query.all()
    if request.method == "POST":
        type_name = request.form["doc_type"]
        dt = DocumentType.query.filter_by(type_name=type_name).first()
        # For SVP: use a placeholder "Pending Release" until first release
        # For Bidding and others: use first DB status
        if type_name == SVP_TYPE_NAME:
            auto_status = SVP_INITIAL_STATUS
        else:
            first_status = DocumentStatus.query.order_by(DocumentStatus.id.asc()).first()
            auto_status  = first_status.name if first_status else "Pending"
        dept_id = get_dept_id(current_user.department)
        doc_code = generate_document_code()
        now = datetime.now(timezone.utc)
        sub_cat = request.form.get("sub_category", "").strip() or None
        record = Document(
            document_code=doc_code,
            title=request.form["title"],
            document_type_id=dt.document_type_id if dt else 1,
            created_by=current_user.user.user_id,
            datetime=now, status=auto_status,
            priority=request.form.get("priority", "Normal"),
            sub_category=sub_cat,
            current_department_id=dept_id,
            implementing_office=current_user.department,
            received_by="", remarks=request.form.get("remarks", ""),
            arrived_at=now, updated_at=now,
        )
        db.session.add(record); db.session.flush()
        db.session.add(make_transaction(
            document_id=record.document_id, transaction_type="create",
            origin=current_user.department, destination=current_user.department,
            action_by_name=current_user.full_name,
            handled_by_user_id=current_user.user.user_id, status=auto_status))
        db.session.commit()
        flash(f"Document {record.document_code} added successfully.", "success")
        return redirect(url_for("main.document_detail", record_id=record.document_id))
    return render_template("admin/new_doc.html", users=dept_accounts,
                           document_type=document_type, document_status=document_status,
                           departments=departments, svp_subcategories=SVP_SUBCATEGORIES)


# ---------------------------------------------------------------------------
# Incoming / Outgoing / Processing / Archived / Assigned
# ---------------------------------------------------------------------------

@bp.route("/incoming")
@login_required
def incoming_documents():
    q    = request.args.get("q", "").strip()
    dept = current_user.department

    records = (visible_documents(dept)
               .filter(Document.current_department_id == get_dept_id(dept))
               .filter(Document.status.notin_(list(COMPLETED_STATUSES)))
               .order_by(Document.updated_at.desc()).all())

    already_received_ids = (db.session.query(Transaction.document_id)
        .filter_by(transaction_type="received", destination=dept).subquery())
    RejH = aliased(Transaction)
    pending_q = (Transaction.query
        .filter_by(transaction_type="transfer", destination=dept)
        .filter(~Transaction.document_id.in_(already_received_ids))
        .filter(~db.session.query(RejH).filter(
            RejH.document_id == Transaction.document_id,
            RejH.transaction_type == "rejected_transfer",
            RejH.destination == dept,
            RejH.datetime > Transaction.datetime).exists())
        .order_by(Transaction.datetime.desc()))

    history_q = (Transaction.query
        .filter(Transaction.destination == dept)
        .filter(Transaction.transaction_type.in_(["received", "rejected_transfer"]))
        .filter(Transaction.document_id.in_(
            db.session.query(Transaction.document_id)
            .filter_by(transaction_type="transfer", destination=dept)))
        .order_by(Transaction.datetime.desc()))

    pending_transfers = pending_q.all()
    transfer_history  = history_q.all()

    if q:
        ql = q.lower()
        pending_transfers = [h for h in pending_transfers
            if ql in (h.document.document_code or "").lower()
            or ql in (h.document.title or "").lower()
            or ql in (h.origin or "").lower()]
        transfer_history = [h for h in transfer_history
            if ql in (h.document.document_code or "").lower()
            or ql in (h.document.title or "").lower()
            or ql in (h.origin or "").lower()]

    # Attach SLA info to pending transfers
    for h in pending_transfers:
        h._sla = h.document.sla_info()

    return render_template("incoming_doc.html", records=records,
                           pending_transfers=pending_transfers,
                           transfer_history=transfer_history, q=q)


@bp.route("/outgoing")
@login_required
def outgoing_documents():
    dept = current_user.department
    outgoing = (Transaction.query.filter_by(transaction_type="transfer", origin=dept)
                .order_by(Transaction.datetime.desc()).all())

    # All docs ready to release:
    # - For SVP: any non-completed doc in current dept (auto-routed, no assignment needed)
    # - For non-SVP: docs assigned to current user with status Assigned
    dept_id = get_dept_id(dept)
    svp_type = DocumentType.query.filter_by(type_name=SVP_TYPE_NAME).first()
    svp_type_id = svp_type.document_type_id if svp_type else -1

    # Release page shows EXACTLY the same docs as Assigned page:
    # docs in current dept assigned to the logged-in user (received_by == me)
    # Status is the real workflow status, not "Assigned"
    my_records = (Document.query
                  .filter(Document.current_department_id == dept_id)
                  .filter(Document.received_by == current_user.full_name)
                  .filter(Document.status.notin_(list(COMPLETED_STATUSES)))
                  .all())

    pending_transfer_ids  = set()
    received_transfer_ids = set()

    for record in my_records:
        last_t = (Transaction.query.filter_by(document_id=record.document_id, transaction_type="transfer")
                  .order_by(Transaction.datetime.desc()).first())
        if last_t:
            was_received = (Transaction.query
                .filter_by(document_id=record.document_id, transaction_type="received",
                           destination=last_t.destination)
                .filter(Transaction.datetime > last_t.datetime).first())
            was_rejected = (Transaction.query
                .filter_by(document_id=record.document_id, transaction_type="rejected_transfer",
                           destination=last_t.destination)
                .filter(Transaction.datetime > last_t.datetime).first())
            if was_received:   received_transfer_ids.add(record.document_id)
            elif not was_rejected: pending_transfer_ids.add(record.document_id)

    transfer_status = {}
    for h in outgoing:
        was_received = (Transaction.query
            .filter_by(document_id=h.document_id, transaction_type="received",
                       destination=h.destination)
            .filter(Transaction.datetime > h.datetime).first())
        was_rejected = (Transaction.query
            .filter_by(document_id=h.document_id, transaction_type="rejected_transfer",
                       destination=h.destination)
            .filter(Transaction.datetime > h.datetime).first())
        if was_received:        transfer_status[h.transaction_id] = "received"
        elif was_rejected:      transfer_status[h.transaction_id] = "rejected"
        else:                   transfer_status[h.transaction_id] = "pending"

    # SVP auto-routing info
    svp_auto_dests = {}
    for r in my_records:
        if is_svp_doc(r):
            step = get_svp_next_step(r.status)
            if step: svp_auto_dests[r.document_id] = step

    departments = Department.query.filter(Department.department_name != dept).all()
    return render_template("outgoing_doc.html", outgoing=outgoing, records=my_records,
                           departments=departments,
                           pending_transfer_ids=pending_transfer_ids,
                           received_transfer_ids=received_transfer_ids,
                           transfer_status=transfer_status,
                           svp_auto_dests=svp_auto_dests)


@bp.route("/processing")
@login_required
def processing_documents():
    dept_id = get_dept_id(current_user.department)
    records = (visible_documents(current_user.department)
               .filter(Document.current_department_id == dept_id)
               .filter(Document.status.notin_(list(COMPLETED_STATUSES)))
               .filter(Document.status.notin_(list(COMPLETED_STATUSES)))
               .filter(or_(Document.received_by == None, Document.received_by == ""))
               .order_by(Document.updated_at.desc()).all())
    dept_accounts = get_dept_users(current_user.department, active_only=True)
    # Attach SLA
    for r in records:
        r._sla = r.sla_info()
    return render_template("processing_doc.html", records=records, dept_users=dept_accounts)


@bp.route("/archived")
@login_required
def archived_documents():
    records = (visible_documents(current_user.department)
               .filter(Document.status.in_(list(COMPLETED_STATUSES)))
               .order_by(Document.updated_at.desc()).all())
    return render_template("closed.html", records=records)


@bp.route("/assigned")
@login_required
def assigned_documents():
    dept_id = get_dept_id(current_user.department)
    # Assigned = docs explicitly handed to the logged-in user (received_by == me).
    # Covers both non-SVP docs (status "Assigned") and SVP docs (status = workflow
    # step like "Request for PR").  Mirrors the non-SVP column in Release exactly.
    records = (Document.query
               .filter(Document.current_department_id == dept_id)
               .filter(Document.status.notin_(list(COMPLETED_STATUSES)))
               .filter(Document.received_by == current_user.full_name)
               .order_by(Document.updated_at.desc()).all())
    dept_accounts = get_dept_users(current_user.department, active_only=True)
    for r in records:
        r._sla = r.sla_info()
    return render_template("assigned.html", records=records, dept_users=dept_accounts)


# ---------------------------------------------------------------------------
# Transfer / Receive / Reject / Cancel / Assign / Pull-out
# ---------------------------------------------------------------------------

@bp.route("/documents/transfer/<int:record_id>", methods=["POST"])
@login_required
def transfer_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    if record.status in COMPLETED_STATUSES:
        return jsonify(success=False, message="This document is already completed.")

    data    = request.get_json() or {}
    to_dept = data.get("to_department", "").strip()
    remarks = data.get("remarks", "").strip()

    # SVP auto-routing — no "Assigned" check, routing is fully automatic
    if is_svp_doc(record):
        current_dept_id = get_dept_id(current_user.department)
        if record.current_department_id != current_dept_id:
            return jsonify(success=False, message="You can only release documents currently in your department.")
        # Derive next step from status; fall back to workflow_step if status was
        # corrupted (e.g. old "Assigned" value from before this fix)
        effective_status = record.status
        if effective_status == "Assigned" or effective_status not in SVP_STATUS_ORDER + [SVP_INITIAL_STATUS]:
            effective_status = record.workflow_step or SVP_INITIAL_STATUS
        step = get_svp_next_step(effective_status)
        if step is None:
            return jsonify(success=False, message="SVP document has completed its workflow.")
        next_status, auto_dest = step
        # If dest is None (e.g. Closed), return to the implementing/requesting office
        if auto_dest is None:
            auto_dest = record.implementing_office or current_user.department
        to_dept = auto_dest
        now = datetime.now(timezone.utc)
        record.workflow_step = next_status
        record.received_by   = ""
        record.status        = next_status
        record.updated_at    = now
        new_dept_id = get_dept_id(to_dept)
        if new_dept_id:
            record.current_department_id = new_dept_id
            record.arrived_at = now
        db.session.add(make_transaction(
            document_id=record.document_id, transaction_type="transfer",
            origin=current_user.department, destination=to_dept,
            action_by_name=current_user.full_name,
            handled_by_user_id=current_user.user.user_id,
            status=next_status, remarks=remarks))
        db.session.commit()
        return jsonify(success=True,
                       message=f"Document advanced to '{next_status}' → sent to {to_dept}.",
                       record_id=record.document_id, status=next_status)

    # Regular transfer
    if not to_dept:
        return jsonify(success=False, message="Please select a target department.")
    if to_dept == current_user.department:
        return jsonify(success=False, message="Cannot transfer to your own department.")

    # Check for unresolved pending transfer
    last_t = (Transaction.query.filter_by(document_id=record.document_id, transaction_type="transfer")
              .order_by(Transaction.datetime.desc()).first())
    if last_t:
        was_received = (Transaction.query
            .filter_by(document_id=record.document_id, transaction_type="received",
                       destination=last_t.destination)
            .filter(Transaction.datetime > last_t.datetime).first())
        was_rejected = (Transaction.query
            .filter_by(document_id=record.document_id, transaction_type="rejected_transfer",
                       destination=last_t.destination)
            .filter(Transaction.datetime > last_t.datetime).first())
        if not was_received and not was_rejected:
            return jsonify(success=False,
                           message=f"Still pending with {last_t.destination}. Wait for receipt or rejection.")

    new_status = get_next_status(record.status)
    record.received_by = ""; record.status = new_status
    record.updated_at = datetime.now(timezone.utc)
    db.session.add(make_transaction(
        document_id=record.document_id, transaction_type="transfer",
        origin=current_user.department, destination=to_dept,
        action_by_name=current_user.full_name,
        handled_by_user_id=current_user.user.user_id,
        status=new_status, remarks=remarks))
    db.session.commit()
    return jsonify(success=True, message=f"Document released to {to_dept}.",
                   record_id=record.document_id, status=new_status)


@bp.route("/documents/cancel-transfer/<int:transfer_history_id>", methods=["POST"])
@login_required
def cancel_transfer(transfer_history_id):
    transfer = db.session.get(Transaction, transfer_history_id) or abort(404)
    if transfer.origin != current_user.department:
        return jsonify(success=False, message="You can only cancel transfers from your department.")
    was_received = (Transaction.query
        .filter_by(document_id=transfer.document_id, transaction_type="received",
                   destination=transfer.destination).first())
    if was_received:
        return jsonify(success=False, message="Cannot cancel a transfer that has already been received.")
    db.session.delete(transfer); db.session.commit()
    return jsonify(success=True, message="Transfer cancelled.")


@bp.route("/documents/receive/<int:record_id>", methods=["POST"])
@login_required
def receive_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    dept   = current_user.department

    already_received_ids = (db.session.query(Transaction.document_id)
        .filter_by(transaction_type="received", destination=dept).subquery())
    pending = (Transaction.query
        .filter_by(transaction_type="transfer", destination=dept, document_id=record_id)
        .filter(~Transaction.document_id.in_(already_received_ids))
        .order_by(Transaction.datetime.desc()).first())

    if not pending:
        return jsonify(success=False, message="No pending transfer found for this document.")

    now = datetime.now(timezone.utc)
    record.current_department_id = get_dept_id(dept)
    record.received_by  = current_user.full_name
    record.arrived_at   = now
    record.updated_at   = now

    # NEVER overwrite status with "Assigned" — status is always the real workflow
    # status (e.g. "Request for PR", "Pending Release", etc.).
    # received_by is what tells us who holds the document.
    tx_status = record.status
    db.session.add(make_transaction(
        document_id=record.document_id, transaction_type="received",
        origin=pending.origin, destination=dept,
        action_by_name=current_user.full_name,
        handled_by_user_id=current_user.user.user_id, status=tx_status))
    db.session.commit()
    return jsonify(success=True, message="Document received and assigned to you.",
                   record_id=record.document_id, new_department=dept,
                   status=tx_status, received_by=current_user.full_name)


@bp.route("/documents/reject/<int:record_id>", methods=["POST"])
@login_required
def reject_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    dept   = current_user.department

    already_received_ids = (db.session.query(Transaction.document_id)
        .filter_by(transaction_type="received", destination=dept).subquery())
    pending = (Transaction.query
        .filter_by(transaction_type="transfer", destination=dept, document_id=record_id)
        .filter(~Transaction.document_id.in_(already_received_ids))
        .order_by(Transaction.datetime.desc()).first())
    if not pending:
        return jsonify(success=False, message="No pending transfer found to reject.")

    prev = (Transaction.query.filter_by(document_id=record.document_id)
            .filter(Transaction.datetime < pending.datetime)
            .order_by(Transaction.datetime.desc()).first())
    if prev:
        record.status = prev.status
        record.updated_at = datetime.now(timezone.utc)
    db.session.add(make_transaction(
        document_id=record.document_id, transaction_type="rejected_transfer",
        origin=pending.origin, destination=dept,
        action_by_name=current_user.full_name,
        handled_by_user_id=current_user.user.user_id, status=record.status))
    db.session.commit()
    return jsonify(success=True,
                   message=f"Transfer rejected. Document returned to {pending.origin}.",
                   record_id=record.document_id)


@bp.route("/documents/assign/<int:record_id>", methods=["POST"])
@login_required
@role_required("admin")
def assign_document(record_id):
    record = db.session.get(Document, record_id) or abort(404)
    data        = request.get_json() or {}
    assigned_to = data.get("assigned_to", "").strip()
    remarks     = data.get("remarks", "").strip()
    if not assigned_to:
        return jsonify(success=False, message="Please select a staff to assign.")
    # Keep the real workflow status — only update who holds the document
    record.received_by = assigned_to
    record.updated_at  = datetime.now(timezone.utc)
    db.session.add(make_transaction(
        document_id=record.document_id, transaction_type="assigned",
        origin=current_user.department, destination=current_user.department,
        action_by_name=current_user.full_name,
        handled_by_user_id=current_user.user.user_id, status=record.status,
        remarks=f"Assigned to {assigned_to}" + (f" — {remarks}" if remarks else "")))
    db.session.commit()
    return jsonify(success=True, message=f"Document assigned to {assigned_to}.",
                   received_by=assigned_to, status=record.status)


@bp.route("/documents/pullout/<int:record_id>", methods=["POST"])
@login_required
@role_required("admin")
def pullout_document(record_id):
    record  = db.session.get(Document, record_id) or abort(404)
    data    = request.get_json() or {}
    to_dept = data.get("to_department", current_user.department).strip()
    remarks = data.get("remarks", "").strip()
    if not to_dept: to_dept = current_user.department
    prev_dept   = record.department or current_user.department
    new_dept_id = get_dept_id(to_dept) or get_dept_id(current_user.department)
    now = datetime.now(timezone.utc)
    record.current_department_id = new_dept_id
    record.received_by = ""
    record.status      = "Pulled Out"
    record.arrived_at  = now
    record.updated_at  = now
    db.session.add(make_transaction(
        document_id=record.document_id, transaction_type="pullout",
        origin=prev_dept, destination=to_dept,
        action_by_name=current_user.full_name,
        handled_by_user_id=current_user.user.user_id, status="Pulled Out",
        remarks=f"Pulled out by {current_user.full_name}" + (f" — {remarks}" if remarks else "")))
    db.session.commit()
    return jsonify(success=True, message=f"Document pulled out to {to_dept}.",
                   record_id=record.document_id)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

@bp.route("/trace")
@login_required
def trace():
    q = request.args.get("q", "").strip()
    results = []
    if q:
        results = (visible_documents(current_user.department)
                   .filter(or_(Document.document_code.ilike(f"%{q}%"),
                               Document.title.ilike(f"%{q}%"))).all())
        for r in results:
            r._sla = r.sla_info()
    return render_template("trace.html", q=q, results=results)


# ---------------------------------------------------------------------------
# Reports (Summarized — no export, no bottlenecks)
# ---------------------------------------------------------------------------

@bp.route("/reports")
@login_required
@role_required("admin")
def reports():
    date_from = request.args.get("from", "").strip()
    date_to   = request.args.get("to",   "").strip()
    records_q = visible_documents(current_user.department)
    if date_from:
        try: records_q = records_q.filter(Document.datetime >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError: pass
    if date_to:
        try: records_q = records_q.filter(Document.datetime <= datetime.strptime(date_to, "%Y-%m-%d"))
        except ValueError: pass
    all_records  = records_q.order_by(Document.datetime.desc()).all()
    total_docs   = len(all_records)
    closed_docs  = sum(1 for r in all_records if r.status in COMPLETED_STATUSES)
    in_process   = total_docs - closed_docs
    total_amount = sum(r.amount or 0 for r in all_records)
    dept_dict = defaultdict(int); status_dict = defaultdict(int)
    type_dict = defaultdict(int); fin_dict = defaultdict(float); monthly_dict = defaultdict(int)
    tier_dict = defaultdict(int); subcat_dict = defaultdict(int); subcat_fin = defaultdict(float)
    for r in all_records:
        dept_dict[r.department or "—"] += 1
        status_dict[r.status] += 1
        type_dict[r.doc_type] += 1
        if r.amount: fin_dict[r.doc_type] += r.amount
        if r.datetime: monthly_dict[r.datetime.strftime("%Y-%m")] += 1
        if r.status not in COMPLETED_STATUSES:
            tier_dict[r.sla_info()["tier"]] += 1
        if r.doc_type == "SVP" and r.sub_category:
            subcat_dict[r.sub_category] += 1
            if r.amount: subcat_fin[r.sub_category] += r.amount
    # --- Per-office processing time analysis ---
    # For each transaction pair (transfer → received), compute time spent at each dept
    from collections import defaultdict as _dd
    office_minutes = _dd(list)   # dept_name -> [minutes_spent, ...]
    office_stuck   = _dd(list)   # dept_name -> [active docs stuck here, ...]

    transfers = (Transaction.query
                 .filter(Transaction.transaction_type == "transfer")
                 .order_by(Transaction.datetime.asc()).all())
    received_map = {}  # (doc_id, dest) -> received datetime
    received_txns = (Transaction.query
                     .filter(Transaction.transaction_type == "received")
                     .all())
    for rt in received_txns:
        received_map[(rt.document_id, rt.destination)] = rt.datetime

    for t in transfers:
        dest = t.destination
        if not dest: continue
        recv_dt = received_map.get((t.document_id, dest))
        if recv_dt:
            t_utc = t.datetime.replace(tzinfo=timezone.utc) if t.datetime.tzinfo is None else t.datetime
            r_utc = recv_dt.replace(tzinfo=timezone.utc) if recv_dt.tzinfo is None else recv_dt
            mins = max(int((r_utc - t_utc).total_seconds() / 60), 0)
            office_minutes[dest].append(mins)

    # Active documents currently stuck in each office
    now_utc = datetime.now(timezone.utc)
    for r in all_records:
        if r.status in COMPLETED_STATUSES: continue
        dept_name = r.department
        if not dept_name: continue
        ref = r.arrived_at or r.datetime
        if ref:
            ref_utc = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
            stuck_mins = int((now_utc - ref_utc).total_seconds() / 60)
            office_stuck[dept_name].append(stuck_mins)

    # Build summary: avg processing time per office (completed), avg stuck time (active)
    office_processing = []
    for dept_name, mins_list in sorted(office_minutes.items()):
        avg_hrs = round(sum(mins_list) / len(mins_list) / 60, 1) if mins_list else 0
        office_processing.append((dept_name, avg_hrs, len(mins_list)))
    office_processing.sort(key=lambda x: -x[1])  # sort by avg hours desc

    office_stuck_summary = []
    for dept_name, mins_list in sorted(office_stuck.items()):
        avg_hrs = round(sum(mins_list) / len(mins_list) / 60, 1) if mins_list else 0
        max_hrs = round(max(mins_list) / 60, 1) if mins_list else 0
        office_stuck_summary.append((dept_name, avg_hrs, max_hrs, len(mins_list)))
    office_stuck_summary.sort(key=lambda x: -x[1])  # sort by avg stuck desc

    return render_template("reports.html",
                           total_docs=total_docs, in_process=in_process,
                           closed_docs=closed_docs, total_amount=total_amount,
                           date_from=date_from, date_to=date_to,
                           dept_summary=sorted(dept_dict.items(),     key=lambda x:-x[1]),
                           status_summary=sorted(status_dict.items(), key=lambda x:-x[1]),
                           type_summary=sorted(type_dict.items(),     key=lambda x:-x[1]),
                           financial=sorted(fin_dict.items(),         key=lambda x:-x[1]),
                           monthly=sorted(monthly_dict.items()),
                           tier_dict=dict(tier_dict),
                           subcat_summary=sorted(subcat_dict.items(), key=lambda x:-x[1]),
                           subcat_financial=sorted(subcat_fin.items(), key=lambda x:-x[1]),
                           office_processing=office_processing,
                           office_stuck_summary=office_stuck_summary)


# ---------------------------------------------------------------------------
# Citizen Charter / SLA Settings
# ---------------------------------------------------------------------------

@bp.route("/charter_settings", methods=["GET", "POST"])
@login_required
@role_required("admin")
def charter_settings():
    doc_types   = DocumentType.query.order_by(DocumentType.type_name).all()
    departments = Department.query.order_by(Department.department_name).all()

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "save_type_sla":
            dt_id    = request.form.get("doc_type_id")
            category = request.form.get("category", "Simple")
            sla_val  = request.form.get("sla_minutes")
            dt = db.session.get(DocumentType, dt_id)
            if dt:
                dt.transaction_category = category
                dt.sla_minutes = int(sla_val) if sla_val else DEFAULT_SLA.get(category, 4320)
                db.session.commit()
                flash(f"SLA for '{dt.type_name}' updated.", "success")
        elif action == "save_charter":
            dt_id     = request.form.get("doc_type_id")
            dept_id   = request.form.get("department_id") or None
            category  = request.form.get("category", "Simple")
            sla_val   = request.form.get("sla_minutes")
            resp      = request.form.get("responsible_person", "").strip()
            cfg = CitizenCharterConfig.query.filter_by(
                doc_type_id=dt_id, department_id=dept_id).first()
            if not cfg:
                cfg = CitizenCharterConfig(doc_type_id=dt_id, department_id=dept_id)
                db.session.add(cfg)
            cfg.category = category
            cfg.sla_minutes = int(sla_val) if sla_val else DEFAULT_SLA.get(category, 4320)
            cfg.responsible_person = resp
            db.session.commit()
            flash("Citizen Charter config saved.", "success")
        elif action == "delete_charter":
            cfg_id = request.form.get("config_id")
            cfg = db.session.get(CitizenCharterConfig, cfg_id)
            if cfg: db.session.delete(cfg); db.session.commit()
            flash("Config removed.", "info")
        return redirect(url_for("main.charter_settings"))

    configs = CitizenCharterConfig.query.all()
    return render_template("charter_settings.html", doc_types=doc_types,
                           departments=departments, configs=configs,
                           categories=TRANSACTION_CATEGORIES, default_sla=DEFAULT_SLA)


# ---------------------------------------------------------------------------
# Office Settings
# ---------------------------------------------------------------------------

@bp.route("/office_settings", methods=["GET", "POST"])
@login_required
@role_required("admin")
def office_settings():
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "add_doc_type":
            name = request.form.get("name", "").strip()
            if name in ("SVP", "Bidding"):
                flash("SVP and Bidding are core types and cannot be re-added.", "warning")
            elif name and not DocumentType.query.filter_by(type_name=name).first():
                db.session.add(DocumentType(type_name=name)); db.session.commit()
                flash(f'Document type "{name}" added.', "success")
            elif name: flash("Document type already exists.", "warning")
        elif action == "delete_doc_type":
            dt = db.session.get(DocumentType, request.form.get("id"))
            if dt and dt.type_name in ("SVP", "Bidding"):
                flash(f'"{dt.type_name}" is a core type and cannot be deleted.', "warning")
            elif dt: db.session.delete(dt); db.session.commit(); flash(f'"{dt.type_name}" deleted.', "info")
        elif action == "add_status":
            name = request.form.get("name", "").strip()
            if name and not DocumentStatus.query.filter_by(name=name).first():
                db.session.add(DocumentStatus(name=name)); db.session.commit()
                flash(f'Status "{name}" added.', "success")
            elif name: flash("Status already exists.", "warning")
        elif action == "delete_status":
            ds = db.session.get(DocumentStatus, request.form.get("id"))
            if ds: db.session.delete(ds); db.session.commit(); flash(f'"{ds.name}" deleted.', "info")
        return redirect(url_for("main.office_settings"))

    doc_types    = DocumentType.query.order_by(DocumentType.type_name).all()
    doc_statuses = DocumentStatus.query.order_by(DocumentStatus.name).all()
    staff_count  = Account.query.join(User).filter(
        User.department_id == get_dept_id(current_user.department)).count()
    return render_template("office_settings.html", doc_types=doc_types,
                           doc_statuses=doc_statuses, staff_count=staff_count,
                           svp_subcategories=SVP_SUBCATEGORIES,
                           svp_workflow=[s for s, _ in SVP_WORKFLOW])


# ---------------------------------------------------------------------------
# Activity Logs
# ---------------------------------------------------------------------------

@bp.route("/activity_logs")
@login_required
@role_required("admin")
def activity_logs():
    dept = current_user.department
    action_filter = request.args.get("action", "").strip()
    records_q = (Transaction.query
                 .join(Document, Transaction.document_id == Document.document_id)
                 .filter((Transaction.origin == dept) | (Transaction.destination == dept))
                 .order_by(Transaction.datetime.desc()))
    if action_filter:
        records_q = records_q.filter(Transaction.transaction_type == action_filter)
    records = records_q.all()
    return render_template("logs.html", records=records, action_filter=action_filter)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@bp.errorhandler(403)
def forbidden(e):  return render_template("403.html"), 403

@bp.errorhandler(404)
def not_found(e):  return render_template("404.html"), 404

from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from collections import defaultdict
import statistics
from .models import Document, Transaction

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/analytics", methods=["GET"])
@login_required
def api_analytics():
    dept_durations = defaultdict(list)
    records = Document.query.all()
    for record in records:
        hist = sorted(record.transactions, key=lambda h: h.datetime) if record.transactions else []
        for i in range(len(hist) - 1):
            cur, nxt = hist[i], hist[i + 1]
            if cur.destination and cur.datetime and nxt.datetime:
                delta = (nxt.datetime - cur.datetime).total_seconds()
                if delta > 0:
                    dept_durations[cur.destination].append(delta)

    avg_hours     = {d: (sum(s) / len(s)) / 3600.0 for d, s in dept_durations.items()}
    avg_values    = list(avg_hours.values())
    overall_mean  = statistics.mean(avg_values)  if avg_values else 0
    overall_stdev = statistics.stdev(avg_values) if len(avg_values) > 1 else 0

    bottlenecks = [
        {"department": d, "avg_hours": round(h, 2), "count": len(dept_durations[d])}
        for d, h in avg_hours.items() if h > overall_mean + overall_stdev
    ]

    return jsonify({
        "labels":        list(avg_hours.keys()),
        "values":        [round(v, 2) for v in avg_hours.values()],
        "bottlenecks":   bottlenecks,
        "overall_mean":  round(overall_mean, 2),
        "overall_stdev": round(overall_stdev, 2),
    })


@api_bp.route("/documents", methods=["GET"])
@login_required
def api_documents():
    from sqlalchemy import or_
    from .routes import visible_documents
    records = visible_documents(current_user.department).order_by(Document.datetime.desc()).all()
    return jsonify({
        "total": len(records),
        "documents": [
            {
                "id":            r.document_id,
                "document_code": r.document_code,
                "title":         r.title,
                "status":        r.status,
                "department":    r.department,
                "created_at":    r.datetime.isoformat() if r.datetime else None,
            }
            for r in records
        ],
    })


# ── Server-Sent Events — real-time notification stream ────────────────────────
# Replaces the 30-second JS polling interval.
# The client connects once; the server pushes updates every 10 seconds.
# Falls back automatically to polling if SSE is unsupported or the connection drops.

import time
import json as _json
from flask import Response, stream_with_context


@api_bp.route("/notifications/stream")
@login_required
def notifications_stream():
    """
    SSE endpoint that streams notification data to the client.

    Emits JSON-encoded data every 10 seconds in the text/event-stream format.
    The client (index.html) parses each message and calls applyNotificationData().

    ISO/IEC 25010 Performance Efficiency: eliminates redundant HTTP polling,
    replacing ~6 requests/min per user with a single persistent connection.
    """
    def _event_generator():
        # Import inside generator so it runs inside the app context
        from .routes_api import _build_notification_payload
        retries = 0
        while retries < 60:   # max ~10 minutes per connection; client reconnects
            try:
                payload = _build_notification_payload(current_user)
                yield f"data: {_json.dumps(payload)}\n\n"
            except Exception as exc:
                yield f"data: {{}}\n\n"   # send empty payload on error, don't drop connection
            time.sleep(10)
            retries += 1

    return Response(
        stream_with_context(_event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering for SSE
        }
    )


def _build_notification_payload(user):
    """
    Build the notification payload dict for the given user.
    Shared between the REST endpoint (/api/notifications) and the SSE stream.
    """
    from .models import Document, Transaction
    from .routes  import visible_documents, COMPLETED_STATUSES

    dept = user.department
    records = visible_documents(dept).all()

    pending_incoming = Transaction.query.filter_by(
        destination=dept, is_received=False
    ).count() if hasattr(Transaction, 'is_received') else (
        Transaction.query.filter_by(destination=dept, transaction_type="transfer")
        .join(Document, Transaction.document_id == Document.document_id)
        .filter(Document.received_by == "")
        .count()
    )

    alerts = []
    for r in records:
        if r.status in COMPLETED_STATUSES:
            continue
        info = r.sla_info()
        if info.get("tier") in ("yellow", "red"):
            alerts.append({
                "id":              r.document_id,
                "code":            r.document_code,
                "title":           r.title,
                "tier":            info["tier"],
                "tier_label":      info["tier_label"],
                "pct":             info["pct"],
                "elapsed_minutes": info["elapsed_minutes"],
                "hours_left":      info["hours_left"],
            })

    alerts.sort(key=lambda x: x["pct"], reverse=True)
    return {"pending_incoming": pending_incoming, "alerts": alerts[:10]}

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

"""
tests/test_doctrack.py
-----------------------
Basic unit & integration tests for DocTrack.
Run with:  pytest tests/ -v

Covers ISO/IEC 25010 Maintainability → Testability sub-characteristic.
"""

import pytest
from app import create_app
from app.models import db as _db, Account, User, Department


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create a test Flask app with an in-memory SQLite DB."""
    test_app = create_app()
    test_app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,       # disable CSRF for test forms
        "RATELIMIT_ENABLED": False,       # disable rate limiting in tests
    })
    with test_app.app_context():
        _db.create_all()
        yield test_app
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_client(app, client):
    """Return a client already logged in as the seed admin."""
    with app.app_context():
        # Use first seeded account
        account = Account.query.filter_by(role="admin").first()
        if not account:
            pytest.skip("No admin account seeded")
        with client.session_transaction() as sess:
            sess["_user_id"] = str(account.account_id)
    return client


# ── Auth tests ────────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_page_loads(self, client):
        """Login page should return 200."""
        r = client.get("/auth/login")
        assert r.status_code == 200

    def test_login_wrong_password(self, client, app):
        """Wrong password should redirect back to login with flash."""
        with app.app_context():
            account = Account.query.filter_by(role="admin").first()
            if not account:
                pytest.skip("No admin account seeded")
            email = account.user.email

        r = client.post("/auth/login",
                        data={"email": email, "password": "wrongpassword"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert b"Incorrect password" in r.data

    def test_login_unknown_email(self, client):
        r = client.post("/auth/login",
                        data={"email": "nobody@nowhere.com", "password": "x"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert b"No account found" in r.data

    def test_dashboard_requires_login(self, client):
        """Unauthenticated access to /dashboard should redirect."""
        r = client.get("/dashboard")
        assert r.status_code in (301, 302)


# ── Document tests ────────────────────────────────────────────────────────────

class TestDocuments:
    def test_documents_list_requires_login(self, client):
        r = client.get("/documents")
        assert r.status_code in (301, 302)

    def test_documents_list_accessible_when_logged_in(self, auth_client):
        r = auth_client.get("/documents")
        assert r.status_code == 200

    def test_dashboard_loads(self, auth_client):
        r = auth_client.get("/dashboard")
        assert r.status_code == 200

    def test_add_document_page_loads(self, auth_client):
        r = auth_client.get("/add_document")
        assert r.status_code == 200


# ── Model tests ───────────────────────────────────────────────────────────────

class TestModels:
    def test_account_password_hashing(self, app):
        """set_password and check_password should work correctly."""
        with app.app_context():
            dept = Department.query.first()
            user = User(first_name="Test", last_name="User",
                        email="testmodel@unit.com",
                        department_id=dept.department_id if dept else None)
            _db.session.add(user)
            _db.session.flush()

            account = Account(user_id=user.user_id, username="testmodel@unit.com", role="user")
            account.set_password("securepass123")
            _db.session.add(account)
            _db.session.commit()

            assert account.check_password("securepass123") is True
            assert account.check_password("wrongpass") is False

    def test_document_code_format(self, app):
        """generate_document_code should match DOC{MMDDYYYY}{HHmmss}{NNN}."""
        with app.app_context():
            from app.models import generate_document_code
            code = generate_document_code()
            assert code.startswith("DOC")
            assert len(code) == 3 + 14 + 3   # DOC + timestamp (14) + seq (3)

    def test_sla_info_returns_expected_keys(self, app):
        """Document.sla_info() should return all required fields."""
        with app.app_context():
            from app.models import Document, DocumentType
            dt = DocumentType.query.first()
            if not dt:
                pytest.skip("No document type seeded")
            doc = Document(
                document_code="TESTUNIT001",
                title="Unit Test Document",
                document_type_id=dt.document_type_id,
                status="Request for PR",
            )
            info = doc.sla_info()
            for key in ("sla_minutes", "elapsed_minutes", "pct", "tier", "tier_label", "hours_left"):
                assert key in info, f"Missing key: {key}"

    def test_audit_log_write(self, app):
        """log_audit should write an AuditLog row without crashing."""
        with app.app_context():
            from app.models import log_audit, AuditLog, AuditAction
            before = AuditLog.query.count()
            # Simulate outside of request context (no current_user) — should not raise
            log_audit(AuditAction.CREATE_DOC, document_code="TESTUNIT001",
                      details="Unit test audit write")
            after = AuditLog.query.count()
            assert after == before + 1


# ── Security tests ────────────────────────────────────────────────────────────

class TestSecurity:
    def test_security_logs_requires_admin(self, client):
        """Security audit log page should be inaccessible to anonymous users."""
        r = client.get("/security_logs")
        assert r.status_code in (301, 302)

    def test_delete_document_requires_admin(self, client):
        """DELETE endpoint should redirect anonymous users."""
        r = client.post("/documents/delete/1")
        assert r.status_code in (301, 302)

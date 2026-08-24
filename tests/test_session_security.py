"""会话 Cookie 标志，以及离职/冻结后立刻作废已登录会话。"""

from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Customer, CustomerKind, User


def _session_cookie_header(response) -> str:
    for header in response.headers.getlist("Set-Cookie"):
        if header.lower().startswith("session="):
            return header
    return ""


def test_development_session_cookie_is_httponly_samesite_lax_not_secure():
    app = create_app()
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    client = app.test_client()
    response = client.get("/auth/login")
    cookie = _session_cookie_header(response)
    assert cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie


def test_production_session_cookie_sets_secure_and_samesite(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    app = create_app()
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SECURE"] is True
    client = app.test_client()
    response = client.get("/auth/login")
    cookie = _session_cookie_header(response)
    assert cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "HttpOnly" in cookie


def test_staff_session_ends_immediately_after_deactivate():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_kick_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_kick_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        staff_id = staff.id

    staff_client = app.test_client()
    admin_client = app.test_client()
    staff_login = staff_client.post(
        "/auth/login",
        data={"username": staff_name, "password": "secret"},
        follow_redirects=True,
    )
    assert staff_login.status_code == 200
    assert "员工工作台".encode("utf-8") in staff_login.data

    admin_client.post(
        "/auth/login",
        data={"username": admin_name, "password": "secret"},
        follow_redirects=True,
    )
    kicked = admin_client.post(
        "/admin/accounts/bulk-delete",
        json={"ids": [staff_id]},
        headers={"Content-Type": "application/json"},
    )
    assert kicked.get_json()["ok"] is True

    with staff_client.session_transaction() as sess:
        assert sess.get("_user_id") == str(staff_id)

    bounced = staff_client.get("/staff/dashboard", follow_redirects=False)
    assert bounced.status_code in (302, 303)
    assert "/auth/login" in bounced.headers.get("Location", "")

    with staff_client.session_transaction() as sess:
        assert "_user_id" not in sess

    login_page = staff_client.get("/auth/login")
    assert "员工账号已离职".encode("utf-8") in login_page.data

    still_blocked = staff_client.get("/staff/dashboard", follow_redirects=False)
    assert still_blocked.status_code in (302, 303)


def test_client_session_ends_immediately_after_freeze():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_freeze_{suffix}", role="admin")
        admin.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_freeze_c_{suffix}")
        db.session.add_all([admin, customer])
        db.session.flush()
        client_user = User(
            username=f"_client_freeze_{suffix}",
            role="client",
            customer_id=customer.id,
        )
        client_user.set_password("secret")
        db.session.add(client_user)
        db.session.commit()
        admin_name = admin.username
        client_name = client_user.username
        client_id = client_user.id

    client_http = app.test_client()
    admin_http = app.test_client()
    entered = client_http.post(
        "/auth/login",
        data={"username": client_name, "password": "secret"},
        follow_redirects=True,
    )
    assert entered.status_code == 200
    assert "客户工作台".encode("utf-8") in entered.data

    admin_http.post(
        "/auth/login",
        data={"username": admin_name, "password": "secret"},
        follow_redirects=True,
    )
    frozen = admin_http.post(
        "/admin/accounts/bulk-delete",
        json={"ids": [client_id]},
        headers={"Content-Type": "application/json"},
    )
    assert frozen.get_json()["ok"] is True

    bounced = client_http.get("/client/dashboard", follow_redirects=True)
    assert bounced.status_code == 200
    assert "客户账号已冻结".encode("utf-8") in bounced.data
    assert "客户工作台".encode("utf-8") not in bounced.data

    with client_http.session_transaction() as sess:
        assert "_user_id" not in sess

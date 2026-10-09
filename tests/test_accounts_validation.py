"""账号分发：手机号格式、重复占用与登录分流。"""

from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Customer, CustomerKind, User


def _phone(suffix: str, lead: str = "138") -> str:
    return f"{lead}{int(suffix[:8], 16) % 10**8:08d}"


def test_account_create_rejects_duplicate_and_invalid_phone():
    app = create_app()
    suffix = uuid4().hex[:8]
    phone = _phone(suffix)
    with app.app_context():
        admin = User(username=f"_accv_admin_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.commit()
        admin_name = admin.username

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    first = client.post(
        "/admin/accounts",
        data={
            "username": f"_accv_staff_a_{suffix}",
            "password": "secret123",
            "role": "staff",
            "phone": phone,
        },
        follow_redirects=True,
    )
    assert first.status_code == 200

    invalid = client.post(
        "/admin/accounts",
        data={
            "username": f"_accv_staff_bad_{suffix}",
            "password": "secret123",
            "role": "staff",
            "phone": "12345",
        },
        follow_redirects=False,
    )
    assert invalid.status_code == 200
    invalid_html = invalid.get_data(as_text=True)
    assert "手机号格式不正确" in invalid_html
    assert f'value="_accv_staff_bad_{suffix}"' in invalid_html

    taken = client.post(
        "/admin/accounts",
        data={
            "username": f"_accv_staff_b_{suffix}",
            "password": "secret123",
            "role": "staff",
            "phone": phone,
        },
        follow_redirects=False,
    )
    assert taken.status_code == 200
    taken_html = taken.get_data(as_text=True)
    assert "该手机号已被其他账号使用" in taken_html
    assert f'value="_accv_staff_b_{suffix}"' in taken_html
    assert f'value="{phone}"' in taken_html
    with app.app_context():
        assert User.query.filter_by(username=f"_accv_staff_b_{suffix}").first() is None


def test_account_update_phone_rejects_duplicate():
    app = create_app()
    suffix = uuid4().hex[:8]
    phone_a = _phone(suffix, "139")
    phone_b = _phone(suffix, "137")
    with app.app_context():
        admin = User(username=f"_accu_admin_{suffix}", role="admin")
        admin.set_password("secret")
        staff_a = User(username=f"_accu_a_{suffix}", role="staff", phone=phone_a)
        staff_a.set_password("secret")
        staff_b = User(username=f"_accu_b_{suffix}", role="staff", phone=phone_b)
        staff_b.set_password("secret")
        db.session.add_all([admin, staff_a, staff_b])
        db.session.commit()
        admin_name = admin.username
        staff_b_id = staff_b.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    blocked = client.post(
        "/admin/accounts",
        data={
            "form_action": "update_phone",
            "user_id": str(staff_b_id),
            "phone": phone_a,
        },
        follow_redirects=False,
    )
    assert blocked.status_code in (302, 303)
    assert "该手机号已被其他账号使用" in unquote(blocked.headers.get("Location", ""))
    with app.app_context():
        assert db.session.get(User, staff_b_id).phone == phone_b

    cleared = client.post(
        "/admin/accounts",
        data={
            "form_action": "update_phone",
            "user_id": str(staff_b_id),
            "phone": "",
        },
        follow_redirects=False,
    )
    assert cleared.status_code in (302, 303)
    assert "手机号已更新" in unquote(cleared.headers.get("Location", ""))
    with app.app_context():
        assert db.session.get(User, staff_b_id).phone is None


def test_login_get_redirects_when_already_authenticated():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_auth_admin_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.commit()
        admin_name = admin.username

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    again = client.get("/auth/login", follow_redirects=False)
    assert again.status_code in (302, 303)
    assert "/admin/dashboard" in again.headers.get("Location", "")


def test_frozen_client_login_shows_freeze_message():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_freeze_login_c_{suffix}")
        db.session.add(customer)
        db.session.flush()
        client_user = User(
            username=f"_freeze_login_{suffix}",
            role="client",
            customer_id=customer.id,
        )
        client_user.set_password("secret")
        client_user.is_active = False
        db.session.add(client_user)
        db.session.commit()
        username = client_user.username

    http = app.test_client()
    page = http.post(
        "/auth/login",
        data={"username": username, "password": "secret"},
        follow_redirects=False,
    )
    assert page.status_code == 200
    assert "客户账号已冻结，请联系管理员。".encode() in page.data
    assert b"qy_toast" not in page.data

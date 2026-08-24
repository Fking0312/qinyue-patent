"""登录失败次数限制：按账号锁定，并限制同一 IP 的失败次数。"""

from datetime import timedelta
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.login_throttle import utc_now
from app.models import LoginThrottle, User


def _make_staff(username: str, password: str = "secret") -> User:
    user = User(username=username, role="staff", staff_function=User.STAFF_FUNCTION_WRITER)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _post_login(client, username: str, password: str, ip: str, follow_redirects: bool = True):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=follow_redirects,
        environ_overrides={"REMOTE_ADDR": ip},
    )


def test_login_lockout_after_max_failures_blocks_even_correct_password():
    app = create_app()
    app.config["LOGIN_MAX_FAILURES"] = 3
    app.config["LOGIN_IP_MAX_FAILURES"] = 50
    username = f"_lock_{uuid4().hex[:8]}"
    ip = "203.0.113.10"
    with app.app_context():
        _make_staff(username)

    client = app.test_client()
    for _ in range(2):
        response = _post_login(client, username, "wrong", ip)
        assert response.status_code == 200
        assert "账号/手机号或密码错误".encode("utf-8") in response.data

    third = _post_login(client, username, "wrong", ip)
    assert third.status_code == 429
    assert "登录失败次数过多".encode("utf-8") in third.data

    blocked = _post_login(client, username, "secret", ip)
    assert blocked.status_code == 429
    assert "登录失败次数过多".encode("utf-8") in blocked.data
    assert "员工工作台".encode("utf-8") not in blocked.data


def test_login_lockout_expires_and_correct_password_works():
    app = create_app()
    app.config["LOGIN_MAX_FAILURES"] = 2
    app.config["LOGIN_IP_MAX_FAILURES"] = 50
    username = f"_unlock_{uuid4().hex[:8]}"
    ip = "203.0.113.11"
    with app.app_context():
        _make_staff(username)

    client = app.test_client()
    _post_login(client, username, "wrong", ip)
    locked = _post_login(client, username, "wrong", ip)
    assert locked.status_code == 429

    with app.app_context():
        row = LoginThrottle.query.filter_by(scope=LoginThrottle.SCOPE_LOGIN, key=username).first()
        assert row is not None
        assert row.locked_until is not None
        row.locked_until = utc_now() - timedelta(seconds=1)
        db.session.commit()

    recovered = _post_login(client, username, "secret", ip)
    assert recovered.status_code == 200
    assert "员工工作台".encode("utf-8") in recovered.data


def test_login_success_clears_account_failures_not_other_accounts():
    app = create_app()
    app.config["LOGIN_MAX_FAILURES"] = 3
    app.config["LOGIN_IP_MAX_FAILURES"] = 50
    suffix = uuid4().hex[:8]
    user_a = f"_clr_a_{suffix}"
    user_b = f"_clr_b_{suffix}"
    ip = "203.0.113.12"
    with app.app_context():
        _make_staff(user_a)
        _make_staff(user_b)

    client = app.test_client()
    _post_login(client, user_a, "wrong", ip)
    _post_login(client, user_a, "wrong", ip)
    ok = _post_login(client, user_a, "secret", ip)
    assert ok.status_code == 200

    client.get("/auth/logout", follow_redirects=True)
    # 成功登录后该账号计数已清，可再失败两次而不锁定。
    first = _post_login(client, user_a, "wrong", ip)
    second = _post_login(client, user_a, "wrong", ip)
    assert first.status_code == 200
    assert second.status_code == 200
    assert "登录失败次数过多".encode("utf-8") not in second.data

    _post_login(client, user_b, "wrong", ip)
    _post_login(client, user_b, "wrong", ip)
    other_locked = _post_login(client, user_b, "wrong", ip)
    assert other_locked.status_code == 429


def test_login_ip_lockout_blocks_other_accounts_from_same_ip():
    app = create_app()
    app.config["LOGIN_MAX_FAILURES"] = 50
    app.config["LOGIN_IP_MAX_FAILURES"] = 3
    suffix = uuid4().hex[:8]
    names = [f"_ip_{suffix}_{i}" for i in range(4)]
    ip = "203.0.113.13"
    with app.app_context():
        for name in names:
            _make_staff(name)

    client = app.test_client()
    _post_login(client, names[0], "wrong", ip)
    _post_login(client, names[1], "wrong", ip)
    locked = _post_login(client, names[2], "wrong", ip)
    assert locked.status_code == 429

    blocked_other = _post_login(client, names[3], "secret", ip)
    assert blocked_other.status_code == 429
    assert "登录失败次数过多".encode("utf-8") in blocked_other.data


def test_other_ip_can_still_login_when_one_ip_is_locked():
    app = create_app()
    app.config["LOGIN_MAX_FAILURES"] = 50
    app.config["LOGIN_IP_MAX_FAILURES"] = 2
    username = f"_ipok_{uuid4().hex[:8]}"
    with app.app_context():
        _make_staff(username)

    client = app.test_client()
    _post_login(client, username, "wrong", "203.0.113.14")
    locked = _post_login(client, username, "wrong", "203.0.113.14")
    assert locked.status_code == 429

    ok = _post_login(client, username, "secret", "198.51.100.20")
    assert ok.status_code == 200
    assert "员工工作台".encode("utf-8") in ok.data

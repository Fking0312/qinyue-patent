"""生产密钥校验，以及演示脚本的生产拒绝 / 不改已有口令。"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from app import create_app
from app.extensions import db
from app.models import User
from config import validate_runtime_secret


SEED_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_data.py"
_STRONG_SECRET = "x" * 32


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo_data_under_test", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_validate_runtime_secret_allows_dev_default_key():
    validate_runtime_secret(env="development", secret="dev-secret-key", wsgi_server=False)


def test_validate_runtime_secret_rejects_production_default_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_runtime_secret(env="production", secret="dev-secret-key", wsgi_server=False)


def test_validate_runtime_secret_rejects_short_production_key():
    with pytest.raises(RuntimeError, match="32"):
        validate_runtime_secret(env="production", secret="a" * 31, wsgi_server=False)


def test_validate_runtime_secret_accepts_long_production_key():
    validate_runtime_secret(env="production", secret=_STRONG_SECRET, wsgi_server=False)


def test_validate_runtime_secret_rejects_wsgi_without_production_env():
    with pytest.raises(RuntimeError, match="FLASK_ENV=production"):
        validate_runtime_secret(env="development", secret=_STRONG_SECRET, wsgi_server=True)


def test_create_app_rejects_production_default_secret(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


def test_create_app_rejects_wsgi_without_production_env(monkeypatch):
    monkeypatch.setenv("SERVER_SOFTWARE", "gunicorn/21.2.0")
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FLASK_ENV=production"):
        create_app()


def test_create_app_accepts_production_with_strong_secret(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", _STRONG_SECRET)
    app = create_app()
    assert app.config["ENV"] == "production"
    assert app.config["SECRET_KEY"] == _STRONG_SECRET


def test_seed_demo_data_refuses_production():
    seed = _load_seed_module()
    with pytest.raises(SystemExit) as caught:
        seed.refuse_if_production("production")
    assert caught.value.code == 1
    seed.refuse_if_production("development")


def test_seed_demo_data_does_not_reset_existing_password():
    seed = _load_seed_module()
    app = create_app()
    username = f"_seed_keep_{uuid4().hex[:8]}"
    with app.app_context():
        existing = User(
            username=username,
            role="staff",
            staff_kind=User.STAFF_KIND_FORMAL,
            staff_function=User.STAFF_FUNCTION_PROCESS,
        )
        existing.set_password("keep-me-secret")
        db.session.add(existing)
        db.session.commit()

        seed._ensure_user(
            username,
            "staff",
            staff_kind=User.STAFF_KIND_FORMAL,
            staff_function=User.STAFF_FUNCTION_WRITER,
        )
        db.session.commit()

        refreshed = User.query.filter_by(username=username).first()
        assert refreshed is not None
        assert refreshed.check_password("keep-me-secret")
        assert not refreshed.check_password("123456")
        assert refreshed.staff_function == User.STAFF_FUNCTION_WRITER


def test_seed_demo_data_sets_password_only_on_create():
    seed = _load_seed_module()
    app = create_app()
    username = f"_seed_new_{uuid4().hex[:8]}"
    with app.app_context():
        created = seed._ensure_user(
            username,
            "staff",
            staff_kind=User.STAFF_KIND_FORMAL,
            staff_function=User.STAFF_FUNCTION_BUSINESS,
        )
        db.session.commit()
        assert created.check_password("123456")
        assert created.staff_function == User.STAFF_FUNCTION_BUSINESS

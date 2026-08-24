import os
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 仅本地 flask / pytest 可回落；生产与 Gunicorn 等 WSGI 进程必须提供强随机密钥。
_DEFAULT_SECRET_KEY = "dev-secret-key"
_INSECURE_SECRET_KEYS = frozenset(
    {
        "",
        _DEFAULT_SECRET_KEY,
        "change-me",
        "changeme",
        "secret",
        "your-secret-key",
    }
)
MIN_PRODUCTION_SECRET_LENGTH = 32
_WSGI_SERVER_PREFIXES = ("gunicorn", "uwsgi", "waitress", "hypercorn")


def current_flask_env() -> str:
    """运行时读取 FLASK_ENV，避免 Config 类在 import 时冻结过期值。"""
    return os.getenv("FLASK_ENV", "development").lower()


def current_secret_key() -> str:
    raw = os.getenv("SECRET_KEY")
    if raw is None or not str(raw).strip():
        return _DEFAULT_SECRET_KEY
    return str(raw)


def is_insecure_secret_key(secret: str | None) -> bool:
    value = "" if secret is None else str(secret).strip()
    return (not value) or value in _INSECURE_SECRET_KEYS


def is_production_ready_secret(secret: str | None) -> bool:
    if is_insecure_secret_key(secret):
        return False
    return len(str(secret).strip()) >= MIN_PRODUCTION_SECRET_LENGTH


def running_as_wsgi_server() -> bool:
    """Gunicorn / uWSGI 等部署进程；本地 flask run 与 pytest 不会命中。"""
    software = (os.getenv("SERVER_SOFTWARE") or "").lower()
    if any(software.startswith(prefix) for prefix in _WSGI_SERVER_PREFIXES):
        return True
    # pytest 可能间接 import gunicorn；勿把测试套件当成生产 WSGI。
    # 测试若要模拟部署进程，应设置 SERVER_SOFTWARE。
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return False
    return any(name in sys.modules for name in _WSGI_SERVER_PREFIXES)


def cookie_secure_enabled(env: str) -> bool:
    """生产默认 Secure；本地 HTTP 可关。QY_SESSION_COOKIE_SECURE=1 可在开发启用。"""
    raw = os.getenv("QY_SESSION_COOKIE_SECURE")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes")
    return env == "production"


def apply_session_cookie_policy(app) -> None:
    """会话与 Remember-Me Cookie：HttpOnly + SameSite=Lax；生产加 Secure。"""
    env = str(app.config.get("ENV") or current_flask_env())
    secure = cookie_secure_enabled(env)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = secure
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = secure
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"


def env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        return default


def validate_runtime_secret(*, env: str, secret: str, wsgi_server: bool | None = None) -> None:
    """生产或 WSGI 部署必须 FLASK_ENV=production 且 SECRET_KEY 至少 32 位随机串。"""
    is_wsgi = running_as_wsgi_server() if wsgi_server is None else wsgi_server
    if env != "production" and not is_wsgi:
        return
    if env != "production":
        raise RuntimeError(
            "检测到 Gunicorn/uWSGI 等服务进程，必须设置 FLASK_ENV=production，"
            "并提供至少 32 位随机 SECRET_KEY（例如 openssl rand -hex 32）。"
            "当前未按生产配置启动，拒绝使用开发默认密钥。"
        )
    if not is_production_ready_secret(secret):
        raise RuntimeError(
            "生产环境必须在环境中设置强随机 SECRET_KEY（例如 openssl rand -hex 32），"
            f"长度至少 {MIN_PRODUCTION_SECRET_LENGTH} 字符，且不得使用仓库默认值或常见占位符。"
            "当前 FLASK_ENV=production。"
        )


class Config:
    # Flask / Werkzeug 约定：development | production（未设置时按开发处理，便于本地与测试）。
    ENV = os.getenv("FLASK_ENV", "development").lower()
    # 开发默认密钥；生产 / WSGI 启动时由 validate_runtime_secret 拒绝弱值。
    SECRET_KEY = os.getenv("SECRET_KEY", _DEFAULT_SECRET_KEY)
    # 测试可通过 QY_DATABASE_URI 指向独立数据库，避免污染本地业务数据。
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "QY_DATABASE_URI",
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "patent.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Set FLASK_DEBUG=1 (or true) to enable legacy SQLite column fallback on startup (see create_app).
    DEBUG = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    # CSRF：默认开启；测试可通过 WTF_CSRF_ENABLED=0 关闭
    WTF_CSRF_ENABLED = os.getenv("WTF_CSRF_ENABLED", "1").lower() not in ("0", "false", "no")
    # SPA 长会话/可能久未刷新页面 → 不限定 token 有效期
    WTF_CSRF_TIME_LIMIT = None

    # 案件材料：单文件大小（默认 500MB，便于上传视频等大附件）；可用 QY_CASE_MATERIAL_MAX_MB 覆盖。
    _material_mb = max(1, int(os.getenv("QY_CASE_MATERIAL_MAX_MB", "500")))
    CASE_MATERIAL_MAX_FILE_BYTES = _material_mb * 1024 * 1024
    # 整站 POST 体上限：默认放到单文件的 4 倍，便于一次建案件上传多个附件；可用 MAX_CONTENT_LENGTH_MB 覆盖。
    _body_mb = max(_material_mb + 2, int(os.getenv("MAX_CONTENT_LENGTH_MB", str(_material_mb * 4))))
    MAX_CONTENT_LENGTH = _body_mb * 1024 * 1024

    # 登录失败限制：同一账号 15 分钟内 5 次失败后锁定 15 分钟；同一 IP 20 次。
    LOGIN_MAX_FAILURES = env_positive_int("QY_LOGIN_MAX_FAILURES", 5)
    LOGIN_FAILURE_WINDOW_MINUTES = env_positive_int("QY_LOGIN_FAILURE_WINDOW_MINUTES", 15)
    LOGIN_LOCKOUT_MINUTES = env_positive_int("QY_LOGIN_LOCKOUT_MINUTES", 15)
    LOGIN_IP_MAX_FAILURES = env_positive_int("QY_LOGIN_IP_MAX_FAILURES", 20)

    # 会话 Cookie：始终 HttpOnly + SameSite=Lax；Secure 由 create_app 按环境写入。
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = False

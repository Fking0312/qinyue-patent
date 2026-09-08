import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 生产部署必须设置 FLASK_ENV=production 且通过环境变量提供强随机 SECRET_KEY（见 create_app 校验）。
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


class Config:
    # Flask / Werkzeug 约定：development | production（未设置时按开发处理，便于本地与测试）。
    ENV = os.getenv("FLASK_ENV", "development").lower()
    # 开发默认密钥；生产必须设置环境变量 SECRET_KEY（且不得为已知弱值）。
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

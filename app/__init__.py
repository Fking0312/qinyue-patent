"""Flask 应用工厂：注册扩展、蓝图、模板过滤器与少量 CLI 命令。

本模块只负责装配（无业务逻辑）：
- `create_app` 是项目唯一入口，被 `wsgi.py / app.py` 与测试调用。
- 仅在 SQLite 调试环境下做 ALTER 兜底，生产以 Alembic 迁移为准。
"""

import os
import hashlib
from pathlib import Path

import click
from werkzeug.exceptions import RequestEntityTooLarge

from flask import Flask, jsonify, redirect, render_template, request, url_for
from sqlalchemy import text
from flask_login import current_user

from config import (
    Config,
    apply_session_cookie_policy,
    current_flask_env,
    current_secret_key,
    env_positive_int,
    validate_runtime_secret,
)
from app.extensions import csrf, db, login_manager, migrate


def create_app():
    """构造并返回配置好的 Flask 应用实例（含扩展、蓝图、CLI、错误处理）。"""

    def _ensure_case_columns_sqlite() -> None:
        """
        Development-only fallback: ALTER TABLE for legacy SQLite DBs that skipped `flask db upgrade`.
        Canonical schema is defined by Alembic (e.g. f1e2d3c4b5a6_case_project_type_and_dates, d1a2b3c4d5e6).
        """
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            return
        required_columns = {
            "project_type": "VARCHAR(80)",
            "case_type_code": "VARCHAR(80)",
            "business_owner_id": "INTEGER",
            "business_owner_label": "VARCHAR(120)",
            "intake_owner_id": "INTEGER",
            "intake_owner_label": "VARCHAR(120)",
            "order_at": "DATETIME",
            "expected_return_at": "DATETIME",
            "actual_return_at": "DATETIME",
            "case_note": "TEXT",
            "material_upload_port": "VARCHAR(255)",
            "patent_application_no": "VARCHAR(100)",
            "rejected_at": "DATETIME",
            "reject_note": "TEXT",
            # 归属必须有默认值，否则老库里的存量案件全成 NULL，读出来无法判断归属。
            "attribution": "VARCHAR(20) NOT NULL DEFAULT 'customer'",
        }
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(cases)")).fetchall()
            existing = {row[1] for row in rows}
            for col_name, col_type in required_columns.items():
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE cases ADD COLUMN {col_name} {col_type}"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cases_case_type_code "
                    "ON cases (case_type_code)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cases_attribution "
                    "ON cases (attribution)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cases_intake_owner_id "
                    "ON cases (intake_owner_id)"
                )
            )

    def _ensure_customer_columns_sqlite() -> None:
        """
        Development-only fallback for legacy SQLite；正式环境请执行迁移（b8e4a1c2d3f4、c9f5b2d3e4a5 等）。
        """
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            return
        extra = {
            "created_by_id": "INTEGER",
            "contact_name": "VARCHAR(120)",
            "contact_phone": "VARCHAR(40)",
            "fee_standard": "TEXT",
        }
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(customers)")).fetchall()
            existing = {row[1] for row in rows}
            for col_name, col_type in extra.items():
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE customers ADD COLUMN {col_name} {col_type}"))

    def _ensure_user_columns_sqlite() -> None:
        """开发兜底：补 users 缺失列（phone / is_active / staff_kind / staff_function / review_inbox_seen_at），不做业务回填。"""
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            return
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
            existing = {row[1] for row in rows}
            if "nickname" in existing and "phone" not in existing:
                conn.execute(text("ALTER TABLE users RENAME COLUMN nickname TO phone"))
                existing = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
            if "phone" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(20)"))
            if "is_active" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
            if "staff_kind" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN staff_kind VARCHAR(20)"))
            if "staff_function" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN staff_function VARCHAR(40)"))
            if "review_inbox_seen_at" not in existing:
                conn.execute(text("ALTER TABLE users ADD COLUMN review_inbox_seen_at DATETIME"))
            index_rows = conn.execute(text("PRAGMA index_list(users)")).fetchall()
            index_names = {row[1] for row in index_rows}
            if "uq_users_phone" not in index_names:
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone ON users (phone)"))

    def _ensure_review_log_columns_sqlite() -> None:
        """SQLite 兜底：审核打回通知的接收人与已读时间。"""
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            return
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(case_review_logs)")).fetchall()
            existing = {row[1] for row in rows}
            if "recipient_id" not in existing:
                conn.execute(text("ALTER TABLE case_review_logs ADD COLUMN recipient_id INTEGER"))
            if "read_at" not in existing:
                conn.execute(text("ALTER TABLE case_review_logs ADD COLUMN read_at DATETIME"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_case_review_logs_recipient_id "
                    "ON case_review_logs (recipient_id)"
                )
            )

    def _ensure_trace_label_columns_sqlite() -> None:
        """SQLite 兜底：案件留痕姓名快照。生产请跑 Alembic t8c9d0e1f2a3。"""
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            return
        extras = {
            "cases": {
                "business_owner_label": "VARCHAR(120)",
                "intake_owner_id": "INTEGER",
                "intake_owner_label": "VARCHAR(120)",
            },
            "tasks": {"assignee_label": "VARCHAR(120)"},
            "case_review_logs": {
                "operator_label": "VARCHAR(120)",
                "recipient_label": "VARCHAR(120)",
            },
            "case_materials": {
                "uploaded_by_label": "VARCHAR(120)",
                "uploaded_by_role": "VARCHAR(20)",
            },
            "case_material_download_logs": {"operator_label": "VARCHAR(120)"},
        }
        with db.engine.begin() as conn:
            for table, columns in extras.items():
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                existing = {row[1] for row in rows}
                if not existing:
                    continue
                for col_name, col_type in columns.items():
                    if col_name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cases_intake_owner_id "
                    "ON cases (intake_owner_id)"
                )
            )

    configured_instance_path = os.getenv("QY_INSTANCE_PATH", "").strip()
    if configured_instance_path:
        app = Flask(
            __name__,
            instance_relative_config=True,
            instance_path=configured_instance_path,
        )
    else:
        app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config["ENV"] = current_flask_env()
    app.config["SECRET_KEY"] = current_secret_key()
    app.config["DEBUG"] = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.debug = bool(app.config.get("DEBUG"))
    validate_runtime_secret(env=str(app.config["ENV"]), secret=str(app.config["SECRET_KEY"]))
    app.config["LOGIN_MAX_FAILURES"] = env_positive_int("QY_LOGIN_MAX_FAILURES", 5)
    app.config["LOGIN_FAILURE_WINDOW_MINUTES"] = env_positive_int(
        "QY_LOGIN_FAILURE_WINDOW_MINUTES", 15
    )
    app.config["LOGIN_LOCKOUT_MINUTES"] = env_positive_int("QY_LOGIN_LOCKOUT_MINUTES", 15)
    app.config["LOGIN_IP_MAX_FAILURES"] = env_positive_int("QY_LOGIN_IP_MAX_FAILURES", 20)
    apply_session_cookie_policy(app)

    def _build_deploy_version() -> str:
        """基于应用代码和前端资源生成稳定版本；各 Gunicorn worker 结果一致。"""
        digest = hashlib.sha256()
        app_root = Path(app.root_path)
        tracked_suffixes = {".py", ".html", ".css", ".js"}
        for file_path in sorted(
            path
            for path in app_root.rglob("*")
            if path.is_file() and path.suffix.lower() in tracked_suffixes
        ):
            try:
                stat = file_path.stat()
            except OSError:
                continue
            digest.update(str(file_path.relative_to(app_root)).encode("utf-8"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(str(stat.st_size).encode("ascii"))
        return digest.hexdigest()[:16]

    app.config["QY_DEPLOY_VERSION"] = _build_deploy_version()

    @app.template_filter("effective_task_due")
    def _effective_task_due_filter(task):
        """模板过滤器：返回任务的有效截止时间（缺省继承项目）。"""
        from app.workflow import effective_task_due_at

        return effective_task_due_at(task)

    @app.template_filter("qy_task_phase_label")
    def _qy_task_phase_label_filter(phase: str) -> str:
        """模板过滤器：把 phase_status 翻译为人类可读中文标签。"""
        from app.workflow import task_phase_label

        return task_phase_label(phase)

    @app.template_filter("qy_task_phase_tone")
    def _qy_task_phase_tone_filter(phase: str) -> str:
        from app.list_ui import task_phase_tone

        return task_phase_tone(phase)

    @app.template_filter("qy_task_urgency_row_class")
    def _qy_task_urgency_row_class_filter(task) -> str:
        from app.list_ui import task_urgency_row_class

        return task_urgency_row_class(task)

    @app.template_filter("qy_effective_task_phase")
    def _qy_effective_task_phase_filter(task) -> str:
        """只读计算任务展示阶段，不在页面渲染期间写数据库。"""
        from app.workflow import effective_task_phase

        return effective_task_phase(task) if task is not None else ""

    @app.template_filter("qy_format_dt")
    def _qy_format_dt_filter(value):
        """模板过滤器：数据库 UTC 时间统一转换为北京时间显示。"""
        from datetime import timedelta, timezone

        if value is None:
            return "—"
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    @app.template_filter("qy_relative_dt")
    def _qy_relative_dt_filter(value):
        from app.time_display import relative_datetime_text

        return relative_datetime_text(value)

    @app.template_filter("qy_user_label")
    def _qy_user_label_filter(user):
        """模板过滤器：用户展示名（手机号 + 账号名）。"""
        if user is None:
            return "—"
        return user.display_label

    @app.template_filter("qy_trace_label")
    def _qy_trace_label_filter(user, snapshot=None):
        """模板过滤器：账号还在用实时姓名，否则用写入时冻结的快照。"""
        from app.case_trace import display_trace

        return display_trace(user, snapshot)

    @app.context_processor
    def _inject_static_asset_version():
        """静态资源版本号：部署后使用新 URL，避开浏览器旧缓存。"""
        return {"static_asset_version": app.config["QY_DEPLOY_VERSION"]}

    # Initialize extensions.
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    # Ensure models are imported so Flask-Login callbacks are registered.
    from app import models  # noqa: F401
    from app.session_security import expire_inactive_sessions

    app.before_request(expire_inactive_sessions)

    # Register blueprints.
    from app.blueprints.admin import admin_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.client import client_bp
    from app.blueprints.staff import staff_bp

    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(staff_bp, url_prefix="/staff")
    app.register_blueprint(client_bp, url_prefix="/client")
    app.register_blueprint(auth_bp, url_prefix="/auth")

    @app.route("/")
    def index():
        """根路径：已登录用户跳转角色仪表盘，未登录显示落地页。"""
        if current_user.is_authenticated:
            return redirect(url_for(current_user.home_endpoint))
        return render_template("index.html")

    @app.route("/system/deploy-version")
    def deploy_version():
        """供已打开页面检测新部署；响应本身禁止缓存。"""
        response = jsonify(version=app.config["QY_DEPLOY_VERSION"])
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @app.after_request
    def disable_dynamic_html_cache(response):
        """动态 HTML 始终重新验证，静态文件则由版本化 URL 安全缓存。"""
        if request.endpoint != "static" and response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.errorhandler(403)
    def forbidden(_error):
        """403 错误处理：渲染统一的禁止访问页面。"""
        return render_template("errors/403.html"), 403

    @app.errorhandler(RequestEntityTooLarge)
    def request_entity_too_large(_error):
        """413 错误处理：上传体过大时给出统一提示页。"""
        return render_template("errors/413.html"), 413

    # Beginner-friendly setup: auto-create tables in SQLite if missing.
    with app.app_context():
        db.create_all()
        # 仅开发兜底：未跑 Alembic 时的 ALTER；生产应依赖 `flask db upgrade`
        if app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            _ensure_user_columns_sqlite()
            _ensure_review_log_columns_sqlite()
            _ensure_trace_label_columns_sqlite()
        if app.debug or app.config.get("DEBUG"):
            _ensure_case_columns_sqlite()
            _ensure_customer_columns_sqlite()

    @app.cli.command("init-demo-users")
    def init_demo_users():
        """
        创建 admin / staff / client 演示账号（仅本地或显式允许的环境）。
        - 生产环境（FLASK_ENV=production）一律拒绝。
        - 其他环境须满足：FLASK_DEBUG=1（Config.DEBUG）或设置 QY_ALLOW_INIT_DEMO_USERS=1。
        - 新建用户使用随机口令并打印一次；已存在用户不会改密。
        - 可选 QY_DEMO_FIXED_PASSWORD：新建账号时统一使用该口令（仅当你明确需要固定口令时）。
        """
        import os
        import secrets
        import sys

        from flask import current_app

        from app.models import Customer, CustomerKind, User

        cfg_env = str(current_app.config.get("ENV") or os.getenv("FLASK_ENV") or "development").lower()
        if cfg_env == "production":
            print("错误：生产环境禁止执行 init-demo-users。", file=sys.stderr)
            sys.exit(1)

        debug_ok = bool(current_app.debug or current_app.config.get("DEBUG"))
        allow_flag = os.getenv("QY_ALLOW_INIT_DEMO_USERS", "").lower() in ("1", "true", "yes")
        if not debug_ok and not allow_flag:
            print(
                "为降低弱口令风险：请在开发环境设置 FLASK_DEBUG=1，"
                "或显式设置环境变量 QY_ALLOW_INIT_DEMO_USERS=1 后再执行。",
                file=sys.stderr,
            )
            sys.exit(1)

        fixed_password = os.getenv("QY_DEMO_FIXED_PASSWORD", "").strip()

        db.create_all()
        demo_customer = Customer.query.filter_by(name="演示客户公司").first()
        if demo_customer is None:
            demo_customer = Customer(kind=CustomerKind.COMPANY, name="演示客户公司")
            db.session.add(demo_customer)
            db.session.flush()

        demo_users_spec = [
            ("admin", "admin"),
            ("staff", "staff"),
            ("client", "client"),
        ]
        created: list[tuple[str, str, str]] = []
        for username, role in demo_users_spec:
            password = fixed_password if fixed_password else secrets.token_urlsafe(14)
            user = User.query.filter_by(username=username).first()
            if user is None:
                user = User(username=username, role=role)
                user.set_password(password)
                if role == "client":
                    user.customer_id = demo_customer.id
                db.session.add(user)
                created.append((username, password, role))
            elif role == "client" and user.customer_id is None:
                user.customer_id = demo_customer.id
        db.session.commit()

        if created:
            print("已创建演示账号（请妥善保管，勿泄露或提交到版本库）：")
            for username, password, role in created:
                print(f"  {username} ({role}): {password}")
            if fixed_password:
                print("（口令来自环境变量 QY_DEMO_FIXED_PASSWORD）")
        else:
            print("演示账号已存在，未新建用户、未修改口令。删除对应用户后可重新生成随机口令。")

    @app.cli.command("reset-staff-client-passwords")
    def reset_staff_client_passwords():
        """
        将所有员工（staff）与客户（client）账号的登录口令重置为 123456。

        不会修改管理员（admin）账号。弱口令仅适用于演示/内网；生产环境须设置
        环境变量 QY_ALLOW_STAFF_CLIENT_PASSWORD_RESET=1 才允许执行。
        """
        import sys

        from flask import current_app

        from app.models import User

        cfg_env = str(current_app.config.get("ENV") or os.getenv("FLASK_ENV") or "development").lower()
        allow_prod = os.getenv("QY_ALLOW_STAFF_CLIENT_PASSWORD_RESET", "").lower() in ("1", "true", "yes")
        debug_ok = bool(current_app.debug or current_app.config.get("DEBUG"))
        if cfg_env == "production" and not allow_prod:
            print(
                "错误：生产环境默认禁止批量弱口令重置。"
                "确认风险后请设置 QY_ALLOW_STAFF_CLIENT_PASSWORD_RESET=1 再执行。",
                file=sys.stderr,
            )
            sys.exit(1)

        fixed = "123456"
        users = User.query.filter(User.role.in_(["staff", "client"])).order_by(User.id.asc()).all()
        for u in users:
            u.set_password(fixed)
        db.session.commit()
        print(f"已将 {len(users)} 个员工/客户账号的密码重置为 {fixed}（admin 未改动）。")

    @app.cli.command("refresh-task-overdue")
    def refresh_task_overdue_command():
        """根据截止时间刷新全部未结案任务的超期阶段（可配合系统定时任务）。"""
        from app.overdue_reminder import refresh_all_open_tasks_overdue

        n = refresh_all_open_tasks_overdue()
        print(f"已更新 {n} 条任务的超期状态。")

    @app.cli.command("backfill-actual-return-at")
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        default=False,
        help="实际提交补算；不加此参数时仅预览并回滚。",
    )
    def backfill_actual_return_at_command(apply_changes: bool):
        """仅补算实际返稿时间为空的已完成案件；默认 dry-run。"""
        from app.workflow import backfill_completed_case_actual_return_at

        updated = backfill_completed_case_actual_return_at()
        if not apply_changes:
            db.session.rollback()
            print(f"dry-run：可补算 {updated} 件，未写入数据库。")
            return
        db.session.commit()
        print(f"已补算 {updated} 件空缺的实际返稿时间；已有时间未改动。")

    @app.cli.command("backfill-project-codes")
    def backfill_project_codes_command():
        """仅为空项目编码补发 YYMM## 自动编号，不修改已有编码。"""
        from app.blueprints.admin.routes import backfill_empty_project_codes

        updated = backfill_empty_project_codes()
        if not updated:
            print("没有空项目编码，无需补发。")
            return
        print(f"已补发 {len(updated)} 个项目编码（已有编码未改动）：")
        for pid, name, code in updated:
            print(f"  #{pid} {code}  {name}")

    @app.cli.command("dedupe-download-logs")
    @click.option(
        "--window-seconds",
        default=5,
        show_default=True,
        type=int,
        help="同一下载人 + 同一文件，在该秒数内的多条留痕视为重复，仅保留最早一条。",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        default=False,
        help="仅统计将删除的数量，不实际删除。",
    )
    def dedupe_download_logs_command(window_seconds: int, dry_run: bool):
        """清理历史下载留痕中的重复记录（同一下载人/同一文件/极短时间内只保留一条）。"""
        from app.models import CaseMaterialDownloadLog

        window = max(0, int(window_seconds))
        logs = (
            CaseMaterialDownloadLog.query.order_by(
                CaseMaterialDownloadLog.material_id.asc(),
                CaseMaterialDownloadLog.operator_id.asc(),
                CaseMaterialDownloadLog.created_at.asc(),
                CaseMaterialDownloadLog.id.asc(),
            ).all()
        )

        to_delete = []
        anchor_key = None
        anchor_time = None
        for log in logs:
            key = (log.material_id, log.operator_id)
            ts = log.created_at
            if (
                key == anchor_key
                and anchor_time is not None
                and ts is not None
                and (ts - anchor_time).total_seconds() <= window
            ):
                # 与已保留的那条属于同一突发请求，判为重复
                to_delete.append(log)
            else:
                anchor_key = key
                anchor_time = ts

        print(
            f"扫描 {len(logs)} 条下载留痕，识别出 {len(to_delete)} 条重复记录"
            f"（窗口 {window}s，按下载人+文件分组）。"
        )
        if dry_run:
            print("dry-run：未做任何删除。")
            return
        if not to_delete:
            print("没有需要清理的重复记录。")
            return
        for log in to_delete:
            db.session.delete(log)
        db.session.commit()
        print(f"已删除 {len(to_delete)} 条重复下载留痕。")

    return app

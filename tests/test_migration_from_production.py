"""生产数据库迁移门禁：从线上 r6 头升级到当前唯一 head。"""

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from flask import Flask
from sqlalchemy import inspect, text

from app.extensions import db, migrate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_REVISION = "r6a7b8c9d0e1"
TARGET_REVISION = "z5d6e7f8a9b0"


def _migration_app(database_path: Path) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path.as_posix()}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    migrate.init_app(app, db, directory=str(PROJECT_ROOT / "migrations"))
    return app


def _alembic_config() -> AlembicConfig:
    config = AlembicConfig(str(PROJECT_ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def _bootstrap_legacy_users_table() -> None:
    """最早的迁移假定这些表已由旧版 create_all 建好。"""
    db.session.execute(
        text(
            """
            CREATE TABLE users (
                id INTEGER NOT NULL PRIMARY KEY,
                username VARCHAR(50) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL,
                created_at DATETIME
            )
            """
        )
    )
    db.session.execute(
        text(
            """
            CREATE TABLE case_review_logs (
                id INTEGER NOT NULL PRIMARY KEY,
                case_id INTEGER NOT NULL,
                operator_id INTEGER NOT NULL,
                action VARCHAR(20) NOT NULL,
                note TEXT,
                created_at DATETIME,
                FOREIGN KEY(case_id) REFERENCES cases (id),
                FOREIGN KEY(operator_id) REFERENCES users (id)
            )
            """
        )
    )
    db.session.execute(
        text(
            """
            CREATE TABLE case_materials (
                id INTEGER NOT NULL PRIMARY KEY,
                case_id INTEGER NOT NULL,
                uploaded_by_id INTEGER NOT NULL,
                original_name VARCHAR(255) NOT NULL,
                stored_name VARCHAR(255) NOT NULL UNIQUE,
                version_tag VARCHAR(20) NOT NULL,
                note TEXT,
                created_at DATETIME,
                FOREIGN KEY(case_id) REFERENCES cases (id),
                FOREIGN KEY(uploaded_by_id) REFERENCES users (id)
            )
            """
        )
    )
    db.session.execute(
        text(
            """
            CREATE TABLE case_material_download_logs (
                id INTEGER NOT NULL PRIMARY KEY,
                case_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                operator_id INTEGER NOT NULL,
                operator_role VARCHAR(20) NOT NULL,
                created_at DATETIME,
                FOREIGN KEY(case_id) REFERENCES cases (id),
                FOREIGN KEY(material_id) REFERENCES case_materials (id),
                FOREIGN KEY(operator_id) REFERENCES users (id)
            )
            """
        )
    )
    db.session.commit()


def _seed_production_shape() -> None:
    """在 r6 结构上放入会被职能回填和姓名快照迁移触及的数据。"""
    db.session.execute(
        text(
            """
            INSERT INTO users
                (id, username, password_hash, role, staff_function, phone, is_active)
            VALUES
                (1, 'legacy_writer', 'hash', 'staff', NULL, '13800000001', 1),
                (2, 'tagged_process', 'hash', 'staff', 'process', NULL, 1),
                (3, 'admin', 'hash', 'admin', NULL, NULL, 1)
            """
        )
    )
    db.session.execute(
        text(
            "INSERT INTO customers (id, kind, name) "
            "VALUES (1, 'company', '生产迁移演练客户')"
        )
    )
    db.session.execute(
        text(
            "INSERT INTO projects (id, customer_id, name) "
            "VALUES (1, 1, '生产迁移演练项目')"
        )
    )
    db.session.execute(
        text(
            """
            INSERT INTO cases
                (id, project_id, title, application_no, business_owner_id, attribution)
            VALUES
                (1, 1, '生产迁移演练案件', '260001', 1, 'customer')
            """
        )
    )
    db.session.execute(
        text(
            """
            INSERT INTO tasks (id, case_id, assignee_id, phase_status)
            VALUES (1, 1, 1, 'in_progress')
            """
        )
    )
    db.session.execute(
        text(
            """
            INSERT INTO case_review_logs
                (id, case_id, operator_id, recipient_id, action)
            VALUES
                (1, 1, 3, 1, 'assigned')
            """
        )
    )
    db.session.commit()


def test_upgrade_documented_production_revision_to_single_head(tmp_path):
    database_path = tmp_path / "production-r6-copy.db"
    app = _migration_app(database_path)
    config = _alembic_config()

    with app.app_context():
        _bootstrap_legacy_users_table()
        command.upgrade(config, PRODUCTION_REVISION)
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == PRODUCTION_REVISION

        _seed_production_shape()
        before = {
            table: db.session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in ("users", "customers", "projects", "cases", "tasks", "case_review_logs")
        }

        command.upgrade(config, "head")

        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == TARGET_REVISION
        after = {
            table: db.session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in before
        }
        assert after == before

        functions = dict(
            db.session.execute(
                text(
                    "SELECT username, staff_function FROM users "
                    "WHERE username IN ('legacy_writer', 'tagged_process')"
                )
            ).all()
        )
        assert functions == {
            "legacy_writer": "writer",
            "tagged_process": "process",
        }
        assert db.session.execute(
            text("SELECT business_owner_label FROM cases WHERE id = 1")
        ).scalar_one() == "13800000001（legacy_writer）"
        assert db.session.execute(
            text("SELECT assignee_label FROM tasks WHERE id = 1")
        ).scalar_one() == "13800000001（legacy_writer）"
        log_labels = db.session.execute(
            text(
                "SELECT operator_label, recipient_label "
                "FROM case_review_logs WHERE id = 1"
            )
        ).one()
        assert log_labels == ("admin", "13800000001（legacy_writer）")

        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        assert {
            "login_throttles",
            "staff_documents",
            "official_notices",
            "case_collections",
            "case_collection_proofs",
        } <= tables
        case_columns = {column["name"] for column in inspector.get_columns("cases")}
        assert {
            "intake_owner_id",
            "intake_owner_label",
            "process_owner_id",
            "process_owner_label",
            "billing_owner_id",
            "billing_owner_label",
        } <= case_columns
        notice_columns = {
            column["name"] for column in inspector.get_columns("official_notices")
        }
        assert {
            "fee_paid_at",
            "fee_paid_proof_original_name",
            "fee_paid_proof_stored_name",
        } <= notice_columns
        notice_indexes = {
            index["name"] for index in inspector.get_indexes("official_notices")
        }
        assert "ix_official_notices_fee_paid_at" in notice_indexes
        assert "ix_official_notices_fee_paid_proof_stored_name" in notice_indexes

        db.session.remove()
        db.engine.dispose()

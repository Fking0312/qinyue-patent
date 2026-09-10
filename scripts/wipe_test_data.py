"""清空业务测试数据，默认保留管理员；--purge-test-admins 会清理下划线开头的测试管理员。"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import (
    Case,
    CaseCollection,
    CaseCollectionProof,
    CaseMaterial,
    CaseMaterialDownloadLog,
    CaseReviewLog,
    Customer,
    LoginThrottle,
    OfficialNotice,
    Project,
    Task,
    User,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="清空本地测试业务数据")
    parser.add_argument(
        "--purge-test-admins",
        action="store_true",
        help="同时删除用户名以 _ 开头的测试管理员，保留正常管理员账号。",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="实际清空时必须填写 DELETE-ALL-TEST-DATA。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅查看当前数据量，不执行删除。",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        print("=== 清空前 ===")
        _print_counts()
        if args.dry_run:
            print("dry-run：未删除任何数据。")
            return

        cfg_env = str(app.config.get("ENV") or os.getenv("FLASK_ENV") or "development").lower()
        allow_production = os.getenv("QY_ALLOW_WIPE_TEST_DATA", "").lower() in {"1", "true", "yes"}
        if cfg_env == "production" and not allow_production:
            print(
                "错误：生产环境禁止执行清库脚本。若确需执行，须设置 "
                "QY_ALLOW_WIPE_TEST_DATA=1 并提供确认口令。",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if args.confirm != "DELETE-ALL-TEST-DATA":
            print(
                "错误：未执行删除。请先用 --dry-run 检查；确认后传入 "
                "--confirm DELETE-ALL-TEST-DATA。",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # 先删依赖子表，再删主表
        n_throttle = LoginThrottle.query.delete()
        n_dl = CaseMaterialDownloadLog.query.delete()
        n_rv = CaseReviewLog.query.delete()
        n_proof = CaseCollectionProof.query.delete()
        n_collection = CaseCollection.query.delete()
        n_notice = OfficialNotice.query.delete()
        materials = CaseMaterial.query.all()
        n_mat = len(materials)
        CaseMaterial.query.delete()
        n_task = Task.query.delete()
        n_case = Case.query.delete()
        n_proj = Project.query.delete()
        n_cust = Customer.query.delete()

        users_to_delete = User.query.filter(User.role.in_(["staff", "client"])).all()
        if args.purge_test_admins:
            users_to_delete.extend(
                User.query.filter(
                    User.role == "admin",
                    User.username.startswith("_", autoescape=True),
                ).all()
            )
        n_users = len(users_to_delete)
        for u in users_to_delete:
            db.session.delete(u)

        # 解绑 admin 误挂的 customer_id（一般没有）
        for admin in User.query.filter_by(role="admin").all():
            admin.customer_id = None
            if hasattr(admin, "is_active"):
                admin.is_active = True

        db.session.commit()

        uploads = Path(app.instance_path) / "uploads"
        if uploads.exists():
            shutil.rmtree(uploads)
            uploads.mkdir(parents=True, exist_ok=True)

        print()
        print("=== 已删除 ===")
        print(
            f"login_throttles={n_throttle} download_logs={n_dl} review_logs={n_rv} "
            f"collection_proofs={n_proof} collections={n_collection} "
            f"official_notices={n_notice} materials={n_mat}"
        )
        print(f"tasks={n_task} cases={n_case} projects={n_proj} customers={n_cust}")
        print(f"deleted staff/client users={n_users}")
        if args.purge_test_admins:
            print("deleted test admins whose usernames start with _")
        print(f"cleared uploads dir: {uploads}")
        print()
        print("=== 清空后 ===")
        _print_counts()
        print()
        print("保留的 admin 账号：")
        for u in User.query.filter_by(role="admin").order_by(User.id).all():
            print(f"  id={u.id} username={u.username}")


def _print_counts() -> None:
    print(f"users={User.query.count()} customers={Customer.query.count()} projects={Project.query.count()}")
    print(f"cases={Case.query.count()} tasks={Task.query.count()} materials={CaseMaterial.query.count()}")
    print(
        f"download_logs={CaseMaterialDownloadLog.query.count()} review_logs={CaseReviewLog.query.count()} "
        f"official_notices={OfficialNotice.query.count()} login_throttles={LoginThrottle.query.count()}"
    )


if __name__ == "__main__":
    main()

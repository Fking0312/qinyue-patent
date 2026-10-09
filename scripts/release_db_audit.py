"""职能改版数据库盘点与升级后核验（只读，不修改数据库）。

升级前：
    python scripts/release_db_audit.py snapshot --db instance/patent.db --output before.json

升级后：
    python scripts/release_db_audit.py verify --db instance/patent.db --before before.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


EXPECTED_PRODUCTION_REVISION = "r6a7b8c9d0e1"
EXPECTED_TARGET_REVISION = "z5d6e7f8a9b0"
COUNTED_TABLES = (
    "users",
    "customers",
    "projects",
    "cases",
    "tasks",
    "case_review_logs",
    "case_materials",
    "case_material_download_logs",
)
REQUIRED_TABLES = {
    "login_throttles",
    "staff_documents",
    "official_notices",
    "case_collections",
    "case_collection_proofs",
}
REQUIRED_COLUMNS = {
    "cases": {
        "intake_owner_id",
        "intake_owner_label",
        "process_owner_id",
        "process_owner_label",
        "billing_owner_id",
        "billing_owner_label",
    },
    "official_notices": {
        "fee_paid_at",
        "fee_paid_proof_original_name",
        "fee_paid_proof_stored_name",
    },
}
REQUIRED_INDEXES = {
    "official_notices": {
        "ix_official_notices_fee_paid_at",
        "ix_official_notices_fee_paid_proof_stored_name",
    }
}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"数据库不存在：{resolved}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _indexes(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall()
    }


def _revision(connection: sqlite3.Connection) -> str:
    if "alembic_version" not in _tables(connection):
        return ""
    rows = connection.execute(
        "SELECT version_num FROM alembic_version ORDER BY version_num"
    ).fetchall()
    return ",".join(row["version_num"] for row in rows)


def _snapshot(connection: sqlite3.Connection) -> dict:
    tables = _tables(connection)
    counts = {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in COUNTED_TABLES
        if table in tables
    }
    staff = []
    if "users" in tables:
        user_columns = _columns(connection, "users")
        selected = ["id", "username", "role"]
        for optional in ("phone", "staff_function", "staff_kind", "is_active"):
            if optional in user_columns:
                selected.append(optional)
        query = (
            f"SELECT {', '.join(selected)} FROM users "
            "WHERE role='staff' ORDER BY id"
        )
        staff = [dict(row) for row in connection.execute(query).fetchall()]
    return {
        "revision": _revision(connection),
        "counts": counts,
        "staff": staff,
    }


def _write_snapshot(database: Path, output: Path) -> int:
    with _connect_readonly(database) as connection:
        payload = _snapshot(connection)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"revision={payload['revision'] or 'unknown'}")
    print(f"staff={len(payload['staff'])}")
    print(f"snapshot={output.resolve()}")
    if payload["revision"] != EXPECTED_PRODUCTION_REVISION:
        print(
            "FAIL: 升级前数据库不是文档记录的生产 revision "
            f"{EXPECTED_PRODUCTION_REVISION}。",
            file=sys.stderr,
        )
        return 1
    print("PASS: 升级前数据库盘点完成。请人工填写并确认员工职能映射。")
    return 0


def _verify(database: Path, before_path: Path) -> int:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    with _connect_readonly(database) as connection:
        after = _snapshot(connection)
        tables = _tables(connection)
        if after["revision"] != EXPECTED_TARGET_REVISION:
            failures.append(
                f"目标 revision 应为 {EXPECTED_TARGET_REVISION}，"
                f"当前为 {after['revision'] or 'unknown'}。"
            )
        missing_tables = sorted(REQUIRED_TABLES - tables)
        if missing_tables:
            failures.append("缺少表：" + ", ".join(missing_tables))
        for table, required in REQUIRED_COLUMNS.items():
            if table not in tables:
                continue
            missing = sorted(required - _columns(connection, table))
            if missing:
                failures.append(f"{table} 缺少列：" + ", ".join(missing))
        for table, required in REQUIRED_INDEXES.items():
            if table not in tables:
                continue
            missing = sorted(required - _indexes(connection, table))
            if missing:
                failures.append(f"{table} 缺少索引：" + ", ".join(missing))

    for table, before_count in before.get("counts", {}).items():
        after_count = after["counts"].get(table)
        if after_count != before_count:
            failures.append(
                f"{table} 行数变化：升级前 {before_count}，升级后 {after_count}。"
            )

    current_staff = {
        row["id"]: row for row in after.get("staff", [])
    }
    for previous in before.get("staff", []):
        current = current_staff.get(previous["id"])
        if current is None:
            failures.append(f"员工 id={previous['id']} 在升级后缺失。")
            continue
        if not (current.get("staff_function") or "").strip():
            failures.append(
                f"员工 {current.get('username')} 升级后仍没有 staff_function。"
            )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"revision={after['revision']}")
    print("PASS: 升级后结构、核心行数与员工职能核验通过。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="只读盘点职能改版数据库")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="升级前生成 JSON 盘点")
    snapshot.add_argument("--db", required=True, type=Path)
    snapshot.add_argument("--output", required=True, type=Path)

    verify = subparsers.add_parser("verify", help="升级后对照 JSON 核验")
    verify.add_argument("--db", required=True, type=Path)
    verify.add_argument("--before", required=True, type=Path)

    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            return _write_snapshot(args.db, args.output)
        return _verify(args.db, args.before)
    except (FileNotFoundError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

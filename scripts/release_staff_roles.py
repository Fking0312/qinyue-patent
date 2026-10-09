"""导出、校验并谨慎应用员工职能映射。

导出后必须由业务负责人逐行确认 target_staff_function：
    python scripts/release_staff_roles.py export --db instance/patent.db --output staff-roles.csv

只校验：
    python scripts/release_staff_roles.py validate --db instance/patent.db --csv staff-roles.csv

确认数据库备份存在后应用：
    python scripts/release_staff_roles.py apply --db instance/patent.db \
        --csv staff-roles.csv --backup backups/patent-before-release.db \
        --confirm APPLY-STAFF-ROLES
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


VALID_FUNCTIONS = frozenset({"writer", "process", "business"})
CSV_FIELDS = (
    "id",
    "username",
    "phone",
    "current_staff_function",
    "target_staff_function",
)


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"数据库不存在：{resolved}")
    if readonly:
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(resolved)
    connection.row_factory = sqlite3.Row
    return connection


def _staff(connection: sqlite3.Connection) -> list[dict]:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "staff_function" not in columns:
        raise RuntimeError("users 表缺少 staff_function，数据库版本不正确。")
    phone_sql = "COALESCE(phone, '')" if "phone" in columns else "''"
    rows = connection.execute(
        f"""
        SELECT id, username, {phone_sql} AS phone,
               COALESCE(staff_function, '') AS staff_function
        FROM users
        WHERE role='staff'
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _export(database: Path, output: Path) -> int:
    with _connect(database, readonly=True) as connection:
        staff = _staff(connection)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in staff:
            current = (row["staff_function"] or "").strip()
            writer.writerow(
                {
                    "id": row["id"],
                    "username": row["username"],
                    "phone": row["phone"],
                    "current_staff_function": current,
                    # 已有合法职能可保留；历史空值必须人工填写，不能静默猜测。
                    "target_staff_function": (
                        current if current in VALID_FUNCTIONS else ""
                    ),
                }
            )
    print(f"staff={len(staff)}")
    print(f"csv={output.resolve()}")
    print("请逐行确认 target_staff_function；空值不能通过校验。")
    return 0


def _read_mapping(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise RuntimeError("CSV 表头被改动，请重新导出。")
        return [dict(row) for row in reader]


def _validated_mapping(database: Path, csv_path: Path) -> list[tuple[int, str]]:
    rows = _read_mapping(csv_path)
    with _connect(database, readonly=True) as connection:
        staff = _staff(connection)
    expected = {int(row["id"]): row for row in staff}
    mapping: list[tuple[int, str]] = []
    seen: set[int] = set()
    for row in rows:
        raw_id = (row.get("id") or "").strip()
        if not raw_id.isdigit():
            raise RuntimeError(f"非法员工 id：{raw_id!r}")
        user_id = int(raw_id)
        if user_id in seen:
            raise RuntimeError(f"员工 id={user_id} 重复。")
        seen.add(user_id)
        current = expected.get(user_id)
        if current is None:
            raise RuntimeError(f"员工 id={user_id} 已不在数据库中。")
        if row.get("username") != current["username"]:
            raise RuntimeError(f"员工 id={user_id} 的用户名与数据库不一致。")
        target = (row.get("target_staff_function") or "").strip()
        if target not in VALID_FUNCTIONS:
            raise RuntimeError(
                f"员工 {current['username']} 的目标职能必须是 "
                "writer/process/business。"
            )
        mapping.append((user_id, target))
    missing = sorted(set(expected) - seen)
    if missing:
        raise RuntimeError("CSV 缺少员工 id：" + ", ".join(map(str, missing)))
    return mapping


def _validate(database: Path, csv_path: Path) -> int:
    mapping = _validated_mapping(database, csv_path)
    counts = {value: 0 for value in sorted(VALID_FUNCTIONS)}
    for _user_id, target in mapping:
        counts[target] += 1
    print("PASS: 员工职能映射完整且与数据库账号一致。")
    print(" ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


def _apply(
    database: Path,
    csv_path: Path,
    backup: Path,
    confirmation: str,
) -> int:
    if confirmation != "APPLY-STAFF-ROLES":
        raise RuntimeError("确认短语不正确，未写入数据库。")
    if not backup.resolve().is_file():
        raise RuntimeError("未找到发布前数据库备份，拒绝写入职能。")
    if backup.resolve() == database.resolve():
        raise RuntimeError("备份文件不能与正在修改的数据库相同。")
    mapping = _validated_mapping(database, csv_path)
    with _connect(database, readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for user_id, target in mapping:
                cursor = connection.execute(
                    """
                    UPDATE users
                    SET staff_function=?
                    WHERE id=? AND role='staff'
                    """,
                    (target, user_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"员工 id={user_id} 更新失败。")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    print(f"PASS: 已应用 {len(mapping)} 名员工的职能映射。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="职能改版员工角色映射")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--db", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--db", required=True, type=Path)
    validate.add_argument("--csv", required=True, type=Path)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--db", required=True, type=Path)
    apply.add_argument("--csv", required=True, type=Path)
    apply.add_argument("--backup", required=True, type=Path)
    apply.add_argument("--confirm", required=True)

    args = parser.parse_args()
    try:
        if args.command == "export":
            return _export(args.db, args.output)
        if args.command == "validate":
            return _validate(args.db, args.csv)
        return _apply(args.db, args.csv, args.backup, args.confirm)
    except (FileNotFoundError, RuntimeError, sqlite3.Error, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

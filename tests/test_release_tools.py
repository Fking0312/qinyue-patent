import csv
import shutil
import sqlite3

import pytest

from scripts import release_db_audit, release_staff_roles


def _create_release_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('r6a7b8c9d0e1');
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                phone TEXT,
                role TEXT NOT NULL,
                staff_function TEXT
            );
            INSERT INTO users VALUES (1, 'writer-1', '13800000001', 'staff', 'writer');
            INSERT INTO users VALUES (2, 'legacy-1', '13800000002', 'staff', '');
            INSERT INTO users VALUES (3, 'client-1', '13800000003', 'client', NULL);
            """
        )


def test_release_database_snapshot_and_verify(tmp_path):
    database = tmp_path / "production-copy.db"
    before = tmp_path / "before.json"
    _create_release_database(database)

    assert release_db_audit._write_snapshot(database, before) == 0

    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            UPDATE alembic_version SET version_num='z5d6e7f8a9b0';
            UPDATE users SET staff_function='process' WHERE id=2;
            CREATE TABLE login_throttles (id INTEGER PRIMARY KEY);
            CREATE TABLE staff_documents (id INTEGER PRIMARY KEY);
            CREATE TABLE case_collections (id INTEGER PRIMARY KEY);
            CREATE TABLE case_collection_proofs (id INTEGER PRIMARY KEY);
            CREATE TABLE cases (
                id INTEGER PRIMARY KEY,
                intake_owner_id INTEGER,
                intake_owner_label TEXT,
                process_owner_id INTEGER,
                process_owner_label TEXT,
                billing_owner_id INTEGER,
                billing_owner_label TEXT
            );
            CREATE TABLE official_notices (
                id INTEGER PRIMARY KEY,
                fee_paid_at DATETIME,
                fee_paid_proof_original_name TEXT,
                fee_paid_proof_stored_name TEXT
            );
            CREATE INDEX ix_official_notices_fee_paid_at
                ON official_notices (fee_paid_at);
            CREATE INDEX ix_official_notices_fee_paid_proof_stored_name
                ON official_notices (fee_paid_proof_stored_name);
            """
        )

    assert release_db_audit._verify(database, before) == 0


def test_staff_role_mapping_requires_confirmation_and_backup(tmp_path):
    database = tmp_path / "production-copy.db"
    csv_path = tmp_path / "staff-roles.csv"
    backup = tmp_path / "production-copy.backup.db"
    _create_release_database(database)

    assert release_staff_roles._export(database, csv_path) == 0
    with pytest.raises(RuntimeError, match="目标职能"):
        release_staff_roles._validate(database, csv_path)

    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    rows[1]["target_staff_function"] = "business"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=release_staff_roles.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    assert release_staff_roles._validate(database, csv_path) == 0
    shutil.copy2(database, backup)
    with pytest.raises(RuntimeError, match="确认短语"):
        release_staff_roles._apply(database, csv_path, backup, "WRONG")
    assert (
        release_staff_roles._apply(
            database,
            csv_path,
            backup,
            "APPLY-STAFF-ROLES",
        )
        == 0
    )
    with sqlite3.connect(database) as connection:
        value = connection.execute(
            "SELECT staff_function FROM users WHERE id=2"
        ).fetchone()[0]
    assert value == "business"

"""职能改版发布门禁：检查 Git 状态与 Alembic 迁移头。

用法：
    python scripts/release_preflight.py
    python scripts/release_preflight.py --allow-dirty  # 仅开发过程中检查迁移图
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEAD = "z5d6e7f8a9b0"
REQUIRED_MIGRATIONS = (
    "migrations/versions/z4c5d6e7f8a9_official_notice_fee_paid.py",
    "migrations/versions/z5d6e7f8a9b0_official_notice_fee_proof.py",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _migration_heads() -> tuple[str, ...]:
    config = AlembicConfig(str(PROJECT_ROOT / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


def main() -> int:
    parser = argparse.ArgumentParser(description="检查职能改版发布候选是否可部署")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="允许工作区有改动；只用于开发过程检查，正式发布不得使用",
    )
    args = parser.parse_args()

    failures: list[str] = []
    inside = _git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        failures.append("当前目录不是 Git 工作区。")

    status = _git("status", "--porcelain")
    if status.returncode != 0:
        failures.append("无法读取 Git 状态。")
    elif status.stdout.strip() and not args.allow_dirty:
        failures.append("工作区不干净；正式发布必须来自唯一 commit。")

    tracked = _git("ls-files", "--", *REQUIRED_MIGRATIONS)
    tracked_paths = {
        value.strip().replace("\\", "/")
        for value in tracked.stdout.splitlines()
        if value.strip()
    }
    for required in REQUIRED_MIGRATIONS:
        if required not in tracked_paths:
            failures.append(f"关键迁移尚未纳入 Git：{required}")

    heads = _migration_heads()
    if heads != (EXPECTED_HEAD,):
        failures.append(
            f"Alembic 必须只有一个 head {EXPECTED_HEAD}，当前为："
            + (", ".join(heads) if heads else "无")
        )

    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    commit = _git("rev-parse", "--short", "HEAD").stdout.strip()
    print(f"branch={branch or 'unknown'}")
    print(f"commit={commit or 'unknown'}")
    print(f"alembic_heads={','.join(heads)}")
    print(f"working_tree={'dirty' if status.stdout.strip() else 'clean'}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: 发布候选 Git 与迁移门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

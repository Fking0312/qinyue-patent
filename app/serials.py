"""案件序列号与项目编码：东八区年月 + 当月两位序号。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Case, Project

_CN_TZ = timezone(timedelta(hours=8))
CASE_SERIAL_RE = re.compile(r"^(\d{4})(\d{2})$")
PROJECT_CODE_RE = re.compile(r"^(\d{4})(\d{2})$")


def ym_prefix_cn(created_at: datetime | None = None) -> str:
    """年月前缀（东八区）：如 2026-07 → 2607。"""
    dt = created_at or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CN_TZ).strftime("%y%m")


def next_case_serial(created_at: datetime | None = None) -> str:
    """
    自动生成 6 位案件序列号：YYMM + 当月序号（两位）。
    例：2026 年 7 月第一个案件 → 260701；8 月重新从 01 起。
    """
    prefix = ym_prefix_cn(created_at)
    max_seq = 0
    for (serial,) in db.session.query(Case.application_no).all():
        match = CASE_SERIAL_RE.fullmatch((serial or "").strip())
        if not match:
            continue
        if match.group(1) != prefix:
            continue
        max_seq = max(max_seq, int(match.group(2)))
    next_seq = max_seq + 1
    if next_seq > 99:
        raise ValueError("当月案件序列号已用尽（最多 99 个）。")
    return f"{prefix}{next_seq:02d}"


def project_code_ym_prefix(created_at: datetime | None = None) -> str:
    """项目编码前四位：创建时间（东八区）的年月，如 2026-04 → 2604。"""
    return ym_prefix_cn(created_at)


def next_project_code(created_at: datetime | None = None) -> str:
    """
    自动生成 6 位项目编码：YYMM + 当月序号（两位）。
    例：2026 年 4 月第一个项目 → 260401。
    """
    prefix = project_code_ym_prefix(created_at)
    max_seq = 0
    for (code,) in db.session.query(Project.code).filter(Project.code.isnot(None)).all():
        match = PROJECT_CODE_RE.fullmatch((code or "").strip())
        if not match:
            continue
        if match.group(1) != prefix:
            continue
        max_seq = max(max_seq, int(match.group(2)))
    next_seq = max_seq + 1
    if next_seq > 99:
        raise ValueError("当月项目编码序号已用尽（最多 99 个）。")
    return f"{prefix}{next_seq:02d}"

"""期限提醒日历：按北京时间自然日聚合有效截止时间。"""

from __future__ import annotations

import calendar as cal_mod
from datetime import date, datetime, timedelta, timezone

from app.models import Task, User
from app.overdue_reminder import tasks_for_reminder_scope
from app.workflow import (
    effective_task_due_at,
    effective_task_phase,
    is_pending_review_phase,
    is_terminal_phase,
)

_CN_TZ = timezone(timedelta(hours=8))
_SEVERITY_RANK = {"future": 1, "due_soon": 2, "overdue": 3}


def parse_calendar_month(raw_year: str, raw_month: str) -> tuple[int, int]:
    """解析日历年月；非法值回落到东八区当前月。"""
    now_cn = datetime.now(timezone.utc).astimezone(_CN_TZ)
    year = int(raw_year) if raw_year.isdigit() else now_cn.year
    month = int(raw_month) if raw_month.isdigit() else now_cn.month
    if year < 2000 or year > 2100:
        year = now_cn.year
    if month < 1 or month > 12:
        month = now_cn.month
    return year, month


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _due_cn_date(task: Task) -> date | None:
    due = effective_task_due_at(task)
    if due is None:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due.astimezone(_CN_TZ).date()


def _task_due_severity(task: Task, now: datetime, horizon: datetime) -> str | None:
    display_phase = effective_task_phase(task, now=now)
    if is_terminal_phase(display_phase) or is_pending_review_phase(display_phase):
        return None
    due = effective_task_due_at(task)
    if due is None:
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if due <= now:
        return "overdue"
    if now < due <= horizon:
        return "due_soon"
    return "future"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = year * 12 + (month - 1) + delta
    return month_index // 12, month_index % 12 + 1


def reminder_calendar_context(
    user: User,
    year: int,
    month: int,
    *,
    due_soon_days: int = 7,
) -> dict:
    """生成指定月份的日历网格与按日聚合的截止任务。"""
    tasks = tasks_for_reminder_scope(user)

    now = _utcnow()
    horizon = now + timedelta(days=due_soon_days)
    today_cn = now.astimezone(_CN_TZ).date()

    days_by_iso: dict[str, dict] = {}
    month_counts = {"total": 0, "overdue": 0, "due_soon": 0, "future": 0}

    for task in tasks:
        severity = _task_due_severity(task, now, horizon)
        if severity is None:
            continue
        due_date = _due_cn_date(task)
        if due_date is None or due_date.year != year or due_date.month != month:
            continue
        iso = due_date.isoformat()
        bucket = days_by_iso.setdefault(
            iso,
            {
                "count": 0,
                "severity": None,
                "severity_counts": {"overdue": 0, "due_soon": 0, "future": 0},
                "tasks": [],
            },
        )
        bucket["count"] += 1
        bucket["severity_counts"][severity] += 1
        bucket["tasks"].append(task)
        month_counts["total"] += 1
        month_counts[severity] += 1
        current = bucket["severity"]
        if current is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[current]:
            bucket["severity"] = severity

    cal_mod.setfirstweekday(cal_mod.MONDAY)
    weeks: list[list[dict | None]] = []
    for week in cal_mod.monthcalendar(year, month):
        row: list[dict | None] = []
        for day in week:
            if day == 0:
                row.append(None)
                continue
            iso = date(year, month, day).isoformat()
            info = days_by_iso.get(
                iso,
                {
                    "count": 0,
                    "severity": None,
                    "severity_counts": {"overdue": 0, "due_soon": 0, "future": 0},
                    "tasks": [],
                },
            )
            row.append(
                {
                    "day": day,
                    "iso": iso,
                    "count": info["count"],
                    "severity": info["severity"],
                    "severity_counts": info["severity_counts"],
                    "load_level": (
                        "high" if info["count"] >= 5 else "medium" if info["count"] >= 3 else "normal"
                    ),
                    "is_today": iso == today_cn.isoformat(),
                    "is_weekend": date(year, month, day).weekday() >= 5,
                }
            )
        weeks.append(row)

    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)

    return {
        "year": year,
        "month": month,
        "weeks": weeks,
        "days_by_iso": days_by_iso,
        "month_counts": month_counts,
        "today_iso": today_cn.isoformat(),
        "today_year": today_cn.year,
        "today_month": today_cn.month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        "weekday_labels": ("一", "二", "三", "四", "五", "六", "日"),
    }

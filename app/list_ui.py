"""列表 / 看板展示辅助：状态色调与行 urgency 标记。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Task
from app.workflow import (
    TaskPhase,
    effective_task_due_at,
    is_overdue_phase,
    is_pending_review_phase,
    is_terminal_phase,
)

_DEFAULT_DUE_SOON_DAYS = 7


def task_phase_tone(phase_status: str | None) -> str:
    """返回状态 pill 的色调 slug。"""
    if not phase_status:
        return "muted"
    if phase_status == TaskPhase.PENDING_ASSIGNMENT:
        return "muted"
    if phase_status == TaskPhase.ORDER_REVISION:
        return "muted"
    if phase_status == TaskPhase.PENDING_ORDER_REVIEW:
        return "warning"
    if is_overdue_phase(phase_status):
        return "danger"
    if is_pending_review_phase(phase_status) or phase_status == TaskPhase.PENDING_FINAL_REVIEW:
        return "warning"
    if phase_status in {TaskPhase.PENDING_SUBMIT, TaskPhase.AUTHORIZED_PENDING_PAYMENT}:
        return "success"
    if phase_status in TaskPhase.TERMINAL or phase_status.startswith("closed"):
        return "secondary"
    if phase_status in {
        TaskPhase.IN_PROGRESS,
        TaskPhase.OFFICE_ACTION,
        TaskPhase.ON_HOLD,
    }:
        return "primary"
    return "info"


def task_urgency(task: Task | None, *, due_soon_days: int = _DEFAULT_DUE_SOON_DAYS) -> str:
    """返回行 urgency：overdue / due_soon / 空字符串。"""
    if task is None:
        return ""
    if is_terminal_phase(task.phase_status):
        return ""
    if is_overdue_phase(task.phase_status):
        return "overdue"
    if is_pending_review_phase(task.phase_status) or task.phase_status == TaskPhase.PENDING_FINAL_REVIEW:
        return ""
    due = effective_task_due_at(task)
    if due is None:
        return ""
    now = datetime.now(timezone.utc)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if due <= now:
        return "overdue"
    if due <= now + timedelta(days=due_soon_days):
        return "due_soon"
    return ""


def task_urgency_row_class(task: Task | None, *, due_soon_days: int = _DEFAULT_DUE_SOON_DAYS) -> str:
    """返回表格行 class。"""
    level = task_urgency(task, due_soon_days=due_soon_days)
    if level == "overdue":
        return "qy-row-overdue"
    if level == "due_soon":
        return "qy-row-due-soon"
    return ""

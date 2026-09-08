"""超期与临期提醒：按角色汇总任务并在展示前同步 phase_status。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Case, Customer, Project, Task, User
from app.workflow import (
    LEGACY_TERMINAL_PHASES,
    TaskPhase,
    apply_task_overdue_status,
    effective_task_due_at,
    effective_task_phase,
    is_overdue_phase,
    is_pending_review_phase,
    is_terminal_phase,
    normalize_legacy_draft_phases,
    normalize_legacy_overdue_pending_review,
)

_NON_TERMINAL_PHASES = tuple(TaskPhase.TERMINAL | LEGACY_TERMINAL_PHASES)


def _utcnow() -> datetime:
    """返回当前 UTC 时间；统一封装便于测试 monkeypatch。"""
    return datetime.now(timezone.utc)


def tasks_for_reminder_scope(user: User) -> list[Task]:
    """非终结任务，按角色限定可见范围（员工：本人负责；客户：本客户项目下）。"""
    # 仅使用 inner join 限定范围；不在此链上使用 joinedload/contains_eager，避免与 filter 组合时 ORM 生成歧义 SQL。
    q = (
        Task.query.join(Task.case)
        .join(Case.project)
        .filter(Task.phase_status.notin_(_NON_TERMINAL_PHASES))
    )
    if user.role == "admin":
        return q.all()
    if user.role == "staff":
        return q.filter(Task.assignee_id == user.id).all()
    if user.role != "client" or user.customer_id is None:
        return []
    return q.filter(Project.customer_id == user.customer_id).all()


def reminder_lists(user: User, *, due_soon_days: int = 7) -> tuple[list[Task], list[Task]]:
    """
    只读返回 (已超期任务列表, 临期任务列表)，不在 GET 展示路径写数据库。
    临期：当前尚未标记为超期，且有效截止日在 (now, now+due_soon_days]。
    """
    tasks = tasks_for_reminder_scope(user)

    now = _utcnow()
    horizon = now + timedelta(days=due_soon_days)

    overdue: list[Task] = []
    due_soon: list[Task] = []

    for t in tasks:
        display_phase = effective_task_phase(t, now=now)
        if is_terminal_phase(display_phase):
            continue
        if is_pending_review_phase(display_phase):
            continue
        if is_overdue_phase(display_phase):
            overdue.append(t)
            continue
        due = effective_task_due_at(t)
        if due is None:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if now < due <= horizon:
            due_soon.append(t)

    def sort_key(task: Task) -> datetime:
        d = effective_task_due_at(task)
        if d is None:
            return datetime.max.replace(tzinfo=timezone.utc)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d

    overdue.sort(key=sort_key)
    due_soon.sort(key=sort_key)
    return overdue, due_soon


def case_detail_endpoint_for_role(role: str) -> str | None:
    """按角色返回案件详情路由名；无详情页的角色返回 None。"""
    if role == "admin":
        return "admin.case_detail"
    if role == "staff":
        return "staff.case_detail_by_id"
    if role == "client":
        return "client.case_detail"
    return None


def reminder_template_kwargs(
    user: User,
    *,
    reminder_page_title: str = "",
    due_soon_days: int = 7,
    calendar_year: int | None = None,
    calendar_month: int | None = None,
    selected_day: str = "",
    calendar_page_endpoint: str | None = None,
    calendar_nav_endpoint: str | None = None,
) -> dict:
    """组装用于提醒模板渲染的上下文（已超期 / 临期 / 阈值天数 / 可选日历）。"""
    from app.deadline_calendar import parse_calendar_month, reminder_calendar_context

    overdue, soon = reminder_lists(user, due_soon_days=due_soon_days)
    cal_year, cal_month = calendar_year, calendar_month
    if cal_year is None or cal_month is None:
        cal_year, cal_month = parse_calendar_month("", "")
    calendar = reminder_calendar_context(
        user,
        cal_year,
        cal_month,
        due_soon_days=due_soon_days,
    )
    selected_day = (selected_day or "").strip()
    if selected_day not in calendar["days_by_iso"]:
        if calendar["today_iso"] in calendar["days_by_iso"]:
            selected_day = calendar["today_iso"]
        elif calendar["days_by_iso"]:
            selected_day = sorted(calendar["days_by_iso"])[0]
        else:
            selected_day = ""
    selected_bucket = calendar["days_by_iso"].get(selected_day, {"tasks": []})
    return {
        "overdue_tasks": overdue,
        "due_soon_tasks": soon,
        "due_soon_days": due_soon_days,
        "reminder_page_title": reminder_page_title,
        "case_detail_endpoint": case_detail_endpoint_for_role(user.role),
        "deadline_calendar": calendar,
        "deadline_calendar_selected_day": selected_day,
        "deadline_calendar_selected_tasks": selected_bucket.get("tasks", []),
        "deadline_calendar_selected_info": selected_bucket,
        "deadline_calendar_page_endpoint": calendar_page_endpoint,
        "deadline_calendar_nav_endpoint": calendar_nav_endpoint or calendar_page_endpoint,
        "show_deadline_calendar_full": bool(reminder_page_title and calendar_page_endpoint),
        "show_deadline_calendar_mini": bool(not reminder_page_title and calendar_page_endpoint),
    }


def refresh_all_open_tasks_overdue() -> int:
    """扫描全部未终结任务并刷新超期标记，返回变更条数（供 CLI / 定时任务）。"""
    normalized = normalize_legacy_overdue_pending_review()
    normalized += normalize_legacy_draft_phases()
    tasks = Task.query.filter(Task.phase_status.notin_(_NON_TERMINAL_PHASES)).all()
    n = sum(1 for t in tasks if apply_task_overdue_status(t))
    if normalized or n:
        db.session.commit()
    return normalized + n

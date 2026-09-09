"""管理工作台 / 员工工作台仪表盘统计卡片。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models import Case, CaseReviewLog, Task, User
from app.overdue_reminder import reminder_lists
from app.task_board import task_board_data_for_user
from app.workflow import TaskPhase

_CN_TZ = timezone(timedelta(hours=8))
_STAFF_NOTIFICATION_ACTIONS = ("approve", "reject", "assigned", "intake_approve", "intake_reject")


@dataclass(frozen=True)
class DashboardStatCard:
    label: str
    value: int
    hint: str
    endpoint: str
    tone: str
    endpoint_kwargs: dict = field(default_factory=dict)


def _current_beijing_year_month() -> tuple[int, int]:
    now = datetime.now(timezone.utc).astimezone(_CN_TZ)
    return now.year, now.month


def _month_bounds_utc(year: int, month: int) -> tuple[datetime, datetime]:
    start_cn = datetime(year, month, 1, tzinfo=_CN_TZ)
    if month == 12:
        end_cn = datetime(year + 1, 1, 1, tzinfo=_CN_TZ)
    else:
        end_cn = datetime(year, month + 1, 1, tzinfo=_CN_TZ)
    return start_cn.astimezone(timezone.utc), end_cn.astimezone(timezone.utc)


def _pending_review_count() -> int:
    return Task.query.filter(
        Task.phase_status.in_([TaskPhase.PENDING_REVIEW, TaskPhase.OVERDUE_PENDING_REVIEW])
    ).count()


def _pending_order_review_count() -> int:
    return Task.query.filter(Task.phase_status == TaskPhase.PENDING_ORDER_REVIEW).count()


def _cases_created_this_month_count() -> int:
    year, month = _current_beijing_year_month()
    start_at, end_at = _month_bounds_utc(year, month)
    return Case.query.filter(Case.created_at >= start_at, Case.created_at < end_at).count()


def _staff_unread_notifications_count(user_id: int) -> int:
    return CaseReviewLog.query.filter(
        CaseReviewLog.action.in_(_STAFF_NOTIFICATION_ACTIONS),
        CaseReviewLog.recipient_id == user_id,
        CaseReviewLog.read_at.is_(None),
    ).count()


def admin_dashboard_stat_cards(user: User) -> list[DashboardStatCard]:
    board = task_board_data_for_user(user, status_filter="all", sort_by="deadline")
    counts = board["counts"]
    year, month = _current_beijing_year_month()
    return [
        DashboardStatCard(
            label="待审核",
            value=_pending_review_count(),
            hint="员工已提交，等待处理",
            endpoint="admin.review_quality",
            tone="danger",
        ),
        DashboardStatCard(
            label="下单待确认",
            value=_pending_order_review_count(),
            hint="业务提交，通过后才能派单",
            endpoint="admin.order_intake",
            tone="warning",
        ),
        DashboardStatCard(
            label="本月新建",
            value=_cases_created_this_month_count(),
            hint=f"{year} 年 {month} 月",
            endpoint="admin.cases",
            tone="primary",
            endpoint_kwargs={"created_year": str(year), "created_month": str(month)},
        ),
        DashboardStatCard(
            label="已超期",
            value=counts["overdue"],
            hint="需尽快跟进",
            endpoint="admin.task_board",
            tone="warning",
            endpoint_kwargs={"status": "overdue"},
        ),
        DashboardStatCard(
            label="待分配",
            value=counts["pending_assignment"],
            hint="尚未指派员工",
            endpoint="admin.task_board",
            tone="info",
            endpoint_kwargs={"status": "pending_assignment"},
        ),
    ]


def staff_dashboard_stat_cards(user: User) -> list[DashboardStatCard]:
    board = task_board_data_for_user(user, status_filter="all", sort_by="deadline")
    counts = board["counts"]
    overdue, due_soon = reminder_lists(user)
    attention = len(overdue) + len(due_soon)
    return [
        DashboardStatCard(
            label="撰写中",
            value=counts["in_progress"],
            hint="进行中的案件",
            endpoint="staff.task_board",
            tone="primary",
            endpoint_kwargs={"status": "in_progress"},
        ),
        DashboardStatCard(
            label="待审核",
            value=counts["pending_review"],
            hint="已提交，等待结果",
            endpoint="staff.task_board",
            tone="info",
            endpoint_kwargs={"status": "pending_review"},
        ),
        DashboardStatCard(
            label="未读消息",
            value=_staff_unread_notifications_count(user.id),
            hint="分配与审核通知",
            endpoint="staff.notifications",
            tone="danger",
        ),
        DashboardStatCard(
            label="期限关注",
            value=attention,
            hint=f"超期 {len(overdue)} · 临期 {len(due_soon)}",
            endpoint="staff.deadline_reminder",
            tone="warning",
        ),
    ]


def dashboard_page_kwargs(user: User, *, reminder_page_title: str = "") -> dict:
    """组装工作台页面模板上下文（统计卡片 + 期限提醒）。"""
    from app.overdue_reminder import reminder_template_kwargs

    calendar_endpoint = None
    calendar_nav_endpoint = None
    if user.role == "staff":
        calendar_endpoint = "staff.deadline_reminder"
        calendar_nav_endpoint = "staff.dashboard"
    elif user.role == "admin":
        calendar_endpoint = "admin.overtime_warning"
        calendar_nav_endpoint = "admin.dashboard"

    kwargs = reminder_template_kwargs(
        user,
        reminder_page_title=reminder_page_title,
        calendar_page_endpoint=calendar_endpoint,
        calendar_nav_endpoint=calendar_nav_endpoint,
    )
    if user.role == "admin":
        kwargs["dashboard_stat_cards"] = admin_dashboard_stat_cards(user)
    elif user.role == "staff":
        kwargs["dashboard_stat_cards"] = staff_dashboard_stat_cards(user)
    else:
        kwargs["dashboard_stat_cards"] = []
    return kwargs

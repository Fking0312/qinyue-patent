"""管理工作台 / 员工工作台仪表盘统计卡片。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models import Case, CaseReviewLog, Task, User
from app.overdue_reminder import reminder_lists
from app.task_board import task_board_data_for_user
from app.workflow import TaskPhase

_CN_TZ = timezone(timedelta(hours=8))
_STAFF_NOTIFICATION_ACTIONS = (
    "approve",
    "reject",
    "assigned",
    "intake_approve",
    "intake_reject",
    "official_forward",
    "official_urge",
    "process_assigned",
    "billing_assigned",
    "submit_for_review",
    "writing_revised",
    "process_accept",
    "process_reject",
    "process_filed",
    "collection_submit",
    "collection_ok",
    "collection_reject",
)


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


def _pending_final_review_count() -> int:
    return Task.query.filter(Task.phase_status == TaskPhase.PENDING_FINAL_REVIEW).count()


def _pending_order_review_count() -> int:
    return Task.query.filter(Task.phase_status == TaskPhase.PENDING_ORDER_REVIEW).count()


def _pending_process_assignment_count() -> int:
    from app.assignment_advisor import pending_process_assignment_count

    return pending_process_assignment_count()


def _pending_billing_assignment_count() -> int:
    from app.assignment_advisor import pending_billing_assignment_count

    return pending_billing_assignment_count()


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
            label="待终审",
            value=_pending_final_review_count(),
            hint="流程已提交，等待办结",
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
            hint="尚未指派撰写师",
            endpoint="admin.smart_assignment",
            tone="info",
        ),
        DashboardStatCard(
            label="待指定流程",
            value=_pending_process_assignment_count(),
            hint="尚未指定流程人员",
            endpoint="admin.process_assignment",
            tone="info",
        ),
        DashboardStatCard(
            label="待指定收账",
            value=_pending_billing_assignment_count(),
            hint="缴费通知待指定业务人员",
            endpoint="admin.billing_assignment",
            tone="info",
        ),
    ]


def business_dashboard_stat_cards(user: User) -> list[DashboardStatCard]:
    """业务工作台：只统计自己当过下单人的单，不含撰写师负载。"""
    from app.collections import billing_queue_counts
    from app.order_intake import my_order_phase_count, my_orders_created_this_month_count

    year, month = _current_beijing_year_month()
    revision = my_order_phase_count(user.id, TaskPhase.ORDER_REVISION)
    pending = my_order_phase_count(user.id, TaskPhase.PENDING_ORDER_REVIEW)
    billing_counts = billing_queue_counts(user.id)
    return [
        DashboardStatCard(
            label="待我修改",
            value=revision,
            hint="管理员打回，改完再提交",
            endpoint="staff.business_orders",
            tone="danger",
        ),
        DashboardStatCard(
            label="待管理员确认",
            value=pending,
            hint="已提交，等待确认",
            endpoint="staff.business_orders",
            tone="warning",
        ),
        DashboardStatCard(
            label="待交证明",
            value=billing_counts["pending_proof"] + billing_counts["rejected"],
            hint="缴费通知待交或已打回",
            endpoint="staff.business_collections",
            tone="danger",
        ),
        DashboardStatCard(
            label="未读消息",
            value=_staff_unread_notifications_count(user.id),
            hint="下单确认与打回",
            endpoint="staff.notifications",
            tone="danger",
            endpoint_kwargs={"status": "unread"},
        ),
        DashboardStatCard(
            label="本月已提交",
            value=my_orders_created_this_month_count(user.id),
            hint=f"{year} 年 {month} 月",
            endpoint="staff.business_orders",
            tone="primary",
        ),
    ]


def business_dashboard_page_kwargs(user: User) -> dict:
    """业务工作台：统计卡片 + 待修改列表 + 待交证明。"""
    from app.collections import list_billing_attention_collections
    from app.order_intake import my_order_revision_rows

    return {
        "page_title": "业务工作台",
        "dashboard_stat_cards": business_dashboard_stat_cards(user),
        "revision_rows": my_order_revision_rows(user.id),
        "attention_collections": list_billing_attention_collections(user.id),
    }


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
            label="待流程核对",
            value=counts["pending_review"],
            hint="已提交，等待流程人员核对",
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


def process_dashboard_stat_cards(user: User) -> list[DashboardStatCard]:
    """流程工作台：待核对 / 待递交 / 待转交 / 未接收。"""
    from app.official_notices import (
        QUEUE_PENDING_COLLECTION,
        QUEUE_PENDING_FORWARD,
        QUEUE_PENDING_PROCESS,
        QUEUE_PENDING_SUBMIT,
        QUEUE_UNRECEIVED,
        process_queue_counts,
    )

    counts = process_queue_counts(user.id)
    return [
        DashboardStatCard(
            label="待核对",
            value=counts[QUEUE_PENDING_PROCESS],
            hint="撰写师已提交，请核对材料",
            endpoint="staff.process_followup",
            tone="danger",
            endpoint_kwargs={"queue": QUEUE_PENDING_PROCESS},
        ),
        DashboardStatCard(
            label="待递交",
            value=counts[QUEUE_PENDING_SUBMIT],
            hint="材料已确认，等待交局",
            endpoint="staff.process_followup",
            tone="warning",
            endpoint_kwargs={"queue": QUEUE_PENDING_SUBMIT},
        ),
        DashboardStatCard(
            label="待转交",
            value=counts[QUEUE_PENDING_FORWARD],
            hint="已上传，尚未转交",
            endpoint="staff.process_followup",
            tone="info",
            endpoint_kwargs={"queue": QUEUE_PENDING_FORWARD},
        ),
        DashboardStatCard(
            label="待确认收款",
            value=counts[QUEUE_PENDING_COLLECTION],
            hint="业务已交证明，请按笔确认",
            endpoint="staff.process_followup",
            tone="primary",
            endpoint_kwargs={"queue": QUEUE_PENDING_COLLECTION},
        ),
        DashboardStatCard(
            label="未接收",
            value=counts[QUEUE_UNRECEIVED],
            hint="转交超过一天未下载",
            endpoint="staff.process_followup",
            tone="primary",
            endpoint_kwargs={"queue": QUEUE_UNRECEIVED},
        ),
    ]


def process_dashboard_page_kwargs(user: User) -> dict:
    from app.case_trace import latest_writing_material_hints
    from app.collections import list_process_pending_confirm_collections
    from app.official_notices import list_process_attention_cases, list_process_attention_notices

    attention_cases = list_process_attention_cases(user.id)
    return {
        "page_title": "流程工作台",
        "dashboard_stat_cards": process_dashboard_stat_cards(user),
        "attention_cases": attention_cases,
        "attention_case_hints": {
            case.id: latest_writing_material_hints(case.id) for case in attention_cases
        },
        "attention_notices": list_process_attention_notices(user.id),
        "attention_collections": list_process_pending_confirm_collections(user.id),
    }


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

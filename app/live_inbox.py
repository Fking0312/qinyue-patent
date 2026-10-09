"""跨端收件箱同步：给侧栏红点与列表静默刷新一个便宜的 cursor。

管理端、员工端、客户端各自打开时，前端按 cursor 判断「别人刚提交了什么」；
cursor 变化才刷新当前收件箱页，避免用户正在填表时被整页盖掉。
"""

from __future__ import annotations

from sqlalchemy import func

from app.assignment_advisor import (
    pending_assignment_count,
    pending_billing_assignment_count,
    pending_process_assignment_count,
)
from app.extensions import db
from app.models import Case, CaseMaterial, CaseReviewLog, Project, Task, User
from app.order_intake import pending_order_review_count
from app.workflow import TaskPhase


def assignment_nav_counts() -> dict[str, int]:
    """智能派单 / 指定流程 / 指定收账 / 下单待确认的侧栏红点口径。"""
    pending_assignment = pending_assignment_count()
    pending_process = pending_process_assignment_count()
    pending_billing = pending_billing_assignment_count()
    pending_order_intake = pending_order_review_count()
    return {
        "pending_assignment": pending_assignment,
        "pending_process": pending_process,
        "pending_billing": pending_billing,
        "pending_order_intake": pending_order_intake,
        "unread": (
            pending_assignment
            + pending_process
            + pending_billing
            + pending_order_intake
        ),
    }


def _iso_stamp(value) -> str:
    if value is None:
        return ""
    return value.replace(microsecond=0).isoformat()


def admin_live_payload() -> dict:
    """管理端：分派红点 + 审核红点（材料审核与终审）+ 全局 cursor（含业务下单提交）。"""
    assignment = assignment_nav_counts()
    review_unread = Task.query.filter(
        Task.phase_status.in_(
            (
                TaskPhase.PENDING_REVIEW,
                TaskPhase.OVERDUE_PENDING_REVIEW,
                TaskPhase.PENDING_FINAL_REVIEW,
            )
        )
    ).count()
    latest_log_id = db.session.query(func.max(CaseReviewLog.id)).scalar() or 0
    latest_task_updated = db.session.query(func.max(Task.updated_at)).scalar()
    cursor = (
        f"{int(latest_log_id)}:{_iso_stamp(latest_task_updated)}:"
        f"{review_unread}:{assignment['unread']}"
    )
    return {
        "ok": True,
        "pending_assignment": assignment["pending_assignment"],
        "pending_process": assignment["pending_process"],
        "pending_billing": assignment["pending_billing"],
        "pending_order_intake": assignment["pending_order_intake"],
        "unread": assignment["unread"],
        "review_total": review_unread,
        "review_unread": review_unread,
        "cursor": cursor,
    }


def staff_live_payload(user_id: int) -> dict:
    """员工端：未读消息 + 本人收件箱最新一条，工作台/跟进列表据此刷新。"""
    from app.blueprints.staff.common.routes import staff_review_notifications_query

    query = staff_review_notifications_query(user_id)
    unread = query.filter(CaseReviewLog.read_at.is_(None)).count()
    latest_id = query.with_entities(func.max(CaseReviewLog.id)).scalar() or 0
    return {
        "ok": True,
        "unread": unread,
        "latest_id": int(latest_id),
        "cursor": f"{int(latest_id)}:{unread}",
    }


def client_live_payload(user: User) -> dict:
    """客户端：自家案件任务/材料/留痕变化时刷新列表。"""
    if user.customer_id is None:
        return {"ok": True, "cursor": "0"}
    case_ids = (
        db.session.query(Case.id)
        .join(Case.project)
        .filter(
            Project.customer_id == user.customer_id,
            Case.attribution_filter(Case.ATTRIBUTION_CUSTOMER),
        )
    )
    latest_log_id = (
        db.session.query(func.max(CaseReviewLog.id))
        .filter(CaseReviewLog.case_id.in_(case_ids))
        .scalar()
        or 0
    )
    latest_material_id = (
        db.session.query(func.max(CaseMaterial.id))
        .filter(CaseMaterial.case_id.in_(case_ids))
        .scalar()
        or 0
    )
    latest_task_updated = (
        db.session.query(func.max(Task.updated_at))
        .filter(Task.case_id.in_(case_ids))
        .scalar()
    )
    cursor = (
        f"{int(latest_log_id)}:{int(latest_material_id)}:"
        f"{_iso_stamp(latest_task_updated)}"
    )
    return {"ok": True, "cursor": cursor}

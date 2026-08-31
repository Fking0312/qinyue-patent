"""员工端共用逻辑：案件通知查询、侧边栏未读数注入与职能占位工作台渲染。

三个职能子模块都从这里取通知口径，避免各自复制一份筛选条件。
"""

from datetime import datetime, timedelta, timezone

from flask_login import current_user

from app.blueprints.staff import staff_bp
from app.blueprints.staff.guards import ensure_staff_function
from app.blueprints.staff.utils import CN_TZ
from app.extensions import db
from app.models import CaseReviewLog
from app.spa_helpers import render_spa_or_full

STAFF_NOTIFICATION_ACTIONS = ("approve", "reject", "assigned")
STAFF_NOTIFICATION_ACTIONABLE = ("reject", "assigned")


def staff_review_notifications_query(user_id: int):
    """当前员工收到的案件通知（分配 / 审核通过 / 打回）。"""
    return CaseReviewLog.query.filter(
        CaseReviewLog.action.in_(STAFF_NOTIFICATION_ACTIONS),
        CaseReviewLog.recipient_id == user_id,
    )


def mark_staff_notification_read(log_id: int) -> CaseReviewLog | None:
    """将指定通知标记为已读；仅本人收件且尚未已读时生效。"""
    log = (
        staff_review_notifications_query(current_user.id)
        .filter_by(id=log_id)
        .first()
    )
    if log is None or log.read_at is not None:
        return log
    log.read_at = datetime.now(timezone.utc)
    db.session.commit()
    return log


def notification_group_label(value: datetime | None, today) -> str:
    """按北京时间将通知归入今天、昨天或更早。"""
    if value is None:
        return "更早"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    notice_date = value.astimezone(CN_TZ).date()
    if notice_date == today:
        return "今天"
    if notice_date == today - timedelta(days=1):
        return "昨天"
    return "更早"


@staff_bp.app_context_processor
def _staff_notification_nav_context():
    """给员工侧边栏注入未读通知数量。"""
    if not current_user.is_authenticated or current_user.role != "staff":
        return {}
    unread = staff_review_notifications_query(current_user.id).filter(
        CaseReviewLog.read_at.is_(None)
    ).count()
    return {"staff_notification_unread": unread}


def render_function_workspace(
    *,
    endpoint: str,
    title: str,
    document_title: str,
    page_desc: str,
    cards: list[dict],
    allowed: str,
):
    """流程/业务占位工作台：独立标题、职责说明与「功能建设中」卡片。"""
    ensure_staff_function(allowed)
    return render_spa_or_full(
        full_template="staff/function_workspace.html",
        inner_template="staff/snippets/function_workspace_inner.html",
        spa_endpoint=endpoint,
        spa_document_title=document_title,
        page_title=title,
        page_desc=page_desc,
        placeholder_cards=cards,
    )

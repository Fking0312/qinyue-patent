"""员工端共用逻辑：案件通知查询、侧边栏未读数、职能占位工作台与内部资料库。

三个职能子模块都从这里取通知口径，避免各自复制一份筛选条件。
内部资料库对撰写师 / 流程 / 业务共用，按职能过滤可见文件。
"""

from datetime import datetime, timedelta, timezone

from flask import abort, send_from_directory
from flask_login import current_user, login_required

from app.blueprints.staff import staff_bp
from app.blueprints.staff.guards import ensure_staff, ensure_staff_function
from app.blueprints.staff.utils import CN_TZ
from app.extensions import db
from app.models import CaseReviewLog
from app.spa_helpers import render_spa_or_full
from app.staff_docs import get_visible_staff_document, list_visible_staff_documents, staff_docs_dir

STAFF_NOTIFICATION_ACTIONS = (
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
STAFF_NOTIFICATION_ACTIONABLE = (
    "reject",
    "assigned",
    "intake_reject",
    "official_forward",
    "official_urge",
    "process_assigned",
    "billing_assigned",
    "submit_for_review",
    "writing_revised",
    "process_reject",
    "collection_submit",
    "collection_reject",
)
STAFF_NOTIFICATION_TYPE_GROUPS = {
    "assigned": ("assigned",),
    "approve": ("approve", "intake_approve", "process_accept", "collection_ok"),
    "reject": ("reject", "intake_reject", "process_reject", "collection_reject"),
    "intake_approve": ("intake_approve",),
    "intake_reject": ("intake_reject",),
    "official": ("official_forward", "official_urge"),
    "process_assigned": ("process_assigned",),
    "billing_assigned": ("billing_assigned",),
    "submit_for_review": ("submit_for_review", "writing_revised"),
    "collection": ("collection_submit", "collection_reject"),
}


def staff_review_notifications_query(user_id: int):
    """当前员工收到的通知：撰写师为分配/审核/官文，流程人员为指定跟进，业务人员为下单确认/打回。"""
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


@staff_bp.route("/staff-docs")
@login_required
def staff_docs():
    """内部资料库：三职能只读；仅能看到全部员工或本职能可见的文件。"""
    ensure_staff()
    documents = list_visible_staff_documents(current_user.staff_function_normalized)
    return render_spa_or_full(
        full_template="staff/staff_docs.html",
        inner_template="staff/snippets/staff_docs_inner.html",
        spa_endpoint="staff.staff_docs",
        spa_document_title="内部资料库 — 琴岳专利管理系统",
        page_title="内部资料库",
        documents=documents,
    )


@staff_bp.route("/staff-docs/<int:doc_id>")
@login_required
def staff_docs_download(doc_id: int):
    """员工下载本职能可见的内部资料；越权按不存在处理。"""
    ensure_staff()
    document = get_visible_staff_document(doc_id, current_user.staff_function_normalized)
    if document is None:
        abort(404)
    return send_from_directory(
        staff_docs_dir(),
        document.stored_name,
        as_attachment=True,
        download_name=document.original_name,
    )

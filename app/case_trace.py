"""案件留痕：把当时的人名钉在记录上，不随账号停用或将来删行而消失。

人名展示优先用还在的账号（含已离职），账号行不在了就回落到写入时的快照。
提交审核时把当时的撰写文件名写进备注，文件本身也不因离职被删或从列表里摘掉。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload

from app.extensions import db

if TYPE_CHECKING:
    from app.models import Case, CaseMaterial, CaseReviewLog, Task, User

CASE_TRACE_ACTIONS: tuple[str, ...] = (
    "assigned",
    "submit_for_review",
    "approve",
    "reject",
    "office_reject",
    "reject_undo",
)

TRACE_ACTION_FILTERS: tuple[str, ...] = (
    "all",
    "assigned",
    "submit_for_review",
    "approve",
    "reject",
)

TRACE_ACTION_LABELS: dict[str, str] = {
    "assigned": "指派",
    "submit_for_review": "提交审核",
    "approve": "通过",
    "reject": "打回",
    "office_reject": "专利局退稿",
    "reject_undo": "撤销退稿",
}

TRACE_ACTION_TONES: dict[str, str] = {
    "assigned": "info",
    "submit_for_review": "primary",
    "approve": "success",
    "reject": "warning",
    "office_reject": "danger",
    "reject_undo": "secondary",
}


def user_trace_label(user: "User | None") -> str:
    """写入时冻结的展示名；账号不存在则空串。"""
    if user is None:
        return ""
    return (user.display_label or user.username or "").strip()


def display_trace(user: "User | None", snapshot: str | None) -> str:
    """页面展示：账号还在用实时姓名，否则用快照，都没有才是 —。"""
    if user is not None:
        label = user_trace_label(user)
        if label:
            return label
    text = (snapshot or "").strip()
    return text or "—"


def stamp_assignee(task: "Task", user: "User | None") -> None:
    """同步任务承办人及姓名快照；取消指派时快照一并清空（历史姓名在流转留痕里）。"""
    if user is None:
        task.assignee_id = None
        task.assignee_label = None
        return
    task.assignee_id = user.id
    task.assignee_label = user_trace_label(user)


def stamp_business_owner(case: "Case", user: "User | None") -> None:
    """同步案件业务负责人及姓名快照。"""
    if user is None:
        case.business_owner_id = None
        case.business_owner_label = None
        return
    case.business_owner_id = user.id
    case.business_owner_label = user_trace_label(user)


def stamp_uploader(material: "CaseMaterial", user: "User | None") -> None:
    """上传时钉上上传人姓名与角色，分组不再依赖账号行是否还在。"""
    if user is None:
        return
    material.uploaded_by_id = user.id
    material.uploaded_by_label = user_trace_label(user)
    material.uploaded_by_role = user.role


def add_review_log(
    *,
    case_id: int,
    action: str,
    operator: "User | None",
    recipient: "User | None" = None,
    note: str | None = None,
) -> "CaseReviewLog":
    """写入一条案件流转留痕，并冻结操作人/接收人姓名。调用方负责 commit。"""
    from app.models import CaseReviewLog

    if operator is None:
        raise ValueError("案件留痕必须有操作人。")
    log = CaseReviewLog(
        case_id=case_id,
        operator_id=operator.id,
        operator_label=user_trace_label(operator),
        recipient_id=recipient.id if recipient is not None else None,
        recipient_label=user_trace_label(recipient) if recipient is not None else None,
        action=action,
        note=note,
    )
    db.session.add(log)
    return log


def writing_material_submit_note(case_id: int) -> str:
    """提交审核时冻结当时的撰写文件名，即使后来有人删文件，留痕里仍能看见交了什么。"""
    from app.models import CaseMaterial

    names: list[str] = []
    for material in (
        CaseMaterial.query.options(joinedload(CaseMaterial.uploaded_by))
        .filter_by(case_id=case_id)
        .order_by(CaseMaterial.created_at.asc(), CaseMaterial.id.asc())
        .all()
    ):
        if material_uploader_role(material) != "staff":
            continue
        name = (material.original_name or "").strip()
        if name:
            names.append(name)
    if not names:
        return "提交审核（未记录到撰写文件名）。"
    shown = names[:8]
    extra = len(names) - len(shown)
    text = "提交撰写材料：" + "、".join(shown)
    if extra > 0:
        text += f" 等 {len(names)} 个文件"
    return text


def material_uploader_role(material: "CaseMaterial") -> str:
    """材料上传者角色：账号还在用实时角色，否则用写入时的快照。"""
    if material.uploaded_by is not None:
        return material.uploaded_by.role or ""
    return (material.uploaded_by_role or "").strip()


def paginate_case_trace_logs(
    case_id: int,
    *,
    action_filter: str,
    operator_filter: str,
    page: int,
    per_page: int = 10,
):
    """案件详情的流转留痕分页；操作人筛选用快照姓名，账号删了也能筛。"""
    from app.models import CaseReviewLog

    if action_filter not in TRACE_ACTION_FILTERS:
        action_filter = "all"
    query = CaseReviewLog.query.options(
        joinedload(CaseReviewLog.operator),
        joinedload(CaseReviewLog.recipient),
    ).filter(
        CaseReviewLog.case_id == case_id,
        CaseReviewLog.action.in_(CASE_TRACE_ACTIONS),
    )
    if action_filter != "all":
        query = query.filter(CaseReviewLog.action == action_filter)

    operator_options: list[str] = []
    seen: set[str] = set()
    for log in (
        CaseReviewLog.query.options(joinedload(CaseReviewLog.operator))
        .filter(
            CaseReviewLog.case_id == case_id,
            CaseReviewLog.action.in_(CASE_TRACE_ACTIONS),
        )
        .order_by(CaseReviewLog.created_at.asc(), CaseReviewLog.id.asc())
        .all()
    ):
        label = display_trace(log.operator, log.operator_label)
        if label != "—" and label not in seen:
            seen.add(label)
            operator_options.append(label)

    if operator_filter != "all":
        if operator_filter in operator_options:
            matching_ids = [
                log.id
                for log in CaseReviewLog.query.options(joinedload(CaseReviewLog.operator))
                .filter(
                    CaseReviewLog.case_id == case_id,
                    CaseReviewLog.action.in_(CASE_TRACE_ACTIONS),
                )
                .all()
                if display_trace(log.operator, log.operator_label) == operator_filter
            ]
            query = query.filter(CaseReviewLog.id.in_(matching_ids or [0]))
        else:
            operator_filter = "all"

    pagination = query.order_by(
        CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    return pagination, operator_options, action_filter, operator_filter

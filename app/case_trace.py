"""案件留痕：把当时的人名钉在记录上，不随账号停用或将来删行而消失。

人名展示优先用还在的账号（含已离职），账号行不在了就回落到写入时的快照。
提交审核时把当时的撰写文件名写进备注，文件本身也不因离职被删或从列表里摘掉。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload

from app.extensions import db

if TYPE_CHECKING:
    from app.models import Case, CaseMaterial, CaseReviewLog, Task, User

CASE_TRACE_ACTIONS: tuple[str, ...] = (
    "assigned",
    "submit_for_review",
    "writing_revised",
    "approve",
    "reject",
    "office_reject",
    "reject_undo",
    "intake_submit",
    "intake_approve",
    "intake_reject",
    "process_assigned",
    "billing_assigned",
    "official_forward",
    "official_urge",
    "collection_submit",
    "collection_ok",
    "collection_reject",
    "process_accept",
    "process_reject",
    "process_filed",
    "submit_final_review",
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
    "submit_for_review": "提交流程核对",
    "writing_revised": "上传改正材料",
    "approve": "终审通过",
    "reject": "终审打回",
    "office_reject": "专利局退稿",
    "reject_undo": "撤销退稿",
    "intake_submit": "提交下单",
    "intake_approve": "下单通过",
    "intake_reject": "下单打回",
    "process_assigned": "指定流程",
    "billing_assigned": "指定收账",
    "official_forward": "转交官文",
    "official_urge": "催办官文",
    "collection_submit": "提交收款证明",
    "collection_ok": "确认收款",
    "collection_reject": "打回收账",
    "process_accept": "流程确认",
    "process_reject": "流程打回",
    "process_filed": "已递交官方",
    "submit_final_review": "提交终审",
}

TRACE_ACTION_TONES: dict[str, str] = {
    "assigned": "info",
    "submit_for_review": "primary",
    "writing_revised": "info",
    "approve": "success",
    "reject": "warning",
    "office_reject": "danger",
    "reject_undo": "secondary",
    "intake_submit": "primary",
    "intake_approve": "success",
    "intake_reject": "warning",
    "process_assigned": "info",
    "billing_assigned": "info",
    "official_forward": "warning",
    "official_urge": "warning",
    "collection_submit": "primary",
    "collection_ok": "success",
    "collection_reject": "warning",
    "process_accept": "success",
    "process_reject": "warning",
    "process_filed": "info",
    "submit_final_review": "primary",
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


def stamp_intake_owner(case: "Case", user: "User | None") -> None:
    """同步下单人及姓名快照；业务人员提交的单用这列，不占用承办撰写师。"""
    if user is None:
        case.intake_owner_id = None
        case.intake_owner_label = None
        return
    case.intake_owner_id = user.id
    case.intake_owner_label = user_trace_label(user)


def stamp_business_owner(case: "Case", user: "User | None") -> None:
    """同步案件业务负责人及姓名快照。"""
    if user is None:
        case.business_owner_id = None
        case.business_owner_label = None
        return
    case.business_owner_id = user.id
    case.business_owner_label = user_trace_label(user)


def stamp_process_owner(case: "Case", user: "User | None") -> None:
    """同步案件流程负责人及姓名快照；取消指定时快照一并清空。"""
    if user is None:
        case.process_owner_id = None
        case.process_owner_label = None
        return
    case.process_owner_id = user.id
    case.process_owner_label = user_trace_label(user)


def stamp_billing_owner(case: "Case", user: "User | None") -> None:
    """同步案件收账负责人及姓名快照；取消指定时快照一并清空。"""
    if user is None:
        case.billing_owner_id = None
        case.billing_owner_label = None
        return
    case.billing_owner_id = user.id
    case.billing_owner_label = user_trace_label(user)


def stamp_uploader(material: "CaseMaterial", user: "User | None") -> None:
    """上传时钉上上传人姓名与角色，分组不再依赖账号行是否还在。"""
    if user is None:
        return
    from app.models import User

    material.uploaded_by_id = user.id
    material.uploaded_by_label = user_trace_label(user)
    if user.role == "staff" and user.staff_function_normalized == User.STAFF_FUNCTION_BUSINESS:
        material.uploaded_by_role = User.STAFF_FUNCTION_BUSINESS
    else:
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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def writing_package_since(case_id: int, *, before_id: int | None = None) -> datetime | None:
    """本轮撰写材料起点：上一份提交、打回或需答复官文的接收时间。"""
    from app.models import CaseReviewLog, OfficialNotice

    times: list[datetime] = []
    log_query = CaseReviewLog.query.filter(
        CaseReviewLog.case_id == case_id,
        CaseReviewLog.action.in_(("submit_for_review", "reject", "process_reject")),
    )
    if before_id is not None:
        log_query = log_query.filter(CaseReviewLog.id < before_id)
    for log in log_query.all():
        stamped = _as_utc(log.created_at)
        if stamped is not None:
            times.append(stamped)

    before_at = None
    if before_id is not None:
        before_log = db.session.get(CaseReviewLog, before_id)
        before_at = _as_utc(before_log.created_at) if before_log is not None else None

    for notice in OfficialNotice.query.filter_by(case_id=case_id).all():
        if not notice.needs_writer_reply:
            continue
        received = _as_utc(notice.received_at)
        if received is None:
            continue
        if before_at is not None and received >= before_at:
            continue
        times.append(received)
    return max(times) if times else None


def list_writing_materials_since(case_id: int, since: datetime | None) -> list:
    """撰写材料；since 有值时只保留该时间之后上传的。"""
    from app.models import CaseMaterial

    materials = (
        CaseMaterial.query.options(joinedload(CaseMaterial.uploaded_by))
        .filter_by(case_id=case_id)
        .order_by(CaseMaterial.created_at.asc(), CaseMaterial.id.asc())
        .all()
    )
    writing = [material for material in materials if not is_disclosure_material(material)]
    if since is None:
        return writing
    since_utc = _as_utc(since)
    if since_utc is None:
        return writing
    return [
        material
        for material in writing
        if material.created_at is not None and _as_utc(material.created_at) > since_utc
    ]


def review_package_writing_materials(case_id: int) -> list:
    """当前待核对这一轮提交的撰写文件（不含以往轮次）。"""
    from app.models import CaseReviewLog

    last_submit = (
        CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review")
        .order_by(CaseReviewLog.id.desc())
        .first()
    )
    since = writing_package_since(case_id, before_id=last_submit.id if last_submit else None)
    return list_writing_materials_since(case_id, since)


def writing_material_submit_note(case_id: int, *, correction: bool = False) -> str:
    """提交审核时冻结当时的撰写文件名，即使后来有人删文件，留痕里仍能看见交了什么。"""
    since = writing_package_since(case_id) if correction else None
    names: list[str] = []
    for material in list_writing_materials_since(case_id, since):
        name = (material.original_name or "").strip()
        if not name:
            continue
        note = (material.note or "").strip()
        names.append(f"{name}（{note}）" if note else name)
    if not names:
        return "提交改正材料（未记录到撰写文件名）。" if correction else "提交审核（未记录到撰写文件名）。"
    shown = names[:8]
    extra = len(names) - len(shown)
    label = "提交改正材料：" if correction else "提交撰写材料："
    text = label + "、".join(shown)
    if extra > 0:
        text += f" 等 {len(names)} 个文件"
    return text


def latest_writing_material_hints(case_id: int, *, limit: int = 3) -> list[str]:
    """流程工作台待核对列表：本轮提交的撰写文件及备注。"""
    lines: list[str] = []
    for material in reversed(review_package_writing_materials(case_id)):
        if len(lines) >= limit:
            break
        name = (material.original_name or "文件").strip() or "文件"
        note = (material.note or "").strip()
        lines.append(f"{name}（{note}）" if note else name)
    return lines


def pending_revised_writing_hints(case_id: int) -> list[str]:
    """撰写师已上传改正材料、尚未再次提交核对时，供流程案件页提示。"""
    from app.models import CaseReviewLog

    last_submit = (
        CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review")
        .order_by(CaseReviewLog.id.desc())
        .first()
    )
    query = CaseReviewLog.query.filter_by(case_id=case_id, action="writing_revised")
    if last_submit is not None:
        query = query.filter(CaseReviewLog.id > last_submit.id)
    logs = query.order_by(CaseReviewLog.id.desc()).all()
    return [log.note.strip() for log in logs if (log.note or "").strip()]


def material_uploader_role(material: "CaseMaterial") -> str:
    """材料上传者角色：账号还在用实时角色，否则用写入时的快照。"""
    if material.uploaded_by is not None:
        return material.uploaded_by.role or ""
    return (material.uploaded_by_role or "").strip()


def is_disclosure_material(material: "CaseMaterial") -> bool:
    """交底材料：管理员或业务人员上传；撰写师上传的才进撰写材料。"""
    from app.models import User

    uploader = material.uploaded_by
    if uploader is not None:
        if uploader.role == "admin":
            return True
        return (
            uploader.role == "staff"
            and uploader.staff_function_normalized == User.STAFF_FUNCTION_BUSINESS
        )
    return (material.uploaded_by_role or "").strip() in {"admin", User.STAFF_FUNCTION_BUSINESS}


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
        if action_filter == "approve":
            query = query.filter(CaseReviewLog.action.in_(("approve", "intake_approve")))
        elif action_filter == "reject":
            query = query.filter(CaseReviewLog.action.in_(("reject", "intake_reject")))
        elif action_filter == "assigned":
            query = query.filter(
                CaseReviewLog.action.in_(("assigned", "process_assigned", "billing_assigned"))
            )
        else:
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

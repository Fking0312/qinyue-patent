"""官方来文：流程人员从专利局系统下载后上传，按种类转交撰写师或自行办理缴费。

与 case_materials / staff_documents 分开存盘。队列只统计当前流程负责人名下的案件。
缴费通知留在流程侧办理官方缴费，不转交、不通知业务人员。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy.orm import joinedload
from werkzeug.datastructures import FileStorage

from app.case_material_upload import persist_uploaded_file
from app.case_trace import add_review_log, user_trace_label
from app.extensions import db
from app.models import Case, CaseCollection, OfficialNotice, Project, Task, User
from app.workflow import (
    TaskPhase,
    apply_task_overdue_status,
    is_terminal_phase,
    phase_for_workflow,
)

_CN_TZ = timezone(timedelta(hours=8))
UNRECEIVED_AFTER = timedelta(days=1)

QUEUE_ALL = "all"
QUEUE_PENDING_PROCESS = "pending_process"
QUEUE_PENDING_FORWARD = "pending_forward"
QUEUE_PENDING_FEE = "pending_fee"
QUEUE_UNRECEIVED = "unreceived"
QUEUE_PENDING_REPLY = "pending_reply"
QUEUE_PENDING_SUBMIT = "pending_submit"
QUEUE_OFFICE_ACTION = "office_action"
QUEUE_PENDING_COLLECTION = "pending_collection"
PROCESS_QUEUES = (
    QUEUE_ALL,
    QUEUE_PENDING_SUBMIT,
    QUEUE_OFFICE_ACTION,
    QUEUE_PENDING_FORWARD,
    QUEUE_PENDING_FEE,
    QUEUE_UNRECEIVED,
    QUEUE_PENDING_REPLY,
    QUEUE_PENDING_COLLECTION,
)
QUEUE_LABELS = {
    QUEUE_ALL: "全部案件",
    QUEUE_PENDING_SUBMIT: "待递交",
    QUEUE_OFFICE_ACTION: "官方处理中",
    QUEUE_PENDING_FORWARD: "待转交",
    QUEUE_PENDING_FEE: "待官方缴费",
    QUEUE_UNRECEIVED: "未接收",
    QUEUE_PENDING_REPLY: "待答复",
    QUEUE_PENDING_COLLECTION: "待确认收款",
}

_WRITING_PHASES = frozenset({TaskPhase.IN_PROGRESS, TaskPhase.OVERDUE_IN_PROGRESS})
_PENDING_SUBMIT_PHASES = frozenset({TaskPhase.PENDING_SUBMIT, TaskPhase.OVERDUE_PENDING_SUBMIT})
_OFFICE_ACTION_PHASES = frozenset({TaskPhase.OFFICE_ACTION, TaskPhase.OVERDUE_OFFICE_ACTION})
# 需要撰写师答复的来文：把已交局/待审核等阶段拉回撰写中。用基础阶段，调用前先 phase_for_workflow。
_REPLY_RESET_PHASES = frozenset(
    {
        TaskPhase.PENDING_REVIEW,
        TaskPhase.PENDING_SUBMIT,
        TaskPhase.OFFICE_ACTION,
        TaskPhase.PENDING_FINAL_REVIEW,
        TaskPhase.AUTHORIZED_PENDING_PAYMENT,
        TaskPhase.ON_HOLD,
    }
)


def official_notices_dir() -> Path:
    """官文磁盘目录：`instance/uploads/official_notices`。"""
    return Path(current_app.instance_path) / "uploads" / "official_notices"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_beijing_date(value: str) -> datetime | None:
    """日期输入按北京当天结束存 UTC，便于当天仍算未到期。"""
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    return datetime(parsed.year, parsed.month, parsed.day, 23, 59, tzinfo=_CN_TZ).astimezone(
        timezone.utc
    )


def date_input_value(value: datetime | None) -> str:
    if value is None:
        return ""
    utc_value = _as_utc(value)
    return utc_value.astimezone(_CN_TZ).strftime("%Y-%m-%d")


def notice_is_unreceived(notice: OfficialNotice, *, now: datetime | None = None) -> bool:
    if notice.forwarded_at is None or notice.received_at is not None:
        return False
    moment = _as_utc(now) or datetime.now(timezone.utc)
    forwarded = _as_utc(notice.forwarded_at)
    return forwarded is not None and (moment - forwarded) >= UNRECEIVED_AFTER


def notice_queue(notice: OfficialNotice, case: Case | None = None) -> str | None:
    """单份官文所属队列；不进队列则 None。待递交按案件阶段，不按官文。"""
    if notice.needs_billing:
        if not notice.fee_paid_complete:
            return QUEUE_PENDING_FEE
        return None
    if notice.forwarded_at is None:
        if notice.needs_writer_reply:
            return QUEUE_PENDING_FORWARD
        return None
    if notice_is_unreceived(notice):
        return QUEUE_UNRECEIVED
    task = (case or notice.case).task if (case or notice.case) else None
    phase = task.phase_status if task is not None else ""
    if notice.needs_writer_reply and phase in _WRITING_PHASES:
        return QUEUE_PENDING_REPLY
    return None


def process_owned_cases_query(user_id: int):
    """当前流程负责人名下的案件。"""
    return (
        Case.query.filter(Case.process_owner_id == user_id)
        .options(
            joinedload(Case.task).joinedload(Task.assignee),
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.business_owner_user),
        )
    )


def _process_notices_query(user_id: int):
    return (
        OfficialNotice.query.join(Case, Case.id == OfficialNotice.case_id)
        .filter(Case.process_owner_id == user_id)
        .options(
            joinedload(OfficialNotice.case).joinedload(Case.task).joinedload(Task.assignee),
            joinedload(OfficialNotice.case).joinedload(Case.project).joinedload(Project.customer),
            joinedload(OfficialNotice.forwarded_to),
        )
    )


def process_queue_counts(user_id: int) -> dict[str, int]:
    notices = _process_notices_query(user_id).all()
    cases = process_owned_cases_query(user_id).all()
    pending_forward = 0
    pending_fee = 0
    unreceived = 0
    pending_reply = 0
    for notice in notices:
        queue = notice_queue(notice, notice.case)
        if queue == QUEUE_PENDING_FORWARD:
            pending_forward += 1
        elif queue == QUEUE_PENDING_FEE:
            pending_fee += 1
        elif queue == QUEUE_UNRECEIVED:
            unreceived += 1
        elif queue == QUEUE_PENDING_REPLY:
            pending_reply += 1
    pending_submit = sum(
        1 for case in cases if case.task is not None and case.task.phase_status in _PENDING_SUBMIT_PHASES
    )
    office_action = sum(
        1 for case in cases if case.task is not None and case.task.phase_status in _OFFICE_ACTION_PHASES
    )
    pending_collection = (
        CaseCollection.query.join(Case, Case.id == CaseCollection.case_id)
        .filter(
            Case.process_owner_id == user_id,
            CaseCollection.status == CaseCollection.STATUS_PENDING_CONFIRM,
        )
        .count()
    )
    return {
        QUEUE_PENDING_FORWARD: pending_forward,
        QUEUE_PENDING_FEE: pending_fee,
        QUEUE_UNRECEIVED: unreceived,
        QUEUE_PENDING_REPLY: pending_reply,
        QUEUE_PENDING_SUBMIT: pending_submit,
        QUEUE_OFFICE_ACTION: office_action,
        QUEUE_PENDING_COLLECTION: pending_collection,
    }


def list_process_attention_cases(user_id: int, *, limit: int = 8) -> list[Case]:
    """首页马上处理：待递交官方的案件。"""
    cases = process_owned_cases_query(user_id).all()
    pending = [
        case
        for case in cases
        if case.task is not None and case.task.phase_status in _PENDING_SUBMIT_PHASES
    ]

    def _sort_key(case: Case):
        stamp = case.task.updated_at if case.task is not None else case.created_at
        return _as_utc(stamp) or datetime.now(timezone.utc)

    pending.sort(key=_sort_key)
    return pending[:limit]


def list_process_attention_notices(user_id: int, *, limit: int = 8) -> list[OfficialNotice]:
    """首页马上处理：未接收优先，其次待转交、待官方缴费。"""
    notices = _process_notices_query(user_id).all()
    ranked: list[tuple[int, datetime, OfficialNotice]] = []
    for notice in notices:
        queue = notice_queue(notice, notice.case)
        due_or_created = _as_utc(notice.official_due_at) or _as_utc(notice.created_at) or datetime.now(timezone.utc)
        if queue == QUEUE_UNRECEIVED:
            ranked.append((0, _as_utc(notice.forwarded_at) or datetime.now(timezone.utc), notice))
        elif queue == QUEUE_PENDING_FORWARD:
            ranked.append((1, due_or_created, notice))
        elif queue == QUEUE_PENDING_FEE:
            ranked.append((2, due_or_created, notice))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].id))
    return [item[2] for item in ranked[:limit]]


def list_followup_cases(user_id: int, queue: str) -> list[Case]:
    """官文跟进列表：按队列筛本案。"""
    if queue not in PROCESS_QUEUES:
        queue = QUEUE_ALL
    cases = (
        process_owned_cases_query(user_id)
        .order_by(Case.created_at.desc(), Case.id.desc())
        .all()
    )
    if queue == QUEUE_ALL:
        return cases
    if queue == QUEUE_PENDING_SUBMIT:
        return [
            case
            for case in cases
            if case.task is not None and case.task.phase_status in _PENDING_SUBMIT_PHASES
        ]
    if queue == QUEUE_OFFICE_ACTION:
        return [
            case
            for case in cases
            if case.task is not None and case.task.phase_status in _OFFICE_ACTION_PHASES
        ]
    if queue == QUEUE_PENDING_COLLECTION:
        pending_ids = {
            row[0]
            for row in db.session.query(CaseCollection.case_id)
            .join(Case, Case.id == CaseCollection.case_id)
            .filter(
                Case.process_owner_id == user_id,
                CaseCollection.status == CaseCollection.STATUS_PENDING_CONFIRM,
            )
            .all()
        }
        return [case for case in cases if case.id in pending_ids]
    notices = _process_notices_query(user_id).all()
    matched_ids: set[int] = set()
    for notice in notices:
        if notice_queue(notice, notice.case) == queue:
            matched_ids.add(notice.case_id)
    return [case for case in cases if case.id in matched_ids]


def list_case_notices(case_id: int) -> list[OfficialNotice]:
    return (
        OfficialNotice.query.options(
            joinedload(OfficialNotice.uploaded_by),
            joinedload(OfficialNotice.forwarded_to),
            joinedload(OfficialNotice.collection),
        )
        .filter_by(case_id=case_id)
        .order_by(OfficialNotice.created_at.desc(), OfficialNotice.id.desc())
        .all()
    )


def list_writer_notices(case_id: int, writer_id: int) -> list[OfficialNotice]:
    """撰写师只看已转交给自己的官文。"""
    return [
        notice
        for notice in list_case_notices(case_id)
        if notice.forwarded_to_id == writer_id and notice.forwarded_at is not None
    ]


def get_notice(notice_id: int) -> OfficialNotice | None:
    return db.session.get(OfficialNotice, notice_id)


def case_writer(case: Case) -> User | None:
    """本案承办撰写师：任务承办人优先，其次业务负责人；必须仍是可派单撰写师。"""
    task = case.task
    candidates = []
    if task is not None and task.assignee is not None:
        candidates.append(task.assignee)
    if case.business_owner_user is not None:
        candidates.append(case.business_owner_user)
    for user in candidates:
        if user.is_assignable_writer:
            return user
    return None


def user_may_manage_case_notices(user: User, case: Case) -> bool:
    return (
        user.role == "staff"
        and user.staff_function_normalized == User.STAFF_FUNCTION_PROCESS
        and case.process_owner_id == user.id
    )


def user_may_download_notice(user: User, notice: OfficialNotice) -> bool:
    if user.role == "admin":
        return True
    if user.role != "staff":
        return False
    case = notice.case
    if case is None:
        return False
    if user_may_manage_case_notices(user, case):
        return True
    if (
        user.staff_function_normalized == User.STAFF_FUNCTION_WRITER
        and notice.forwarded_to_id == user.id
        and notice.forwarded_at is not None
        and not notice.needs_billing
    ):
        return True
    if (
        user.staff_function_normalized == User.STAFF_FUNCTION_BUSINESS
        and notice.needs_billing
        and case.billing_owner_id == user.id
    ):
        if notice.forwarded_to_id == user.id:
            return True
        return CaseCollection.query.filter_by(notice_id=notice.id).first() is not None
    return False


def user_may_download_fee_proof(user: User, notice: OfficialNotice) -> bool:
    """官方缴费证明只给本案流程负责人和管理员。"""
    if not notice.needs_billing or not (notice.fee_paid_proof_stored_name or "").strip():
        return False
    if user.role == "admin":
        return True
    case = notice.case
    if case is None:
        return False
    return user_may_manage_case_notices(user, case)


def save_official_notice(
    *,
    case: Case,
    uploader: User,
    notice_type: str | None,
    official_due_raw: str,
    file_storage: FileStorage | None,
) -> tuple[OfficialNotice | None, str | None]:
    """流程负责人上传官文。成功 (notice, None)；失败 (None, Toast)。调用方负责 commit。"""
    if not user_may_manage_case_notices(uploader, case):
        return None, "只有本案流程负责人可以上传官方来文。"
    normalized = OfficialNotice.normalize_type(notice_type)
    if normalized is None:
        return None, "请选择来文种类。"
    try:
        official_due = parse_beijing_date(official_due_raw)
    except ValueError:
        return None, "官方期限格式不正确。"
    if official_due is None:
        return None, "请填写官方期限。"
    display_name, stored_name, error = persist_uploaded_file(official_notices_dir(), file_storage)
    if error or not display_name or not stored_name:
        return None, error or "请选择要上传的文件。"
    notice = OfficialNotice(
        case_id=case.id,
        notice_type=normalized,
        original_name=display_name,
        stored_name=stored_name,
        official_due_at=official_due,
        uploaded_by_id=uploader.id,
        uploaded_by_label=user_trace_label(uploader),
    )
    db.session.add(notice)
    return notice, None


def delete_unforwarded_notice(notice: OfficialNotice, operator: User) -> tuple[bool, str]:
    """未转交、未缴费、未通知收账的官文可由流程负责人删除。"""
    case = notice.case
    if case is None or not user_may_manage_case_notices(operator, case):
        return False, "不能删除这份官方来文。"
    if notice.forwarded_at is not None:
        return False, "已转交的来文不能删除。"
    if notice.fee_paid_at is not None:
        return False, "已标记官方缴费的来文不能删除。"
    if notice.collection is not None or CaseCollection.query.filter_by(notice_id=notice.id).first() is not None:
        return False, "已通知业务收账的缴费通知不能删除。"
    stored = notice.stored_name
    proof_stored = notice.fee_paid_proof_stored_name
    notice_id = notice.id
    db.session.delete(notice)
    db.session.flush()
    try:
        (official_notices_dir() / stored).unlink(missing_ok=True)
        if proof_stored:
            (official_notices_dir() / proof_stored).unlink(missing_ok=True)
    except OSError:
        current_app.logger.exception("official_notice_file_cleanup notice_id=%s", notice_id)
    return True, "已删除未转交的官方来文。"


def mark_fee_paid(
    notice: OfficialNotice,
    operator: User,
    *,
    file_storage: FileStorage | None,
) -> tuple[bool, str]:
    """流程负责人标记已向专利局缴费，必须同时提交缴费证明。调用方负责 commit。"""
    case = notice.case
    if case is None or not user_may_manage_case_notices(operator, case):
        return False, "只有本案流程负责人可以标记官方缴费。"
    if not notice.needs_billing:
        return False, "只有缴费通知需要标记官方缴费。"
    if notice.fee_paid_complete:
        return False, "这份缴费通知已经标记过已缴费。"
    if file_storage is None or not (file_storage.filename or "").strip():
        return False, "请上传官方缴费证明。"
    display_name, stored_name, error = persist_uploaded_file(official_notices_dir(), file_storage)
    if error or not display_name or not stored_name:
        return False, error or "请上传官方缴费证明。"
    notice.fee_paid_proof_original_name = display_name
    notice.fee_paid_proof_stored_name = stored_name
    if notice.fee_paid_at is None:
        notice.fee_paid_at = datetime.now(timezone.utc)
    add_review_log(
        case_id=case.id,
        action="fee_paid",
        operator=operator,
        note=f"{notice.type_label} · {display_name}",
    )
    return True, "已标记官方缴费完成。"


def forward_notice(
    notice: OfficialNotice,
    operator: User,
    *,
    internal_due_raw: str = "",
) -> tuple[bool, str]:
    """转交需答复的官文给撰写师。缴费通知留在流程侧，不转交业务人员。"""
    case = notice.case
    if case is None or not user_may_manage_case_notices(operator, case):
        return False, "只有本案流程负责人可以转交官方来文。"
    if notice.needs_billing:
        return False, "缴费通知由流程人员办理官方缴费，请使用「标记已缴费」。"
    if notice.forwarded_at is not None:
        return False, "这份来文已经转交过了。"
    try:
        internal_due = parse_beijing_date(internal_due_raw)
    except ValueError:
        return False, "内部截止日格式不正确。"
    if internal_due is None:
        internal_due = notice.official_due_at
    now = datetime.now(timezone.utc)

    writer = case_writer(case)
    if writer is None:
        return False, "案件尚未指定在职撰写师，请先让管理员派单。"
    notice.forwarded_to_id = writer.id
    notice.forwarded_to_label = user_trace_label(writer)
    notice.forwarded_at = now
    notice.internal_due_at = internal_due
    task = case.task
    if notice.needs_writer_reply and task is not None:
        if internal_due is not None:
            task.due_at = internal_due
        reopen_task_for_writer_reply(task)
    add_review_log(
        case_id=case.id,
        action="official_forward",
        operator=operator,
        recipient=writer,
        note=notice.type_label,
    )
    return True, f"已转交给撰写师 {writer.display_label}。"


def forward_notice_to_writer(
    notice: OfficialNotice,
    operator: User,
    *,
    internal_due_raw: str = "",
) -> tuple[bool, str]:
    """兼容旧调用：转交需答复来文给撰写师。"""
    return forward_notice(notice, operator, internal_due_raw=internal_due_raw)


def urge_notice(notice: OfficialNotice, operator: User) -> tuple[bool, str]:
    """未接收超过一天后可催办，再给接收人一条待处理通知。"""
    case = notice.case
    if case is None or not user_may_manage_case_notices(operator, case):
        return False, "只有本案流程负责人可以催办。"
    if notice.needs_billing:
        return False, "缴费通知由流程人员办理官方缴费，不转交业务人员，无需催办查收。"
    if notice.forwarded_at is None:
        return False, "请先转交。"
    if notice.received_at is not None:
        return False, "对方已经下载过这份来文。"
    if not notice_is_unreceived(notice):
        return False, "转交未满一天，暂不催办。"
    recipient = notice.forwarded_to
    if recipient is None:
        recipient = case_writer(case)
    if recipient is None:
        return False, "找不到可催办的接收人。"
    add_review_log(
        case_id=case.id,
        action="official_urge",
        operator=operator,
        recipient=recipient,
        note=notice.type_label,
    )
    return True, "已催办撰写师查收官方来文。"


def reopen_task_for_writer_reply(task: Task | None) -> None:
    """审查意见/补正等需答复来文：已交局或待审核的案件拉回撰写中，便于上传改正材料。"""
    if task is None:
        return
    base = phase_for_workflow(task.phase_status)
    if is_terminal_phase(base):
        return
    if base in _REPLY_RESET_PHASES:
        task.phase_status = TaskPhase.IN_PROGRESS
        apply_task_overdue_status(task)


def latest_open_writer_reply_notice(case_id: int, writer_id: int) -> OfficialNotice | None:
    """撰写师尚未就最新一份需答复来文再次提交审核的那份官文。"""
    from app.models import CaseReviewLog

    for notice in list_writer_notices(case_id, writer_id):
        if not notice.needs_writer_reply:
            continue
        since = notice.received_at or notice.forwarded_at
        if since is None:
            return notice
        since_utc = _as_utc(since)
        submits = CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review").all()
        if any(log.created_at is not None and _as_utc(log.created_at) > since_utc for log in submits):
            continue
        return notice
    return None


def has_staff_writing_since(case_id: int, since: datetime | None) -> bool:
    """since 之后是否有撰写师上传的撰写材料（不含交底）。"""
    if since is None:
        return False
    from app.case_trace import is_disclosure_material
    from app.models import CaseMaterial

    since_utc = _as_utc(since)
    materials = CaseMaterial.query.options(joinedload(CaseMaterial.uploaded_by)).filter_by(case_id=case_id).all()
    return any(
        not is_disclosure_material(material)
        and material.created_at is not None
        and _as_utc(material.created_at) > since_utc
        for material in materials
    )


def writer_material_lock_state(
    task: Task | None, case_id: int, writer_id: int
) -> tuple[bool, str | None, OfficialNotice | None]:
    """撰写材料是否锁定。有待答复官文时：未接收先锁，接收后解锁以便上传改正件。"""
    open_reply = latest_open_writer_reply_notice(case_id, writer_id)
    if open_reply is not None:
        if open_reply.received_at is None:
            return True, "await_receipt", open_reply
        return False, None, open_reply
    if task is None or phase_for_workflow(task.phase_status) != TaskPhase.IN_PROGRESS:
        return True, "phase", None
    return False, None, None


def stamp_notice_received(notice: OfficialNotice, user: User) -> None:
    """撰写师首次下载记为已接收；需答复来文同时打开改正材料上传。调用方负责 commit。"""
    if notice.received_at is not None:
        return
    if notice.forwarded_to_id != user.id:
        return
    notice.received_at = datetime.now(timezone.utc)
    if notice.needs_writer_reply:
        case = notice.case
        reopen_task_for_writer_reply(case.task if case is not None else None)

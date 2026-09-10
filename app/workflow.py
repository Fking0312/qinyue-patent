"""案件任务阶段与超期状态（阶段 A：规则集中定义，供服务层与定时任务复用）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Case, Task


class TaskPhase:
    """工作流阶段（非终结）；超期时映射为对应的 overdue_* 状态。"""

    PENDING_ASSIGNMENT = "pending_assignment"
    PENDING_ORDER_REVIEW = "pending_order_review"
    ORDER_REVISION = "order_revision"
    IN_PROGRESS = "in_progress"
    PENDING_REVIEW = "pending_review"
    PENDING_SUBMIT = "pending_submit"
    AUTHORIZED_PENDING_PAYMENT = "authorized_pending_payment"
    OFFICE_ACTION = "office_action"
    PENDING_FINAL_REVIEW = "pending_final_review"
    ON_HOLD = "on_hold"

    OVERDUE_IN_PROGRESS = "overdue_in_progress"
    OVERDUE_PENDING_REVIEW = "overdue_pending_review"
    OVERDUE_PENDING_SUBMIT = "overdue_pending_submit"
    OVERDUE_OFFICE_ACTION = "overdue_office_action"
    OVERDUE_ON_HOLD = "overdue_on_hold"

    COMPLETED = "completed"

    BASE_PHASES = frozenset(
        {
            PENDING_ASSIGNMENT,
            PENDING_ORDER_REVIEW,
            ORDER_REVISION,
            IN_PROGRESS,
            PENDING_REVIEW,
            PENDING_SUBMIT,
            AUTHORIZED_PENDING_PAYMENT,
            OFFICE_ACTION,
            PENDING_FINAL_REVIEW,
            ON_HOLD,
        },
    )

    TERMINAL = frozenset({COMPLETED})


# 旧终态（已合并为「已完成」）；迁移后库中不应再出现，读取时仍作终态处理。
LEGACY_TERMINAL_PHASES = frozenset(
    {
        "closed_granted",
        "closed_rejected",
        "closed_withdrawn",
    },
)

# 已废弃的「草稿」阶段；迁移后库中不应再出现，读取时映射为撰写中。
LEGACY_DRAFT_PHASES = frozenset({"draft", "overdue_draft"})


_OVERDUE_MAP = {
    TaskPhase.IN_PROGRESS: TaskPhase.OVERDUE_IN_PROGRESS,
    # 待流程核对、待终审不因超期改写 phase_status
    TaskPhase.PENDING_SUBMIT: TaskPhase.OVERDUE_PENDING_SUBMIT,
    TaskPhase.OFFICE_ACTION: TaskPhase.OVERDUE_OFFICE_ACTION,
    TaskPhase.ON_HOLD: TaskPhase.OVERDUE_ON_HOLD,
}

_OVERDUE_TO_BASE = {v: k for k, v in _OVERDUE_MAP.items()}

OVERDUE_PHASES: frozenset[str] = frozenset(_OVERDUE_MAP.values())

_PENDING_REVIEW_PHASES = frozenset(
    {TaskPhase.PENDING_REVIEW, TaskPhase.OVERDUE_PENDING_REVIEW},
)

# 员工端仅允许下列迁移；待递交 / 待终审由流程或管理专用动作进入，撰写师不得直拨。
_STAFF_PHASE_ORDER: tuple[str, ...] = (
    TaskPhase.PENDING_ASSIGNMENT,
    TaskPhase.IN_PROGRESS,
    TaskPhase.PENDING_REVIEW,
    TaskPhase.PENDING_SUBMIT,
    TaskPhase.AUTHORIZED_PENDING_PAYMENT,
    TaskPhase.OFFICE_ACTION,
    TaskPhase.PENDING_FINAL_REVIEW,
    TaskPhase.ON_HOLD,
)

STAFF_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskPhase.IN_PROGRESS: frozenset(
        {
            TaskPhase.PENDING_REVIEW,
            TaskPhase.ON_HOLD,
        },
    ),
    TaskPhase.PENDING_REVIEW: frozenset(),
    TaskPhase.PENDING_SUBMIT: frozenset({TaskPhase.ON_HOLD}),
    TaskPhase.OFFICE_ACTION: frozenset({TaskPhase.IN_PROGRESS, TaskPhase.ON_HOLD}),
    TaskPhase.PENDING_FINAL_REVIEW: frozenset(),
    TaskPhase.ON_HOLD: frozenset(
        {
            TaskPhase.IN_PROGRESS,
            TaskPhase.PENDING_REVIEW,
            TaskPhase.OFFICE_ACTION,
        },
    ),
}


INTAKE_PHASES: frozenset[str] = frozenset(
    {TaskPhase.PENDING_ORDER_REVIEW, TaskPhase.ORDER_REVISION},
)


ADMIN_CASE_PHASE_OPTIONS: tuple[str, ...] = (
    TaskPhase.PENDING_ORDER_REVIEW,
    TaskPhase.ORDER_REVISION,
    TaskPhase.PENDING_ASSIGNMENT,
    TaskPhase.IN_PROGRESS,
    TaskPhase.PENDING_REVIEW,
    TaskPhase.PENDING_SUBMIT,
    TaskPhase.AUTHORIZED_PENDING_PAYMENT,
    TaskPhase.OFFICE_ACTION,
    TaskPhase.PENDING_FINAL_REVIEW,
    TaskPhase.ON_HOLD,
    TaskPhase.COMPLETED,
)


def is_terminal_phase(phase_status: str) -> bool:
    """任务是否已终结（仅「已完成」，含尚未迁移的旧结案状态）。"""
    return phase_status in TaskPhase.TERMINAL or phase_status in LEGACY_TERMINAL_PHASES


def normalize_task_phase(phase_status: str) -> str:
    """将旧结案状态统一为「已完成」；将已废弃草稿阶段映射为撰写中。"""
    if phase_status in LEGACY_TERMINAL_PHASES:
        return TaskPhase.COMPLETED
    if phase_status in LEGACY_DRAFT_PHASES:
        return TaskPhase.IN_PROGRESS if phase_status == "draft" else TaskPhase.OVERDUE_IN_PROGRESS
    return phase_status

def is_order_intake_phase(phase_status: str) -> bool:
    """是否处于下单确认链路（待确认或打回待改），与撰写审核分开。"""
    return normalize_task_phase(phase_status) in INTAKE_PHASES


def is_pending_order_review_phase(phase_status: str) -> bool:
    """是否正在等管理员确认下单。"""
    return normalize_task_phase(phase_status) == TaskPhase.PENDING_ORDER_REVIEW


def resolve_case_task_phase(business_owner_id: int | None, requested_phase: str) -> str:
    """未指派员工时强制待分配；已指派时不允许停留在待分配。下单确认允许无承办人。"""
    requested_phase = normalize_task_phase(requested_phase)
    if requested_phase in INTAKE_PHASES:
        if business_owner_id is None:
            return requested_phase
        return TaskPhase.IN_PROGRESS
    if business_owner_id is None:
        return TaskPhase.PENDING_ASSIGNMENT
    if requested_phase == TaskPhase.PENDING_ASSIGNMENT:
        return TaskPhase.IN_PROGRESS
    if requested_phase in TaskPhase.BASE_PHASES or requested_phase in TaskPhase.TERMINAL:
        return requested_phase
    return TaskPhase.IN_PROGRESS


def normalize_assigned_pending_tasks() -> int:
    """修复历史不一致数据：已有承办员工的任务不得继续处于「待分配」。"""
    from app.models import Task

    return Task.query.filter(
        Task.assignee_id.isnot(None),
        Task.phase_status == TaskPhase.PENDING_ASSIGNMENT,
    ).update(
        {Task.phase_status: TaskPhase.IN_PROGRESS},
        synchronize_session=False,
    )


def phase_for_workflow(phase_status: str) -> str:
    """将 overdue_* 映射回基础阶段；终结状态与非映射值原样返回。"""
    phase_status = normalize_task_phase(phase_status)
    if phase_status in TaskPhase.TERMINAL:
        return phase_status
    return _OVERDUE_TO_BASE.get(phase_status, phase_status)





def staff_may_transition_phase(current_phase: str, new_phase: str) -> bool:
    """员工是否允许将任务从 current_phase 改为 new_phase（不含流程/管理专用动作）。"""
    if new_phase in {
        TaskPhase.PENDING_SUBMIT,
        TaskPhase.PENDING_FINAL_REVIEW,
        TaskPhase.OFFICE_ACTION,
    }:
        return False
    if new_phase == TaskPhase.PENDING_ASSIGNMENT:
        return False
    if new_phase in INTAKE_PHASES or is_order_intake_phase(current_phase):
        return False
    if new_phase not in TaskPhase.BASE_PHASES:
        return False
    cur = phase_for_workflow(current_phase)
    if is_terminal_phase(cur):
        return False
    allowed = STAFF_ALLOWED_TRANSITIONS.get(cur)
    if allowed is None:
        return False
    return new_phase in allowed


def _active_process_owner(case: "Case | None"):
    """本案在职流程负责人；未指定、离职或职能不对则 None。"""
    from app.models import User

    if case is None:
        return None
    owner = case.process_owner_user
    if owner is None or not owner.is_active:
        return None
    if owner.staff_function_normalized != User.STAFF_FUNCTION_PROCESS:
        return None
    return owner


def _require_process_owner(task: "Task", operator) -> tuple[bool, str]:
    from app.models import User

    if operator is None or operator.staff_function_normalized != User.STAFF_FUNCTION_PROCESS:
        return False, "只有流程人员可以执行该操作。"
    case = task.case
    if case is None or case.process_owner_id != operator.id:
        return False, "只有本案流程负责人可以执行该操作。"
    return True, ""


def is_writer_correction_cycle(case_id: int, *, open_reply_notice=None) -> bool:
    """打回后尚未再提交，或已接收需答复官文：此次上传/提交视为改正材料。"""
    if open_reply_notice is not None and getattr(open_reply_notice, "received_at", None) is not None:
        return True
    from app.models import CaseReviewLog

    last_reject = (
        CaseReviewLog.query.filter(
            CaseReviewLog.case_id == case_id,
            CaseReviewLog.action.in_(("reject", "process_reject")),
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    if last_reject is None:
        return False
    later_submit = CaseReviewLog.query.filter(
        CaseReviewLog.case_id == case_id,
        CaseReviewLog.action == "submit_for_review",
        CaseReviewLog.id > last_reject.id,
    ).first()
    return later_submit is None


def notify_process_of_revised_writing(case, operator, material, *, open_reply_notice=None) -> bool:
    """撰写师上传改正材料后通知本案流程负责人。调用方负责 commit。"""
    from app.case_trace import add_review_log

    if case is None or operator is None or material is None:
        return False
    if not is_writer_correction_cycle(case.id, open_reply_notice=open_reply_notice):
        return False
    process_owner = _active_process_owner(case)
    if process_owner is None:
        return False
    name = (material.original_name or "文件").strip() or "文件"
    note = (material.note or "").strip()
    add_review_log(
        case_id=case.id,
        action="writing_revised",
        operator=operator,
        recipient=process_owner,
        note=f"{name}（{note}）" if note else name,
    )
    return True


def staff_submit_case_for_review(
    task: "Task", *, operator_id: int | None = None, correction: bool = False
) -> bool:
    """
    撰写师提交给流程人员核对：撰写中（含已超期）→ 待流程核对。
    必须已指定在职流程人员。返回是否发生状态变更。
    """
    from app.case_trace import add_review_log, writing_material_submit_note
    from app.extensions import db
    from app.models import User

    if is_terminal_phase(task.phase_status):
        return False
    if phase_for_workflow(task.phase_status) != TaskPhase.IN_PROGRESS:
        return False
    operator = db.session.get(User, operator_id) if operator_id else None
    if operator is None and task.assignee_id:
        operator = db.session.get(User, task.assignee_id)
    if operator is None:
        return False
    process_owner = _active_process_owner(task.case)
    if process_owner is None:
        return False
    if not correction:
        correction = is_writer_correction_cycle(task.case_id)
    task.phase_status = TaskPhase.PENDING_REVIEW
    add_review_log(
        case_id=task.case_id,
        action="submit_for_review",
        operator=operator,
        recipient=process_owner,
        note=writing_material_submit_note(task.case_id, correction=correction),
    )
    return True


def process_accept_writing(task: "Task", operator) -> tuple[bool, str]:
    """流程确认撰写材料：待流程核对 → 待递交官方。"""
    ok, message = _require_process_owner(task, operator)
    if not ok:
        return False, message
    if phase_for_workflow(task.phase_status) != TaskPhase.PENDING_REVIEW:
        return False, "当前不是待流程核对，无法确认材料。"
    from app.case_trace import add_review_log

    task.phase_status = TaskPhase.PENDING_SUBMIT
    add_review_log(
        case_id=task.case_id,
        action="process_accept",
        operator=operator,
        recipient=task.assignee,
    )
    return True, "已确认材料，案件进入待递交官方。"


def process_reject_writing(task: "Task", operator, *, note: str) -> tuple[bool, str]:
    """流程打回撰写师：待流程核对 → 撰写中。"""
    ok, message = _require_process_owner(task, operator)
    if not ok:
        return False, message
    if phase_for_workflow(task.phase_status) != TaskPhase.PENDING_REVIEW:
        return False, "当前不是待流程核对，无法打回。"
    reason = (note or "").strip()
    if not reason:
        return False, "打回时请填写原因。"
    from app.case_trace import add_review_log

    task.phase_status = TaskPhase.IN_PROGRESS
    add_review_log(
        case_id=task.case_id,
        action="process_reject",
        operator=operator,
        recipient=task.assignee,
        note=reason,
    )
    return True, "已打回撰写师修改。"


def process_mark_filed(task: "Task", operator) -> tuple[bool, str]:
    """流程标记已向官方递交：待递交官方 → 官方处理中。"""
    ok, message = _require_process_owner(task, operator)
    if not ok:
        return False, message
    if phase_for_workflow(task.phase_status) != TaskPhase.PENDING_SUBMIT:
        return False, "当前不是待递交官方，无法标记已递交。"
    from app.case_trace import add_review_log

    task.phase_status = TaskPhase.OFFICE_ACTION
    add_review_log(
        case_id=task.case_id,
        action="process_filed",
        operator=operator,
        recipient=task.assignee,
    )
    return True, "已标记递交官方，案件进入官方处理中。"


def process_submit_final_review(task: "Task", operator) -> tuple[bool, str]:
    """流程提交管理终审：官方处理中 / 授权待缴费 → 待终审。"""
    ok, message = _require_process_owner(task, operator)
    if not ok:
        return False, message
    base = phase_for_workflow(task.phase_status)
    if base not in {TaskPhase.OFFICE_ACTION, TaskPhase.AUTHORIZED_PENDING_PAYMENT}:
        return False, "请先完成官方递交与往来，再提交终审。"
    from app.collections import final_review_block_reason

    blocked = final_review_block_reason(task.case)
    if blocked:
        return False, blocked
    from app.case_trace import add_review_log

    task.phase_status = TaskPhase.PENDING_FINAL_REVIEW
    add_review_log(
        case_id=task.case_id,
        action="submit_final_review",
        operator=operator,
        recipient=task.assignee,
    )
    return True, "已提交管理员终审。"


def staff_phase_options_for_ui(current_phase: str) -> list[str]:
    """案件详情页下拉框：仅展示当前阶段允许切换到的阶段（含当前逻辑阶段）。"""
    cur = phase_for_workflow(current_phase)
    if is_terminal_phase(cur):
        return [current_phase]
    allowed = STAFF_ALLOWED_TRANSITIONS.get(cur)
    if allowed is None:
        return [cur]
    if not allowed:
        return [cur]
    order_index = {p: i for i, p in enumerate(_STAFF_PHASE_ORDER)}
    merged = set(allowed) | {cur}
    return sorted(merged, key=lambda p: order_index.get(p, 99))

_PHASE_LABELS: dict[str, str] = {
    TaskPhase.PENDING_ASSIGNMENT: "待分配",
    TaskPhase.PENDING_ORDER_REVIEW: "待下单确认",
    TaskPhase.ORDER_REVISION: "下单待修改",
    TaskPhase.IN_PROGRESS: "撰写中",
    TaskPhase.PENDING_REVIEW: "待流程核对",
    TaskPhase.PENDING_SUBMIT: "待递交官方",
    TaskPhase.AUTHORIZED_PENDING_PAYMENT: "授权待缴费",
    TaskPhase.OFFICE_ACTION: "官方处理中",
    TaskPhase.PENDING_FINAL_REVIEW: "待终审",
    TaskPhase.ON_HOLD: "暂停",
    TaskPhase.OVERDUE_IN_PROGRESS: "撰写中（已超期）",
    TaskPhase.OVERDUE_PENDING_REVIEW: "待流程核对（已超期）",
    TaskPhase.OVERDUE_PENDING_SUBMIT: "待递交官方（已超期）",
    TaskPhase.OVERDUE_OFFICE_ACTION: "官方处理中（已超期）",
    TaskPhase.OVERDUE_ON_HOLD: "暂停（已超期）",
    TaskPhase.COMPLETED: "已完成",
}

for _legacy in LEGACY_TERMINAL_PHASES:
    _PHASE_LABELS[_legacy] = "已完成"


def is_overdue_phase(phase_status: str) -> bool:
    """阶段串是否为系统标记的"已超期"派生状态（overdue_*）。"""
    return phase_status in OVERDUE_PHASES


def normalize_legacy_overdue_pending_review() -> int:
    """将历史 overdue_pending_review 批量改回 pending_review；返回更新条数。"""
    from app.models import Task

    return Task.query.filter_by(phase_status=TaskPhase.OVERDUE_PENDING_REVIEW).update(
        {Task.phase_status: TaskPhase.PENDING_REVIEW},
        synchronize_session=False,
    )


def normalize_legacy_draft_phases() -> int:
    """将已废弃的草稿阶段批量改为撰写中；返回更新条数。"""
    from app.models import Task

    n = Task.query.filter_by(phase_status="draft").update(
        {Task.phase_status: TaskPhase.IN_PROGRESS},
        synchronize_session=False,
    )
    n += Task.query.filter_by(phase_status="overdue_draft").update(
        {Task.phase_status: TaskPhase.OVERDUE_IN_PROGRESS},
        synchronize_session=False,
    )
    return n


def _latest_staff_material_upload_at(case: "Case", *, before: datetime | None = None) -> datetime | None:
    """取员工撰写材料上传时间；可选仅统计某时刻之前。

    账号停用或行不在时，用上传时冻结的 uploaded_by_role，避免办结时间算丢。
    """
    from sqlalchemy import or_

    from app.models import CaseMaterial, User

    query = (
        CaseMaterial.query.outerjoin(User, CaseMaterial.uploaded_by_id == User.id)
        .filter(
            CaseMaterial.case_id == case.id,
            or_(User.role == "staff", CaseMaterial.uploaded_by_role == "staff"),
        )
    )
    if before is not None:
        query = query.filter(CaseMaterial.created_at <= before)
    latest_writing = query.order_by(
        CaseMaterial.created_at.desc(),
        CaseMaterial.id.desc(),
    ).first()
    return latest_writing.created_at if latest_writing is not None else None


def _material_upload_before_approval(case: "Case", approval_at: datetime) -> datetime | None:
    """历史兼容：取审核通过前最后一次员工撰写材料上传时间。"""
    return _latest_staff_material_upload_at(case, before=approval_at)


def _submit_for_review_before_approval(case: "Case", approval_at: datetime) -> datetime | None:
    """取某次审核通过所对应的最后一次提交审核时间。"""
    from app.models import CaseReviewLog

    last_reject = (
        CaseReviewLog.query.filter(
            CaseReviewLog.case_id == case.id,
            CaseReviewLog.action.in_(("reject", "process_reject")),
            CaseReviewLog.created_at <= approval_at,
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    submit_query = CaseReviewLog.query.filter(
        CaseReviewLog.case_id == case.id,
        CaseReviewLog.action == "submit_for_review",
        CaseReviewLog.created_at <= approval_at,
    )
    if last_reject is not None:
        submit_query = submit_query.filter(CaseReviewLog.created_at > last_reject.created_at)
    latest_submit = submit_query.order_by(
        CaseReviewLog.created_at.desc(),
        CaseReviewLog.id.desc(),
    ).first()
    return latest_submit.created_at if latest_submit is not None else None


def set_actual_return_at_from_latest_approved_writing(case: "Case") -> datetime | None:
    """设置案件实际返稿时间（两条路径互斥，不必同时满足）：

    1. 有审核通过记录 → 以审核为准（对应那次提交审核时间；无提交留痕则用通过前最后员工材料时间）。
    2. 无审核通过记录 → 以员工最后一次上传撰写材料的时间为准（早期直接改已完成、未走审核）。
    """
    from app.models import CaseReviewLog

    latest_approval = (
        CaseReviewLog.query.filter_by(case_id=case.id, action="approve")
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    if latest_approval is None:
        # 无审核：仅看员工材料，有则取最晚上传时间
        case.actual_return_at = _latest_staff_material_upload_at(case)
        return case.actual_return_at

    # 有审核：只走审核链路，不要求「同时还要有材料」之外的额外条件
    approval_at = latest_approval.created_at
    submit_at = _submit_for_review_before_approval(case, approval_at)
    case.actual_return_at = (
        submit_at
        or _material_upload_before_approval(case, approval_at)
        or _latest_staff_material_upload_at(case)
    )
    return case.actual_return_at


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    """比较两个时间是否同一时刻（忽略 naive/aware 差异）。"""
    if left is None or right is None:
        return left is right
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left == right


def backfill_completed_case_actual_return_at() -> int:
    """仅为「已完成且实际返稿时间为空」的案件补算，绝不覆盖已有时间。"""
    from app.models import Case, Task

    completed_cases = (
        Case.query.join(Task, Task.case_id == Case.id)
        .filter(
            Task.phase_status.in_(TaskPhase.TERMINAL | LEGACY_TERMINAL_PHASES),
            Case.actual_return_at.is_(None),
        )
        .all()
    )
    changed = 0
    for case in completed_cases:
        previous = case.actual_return_at
        current = set_actual_return_at_from_latest_approved_writing(case)
        # 算不出有效时间时保持为空，避免把已有值写成 None（本函数已排除有值案件）
        if current is None:
            case.actual_return_at = previous
            continue
        if not _same_instant(previous, current):
            changed += 1
    return changed


_PENDING_FINAL_REVIEW_PHASES = frozenset({TaskPhase.PENDING_FINAL_REVIEW})


def is_pending_review_phase(phase_status: str) -> bool:
    """是否为待流程核对（含历史 overdue_pending_review）。"""
    return phase_status in _PENDING_REVIEW_PHASES


def is_pending_final_review_phase(phase_status: str) -> bool:
    """是否为待管理终审。"""
    return phase_status in _PENDING_FINAL_REVIEW_PHASES


def is_gated_review_phase(phase_status: str) -> bool:
    """流程核对或管理终审：都不因超期改写 phase_status。"""
    return is_pending_review_phase(phase_status) or is_pending_final_review_phase(phase_status)


def task_phase_label(phase_status: str) -> str:
    """将 phase_status 翻译为中文界面标签；未知值原样返回，便于排查。"""
    if phase_status == TaskPhase.OVERDUE_PENDING_REVIEW:
        return _PHASE_LABELS[TaskPhase.PENDING_REVIEW]
    return _PHASE_LABELS.get(phase_status, phase_status)


def _utcnow() -> datetime:
    """返回当前 UTC 时间；集中封装便于在测试中 monkeypatch。"""
    return datetime.now(timezone.utc)


def effective_task_due_at(task: Task) -> datetime | None:
    """任务截止：任务 due_at > 案件应返稿时间 > 项目 due_at。"""
    if task.due_at is not None:
        return task.due_at
    if task.case and task.case.expected_return_at is not None:
        return task.case.expected_return_at
    if task.case and task.case.project:
        return task.case.project.due_at
    return None


def is_task_past_due(task: Task, *, now: datetime | None = None) -> bool:
    """是否已过有效截止时间（与是否改写 phase_status 无关）。"""
    due = effective_task_due_at(task)
    if due is None:
        return False
    now = now or _utcnow()
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now > due


def effective_task_phase(task: Task, *, now: datetime | None = None) -> str:
    """只读计算任务当前展示阶段，不修改 ORM 对象或数据库。"""
    status = normalize_task_phase(task.phase_status)
    if is_terminal_phase(status) or status == TaskPhase.PENDING_ASSIGNMENT or is_order_intake_phase(status):
        return status
    if is_gated_review_phase(status):
        if is_pending_review_phase(status):
            return TaskPhase.PENDING_REVIEW
        return TaskPhase.PENDING_FINAL_REVIEW

    base = _OVERDUE_TO_BASE.get(status, status)
    if is_task_past_due(task, now=now) and base in _OVERDUE_MAP:
        return _OVERDUE_MAP[base]
    return base


def apply_task_overdue_status(task: Task, *, now: datetime | None = None) -> bool:
    """
    根据截止时间刷新 task.phase_status（超期视为独立状态）。
    待流程核对、待终审不因超期改写状态，便于各自队列统一处理。
    返回是否发生了变更（便于提交前判断）。
    """
    now = now or _utcnow()
    if (
        is_terminal_phase(task.phase_status)
        or task.phase_status == TaskPhase.PENDING_ASSIGNMENT
        or is_order_intake_phase(task.phase_status)
    ):
        return False

    if is_gated_review_phase(task.phase_status):
        if task.phase_status == TaskPhase.OVERDUE_PENDING_REVIEW:
            task.phase_status = TaskPhase.PENDING_REVIEW
            return True
        return False

    due = effective_task_due_at(task)
    if due is None:
        return False

    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    past_due = now > due
    status = task.phase_status
    changed = False

    if past_due:
        base = _OVERDUE_TO_BASE.get(status, status)
        if base in _OVERDUE_MAP:
            next_status = _OVERDUE_MAP[base]
            if status != next_status:
                task.phase_status = next_status
                changed = True
    else:
        if status in _OVERDUE_TO_BASE:
            base = _OVERDUE_TO_BASE[status]
            if status != base:
                task.phase_status = base
                changed = True

    return changed

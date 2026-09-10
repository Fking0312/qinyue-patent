"""智能派单：按撰写师在派单窗口内的负载排序，给管理员一个手动派单的建议顺序。

排序思路：只数在办件数会失真——手上 5 件但都在三个月后交的人，比手上 2 件都在
本周交的人更闲。所以以待派案件的截止时间为窗口末端，只统计截止时间落在窗口内的
在办案件，这些才是真正与新案件抢时间的。窗口内折算工作量越少的排在越前面。

不设人均产能上限，只比相对忙闲；因此这里给出的是排序建议，派单仍由管理员手动点。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app.case_types import (
    CASE_TYPE_BY_CODE,
    legacy_case_type_code,
    normalize_case_type_code,
)
from app.extensions import db
from app.models import Case, CaseCollection, CaseMaterial, CaseReviewLog, OfficialNotice, Project, Task, User
from app.workflow import (
    INTAKE_PHASES,
    LEGACY_TERMINAL_PHASES,
    TaskPhase,
    effective_task_due_at,
    effective_task_phase,
    is_order_intake_phase,
    is_overdue_phase,
    is_task_past_due,
    is_terminal_phase,
)

# 案件折算工作量：以「实用新型 = 1」为基准的一级类型系数。
# 口径要按实际工时调整时只改这张表，页面排序与展示都会跟着变。
PRIMARY_WORKLOAD_WEIGHTS: dict[str, float] = {
    "invention": 3.0,
    "utility": 1.0,
    "trademark": 0.5,
    "ic_layout": 1.0,
    "other": 1.0,
}

# 叶子类型覆盖：外观专利挂在「实用新型」一级下，但工作量更接近商标。
LEAF_WORKLOAD_WEIGHTS: dict[str, float] = {
    "utility_design": 0.5,
}

DEFAULT_WORKLOAD_WEIGHT = 1.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def case_workload_weight(case: Case | None) -> float:
    """案件折算工作量；类型缺失或未知时按 1 件基准计。"""
    if case is None:
        return DEFAULT_WORKLOAD_WEIGHT
    code = normalize_case_type_code(
        case.case_type_code or legacy_case_type_code(case.project_type) or ""
    )
    if code in LEAF_WORKLOAD_WEIGHTS:
        return LEAF_WORKLOAD_WEIGHTS[code]
    leaf = CASE_TYPE_BY_CODE.get(code)
    if leaf is None:
        return DEFAULT_WORKLOAD_WEIGHT
    return PRIMARY_WORKLOAD_WEIGHTS.get(leaf.primary, DEFAULT_WORKLOAD_WEIGHT)


def case_due_at(case: Case) -> datetime | None:
    """案件的有效截止时间；沿用任务侧的回落链，无任务时直接看案件与项目。"""
    if case.task is not None:
        return effective_task_due_at(case.task)
    if case.expected_return_at is not None:
        return case.expected_return_at
    if case.project is not None:
        return case.project.due_at
    return None


def pending_assignment_cases() -> list[Case]:
    """待分配案件：任务处于待分配，或历史脏数据里根本没有任务行。

    退稿转入的内部案件仍然要能派单（客户端不可见，但所里可以自己重做），
    所以这里不按归属过滤，只在页面上标出内部案件。
    """
    cases = (
        Case.query.outerjoin(Task, Task.case_id == Case.id)
        .filter(or_(Task.id.is_(None), Task.phase_status == TaskPhase.PENDING_ASSIGNMENT))
        .all()
    )

    def sort_key(case: Case) -> tuple:
        due = _as_utc(case_due_at(case))
        fallback = _as_utc(case.created_at) or _utcnow()
        return (due is None, due or fallback)

    cases.sort(key=sort_key)
    return cases


def _staff_user(user_id: int | None) -> User | None:
    """按主键取员工；用户不存在或不是员工时视为无线索。"""
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None or user.role != "staff":
        return None
    return user


def original_writer(case: Case) -> tuple[User | None, str]:
    """推导「原来写这件案子的人」，返回（员工, 线索来源）。

    退稿案件一般派回原撰写师，所以这个人必须能查出来。2026-09-08 起标记退稿会把
    当时的承办人写进 office_reject 日志，那是最权威的一条；之前的历史案件只能按
    可信度依次回落，都查不到时返回 (None, "")。
    """
    if case is None:
        return None, ""

    reject_log = (
        CaseReviewLog.query.filter(
            CaseReviewLog.case_id == case.id,
            CaseReviewLog.action == "office_reject",
            CaseReviewLog.recipient_id.isnot(None),
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    if reject_log is not None:
        user = _staff_user(reject_log.recipient_id)
        if user is not None:
            return user, "退稿时的承办人"

    assigned_log = (
        CaseReviewLog.query.filter(
            CaseReviewLog.case_id == case.id,
            CaseReviewLog.action == "assigned",
            CaseReviewLog.recipient_id.isnot(None),
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    if assigned_log is not None:
        user = _staff_user(assigned_log.recipient_id)
        if user is not None:
            return user, "最后一次指派记录"

    if case.business_owner_id is not None:
        user = _staff_user(case.business_owner_id)
        if user is not None:
            return user, "案件业务负责人"

    material = (
        CaseMaterial.query.outerjoin(User, CaseMaterial.uploaded_by_id == User.id)
        .filter(
            CaseMaterial.case_id == case.id,
            or_(User.role == "staff", CaseMaterial.uploaded_by_role == "staff"),
        )
        .order_by(CaseMaterial.created_at.desc(), CaseMaterial.id.desc())
        .first()
    )
    if material is not None and material.uploaded_by is not None:
        return material.uploaded_by, "撰写稿上传人"

    return None, ""


def departed_assignee_cases() -> list[Case]:
    """承办人已离职或已转非撰写职能、且仍需要处理的案件。

    离职退单只把「撰写中」退回待分配，而退稿通常发生在提交之后（案件停在已完成），
    于是这类案件会继续挂在离职者名下，既不在派单池里也没人管。这里把它们捞出来，
    是否重派由管理员逐件决定——退稿了不一定要自己重做。
    """
    tasks = (
        Task.query.join(User, Task.assignee_id == User.id)
        .join(Case, Task.case_id == Case.id)
        .filter(Task.assignee_id.isnot(None))
        .all()
    )
    cases: list[Case] = []
    for task in tasks:
        assignee = task.assignee
        if assignee is None or assignee.is_assignable_writer:
            continue
        case = task.case
        if case is None:
            continue
        # 已完成且未退稿的案件属于正常历史留痕，不需要重新分配。
        if is_terminal_phase(task.phase_status) and not case.is_rejected:
            continue
        cases.append(case)

    def sort_key(case: Case) -> tuple:
        due = _as_utc(case_due_at(case))
        fallback = _as_utc(case.created_at) or _utcnow()
        return (not case.is_rejected, due is None, due or fallback)

    cases.sort(key=sort_key)
    return cases


def _open_tasks_for(writer_ids: list[int]) -> list[Task]:
    """撰写师手上未完成且已指派的任务；待分配不算任何人的负载。"""
    if not writer_ids:
        return []
    tasks = (
        Task.query.filter(
            Task.assignee_id.in_(writer_ids),
            Task.phase_status != TaskPhase.PENDING_ASSIGNMENT,
        )
        .all()
    )
    return [task for task in tasks if not is_terminal_phase(task.phase_status)]


def writer_load_rows(
    writers: list[User],
    *,
    window_end: datetime | None = None,
    now: datetime | None = None,
    exclude_case_id: int | None = None,
    pin_user_id: int | None = None,
) -> list[dict]:
    """按窗口内折算工作量升序排出撰写师建议顺序。

    window_end 为空时退化为只比当前总负载（不做时间窗口计算）。
    pin_user_id 用于把原撰写师顶到第一位（退稿案件一般派回原来写的那个人），
    他的忙闲数字照常计算并展示，方便管理员判断他现在忙不忙。
    """
    now = _as_utc(now) or _utcnow()
    window_end = _as_utc(window_end)
    # 待派案件本身已超期时，窗口会缩成一个过去的时间点，导致谁都「没有负载」。
    # 窗口末端至少取到现在，这样已经积压的超期案件始终算进负载——它们照样在抢时间。
    if window_end is not None and window_end < now:
        window_end = now
    stats = {
        writer.id: {
            "user": writer,
            "open_count": 0,
            "open_workload": 0.0,
            "window_count": 0,
            "window_workload": 0.0,
            "overdue_count": 0,
            "undated_count": 0,
            "next_due_at": None,
        }
        for writer in writers
    }

    for task in _open_tasks_for(list(stats.keys())):
        row = stats.get(task.assignee_id)
        if row is None or task.case_id == exclude_case_id:
            continue
        weight = case_workload_weight(task.case)
        due = _as_utc(effective_task_due_at(task))
        row["open_count"] += 1
        row["open_workload"] += weight
        if is_overdue_phase(effective_task_phase(task)) or is_task_past_due(task, now=now):
            row["overdue_count"] += 1
        if due is None:
            row["undated_count"] += 1
        else:
            if row["next_due_at"] is None or due < row["next_due_at"]:
                row["next_due_at"] = due
            if window_end is None or due <= window_end:
                row["window_count"] += 1
                row["window_workload"] += weight

    rows = list(stats.values())
    rows.sort(
        key=lambda row: (
            round(row["window_workload"], 3),
            row["overdue_count"],
            round(row["open_workload"], 3),
            (row["user"].username or "").lower(),
            row["user"].id,
        )
    )

    heaviest = max((row["window_workload"] for row in rows), default=0.0)
    for row in rows:
        row["is_least_busy"] = False
    if rows:
        # 「最闲」按负载判定，与置顶无关：原撰写师被顶到第一位时不该冒充最闲。
        rows[0]["is_least_busy"] = True

    if pin_user_id is not None:
        pinned = next((row for row in rows if row["user"].id == pin_user_id), None)
        if pinned is not None:
            rows.remove(pinned)
            rows.insert(0, pinned)

    for index, row in enumerate(rows):
        row["rank"] = index + 1
        row["is_original_writer"] = row["user"].id == pin_user_id
        # 被置顶的原撰写师如果自己也偏忙，要明确说出来，别让置顶盖住负载事实。
        row["pinned_but_busy"] = row["is_original_writer"] and heaviest > 0 and (
            row["window_workload"] >= heaviest * 0.8
        )
        # 相对忙闲条：以当前最忙的人为满格，全员为 0 时不画条。
        row["busy_percent"] = (
            int(round(row["window_workload"] / heaviest * 100)) if heaviest > 0 else 0
        )
    return rows


def window_days(window_end: datetime | None, now: datetime | None = None) -> int | None:
    """窗口剩余天数；已过截止时间返回负数，供页面提示。"""
    window_end = _as_utc(window_end)
    if window_end is None:
        return None
    now = _as_utc(now) or _utcnow()
    delta = window_end - now
    return int(delta.total_seconds() // 86400)


def _intake_and_terminal_phases() -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(INTAKE_PHASES), tuple(TaskPhase.TERMINAL | LEGACY_TERMINAL_PHASES)


def pending_process_assignment_query():
    """未指定（或原流程人员已不在岗）且仍需要流程跟进的案件。

    下单待确认/待修改在确认页一并指定，不进这个池子。已完成且未退稿的历史案件
    即使没留流程负责人也不捞，避免把旧案塞进来。退稿后仍挂着已离职流程人员的，
    要能重新指定，否则核对/交局/官文会找不到人。
    """
    intake, terminal = _intake_and_terminal_phases()
    return (
        Case.query.outerjoin(Task, Task.case_id == Case.id)
        .outerjoin(User, Case.process_owner_id == User.id)
        .filter(or_(Task.id.is_(None), Task.phase_status.notin_(intake)))
        .filter(
            or_(
                Case.process_owner_id.is_(None),
                User.id.is_(None),
                User.is_active.is_(False),
                User.staff_function.is_(None),
                User.staff_function != User.STAFF_FUNCTION_PROCESS,
            )
        )
        .filter(
            or_(
                Task.id.is_(None),
                Task.phase_status.notin_(terminal),
                and_(Case.rejected_at.isnot(None), Case.process_owner_id.isnot(None)),
            )
        )
    )


def pending_process_assignment_count() -> int:
    return pending_process_assignment_query().count()


def pending_process_assignment_cases() -> list[Case]:
    cases = (
        pending_process_assignment_query()
        .options(
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.task).joinedload(Task.assignee),
            joinedload(Case.process_owner_user),
            joinedload(Case.business_owner_user),
        )
        .all()
    )

    def sort_key(case: Case) -> tuple:
        due = _as_utc(case_due_at(case))
        fallback = _as_utc(case.created_at) or _utcnow()
        departed = case.process_owner_id is not None
        return (not departed, due is None, due or fallback)

    cases.sort(key=sort_key)
    return cases


def pending_billing_assignment_query():
    """未指定（或原收账人员已不在岗）且已有缴费/收账的案件。

    没有缴费通知也没有收账记录的案子不必塞进这个池子，管理员仍可在案件编辑里预指定。
    下单待确认不进池。已完成且未退稿的历史案件不捞。
    """
    intake, terminal = _intake_and_terminal_phases()
    fee_case_ids = db.session.query(OfficialNotice.case_id).filter(
        OfficialNotice.notice_type.in_(tuple(OfficialNotice.BILLING_TYPES))
    )
    collection_case_ids = db.session.query(CaseCollection.case_id)
    return (
        Case.query.outerjoin(Task, Task.case_id == Case.id)
        .outerjoin(User, Case.billing_owner_id == User.id)
        .filter(or_(Task.id.is_(None), Task.phase_status.notin_(intake)))
        .filter(
            or_(
                Case.billing_owner_id.isnot(None),
                Case.id.in_(fee_case_ids),
                Case.id.in_(collection_case_ids),
            )
        )
        .filter(
            or_(
                Case.billing_owner_id.is_(None),
                User.id.is_(None),
                User.is_active.is_(False),
                User.staff_function.is_(None),
                User.staff_function != User.STAFF_FUNCTION_BUSINESS,
            )
        )
        .filter(
            or_(
                Task.id.is_(None),
                Task.phase_status.notin_(terminal),
                and_(Case.rejected_at.isnot(None), Case.billing_owner_id.isnot(None)),
            )
        )
    )


def pending_billing_assignment_count() -> int:
    return pending_billing_assignment_query().count()


def pending_billing_assignment_cases() -> list[Case]:
    cases = (
        pending_billing_assignment_query()
        .options(
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.task).joinedload(Task.assignee),
            joinedload(Case.billing_owner_user),
            joinedload(Case.business_owner_user),
        )
        .all()
    )

    def sort_key(case: Case) -> tuple:
        due = _as_utc(case_due_at(case))
        fallback = _as_utc(case.created_at) or _utcnow()
        departed = case.billing_owner_id is not None
        return (not departed, due is None, due or fallback)

    cases.sort(key=sort_key)
    return cases


def original_billing_owner(case: Case) -> tuple[User | None, str]:
    """推导「原来负责收账的业务人员」，返回（员工, 线索来源）。"""
    if case is None:
        return None, ""

    assigned_log = (
        CaseReviewLog.query.filter(
            CaseReviewLog.case_id == case.id,
            CaseReviewLog.action == "billing_assigned",
            CaseReviewLog.recipient_id.isnot(None),
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    if assigned_log is not None:
        user = _staff_user(assigned_log.recipient_id)
        if user is not None:
            return user, "最后一次指定记录"

    if case.billing_owner_id is not None:
        user = _staff_user(case.billing_owner_id)
        if user is not None:
            return user, "案件收账负责人"

    return None, ""


def original_process_owner(case: Case) -> tuple[User | None, str]:
    """推导「原来跟这件案子的流程人员」，返回（员工, 线索来源）。"""
    if case is None:
        return None, ""

    assigned_log = (
        CaseReviewLog.query.filter(
            CaseReviewLog.case_id == case.id,
            CaseReviewLog.action == "process_assigned",
            CaseReviewLog.recipient_id.isnot(None),
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    if assigned_log is not None:
        user = _staff_user(assigned_log.recipient_id)
        if user is not None:
            return user, "最后一次指定记录"

    if case.process_owner_id is not None:
        user = _staff_user(case.process_owner_id)
        if user is not None:
            return user, "案件流程负责人"

    return None, ""


def _nearest_official_due_by_case(case_ids: list[int]) -> dict[int, datetime]:
    if not case_ids:
        return {}
    rows = (
        db.session.query(OfficialNotice.case_id, func.min(OfficialNotice.official_due_at))
        .filter(
            OfficialNotice.case_id.in_(case_ids),
            OfficialNotice.official_due_at.isnot(None),
        )
        .group_by(OfficialNotice.case_id)
        .all()
    )
    result: dict[int, datetime] = {}
    for case_id, due in rows:
        utc_due = _as_utc(due)
        if utc_due is not None:
            result[case_id] = utc_due
    return result


def _open_process_cases(process_ids: list[int]) -> list[Case]:
    """流程人员名下未完成的案件；下单确认链路与已完成不算负载。"""
    if not process_ids:
        return []
    cases = (
        Case.query.outerjoin(Task, Task.case_id == Case.id)
        .options(joinedload(Case.task))
        .filter(Case.process_owner_id.in_(process_ids))
        .all()
    )
    open_cases: list[Case] = []
    for case in cases:
        task = case.task
        if task is None:
            open_cases.append(case)
            continue
        if is_order_intake_phase(task.phase_status) or is_terminal_phase(task.phase_status):
            continue
        open_cases.append(case)
    return open_cases


def process_load_rows(
    process_staff: list[User],
    *,
    window_end: datetime | None = None,
    now: datetime | None = None,
    exclude_case_id: int | None = None,
    pin_user_id: int | None = None,
) -> list[dict]:
    """按窗口内折算工作量升序排出流程人员建议顺序。

    流程跟进的截止优先看官文期限，没有官文再回落到案件应返稿时间。
    选中案件没有应返稿时间时不做窗口裁剪，改按在办总负载排，避免卡死指定。
    """
    now = _as_utc(now) or _utcnow()
    window_end = _as_utc(window_end)
    if window_end is not None and window_end < now:
        window_end = now
    stats = {
        person.id: {
            "user": person,
            "open_count": 0,
            "open_workload": 0.0,
            "window_count": 0,
            "window_workload": 0.0,
            "overdue_count": 0,
            "undated_count": 0,
            "next_due_at": None,
        }
        for person in process_staff
    }

    open_cases = _open_process_cases(list(stats.keys()))
    official_dues = _nearest_official_due_by_case(
        [case.id for case in open_cases if case.id != exclude_case_id]
    )

    for case in open_cases:
        row = stats.get(case.process_owner_id)
        if row is None or case.id == exclude_case_id:
            continue
        weight = case_workload_weight(case)
        due = official_dues.get(case.id) or _as_utc(case_due_at(case))
        task = case.task
        row["open_count"] += 1
        row["open_workload"] += weight
        if task is not None and (
            is_overdue_phase(effective_task_phase(task)) or is_task_past_due(task, now=now)
        ):
            row["overdue_count"] += 1
        elif due is not None and due < now:
            row["overdue_count"] += 1
        if due is None:
            row["undated_count"] += 1
            if window_end is None:
                row["window_count"] += 1
                row["window_workload"] += weight
        else:
            if row["next_due_at"] is None or due < row["next_due_at"]:
                row["next_due_at"] = due
            if window_end is None or due <= window_end:
                row["window_count"] += 1
                row["window_workload"] += weight

    rows = list(stats.values())
    rows.sort(
        key=lambda row: (
            round(row["window_workload"], 3),
            row["overdue_count"],
            round(row["open_workload"], 3),
            (row["user"].username or "").lower(),
            row["user"].id,
        )
    )

    heaviest = max((row["window_workload"] for row in rows), default=0.0)
    for row in rows:
        row["is_least_busy"] = False
    if rows:
        rows[0]["is_least_busy"] = True

    if pin_user_id is not None:
        pinned = next((row for row in rows if row["user"].id == pin_user_id), None)
        if pinned is not None:
            rows.remove(pinned)
            rows.insert(0, pinned)

    for index, row in enumerate(rows):
        row["rank"] = index + 1
        row["is_original_process"] = row["user"].id == pin_user_id
        row["pinned_but_busy"] = row["is_original_process"] and heaviest > 0 and (
            row["window_workload"] >= heaviest * 0.8
        )
        row["busy_percent"] = (
            int(round(row["window_workload"] / heaviest * 100)) if heaviest > 0 else 0
        )
    return rows


def _open_billing_cases(billing_ids: list[int]) -> list[Case]:
    """收账人员名下未完成的案件；下单确认链路与已完成不算负载。"""
    if not billing_ids:
        return []
    cases = (
        Case.query.outerjoin(Task, Task.case_id == Case.id)
        .options(joinedload(Case.task))
        .filter(Case.billing_owner_id.in_(billing_ids))
        .all()
    )
    open_cases: list[Case] = []
    for case in cases:
        task = case.task
        if task is None:
            open_cases.append(case)
            continue
        if is_order_intake_phase(task.phase_status) or is_terminal_phase(task.phase_status):
            continue
        open_cases.append(case)
    return open_cases


def billing_load_rows(
    billing_staff: list[User],
    *,
    window_end: datetime | None = None,
    now: datetime | None = None,
    exclude_case_id: int | None = None,
    pin_user_id: int | None = None,
) -> list[dict]:
    """按窗口内折算工作量升序排出业务收账人员建议顺序。"""
    now = _as_utc(now) or _utcnow()
    window_end = _as_utc(window_end)
    if window_end is not None and window_end < now:
        window_end = now
    stats = {
        person.id: {
            "user": person,
            "open_count": 0,
            "open_workload": 0.0,
            "window_count": 0,
            "window_workload": 0.0,
            "overdue_count": 0,
            "undated_count": 0,
            "next_due_at": None,
        }
        for person in billing_staff
    }

    open_cases = _open_billing_cases(list(stats.keys()))
    official_dues = _nearest_official_due_by_case(
        [case.id for case in open_cases if case.id != exclude_case_id]
    )

    for case in open_cases:
        row = stats.get(case.billing_owner_id)
        if row is None or case.id == exclude_case_id:
            continue
        weight = case_workload_weight(case)
        due = official_dues.get(case.id) or _as_utc(case_due_at(case))
        task = case.task
        row["open_count"] += 1
        row["open_workload"] += weight
        if task is not None and (
            is_overdue_phase(effective_task_phase(task)) or is_task_past_due(task, now=now)
        ):
            row["overdue_count"] += 1
        elif due is not None and due < now:
            row["overdue_count"] += 1
        if due is None:
            row["undated_count"] += 1
            if window_end is None:
                row["window_count"] += 1
                row["window_workload"] += weight
        else:
            if row["next_due_at"] is None or due < row["next_due_at"]:
                row["next_due_at"] = due
            if window_end is None or due <= window_end:
                row["window_count"] += 1
                row["window_workload"] += weight

    rows = list(stats.values())
    rows.sort(
        key=lambda row: (
            round(row["window_workload"], 3),
            row["overdue_count"],
            round(row["open_workload"], 3),
            (row["user"].username or "").lower(),
            row["user"].id,
        )
    )

    heaviest = max((row["window_workload"] for row in rows), default=0.0)
    for row in rows:
        row["is_least_busy"] = False
    if rows:
        rows[0]["is_least_busy"] = True

    if pin_user_id is not None:
        pinned = next((row for row in rows if row["user"].id == pin_user_id), None)
        if pinned is not None:
            rows.remove(pinned)
            rows.insert(0, pinned)

    for index, row in enumerate(rows):
        row["rank"] = index + 1
        row["is_original_billing"] = row["user"].id == pin_user_id
        row["pinned_but_busy"] = row["is_original_billing"] and heaviest > 0 and (
            row["window_workload"] >= heaviest * 0.8
        )
        row["busy_percent"] = (
            int(round(row["window_workload"] / heaviest * 100)) if heaviest > 0 else 0
        )
    return rows

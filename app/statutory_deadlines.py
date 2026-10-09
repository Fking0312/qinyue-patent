"""管理端官方期限看板：监控已上传官文的 official_due_at，并催办当前负责人。

与「超时预警看板」分开：那边看所内应返稿，这边只看流程人员上传的官方期限。
未上传的来文没有官方期限，不在此生成任务、不做法定期限日历。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import joinedload

from app.case_trace import add_review_log
from app.extensions import db
from app.models import Case, CaseReviewLog, OfficialNotice, Project, Task, User
from app.official_notices import case_writer, date_input_value
from app.workflow import TaskPhase, is_terminal_phase, phase_for_workflow

_CN_TZ = timezone(timedelta(hours=8))
ADMIN_URGE_ACTION = "admin_urge"
URGE_COOLDOWN = timedelta(hours=12)

KIND_FEE = "fee"
KIND_FORWARD = "forward"
KIND_UNRECEIVED = "unreceived"
KIND_REPLY = "reply"
KIND_REVIEW = "review"
KIND_SUBMIT = "submit"
KIND_LABELS = {
    KIND_FEE: "待官方缴费",
    KIND_FORWARD: "待转交撰写师",
    KIND_UNRECEIVED: "撰写师未接收",
    KIND_REPLY: "待撰写师答复",
    KIND_REVIEW: "待管理员审核",
    KIND_SUBMIT: "待递交官方",
}
KIND_VALUES = frozenset(KIND_LABELS)

OWNER_ROLE_WRITER = "writer"
OWNER_ROLE_PROCESS = "process"
OWNER_ROLE_ADMIN = "admin"
OWNER_ROLE_LABELS = {
    OWNER_ROLE_WRITER: "撰写师",
    OWNER_ROLE_PROCESS: "流程人员",
    OWNER_ROLE_ADMIN: "管理员",
}
OWNER_ROLE_VALUES = frozenset(OWNER_ROLE_LABELS)

WINDOW_7D = "7d"
WINDOW_3D = "3d"
WINDOW_OVERDUE = "overdue"
WINDOW_ALL = "all"
WINDOW_VALUES = frozenset({WINDOW_7D, WINDOW_3D, WINDOW_OVERDUE, WINDOW_ALL})
WINDOW_LABELS = {
    WINDOW_7D: "临期与超期",
    WINDOW_OVERDUE: "已超期",
    WINDOW_3D: "3 天内",
    WINDOW_ALL: "全部待办",
}

URGENCY_OVERDUE = "overdue"
URGENCY_3D = "3d"
URGENCY_7D = "7d"
URGENCY_LATER = "later"
URGENCY_LABELS = {
    URGENCY_OVERDUE: "已超期",
    URGENCY_3D: "3 天内",
    URGENCY_7D: "7 天内",
    URGENCY_LATER: "更晚",
}
URGENCY_RANK = {
    URGENCY_OVERDUE: 0,
    URGENCY_3D: 1,
    URGENCY_7D: 2,
    URGENCY_LATER: 3,
}


@dataclass(frozen=True)
class DeadlineItem:
    notice_id: int
    case_id: int
    case_title: str
    application_no: str
    customer_name: str
    notice_type: str
    notice_type_label: str
    kind: str
    kind_label: str
    official_due_at: datetime | None
    remaining_days: int | None
    remaining_label: str
    urgency: str
    urgency_label: str
    urgency_rank: int
    owner_id: int | None
    owner_label: str
    owner_role: str
    owner_role_label: str
    can_urge: bool
    urge_ready: bool = True
    cooldown_label: str = ""


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def remaining_calendar_days(due: datetime | None, now: datetime) -> int | None:
    due_utc = _as_utc(due)
    if due_utc is None:
        return None
    return (due_utc.astimezone(_CN_TZ).date() - now.astimezone(_CN_TZ).date()).days


def _urgency_for_remaining(days: int | None) -> tuple[str, str, int]:
    if days is None:
        return URGENCY_LATER, URGENCY_LABELS[URGENCY_LATER], URGENCY_RANK[URGENCY_LATER]
    if days < 0:
        return URGENCY_OVERDUE, URGENCY_LABELS[URGENCY_OVERDUE], URGENCY_RANK[URGENCY_OVERDUE]
    if days <= 3:
        return URGENCY_3D, URGENCY_LABELS[URGENCY_3D], URGENCY_RANK[URGENCY_3D]
    if days <= 7:
        return URGENCY_7D, URGENCY_LABELS[URGENCY_7D], URGENCY_RANK[URGENCY_7D]
    return URGENCY_LATER, URGENCY_LABELS[URGENCY_LATER], URGENCY_RANK[URGENCY_LATER]


def _remaining_label(days: int | None) -> str:
    if days is None:
        return "未填官方期限"
    if days < 0:
        return f"已超期 {abs(days)} 天"
    if days == 0:
        return "今天到期"
    return f"还剩 {days} 天"


def admin_urge_note_prefix(notice_id: int) -> str:
    return f"#N{notice_id} "


def parse_admin_urge_notice_id(note: str | None) -> int | None:
    text = (note or "").strip()
    if not text.startswith("#N"):
        return None
    token, _, _rest = text[2:].partition(" ")
    if token.isdigit():
        return int(token)
    return None


def display_admin_urge_note(note: str | None) -> str:
    text = (note or "").strip()
    notice_id = parse_admin_urge_notice_id(text)
    if notice_id is None:
        return text
    _prefix, sep, rest = text.partition(" ")
    return rest.strip() if sep else text


def _process_owner(case: Case | None) -> tuple[User | None, str]:
    if case is None:
        return None, "未指定流程"
    user = case.process_owner_user
    label = case.process_owner_display
    if user is not None and user.is_assignable_process:
        return user, label
    return None, label if label and label != "—" else "未指定流程"


def _writer_owner(notice: OfficialNotice, case: Case | None) -> tuple[User | None, str]:
    candidates = []
    if notice.forwarded_to is not None:
        candidates.append(notice.forwarded_to)
    writer = case_writer(case) if case is not None else None
    if writer is not None and all(user.id != writer.id for user in candidates):
        candidates.append(writer)
    for user in candidates:
        if user.is_assignable_writer:
            return user, user.display_label
    if notice.forwarded_to_display and notice.forwarded_to_display != "—":
        return None, notice.forwarded_to_display
    return None, "未指定撰写师"


def _item(
    *,
    notice: OfficialNotice,
    case: Case,
    kind: str,
    owner: User | None,
    owner_label: str,
    owner_role: str,
    can_urge: bool,
    now: datetime,
) -> DeadlineItem:
    remaining = remaining_calendar_days(notice.official_due_at, now)
    urgency, urgency_label, urgency_rank = _urgency_for_remaining(remaining)
    return DeadlineItem(
        notice_id=notice.id,
        case_id=case.id,
        case_title=case.title or "—",
        application_no=case.application_no or "—",
        customer_name=(
            case.project.customer.name
            if case.project is not None and case.project.customer is not None
            else "—"
        ),
        notice_type=notice.notice_type or "",
        notice_type_label=notice.type_label,
        kind=kind,
        kind_label=KIND_LABELS[kind],
        official_due_at=notice.official_due_at,
        remaining_days=remaining,
        remaining_label=_remaining_label(remaining),
        urgency=urgency,
        urgency_label=urgency_label,
        urgency_rank=urgency_rank,
        owner_id=owner.id if owner is not None else None,
        owner_label=owner_label,
        owner_role=owner_role,
        owner_role_label=OWNER_ROLE_LABELS[owner_role],
        can_urge=can_urge and owner is not None,
    )


def _latest_received_reply_ids(notices: list[OfficialNotice]) -> dict[int, int]:
    latest: dict[int, tuple[datetime, int]] = {}
    for notice in notices:
        if not notice.needs_writer_reply:
            continue
        received = _as_utc(notice.received_at)
        if received is None:
            continue
        current = latest.get(notice.case_id)
        if current is None or (received, notice.id) > current:
            latest[notice.case_id] = (received, notice.id)
    return {case_id: notice_id for case_id, (_when, notice_id) in latest.items()}


def classify_notice(
    notice: OfficialNotice,
    *,
    now: datetime,
    latest_received_reply_id: int | None,
) -> DeadlineItem | None:
    case = notice.case
    if case is None:
        return None
    task = case.task
    phase = phase_for_workflow(task.phase_status) if task is not None else ""
    if phase and is_terminal_phase(phase):
        return None

    if notice.needs_billing:
        if notice.fee_paid_complete:
            return None
        owner, owner_label = _process_owner(case)
        return _item(
            notice=notice,
            case=case,
            kind=KIND_FEE,
            owner=owner,
            owner_label=owner_label,
            owner_role=OWNER_ROLE_PROCESS,
            can_urge=owner is not None,
            now=now,
        )

    if not notice.needs_writer_reply:
        return None

    if notice.forwarded_at is None:
        owner, owner_label = _process_owner(case)
        return _item(
            notice=notice,
            case=case,
            kind=KIND_FORWARD,
            owner=owner,
            owner_label=owner_label,
            owner_role=OWNER_ROLE_PROCESS,
            can_urge=owner is not None,
            now=now,
        )

    if notice.received_at is None:
        owner, owner_label = _writer_owner(notice, case)
        return _item(
            notice=notice,
            case=case,
            kind=KIND_UNRECEIVED,
            owner=owner,
            owner_label=owner_label,
            owner_role=OWNER_ROLE_WRITER,
            can_urge=owner is not None,
            now=now,
        )

    if phase == TaskPhase.IN_PROGRESS:
        owner, owner_label = _writer_owner(notice, case)
        return _item(
            notice=notice,
            case=case,
            kind=KIND_REPLY,
            owner=owner,
            owner_label=owner_label,
            owner_role=OWNER_ROLE_WRITER,
            can_urge=owner is not None,
            now=now,
        )

    if latest_received_reply_id != notice.id:
        return None

    if phase == TaskPhase.PENDING_REVIEW:
        return _item(
            notice=notice,
            case=case,
            kind=KIND_REVIEW,
            owner=None,
            owner_label="管理员",
            owner_role=OWNER_ROLE_ADMIN,
            can_urge=False,
            now=now,
        )

    if phase == TaskPhase.PENDING_SUBMIT:
        owner, owner_label = _process_owner(case)
        return _item(
            notice=notice,
            case=case,
            kind=KIND_SUBMIT,
            owner=owner,
            owner_label=owner_label,
            owner_role=OWNER_ROLE_PROCESS,
            can_urge=owner is not None,
            now=now,
        )
    return None


def _open_notices_query():
    return OfficialNotice.query.options(
        joinedload(OfficialNotice.case).joinedload(Case.task).joinedload(Task.assignee),
        joinedload(OfficialNotice.case).joinedload(Case.process_owner_user),
        joinedload(OfficialNotice.case).joinedload(Case.project).joinedload(Project.customer),
        joinedload(OfficialNotice.forwarded_to),
    ).filter(
        OfficialNotice.notice_type.in_(
            tuple(OfficialNotice.BILLING_TYPES | OfficialNotice.WRITER_REPLY_TYPES)
        )
    )


def _attach_urge_state(items: list[DeadlineItem], now: datetime) -> list[DeadlineItem]:
    if not items:
        return items
    cutoff = now - URGE_COOLDOWN
    logs = CaseReviewLog.query.filter(CaseReviewLog.action == ADMIN_URGE_ACTION).all()
    latest: dict[tuple[int, int], datetime] = {}
    for log in logs:
        notice_id = parse_admin_urge_notice_id(log.note)
        if notice_id is None or log.recipient_id is None:
            continue
        stamped = _as_utc(log.created_at)
        if stamped is None:
            continue
        key = (notice_id, log.recipient_id)
        previous = latest.get(key)
        if previous is None or stamped > previous:
            latest[key] = stamped
    attached: list[DeadlineItem] = []
    for item in items:
        if not item.can_urge or item.owner_id is None:
            attached.append(replace(item, urge_ready=False))
            continue
        last = latest.get((item.notice_id, item.owner_id))
        if last is None or last < cutoff:
            attached.append(item)
            continue
        wait = URGE_COOLDOWN - (now - last)
        hours = max(1, int(wait.total_seconds() // 3600) + (1 if wait.total_seconds() % 3600 else 0))
        attached.append(
            replace(
                item,
                urge_ready=False,
                cooldown_label=f"{hours} 小时内已催办",
            )
        )
    return attached


def list_open_deadline_items(*, now: datetime | None = None, with_urge_state: bool = True) -> list[DeadlineItem]:
    moment = _as_utc(now) or datetime.now(timezone.utc)
    notices = _open_notices_query().all()
    latest_ids = _latest_received_reply_ids(notices)
    items: list[DeadlineItem] = []
    for notice in notices:
        item = classify_notice(
            notice,
            now=moment,
            latest_received_reply_id=latest_ids.get(notice.case_id),
        )
        if item is not None:
            items.append(item)
    if with_urge_state:
        items = _attach_urge_state(items, moment)
    items.sort(
        key=lambda item: (
            item.urgency_rank,
            _as_utc(item.official_due_at) or datetime.max.replace(tzinfo=timezone.utc),
            item.notice_id,
        )
    )
    return items


def matches_window(item: DeadlineItem, window: str) -> bool:
    if window == WINDOW_ALL:
        return True
    if item.remaining_days is None:
        return False
    if window == WINDOW_OVERDUE:
        return item.urgency == URGENCY_OVERDUE
    if window == WINDOW_3D:
        return item.urgency in {URGENCY_OVERDUE, URGENCY_3D}
    return item.urgency in {URGENCY_OVERDUE, URGENCY_3D, URGENCY_7D}


def filter_deadline_items(
    items: list[DeadlineItem],
    *,
    window: str = WINDOW_7D,
    kind: str = "",
    owner_role: str = "",
    notice_type: str = "",
    q: str = "",
) -> list[DeadlineItem]:
    keyword = (q or "").strip().lower()
    kind_value = kind.strip()
    owner_value = owner_role.strip()
    type_value = notice_type.strip()
    matched: list[DeadlineItem] = []
    for item in items:
        if kind_value and item.kind != kind_value:
            continue
        if owner_value and item.owner_role != owner_value:
            continue
        if type_value and item.notice_type != type_value:
            continue
        if keyword:
            blob = " ".join(
                (
                    item.case_title,
                    item.application_no,
                    item.customer_name,
                    item.notice_type_label,
                    item.kind_label,
                    item.owner_label,
                )
            ).lower()
            if keyword not in blob:
                continue
        if not matches_window(item, window):
            continue
        matched.append(item)
    return matched


def window_counts(items: list[DeadlineItem]) -> dict[str, int]:
    return {
        WINDOW_OVERDUE: sum(1 for item in items if matches_window(item, WINDOW_OVERDUE)),
        WINDOW_3D: sum(1 for item in items if matches_window(item, WINDOW_3D)),
        WINDOW_7D: sum(1 for item in items if matches_window(item, WINDOW_7D)),
        WINDOW_ALL: len(items),
    }


def statutory_deadline_counts(*, now: datetime | None = None) -> dict[str, int]:
    return window_counts(list_open_deadline_items(now=now, with_urge_state=False))


def normalize_board_filters(
    *,
    window: str = "",
    kind: str = "",
    owner_role: str = "",
    notice_type: str = "",
    q: str = "",
) -> dict[str, str]:
    window_value = (window or "").strip() or WINDOW_7D
    if window_value not in WINDOW_VALUES:
        window_value = WINDOW_7D
    kind_value = (kind or "").strip()
    if kind_value not in KIND_VALUES:
        kind_value = ""
    owner_value = (owner_role or "").strip()
    if owner_value not in OWNER_ROLE_VALUES:
        owner_value = ""
    type_value = OfficialNotice.normalize_type(notice_type) or ""
    return {
        "window": window_value,
        "kind": kind_value,
        "owner_role": owner_value,
        "notice_type": type_value,
        "q": (q or "").strip()[:200],
    }


def board_filter_url_kwargs(filters: dict[str, str]) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    for key in ("window", "kind", "owner_role", "notice_type", "q"):
        value = (filters.get(key) or "").strip()
        if not value:
            continue
        if key == "window" and value == WINDOW_7D:
            continue
        kwargs[key] = value
    return kwargs


def _classify_current_notice(notice: OfficialNotice, now: datetime) -> DeadlineItem | None:
    siblings = (
        OfficialNotice.query.filter_by(case_id=notice.case_id).all()
        if notice.case_id
        else [notice]
    )
    latest_ids = _latest_received_reply_ids(siblings)
    return classify_notice(
        notice,
        now=now,
        latest_received_reply_id=latest_ids.get(notice.case_id),
    )


def _latest_urge_at(notice_id: int, recipient_id: int) -> datetime | None:
    logs = (
        CaseReviewLog.query.filter(
            CaseReviewLog.action == ADMIN_URGE_ACTION,
            CaseReviewLog.recipient_id == recipient_id,
            CaseReviewLog.note.startswith(admin_urge_note_prefix(notice_id)),
        )
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .all()
    )
    for log in logs:
        stamped = _as_utc(log.created_at)
        if stamped is not None:
            return stamped
    return None


def admin_urge_deadline(notice: OfficialNotice, operator: User) -> tuple[bool, str]:
    """管理员催办当前官文负责人。同一官文对同一接收人 12 小时内不可连点。"""
    if operator is None or operator.role != "admin":
        return False, "只有管理员可以催办官方期限。"
    now = datetime.now(timezone.utc)
    item = _classify_current_notice(notice, now)
    if item is None:
        return False, "这份来文当前没有待办事项。"
    if item.kind == KIND_REVIEW or not item.can_urge or item.owner_id is None:
        if item.kind == KIND_REVIEW:
            return False, "材料正在由管理员审核，无需催办员工。"
        return False, "当前找不到可催办的负责人。"
    recipient = db.session.get(User, item.owner_id)
    if recipient is None:
        return False, "当前找不到可催办的负责人。"
    last = _latest_urge_at(notice.id, recipient.id)
    if last is not None and now - last < URGE_COOLDOWN:
        return False, "同一事项对同一人 12 小时内已催办过，请稍后再试。"
    due_text = date_input_value(notice.official_due_at) or "未填"
    add_review_log(
        case_id=item.case_id,
        action=ADMIN_URGE_ACTION,
        operator=operator,
        recipient=recipient,
        note=(
            f"{admin_urge_note_prefix(notice.id)}请尽快办理「{notice.type_label}」："
            f"{item.kind_label}。官方期限 {due_text}。"
        ),
    )
    return True, f"已催办{item.owner_role_label} {item.owner_label}。"

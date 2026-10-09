"""管理端员工绩效：按职能分组，再按每人本月完成案件的类型展开。

三种职能的「完成」口径不同，不能加总成一个数字：
- 撰写师：本月实际返稿（与月度统计一致）
- 流程人员：本月终审办结；交局为当月递交官方次数
- 业务人员：本月下单确认；收账为当月确认收款笔数
空职能按撰写师统计。在职员工即使本月零完成也列出；离职的只在有本月业绩或在办时出现。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import joinedload

from app.case_statistics import case_statistics_available_years, case_statistics_month_bounds
from app.case_types import PRIMARY_LABELS, PRIMARY_OPTIONS, case_type_primary
from app.extensions import db
from app.models import Case, CaseReviewLog, Project, Task, User
from app.workflow import (
    LEGACY_TERMINAL_PHASES,
    TaskPhase,
    is_order_intake_phase,
    is_terminal_phase,
    task_phase_label,
)

_CN_TZ = timezone(timedelta(hours=8))

FUNCTION_ORDER = (
    User.STAFF_FUNCTION_WRITER,
    User.STAFF_FUNCTION_PROCESS,
    User.STAFF_FUNCTION_BUSINESS,
)

FUNCTION_CONFIG = {
    User.STAFF_FUNCTION_WRITER: {
        "label": "撰写师",
        "hint": "完成按本月实际返稿计，与撰写师月度统计一致。案件按发明 / 实用新型等类型归类。",
        "completed_label": "返稿",
        "reject_label": "打回",
        "extra_label": None,
    },
    User.STAFF_FUNCTION_PROCESS: {
        "label": "流程人员",
        "hint": "完成按本月终审办结计。交局是当月标记递交官方的次数，同一案件多次递交会分开计数。",
        "completed_label": "办结",
        "reject_label": None,
        "extra_label": "交局",
    },
    User.STAFF_FUNCTION_BUSINESS: {
        "label": "业务人员",
        "hint": "完成按本月下单确认计。打回是当月下单被退回次数，收账是当月确认收款笔数。",
        "completed_label": "确认",
        "reject_label": "打回",
        "extra_label": "收账",
    },
}

_MONTH_LOG_ACTIONS = frozenset(
    {
        "approve",
        "writing_reject",
        "reject",
        "process_filed",
        "intake_approve",
        "intake_reject",
        "collection_ok",
    }
)

_TERMINAL_PHASES = frozenset(TaskPhase.TERMINAL) | frozenset(LEGACY_TERMINAL_PHASES)


def staff_performance_function(raw: str | None) -> str:
    value = (raw or "all").strip()
    if value in User.STAFF_FUNCTIONS:
        return value
    return "all"


def _as_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _in_bounds(value: datetime | None, start_at: datetime, end_at: datetime) -> bool:
    stamped = _as_naive(value)
    return stamped is not None and start_at <= stamped < end_at


def _days_between(start: datetime | None, end: datetime | None) -> float | None:
    begin = _as_naive(start)
    finish = _as_naive(end)
    if begin is None or finish is None:
        return None
    seconds = (finish - begin).total_seconds()
    if seconds < 0:
        return 0.0
    return round(seconds / 86400, 1)


def _mean_days(values: list[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 1)


def _case_type_bucket(case: Case) -> tuple[str, str]:
    primary = case_type_primary(case.case_type_code)
    if primary and primary in PRIMARY_LABELS:
        return primary, PRIMARY_LABELS[primary]
    display = (case.case_type_display or "").strip()
    if display:
        first = display.split("/")[0].strip()
        for value, label in PRIMARY_OPTIONS:
            if first in {value, label}:
                return value, label
    return "unclassified", "未分类"


def _case_row(case: Case, *, done_at: datetime | None) -> dict:
    primary, primary_label = _case_type_bucket(case)
    task = case.task
    project = case.project
    customer = project.customer if project is not None else None
    return {
        "id": case.id,
        "title": case.title,
        "application_no": case.application_no,
        "case_type_display": case.case_type_display or "未分类",
        "primary": primary,
        "primary_label": primary_label,
        "done_at": done_at,
        "status": task_phase_label(task.phase_status) if task is not None else "—",
        "customer_name": customer.name if customer is not None else "—",
    }


def _group_case_rows(rows: list[dict]) -> list[dict]:
    by_primary: dict[str, list[dict]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in rows:
        by_primary[row["primary"]].append(row)
        labels[row["primary"]] = row["primary_label"]
    groups: list[dict] = []
    for value, label in PRIMARY_OPTIONS:
        items = by_primary.pop(value, [])
        if not items:
            continue
        items.sort(key=lambda item: (_as_naive(item["done_at"]) or datetime.min, item["id"]))
        groups.append({"key": value, "label": label, "count": len(items), "cases": items})
    leftover_keys = list(by_primary.keys())
    leftover_keys.sort()
    for key in leftover_keys:
        items = by_primary[key]
        items.sort(key=lambda item: (_as_naive(item["done_at"]) or datetime.min, item["id"]))
        groups.append(
            {
                "key": key,
                "label": labels.get(key, "未分类"),
                "count": len(items),
                "cases": items,
            }
        )
    return groups


def _writer_open(task: Task | None) -> bool:
    if task is None or task.assignee_id is None:
        return False
    if is_terminal_phase(task.phase_status) or is_order_intake_phase(task.phase_status):
        return False
    return task.phase_status != TaskPhase.PENDING_ASSIGNMENT


def _process_open(case: Case) -> bool:
    if case.process_owner_id is None:
        return False
    task = case.task
    if task is None:
        return True
    return not is_terminal_phase(task.phase_status)


def _business_open(case: Case, user_id: int) -> bool:
    task = case.task
    if task is None:
        return False
    return case.intake_owner_id == user_id and is_order_intake_phase(task.phase_status)


def _include_person(user: User, row: dict) -> bool:
    if user.is_active:
        return True
    return (
        row["completed_count"] > 0
        or row["in_progress_count"] > 0
        or row["reject_count"] > 0
        or row["extra_count"] > 0
    )


def _person_sort_key(row: dict) -> tuple:
    return (
        -row["completed_count"],
        -row["in_progress_count"],
        (row["user"].username or "").lower(),
    )


def _empty_person(user: User, *, function: str) -> dict:
    config = FUNCTION_CONFIG[function]
    return {
        "user": user,
        "function": function,
        "completed_count": 0,
        "in_progress_count": 0,
        "reject_count": 0,
        "extra_count": 0,
        "avg_days": None,
        "completed_label": config["completed_label"],
        "reject_label": config["reject_label"],
        "extra_label": config["extra_label"],
        "type_groups": [],
    }


def _load_month_logs(start_at: datetime, end_at: datetime) -> list[CaseReviewLog]:
    return (
        CaseReviewLog.query.filter(
            CaseReviewLog.created_at >= start_at,
            CaseReviewLog.created_at < end_at,
            CaseReviewLog.action.in_(tuple(_MONTH_LOG_ACTIONS)),
        ).all()
    )


def _load_cases(start_at: datetime, end_at: datetime, log_case_ids: set[int]) -> list[Case]:
    clauses = [
        db.and_(Case.actual_return_at >= start_at, Case.actual_return_at < end_at),
        Case.task.has(~Task.phase_status.in_(tuple(_TERMINAL_PHASES))),
    ]
    if log_case_ids:
        clauses.append(Case.id.in_(log_case_ids))
    return (
        Case.query.options(
            joinedload(Case.task).joinedload(Task.assignee),
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.intake_owner_user),
            joinedload(Case.process_owner_user),
            joinedload(Case.billing_owner_user),
        )
        .filter(Case.attribution_filter(Case.ATTRIBUTION_CUSTOMER))
        .filter(db.or_(*clauses))
        .all()
    )


def _build_groups(year: int, month: int) -> list[dict]:
    start_at, end_at = case_statistics_month_bounds(year, month)
    staff_users = User.query.filter(User.role == "staff").order_by(User.id.asc()).all()
    logs = _load_month_logs(start_at, end_at)
    cases = _load_cases(start_at, end_at, {log.case_id for log in logs})
    cases_by_id = {case.id: case for case in cases}

    people: dict[str, dict[int, dict]] = {
        function: {} for function in FUNCTION_ORDER
    }
    for user in staff_users:
        function = user.staff_function_normalized
        people[function][user.id] = _empty_person(user, function=function)

    writer_completed: dict[int, list[dict]] = defaultdict(list)
    writer_days: dict[int, list[float | None]] = defaultdict(list)
    process_completed: dict[int, list[dict]] = defaultdict(list)
    process_days: dict[int, list[float | None]] = defaultdict(list)
    business_completed: dict[int, list[dict]] = defaultdict(list)
    business_days: dict[int, list[float | None]] = defaultdict(list)

    for case in cases:
        task = case.task
        if task is not None and _in_bounds(case.actual_return_at, start_at, end_at):
            writer_id = task.assignee_id
            if writer_id in people[User.STAFF_FUNCTION_WRITER]:
                row = _case_row(case, done_at=case.actual_return_at)
                writer_completed[writer_id].append(row)
                writer_days[writer_id].append(_days_between(case.created_at, case.actual_return_at))
        if task is not None and _writer_open(task):
            writer_id = task.assignee_id
            if writer_id in people[User.STAFF_FUNCTION_WRITER]:
                people[User.STAFF_FUNCTION_WRITER][writer_id]["in_progress_count"] += 1
        if _process_open(case):
            process_id = case.process_owner_id
            if process_id in people[User.STAFF_FUNCTION_PROCESS]:
                people[User.STAFF_FUNCTION_PROCESS][process_id]["in_progress_count"] += 1
        intake_id = case.intake_owner_id
        if intake_id in people[User.STAFF_FUNCTION_BUSINESS] and _business_open(case, intake_id):
            people[User.STAFF_FUNCTION_BUSINESS][intake_id]["in_progress_count"] += 1

    seen_approve: set[tuple[int, int]] = set()
    seen_intake: set[tuple[int, int]] = set()
    for log in logs:
        case = cases_by_id.get(log.case_id)
        if log.action in {"writing_reject", "reject"} and log.recipient_id in people[User.STAFF_FUNCTION_WRITER]:
            people[User.STAFF_FUNCTION_WRITER][log.recipient_id]["reject_count"] += 1
        if log.action == "process_filed" and log.operator_id in people[User.STAFF_FUNCTION_PROCESS]:
            people[User.STAFF_FUNCTION_PROCESS][log.operator_id]["extra_count"] += 1
        if log.action == "intake_reject" and log.recipient_id in people[User.STAFF_FUNCTION_BUSINESS]:
            people[User.STAFF_FUNCTION_BUSINESS][log.recipient_id]["reject_count"] += 1
        if log.action == "collection_ok" and log.recipient_id in people[User.STAFF_FUNCTION_BUSINESS]:
            people[User.STAFF_FUNCTION_BUSINESS][log.recipient_id]["extra_count"] += 1
        if case is None:
            continue
        if log.action == "approve":
            process_id = case.process_owner_id
            key = (process_id or 0, case.id)
            if process_id in people[User.STAFF_FUNCTION_PROCESS] and key not in seen_approve:
                seen_approve.add(key)
                process_completed[process_id].append(_case_row(case, done_at=log.created_at))
                process_days[process_id].append(_days_between(case.created_at, log.created_at))
        if log.action == "intake_approve":
            business_id = log.recipient_id or case.intake_owner_id
            key = (business_id or 0, case.id)
            if business_id in people[User.STAFF_FUNCTION_BUSINESS] and key not in seen_intake:
                seen_intake.add(key)
                business_completed[business_id].append(_case_row(case, done_at=log.created_at))
                business_days[business_id].append(
                    _days_between(case.order_at or case.created_at, log.created_at)
                )

    completed_map = {
        User.STAFF_FUNCTION_WRITER: writer_completed,
        User.STAFF_FUNCTION_PROCESS: process_completed,
        User.STAFF_FUNCTION_BUSINESS: business_completed,
    }
    days_map = {
        User.STAFF_FUNCTION_WRITER: writer_days,
        User.STAFF_FUNCTION_PROCESS: process_days,
        User.STAFF_FUNCTION_BUSINESS: business_days,
    }

    groups: list[dict] = []
    for function in FUNCTION_ORDER:
        config = FUNCTION_CONFIG[function]
        rows: list[dict] = []
        for user_id, row in people[function].items():
            completed_rows = completed_map[function].get(user_id, [])
            row["completed_count"] = len(completed_rows)
            row["avg_days"] = _mean_days(days_map[function].get(user_id, []))
            row["type_groups"] = _group_case_rows(completed_rows)
            if _include_person(row["user"], row):
                rows.append(row)
        rows.sort(key=_person_sort_key)
        groups.append(
            {
                "function": function,
                "label": config["label"],
                "hint": config["hint"],
                "completed_label": config["completed_label"],
                "reject_label": config["reject_label"],
                "extra_label": config["extra_label"],
                "staff_count": len(rows),
                "active_count": sum(1 for row in rows if row["user"].is_active),
                "completed_count": sum(row["completed_count"] for row in rows),
                "in_progress_count": sum(row["in_progress_count"] for row in rows),
                "people": rows,
            }
        )
    return groups


def _previous_month(year: int, month: int) -> tuple[int, int]:
    first = datetime(year, month, 1)
    previous = first - timedelta(days=1)
    return previous.year, previous.month


def staff_performance_data(year: int, month: int, *, function_filter: str = "all") -> dict:
    """按月份汇总员工绩效；function_filter 为 writer/process/business 或 all。"""
    function_filter = staff_performance_function(function_filter)
    groups = _build_groups(year, month)
    previous_year, previous_month = _previous_month(year, month)
    previous_groups = {
        group["function"]: group["completed_count"] for group in _build_groups(previous_year, previous_month)
    }
    visible = [group for group in groups if function_filter == "all" or group["function"] == function_filter]
    now_cn = datetime.now(timezone.utc).astimezone(_CN_TZ)
    function_options = [
        {
            "value": "all",
            "label": "全部职能",
            "staff_count": sum(group["staff_count"] for group in groups),
            "completed_count": sum(group["completed_count"] for group in groups),
        }
    ]
    for group in groups:
        function_options.append(
            {
                "value": group["function"],
                "label": group["label"],
                "staff_count": group["staff_count"],
                "completed_count": group["completed_count"],
            }
        )
    summaries = [
        {
            "function": group["function"],
            "label": group["label"],
            "completed_label": group["completed_label"],
            "completed_count": group["completed_count"],
            "previous_count": previous_groups.get(group["function"], 0),
        }
        for group in (groups if function_filter == "all" else visible)
    ]
    return {
        "year": year,
        "month": month,
        "previous_year": previous_year,
        "previous_month": previous_month,
        "current_year": now_cn.year,
        "current_month": now_cn.month,
        "function_filter": function_filter,
        "function_options": function_options,
        "available_years": case_statistics_available_years("completed", selected_year=year),
        "groups": visible,
        "summaries": summaries,
        "staff_count": sum(group["staff_count"] for group in visible),
        "active_count": sum(group["active_count"] for group in visible),
    }

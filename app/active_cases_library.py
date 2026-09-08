"""办结案件库：在办 / 办结案件的查询、排序与分组。"""

from __future__ import annotations

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Case, Project, Task
from app.workflow import (
    LEGACY_TERMINAL_PHASES,
    TaskPhase,
    effective_task_due_at,
    is_task_past_due,
)

_TERMINAL = tuple(TaskPhase.TERMINAL | LEGACY_TERMINAL_PHASES)


def _task_case_options():
    return (
        joinedload(Task.case).joinedload(Case.business_owner_user),
        joinedload(Task.case).joinedload(Case.project).joinedload(Project.customer),
        joinedload(Task.assignee),
    )


def _active_base_query():
    """未完成、已分配（排除待分配池）的案件任务。"""
    return (
        Task.query.join(Task.case)
        .join(Case.project)
        .join(Project.customer)
        .options(*_task_case_options())
        .filter(
            Task.phase_status.notin_(_TERMINAL),
            Task.phase_status != TaskPhase.PENDING_ASSIGNMENT,
            db.or_(
                Task.assignee_id.isnot(None),
                Case.business_owner_id.isnot(None),
            ),
        )
    )


def _completed_base_query():
    """全部已完成（含历史终结状态）案件任务。"""
    return (
        Task.query.join(Task.case)
        .join(Case.project)
        .join(Project.customer)
        .options(*_task_case_options())
        .filter(Task.phase_status.in_(_TERMINAL))
    )


def _sort_by_datetime_field(rows: list[dict], *, field: str, reverse: bool) -> None:
    """按行内时间字段排序；空值排在最后。"""
    with_value = [row for row in rows if row.get(field) is not None]
    without_value = [row for row in rows if row.get(field) is None]
    with_value.sort(key=lambda x: (x[field], x["case"].id), reverse=reverse)
    without_value.sort(key=lambda x: x["case"].id)
    rows[:] = with_value + without_value


def _sort_rows(rows: list[dict], *, sort_by: str) -> None:
    """在办：按截止日期排序。"""
    _sort_by_datetime_field(
        rows,
        field="effective_due_at",
        reverse=sort_by == "deadline_desc",
    )


def _sort_completed_rows(rows: list[dict], *, sort_by: str) -> None:
    """办结：按实际返稿时间排序（默认新→旧）。"""
    if sort_by == "actual_return_asc":
        _sort_by_datetime_field(rows, field="actual_return_at", reverse=False)
    else:
        _sort_by_datetime_field(rows, field="actual_return_at", reverse=True)


def _group_rows(rows: list[dict], *, group_by: str) -> list[dict]:
    if group_by == "customer":
        buckets: dict[int | None, dict] = {}
        order: list[int | None] = []
        for row in rows:
            customer = row["customer"]
            key = customer.id if customer else None
            if key not in buckets:
                buckets[key] = {
                    "kind": "customer",
                    "customer": customer,
                    "project": None,
                    "rows": [],
                }
                order.append(key)
            buckets[key]["rows"].append(row)
        return [buckets[k] for k in order]

    if group_by == "project":
        buckets = {}
        order = []
        for row in rows:
            project = row["project"]
            key = project.id if project else None
            if key not in buckets:
                buckets[key] = {
                    "kind": "project",
                    "customer": row["customer"],
                    "project": project,
                    "rows": [],
                }
                order.append(key)
            buckets[key]["rows"].append(row)
        return [buckets[k] for k in order]

    return [
        {
            "kind": "flat",
            "customer": None,
            "project": None,
            "rows": rows,
        }
    ]


def active_cases_library_data(
    *,
    keyword: str = "",
    staff_id: int | None = None,
    customer_id: int | None = None,
    project_id: int | None = None,
    sort_by: str = "deadline",
    group_by: str = "none",
) -> dict:
    """返回在办已分配案件行与客户/项目分组结果。"""
    if sort_by not in {"deadline", "deadline_desc"}:
        sort_by = "deadline"
    if group_by not in {"none", "project", "customer"}:
        group_by = "none"

    q = _active_base_query()
    q = _apply_common_filters(
        q,
        keyword=keyword,
        staff_id=staff_id,
        customer_id=customer_id,
        project_id=project_id,
    )

    tasks = q.order_by(Task.id.desc()).all()

    rows: list[dict] = []
    for task in tasks:
        case = task.case
        project = case.project if case else None
        customer = project.customer if project else None
        due_at = effective_task_due_at(task)
        rows.append(
            {
                "task": task,
                "case": case,
                "project": project,
                "customer": customer,
                "staff": task.assignee or (case.business_owner_user if case else None),
                "staff_label": (task.assignee_label if task else None)
                or (case.business_owner_label if case else None),
                "effective_due_at": due_at,
                "actual_return_at": case.actual_return_at if case else None,
                "is_past_due": is_task_past_due(task),
            }
        )

    _sort_rows(rows, sort_by=sort_by)
    groups = _group_rows(rows, group_by=group_by)
    return {
        "rows": rows,
        "groups": groups,
        "total": len(rows),
        "sort_by": sort_by,
        "group_by": group_by,
    }


def _apply_common_filters(q, *, keyword: str, staff_id: int | None, customer_id: int | None, project_id: int | None):
    if keyword:
        q = q.filter(
            Case.title.contains(keyword)
            | Case.application_no.contains(keyword)
            | Case.patent_application_no.contains(keyword)
        )
    if staff_id is not None:
        q = q.filter(
            db.or_(
                Task.assignee_id == staff_id,
                Case.business_owner_id == staff_id,
            )
        )
    if customer_id is not None:
        q = q.filter(Project.customer_id == customer_id)
    if project_id is not None:
        q = q.filter(Case.project_id == project_id)
    return q


def completed_cases_library_data(
    *,
    keyword: str = "",
    staff_id: int | None = None,
    customer_id: int | None = None,
    project_id: int | None = None,
    sort_by: str = "actual_return_desc",
    group_by: str = "none",
    missing_return_only: bool = False,
) -> dict:
    """返回全部已完成案件行与客户/项目分组结果。"""
    if sort_by not in {"actual_return_desc", "actual_return_asc"}:
        sort_by = "actual_return_desc"
    if group_by not in {"none", "project", "customer"}:
        group_by = "none"

    q = _apply_common_filters(
        _completed_base_query(),
        keyword=keyword,
        staff_id=staff_id,
        customer_id=customer_id,
        project_id=project_id,
    )
    if missing_return_only:
        q = q.filter(Case.actual_return_at.is_(None))
    tasks = q.order_by(Task.id.desc()).all()

    rows: list[dict] = []
    missing_return_count = 0
    for task in tasks:
        case = task.case
        project = case.project if case else None
        customer = project.customer if project else None
        actual_return_at = case.actual_return_at if case else None
        if actual_return_at is None:
            missing_return_count += 1
        rows.append(
            {
                "task": task,
                "case": case,
                "project": project,
                "customer": customer,
                "staff": task.assignee or (case.business_owner_user if case else None),
                "staff_label": (task.assignee_label if task else None)
                or (case.business_owner_label if case else None),
                "effective_due_at": effective_task_due_at(task),
                "actual_return_at": actual_return_at,
                "is_past_due": False,
            }
        )

    missing_rows = [row for row in rows if row["actual_return_at"] is None]
    filled_rows = [row for row in rows if row["actual_return_at"] is not None]
    _sort_completed_rows(filled_rows, sort_by=sort_by)
    # 缺返稿时间的排在前面，便于补填
    rows[:] = missing_rows + filled_rows
    groups = _group_rows(rows, group_by=group_by)
    return {
        "rows": rows,
        "groups": groups,
        "total": len(rows),
        "missing_return_count": missing_return_count,
        "sort_by": sort_by,
        "group_by": group_by,
    }

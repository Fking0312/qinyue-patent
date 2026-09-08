"""任务看板的共享查询与视图模型构造（管理员/员工共用）。

入口是 `task_board_data_for_user`：根据角色限定可见任务，统计计数，
按状态过滤后返回 `{rows, counts}` 给模板。
"""

from __future__ import annotations

from app.models import Case, Customer, Project, Task, User
from app.workflow import (
    TaskPhase,
    effective_task_due_at,
    effective_task_phase,
    is_overdue_phase,
    is_pending_review_phase,
    is_task_past_due,
)

_IN_PROGRESS_PHASES = frozenset(
    {
        TaskPhase.IN_PROGRESS,
        TaskPhase.PENDING_SUBMIT,
        TaskPhase.AUTHORIZED_PENDING_PAYMENT,
        TaskPhase.OFFICE_ACTION,
        TaskPhase.ON_HOLD,
    },
)


def _base_query_for_user(user: User):
    """构造任务看板的基础查询：员工仅看自己负责的任务，其他角色看全部。"""
    q = Task.query.join(Task.case).join(Case.project).join(Project.customer)
    if user.role == "staff":
        # 「待分配」只属于管理端分配池；即使历史脏数据同时写入了 assignee，也不向员工展示。
        q = q.filter(
            Task.assignee_id == user.id,
            Task.phase_status != TaskPhase.PENDING_ASSIGNMENT,
        )
    return q


def _matches_status(phase_status: str, status_filter: str) -> bool:
    """判断任务是否符合界面状态筛选；`all` 不筛，未知值视为 all。"""
    if status_filter == "all":
        return True
    if status_filter == "overdue":
        return is_overdue_phase(phase_status) and not is_pending_review_phase(phase_status)
    if status_filter == "pending_review":
        return is_pending_review_phase(phase_status)
    if status_filter == "pending_assignment":
        return phase_status == TaskPhase.PENDING_ASSIGNMENT
    if status_filter == "in_progress":
        return phase_status in _IN_PROGRESS_PHASES
    return True


def task_board_data_for_user(user: User, *, status_filter: str, sort_by: str) -> dict:
    """只读汇总任务看板数据：按权限筛任务、统计并排序。"""
    tasks = _base_query_for_user(user).order_by(Task.updated_at.desc(), Task.id.desc()).all()

    rows = []
    counts = {"in_progress": 0, "pending_review": 0, "overdue": 0, "pending_assignment": 0}
    for task in tasks:
        case = task.case
        project = case.project if case else None
        customer = project.customer if project else None
        due_at = effective_task_due_at(task)
        display_phase = effective_task_phase(task)
        is_overdue = is_overdue_phase(display_phase)
        if display_phase == TaskPhase.PENDING_ASSIGNMENT:
            counts["pending_assignment"] += 1
        elif is_pending_review_phase(display_phase):
            counts["pending_review"] += 1
        elif is_overdue:
            counts["overdue"] += 1
        elif display_phase in _IN_PROGRESS_PHASES:
            counts["in_progress"] += 1

        if not _matches_status(display_phase, status_filter):
            continue

        rows.append(
            {
                "task": task,
                "case": case,
                "project": project,
                "customer": customer,
                "effective_due_at": due_at,
                "is_past_due": is_task_past_due(task),
                "display_phase": display_phase,
            }
        )

    if sort_by == "customer":
        rows.sort(key=lambda x: ((x["customer"].name if x["customer"] else "").lower(), -x["task"].id))
    else:
        rows.sort(
            key=lambda x: (
                x["effective_due_at"] is None,
                x["effective_due_at"] if x["effective_due_at"] is not None else x["task"].updated_at,
            )
        )

    return {"rows": rows, "counts": counts}

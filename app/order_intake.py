"""下单：业务人员建客户/项目/案件，管理员确认时指定撰写师与流程人员。

归属链是 客户 → 项目 → 案件。案件不直接绑客户；业务端能看客户名和项目名，
不能浏览项目下的案件。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.case_trace import (
    add_review_log,
    stamp_assignee,
    stamp_business_owner,
    stamp_intake_owner,
    stamp_process_owner,
)
from app.case_types import validate_case_type_code
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.serials import next_case_serial, next_project_code
from app.workflow import (
    TaskPhase,
    is_pending_order_review_phase,
)

_CN_TZ = timezone(timedelta(hours=8))
_CUSTOMER_PHONE_RE = re.compile(r"^[\d+\s\-()（）]{5,40}$")


def pending_order_review_query():
    """待管理员确认的下单任务。"""
    return (
        Task.query.join(Case, Case.id == Task.case_id)
        .filter(Task.phase_status == TaskPhase.PENDING_ORDER_REVIEW)
        .options(
            joinedload(Task.case).joinedload(Case.project),
            joinedload(Task.case).joinedload(Case.intake_owner_user),
        )
    )


def pending_order_review_count() -> int:
    return Task.query.filter(Task.phase_status == TaskPhase.PENDING_ORDER_REVIEW).count()


def apply_order_intake_review(
    case: Case,
    task: Task | None,
    action: str,
    reject_note: str,
    operator: "User",
    *,
    writer_id: int | None = None,
    process_owner_id: int | None = None,
) -> tuple[bool, str, str]:
    """确认或打回下单。通过须指定撰写师与流程人员，进入撰写中；打回 → 下单待修改。调用方负责 commit。"""
    if task is None:
        return False, "该案件暂无任务，无法确认下单。", "warning"
    if not is_pending_order_review_phase(task.phase_status):
        return False, "当前不是待下单确认，无法执行该动作。", "warning"
    recipient = case.intake_owner_user
    if action == "approve":
        writer = db.session.get(User, writer_id) if writer_id else None
        if writer is None or not writer.is_assignable_writer:
            return False, "确认下单请指定在职撰写师。", "warning"
        process_owner = db.session.get(User, process_owner_id) if process_owner_id else None
        if process_owner is None or not process_owner.is_assignable_process:
            return False, "确认下单请指定在职流程人员。", "warning"
        stamp_business_owner(case, writer)
        stamp_assignee(task, writer)
        stamp_process_owner(case, process_owner)
        task.phase_status = TaskPhase.IN_PROGRESS
        add_review_log(
            case_id=case.id,
            action="intake_approve",
            operator=operator,
            recipient=recipient,
        )
        add_review_log(
            case_id=case.id,
            action="assigned",
            operator=operator,
            recipient=writer,
        )
        add_review_log(
            case_id=case.id,
            action="process_assigned",
            operator=operator,
            recipient=process_owner,
        )
        return True, "已确认下单，并指定撰写师与流程人员，案件进入撰写中。", "success"
    if action == "reject":
        note = (reject_note or "").strip()
        if not note:
            return False, "打回时请填写原因。", "warning"
        task.phase_status = TaskPhase.ORDER_REVISION
        add_review_log(
            case_id=case.id,
            action="intake_reject",
            operator=operator,
            recipient=recipient,
            note=note,
        )
        return True, "已打回，等待业务人员修改后再提交。", "info"
    return False, "下单确认动作无效。", "warning"


def customer_project_catalog() -> list[dict]:
    """全部客户及其项目名称。不加载、不返回项目下的案件。"""
    customers = Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all()
    projects = Project.query.order_by(Project.name.asc(), Project.id.asc()).all()
    by_customer: dict[int, list[Project]] = {}
    for project in projects:
        by_customer.setdefault(project.customer_id, []).append(project)
    return [
        {
            "customer": customer,
            "projects": by_customer.get(customer.id, []),
        }
        for customer in customers
    ]


def project_options_payload() -> list[dict]:
    """下拉用的项目列表：只含 id / 名称 / 所属客户，不含案件。"""
    projects = Project.query.order_by(Project.name.asc(), Project.id.asc()).all()
    return [
        {"id": project.id, "name": project.name, "customer_id": project.customer_id}
        for project in projects
    ]


def _my_orders_query(user_id: int):
    """当前业务人员自己当过下单人的任务，不含别人在同一项目下的案件。"""
    return Task.query.join(Case, Case.id == Task.case_id).filter(Case.intake_owner_id == user_id)


def my_submitted_orders(user_id: int) -> list[Task]:
    """当前业务人员自己提交的下单，不是某个项目下的全部案件。"""
    return (
        _my_orders_query(user_id)
        .options(
            joinedload(Task.case).joinedload(Case.project).joinedload(Project.customer),
        )
        .order_by(func.coalesce(Task.updated_at, Task.created_at).desc(), Task.id.desc())
        .all()
    )


def my_order_phase_count(user_id: int, phase: str) -> int:
    """自己提交的下单中，处于某阶段的件数。"""
    return _my_orders_query(user_id).filter(Task.phase_status == phase).count()


def my_orders_created_this_month_count(user_id: int) -> int:
    """本月自己提交的案件数（按创建时间，北京时间自然月）。"""
    now = datetime.now(timezone.utc).astimezone(_CN_TZ)
    start_cn = datetime(now.year, now.month, 1, tzinfo=_CN_TZ)
    if now.month == 12:
        end_cn = datetime(now.year + 1, 1, 1, tzinfo=_CN_TZ)
    else:
        end_cn = datetime(now.year, now.month + 1, 1, tzinfo=_CN_TZ)
    start_at = start_cn.astimezone(timezone.utc)
    end_at = end_cn.astimezone(timezone.utc)
    return Case.query.filter(
        Case.intake_owner_id == user_id,
        Case.created_at >= start_at,
        Case.created_at < end_at,
    ).count()


def my_order_revision_rows(user_id: int) -> list[tuple[Task, str]]:
    """首页待改列表：自己的下单待修改，等得最久的在前。"""
    tasks = (
        _my_orders_query(user_id)
        .filter(Task.phase_status == TaskPhase.ORDER_REVISION)
        .options(
            joinedload(Task.case).joinedload(Case.project).joinedload(Project.customer),
        )
        .order_by(func.coalesce(Task.updated_at, Task.created_at).asc(), Task.id.asc())
        .all()
    )
    return [(task, latest_intake_reject_note(task.case_id)) for task in tasks]


def order_revision_for_owner(user_id: int, case_id: int) -> Case | None:
    """业务人员可改的下单待修改案件；别人的或已不在该状态则没有。"""
    if user_id <= 0 or case_id <= 0:
        return None
    case = (
        Case.query.options(
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.task),
        )
        .filter(Case.id == case_id, Case.intake_owner_id == user_id)
        .first()
    )
    if case is None or case.task is None:
        return None
    if case.task.phase_status != TaskPhase.ORDER_REVISION:
        return None
    return case


def latest_intake_reject_note(case_id: int) -> str:
    log = (
        CaseReviewLog.query.filter_by(case_id=case_id, action="intake_reject")
        .order_by(CaseReviewLog.id.desc())
        .first()
    )
    return (log.note or "").strip() if log else ""


def parse_beijing_datetime(value: str) -> datetime | None:
    """将用户输入的北京时间转为 UTC；空串为 None。"""
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def datetime_local_value(value: datetime | None) -> str:
    if value is None:
        return ""
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return utc_value.astimezone(_CN_TZ).strftime("%Y-%m-%dT%H:%M")


def create_intake_customer(
    operator: "User",
    *,
    name: str,
    kind: str,
    contact_name: str = "",
    contact_phone: str = "",
    note: str = "",
) -> tuple[Customer | None, str]:
    """业务人员新建客户。调用方负责 commit。"""
    name = (name or "").strip()
    if not name:
        return None, "客户名称不能为空。"
    if len(name) > 200:
        return None, "客户名称不能超过 200 个字符。"
    kind = (kind or CustomerKind.COMPANY).strip()
    if kind not in {CustomerKind.COMPANY, CustomerKind.INDIVIDUAL}:
        return None, "客户类型不合法。"
    contact_name = (contact_name or "").strip()
    if len(contact_name) > 120:
        return None, "联系人不能超过 120 个字符。"
    contact_phone = (contact_phone or "").strip()
    if contact_phone and not _CUSTOMER_PHONE_RE.match(contact_phone):
        return None, "电话格式不正确。"
    note = (note or "").strip()
    if len(note) > 5000:
        return None, "备注不能超过 5000 字。"
    customer = Customer(
        name=name,
        kind=kind,
        contact_name=contact_name or None,
        contact_phone=contact_phone or None,
        note=note or None,
        created_by_id=operator.id,
    )
    db.session.add(customer)
    db.session.flush()
    return customer, ""


def create_intake_project(
    operator: "User",
    *,
    customer_id: int | None,
    name: str,
    description: str = "",
    due_at: datetime | None = None,
) -> tuple[Project | None, str]:
    """业务人员新建项目，必须绑定已有客户。调用方负责 commit。"""
    name = (name or "").strip()
    if not name:
        return None, "项目名称不能为空。"
    if len(name) > 200:
        return None, "项目名称不能超过 200 个字符。"
    if customer_id is None or customer_id <= 0:
        return None, "请选择归属客户。"
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        return None, "客户不存在，请刷新后重试。"
    description = (description or "").strip()
    if len(description) > 1000:
        return None, "项目说明不能超过 1000 字。"
    project = Project(
        customer_id=customer.id,
        name=name,
        description=description or None,
        due_at=due_at,
        created_by_id=operator.id,
    )
    db.session.add(project)
    db.session.flush()
    project.code = next_project_code(project.created_at)
    project.initiated_at = project.created_at
    return project, ""


def _validated_order_fields(
    *,
    customer_id: int | None,
    project_id: int | None,
    title: str,
    case_type_code: str,
    patent_application_no: str = "",
    case_note: str = "",
    material_upload_port: str = "",
    exclude_case_id: int | None = None,
) -> tuple[dict | None, str]:
    title = Case.normalize_title(title)
    if not title:
        return None, "案件标题不能为空。"
    if len(title) > 200:
        return None, "案件标题不能超过 200 个字符。"
    if customer_id is None or customer_id <= 0:
        return None, "请选择客户。"
    if project_id is None or project_id <= 0:
        return None, "请选择所属项目。"
    project = db.session.get(Project, project_id)
    if project is None:
        return None, "项目不存在，请刷新后重试。"
    if project.customer_id != customer_id:
        return None, "项目必须归属所选客户，案件不能直接绑客户。"
    if Case.title_taken_in_project(project.id, title, exclude_id=exclude_case_id):
        return None, Case.DUPLICATE_TITLE_IN_PROJECT_MSG
    leaf, type_err = validate_case_type_code(case_type_code)
    if type_err or leaf is None:
        return None, type_err or "请选择案件类型。"
    patent_application_no = (patent_application_no or "").strip()
    if len(patent_application_no) > 100:
        return None, "申请号不能超过 100 个字符。"
    case_note = (case_note or "").strip()
    material_upload_port = (material_upload_port or "").strip()
    if len(material_upload_port) > 255:
        return None, "材料上传端口不能超过 255 个字符。"
    return {
        "project": project,
        "title": title,
        "case_type_code": leaf.code,
        "patent_application_no": patent_application_no or None,
        "case_note": case_note or None,
        "material_upload_port": material_upload_port or None,
    }, ""


def _apply_order_fields(
    case: Case,
    fields: dict,
    *,
    expected_return_at: datetime | None,
    order_at: datetime | None,
) -> None:
    case.project_id = fields["project"].id
    case.title = fields["title"]
    case.case_type_code = fields["case_type_code"]
    case.patent_application_no = fields["patent_application_no"]
    case.expected_return_at = expected_return_at
    case.order_at = order_at
    case.case_note = fields["case_note"]
    case.material_upload_port = fields["material_upload_port"]


def submit_business_order(
    operator: "User",
    *,
    customer_id: int | None,
    project_id: int | None,
    title: str,
    case_type_code: str,
    patent_application_no: str = "",
    expected_return_at: datetime | None = None,
    order_at: datetime | None = None,
    case_note: str = "",
    material_upload_port: str = "",
) -> tuple[Case | None, str]:
    """
    业务人员提交案件：归属于所选项目（项目已绑定客户），不指派撰写师。
    进入待下单确认。不能改任务状态。调用方负责 commit。
    """
    fields, err = _validated_order_fields(
        customer_id=customer_id,
        project_id=project_id,
        title=title,
        case_type_code=case_type_code,
        patent_application_no=patent_application_no,
        case_note=case_note,
        material_upload_port=material_upload_port,
    )
    if err or fields is None:
        return None, err
    created_at = datetime.now(timezone.utc)
    try:
        application_no = next_case_serial(created_at)
    except ValueError as exc:
        return None, str(exc)
    case = Case(
        application_no=application_no,
        created_at=created_at,
    )
    _apply_order_fields(
        case,
        fields,
        expected_return_at=expected_return_at,
        order_at=order_at or created_at,
    )
    db.session.add(case)
    db.session.flush()
    stamp_intake_owner(case, operator)
    task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_ORDER_REVIEW)
    stamp_assignee(task, None)
    db.session.add(task)
    add_review_log(
        case_id=case.id,
        action="intake_submit",
        operator=operator,
        recipient=None,
    )
    return case, ""


def resubmit_business_order(
    operator: "User",
    case_id: int | None,
    *,
    customer_id: int | None,
    project_id: int | None,
    title: str,
    case_type_code: str,
    patent_application_no: str = "",
    expected_return_at: datetime | None = None,
    order_at: datetime | None = None,
    case_note: str = "",
    material_upload_port: str = "",
) -> tuple[Case | None, str]:
    """打回后改原单再交管理员，沿用序列号，不新建案件。调用方负责 commit。"""
    if case_id is None or case_id <= 0:
        return None, "请选择要修改的下单。"
    case = order_revision_for_owner(operator.id, case_id)
    if case is None:
        return None, "只能修改自己被打回的下单。"
    fields, err = _validated_order_fields(
        customer_id=customer_id,
        project_id=project_id,
        title=title,
        case_type_code=case_type_code,
        patent_application_no=patent_application_no,
        case_note=case_note,
        material_upload_port=material_upload_port,
        exclude_case_id=case.id,
    )
    if err or fields is None:
        return None, err
    _apply_order_fields(
        case,
        fields,
        expected_return_at=expected_return_at,
        order_at=order_at or case.order_at,
    )
    stamp_intake_owner(case, operator)
    stamp_assignee(case.task, None)
    case.task.phase_status = TaskPhase.PENDING_ORDER_REVIEW
    add_review_log(
        case_id=case.id,
        action="intake_submit",
        operator=operator,
        recipient=None,
    )
    return case, ""


def case_material_counts(case_ids: list[int]) -> dict[int, int]:
    """下单列表用的附件件数，按案件汇总。"""
    from sqlalchemy import func

    from app.models import CaseMaterial

    if not case_ids:
        return {}
    rows = (
        db.session.query(CaseMaterial.case_id, func.count(CaseMaterial.id))
        .filter(CaseMaterial.case_id.in_(case_ids))
        .group_by(CaseMaterial.case_id)
        .all()
    )
    return {int(case_id): int(count) for case_id, count in rows}


def case_materials_for_cases(case_ids: list[int]):
    """按案件取出材料，新的在前。"""
    from app.models import CaseMaterial

    if not case_ids:
        return {}
    rows = (
        CaseMaterial.query.filter(CaseMaterial.case_id.in_(case_ids))
        .order_by(CaseMaterial.created_at.desc(), CaseMaterial.id.desc())
        .all()
    )
    by_case: dict[int, list] = {case_id: [] for case_id in case_ids}
    for row in rows:
        by_case.setdefault(row.case_id, []).append(row)
    return by_case


def save_intake_attachments(case_id: int, file_storages, operator: "User") -> tuple[int, list[str]]:
    """业务下单附带的交底材料。调用前案件须已 commit。"""
    from app.case_material_upload import save_case_material_upload

    saved = 0
    errors: list[str] = []
    for file_storage in file_storages or []:
        if file_storage is None or not (file_storage.filename or "").strip():
            continue
        material, err = save_case_material_upload(
            case_id,
            file_storage,
            operator.id,
            "draft",
            "交底材料",
        )
        if material is not None:
            saved += 1
        else:
            errors.append(f"{file_storage.filename}：{err or '上传失败'}")
    return saved, errors

"""唯一在职流程人员兜底：漏选流程负责人时自动指定，多人或开关关闭时仍要求人工选。"""
from uuid import uuid4

from app import create_app
from app.case_trace import stamp_intake_owner
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _staff(suffix: str, tag: str, function: str) -> User:
    user = User(username=f"_{tag}_{suffix}", role="staff", staff_function=function)
    user.set_password("secret")
    return user


def _seed(suffix: str, *, process_count: int = 1, phase: str = TaskPhase.PENDING_ORDER_REVIEW):
    """建管理员/撰写师/业务人员与 process_count 名流程人员，外加一个指定阶段的案件。"""
    admin = User(username=f"_soleproc_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = _staff(suffix, "soleproc_writer", User.STAFF_FUNCTION_WRITER)
    processes = [
        _staff(suffix, f"soleproc_proc{index}", User.STAFF_FUNCTION_PROCESS)
        for index in range(process_count)
    ]
    business = _staff(suffix, "soleproc_biz", User.STAFF_FUNCTION_BUSINESS)
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_soleproc_customer_{suffix}")
    db.session.add_all([admin, writer, business, customer, *processes])
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_soleproc_project_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_soleproc_case_{suffix}",
        application_no=f"SP1{suffix}",
        case_type_code="invention_nonrisk_software",
    )
    stamp_intake_owner(case, business)
    db.session.add(case)
    db.session.flush()
    task = Task(case_id=case.id, phase_status=phase)
    db.session.add(task)
    db.session.commit()
    return {
        "admin_name": admin.username,
        "writer_id": writer.id,
        "process_ids": [user.id for user in processes],
        "project_id": project.id,
        "case_id": case.id,
        "task_id": task.id,
    }


def _isolate_process_owner(keep_ids: set[int]) -> list[int]:
    """临时停用其他在职流程人员，让「唯一在职流程人员」可确定；返回待恢复主键。"""
    others = [
        user
        for user in User.query.filter(User.role == "staff", User.is_active.is_(True)).all()
        if user.is_assignable_process and user.id not in keep_ids
    ]
    user_ids = [user.id for user in others]
    for user in others:
        user.is_active = False
    db.session.commit()
    return user_ids


def _restore_process_staff(user_ids: list[int]) -> None:
    for user_id in user_ids:
        user = db.session.get(User, user_id)
        if user is not None:
            user.is_active = True
    db.session.commit()


def _login(client, username: str) -> None:
    client.post("/auth/login", data={"username": username, "password": "secret"})


def test_single_process_owner_is_auto_assigned_on_order_intake():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix)
        process_id = seeded["process_ids"][0]
        quiet_ids = _isolate_process_owner({process_id})
    try:
        client = app.test_client()
        _login(client, seeded["admin_name"])

        page = client.get("/admin/order-intake")
        assert page.status_code == 200
        assert f'value="{process_id}" selected'.encode() in page.data

        approved = client.post(
            f"/admin/order-intake/{seeded['case_id']}/action",
            data={"review_action": "approve", "writer_id": str(seeded["writer_id"])},
            follow_redirects=False,
        )
        assert approved.status_code in (302, 303)

        with app.app_context():
            case = db.session.get(Case, seeded["case_id"])
            assert case.process_owner_id == process_id
            task = db.session.get(Task, seeded["task_id"])
            assert task.phase_status == TaskPhase.IN_PROGRESS
            assert task.assignee_id == seeded["writer_id"]
            log = CaseReviewLog.query.filter_by(
                case_id=seeded["case_id"], action="process_assigned"
            ).one()
            assert log.recipient_id == process_id
            assert log.note == "按唯一在职流程人员自动指定"
    finally:
        with app.app_context():
            _restore_process_staff(quiet_ids)


def test_multiple_process_owners_still_require_manual_selection():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix, process_count=2)
        quiet_ids = _isolate_process_owner(set(seeded["process_ids"]))
    try:
        client = app.test_client()
        _login(client, seeded["admin_name"])

        page = client.get("/admin/order-intake")
        assert page.status_code == 200
        assert b'name="process_owner_id"' in page.data

        approved = client.post(
            f"/admin/order-intake/{seeded['case_id']}/action",
            data={"review_action": "approve", "writer_id": str(seeded["writer_id"])},
            follow_redirects=False,
        )
        assert approved.status_code in (302, 303)

        with app.app_context():
            case = db.session.get(Case, seeded["case_id"])
            assert case.process_owner_id is None
            task = db.session.get(Task, seeded["task_id"])
            assert task.phase_status == TaskPhase.PENDING_ORDER_REVIEW
    finally:
        with app.app_context():
            _restore_process_staff(quiet_ids)


def test_auto_assign_can_be_switched_off():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix)
        quiet_ids = _isolate_process_owner({seeded["process_ids"][0]})
    app.config["AUTO_ASSIGN_SOLE_PROCESS"] = False
    try:
        client = app.test_client()
        _login(client, seeded["admin_name"])
        approved = client.post(
            f"/admin/order-intake/{seeded['case_id']}/action",
            data={"review_action": "approve", "writer_id": str(seeded["writer_id"])},
            follow_redirects=False,
        )
        assert approved.status_code in (302, 303)
        with app.app_context():
            case = db.session.get(Case, seeded["case_id"])
            assert case.process_owner_id is None
            task = db.session.get(Task, seeded["task_id"])
            assert task.phase_status == TaskPhase.PENDING_ORDER_REVIEW
    finally:
        app.config["AUTO_ASSIGN_SOLE_PROCESS"] = True
        with app.app_context():
            _restore_process_staff(quiet_ids)


def test_single_process_owner_is_auto_assigned_on_case_create():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix)
        process_id = seeded["process_ids"][0]
        quiet_ids = _isolate_process_owner({process_id})
    title = f"_soleproc_created_{suffix}"
    try:
        client = app.test_client()
        _login(client, seeded["admin_name"])
        created = client.post(
            "/admin/case-create",
            data={
                "project_id": str(seeded["project_id"]),
                "title": title,
                "case_type_code": "invention_nonrisk_software",
                "phase_status": TaskPhase.IN_PROGRESS,
            },
            follow_redirects=False,
        )
        assert created.status_code in (302, 303)
        with app.app_context():
            case = Case.query.filter_by(title=title).one()
            assert case.process_owner_id == process_id
    finally:
        with app.app_context():
            _restore_process_staff(quiet_ids)


def test_single_process_owner_is_auto_assigned_on_process_assignment():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix, phase=TaskPhase.IN_PROGRESS)
        process_id = seeded["process_ids"][0]
        quiet_ids = _isolate_process_owner({process_id})
    try:
        client = app.test_client()
        _login(client, seeded["admin_name"])
        assigned = client.post(
            "/admin/process-assignment/assign",
            data={"case_id": str(seeded["case_id"])},
            follow_redirects=True,
        )
        assert assigned.status_code == 200
        with app.app_context():
            case = db.session.get(Case, seeded["case_id"])
            assert case.process_owner_id == process_id
    finally:
        with app.app_context():
            _restore_process_staff(quiet_ids)



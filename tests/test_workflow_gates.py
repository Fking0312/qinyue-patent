"""流程/撰写闸门：没指定流程不能提交；材料下载与终审必须是本案负责人。"""

from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.case_trace import stamp_assignee, stamp_process_owner
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _seed(suffix: str, *, with_process: bool = True):
    writer = User(
        username=f"_gate_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    process = User(
        username=f"_gate_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    process.set_password("secret")
    other_process = User(
        username=f"_gate_proc2_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    other_process.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_gate_c_{suffix}")
    db.session.add_all([writer, process, other_process, customer])
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_gate_p_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_gate_case_{suffix}",
        application_no=f"GT{suffix}",
    )
    if with_process:
        stamp_process_owner(case, process)
    db.session.add(case)
    db.session.flush()
    task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
    stamp_assignee(task, writer)
    db.session.add(task)
    db.session.commit()
    return {
        "writer": writer.username,
        "process": process.username,
        "other_process": other_process.username,
        "case_id": case.id,
        "task_id": task.id,
        "process_id": process.id,
        "other_process_id": other_process.id,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def _toast(response) -> str:
    return unquote(response.headers.get("Location", ""))


def test_writer_submit_for_review_blocked_without_process_owner():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix, with_process=False)
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]

    writer = _login(app, seeded["writer"])
    uploaded = writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"writing"), "稿.docx"),
            "material_version_tag": "draft",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert uploaded.status_code in (302, 303)

    blocked = writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    )
    assert blocked.status_code in (302, 303)
    assert "尚未指定流程人员" in _toast(blocked)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.IN_PROGRESS


def test_process_owner_can_download_writer_materials_others_cannot():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix, with_process=True)
        case_id = seeded["case_id"]

    writer = _login(app, seeded["writer"])
    writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"check-me"), "核对稿.txt"),
            "material_version_tag": "final",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        from app.models import CaseMaterial

        material = CaseMaterial.query.filter_by(case_id=case_id).one()
        material_id = material.id

    owner = _login(app, seeded["process"])
    ok = owner.get(f"/staff/case-material/{case_id}/{material_id}")
    assert ok.status_code == 200
    assert ok.data == b"check-me"

    outsider = _login(app, seeded["other_process"])
    assert outsider.get(f"/staff/case-material/{case_id}/{material_id}").status_code == 404


def test_other_process_staff_cannot_submit_final_review():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix, with_process=True)
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]
        task = db.session.get(Task, task_id)
        task.phase_status = TaskPhase.OFFICE_ACTION
        db.session.commit()

    outsider = _login(app, seeded["other_process"])
    blocked = outsider.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert blocked.status_code == 404
    with app.app_context():
        from app.workflow import process_submit_final_review

        task = db.session.get(Task, task_id)
        other = db.session.get(User, seeded["other_process_id"])
        ok, message = process_submit_final_review(task, other)
        assert ok is False
        assert "只有本案流程负责人" in message
        assert db.session.get(Task, task_id).phase_status == TaskPhase.OFFICE_ACTION

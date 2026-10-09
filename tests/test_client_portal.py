"""客户端门户：角色隔离、材料版本筛选、内部案件不可见。"""

from io import BytesIO
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, CaseMaterial, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _seed_client_world(suffix: str):
    admin = User(username=f"_cp_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_cp_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_cp_cust_{suffix}")
    db.session.add_all([admin, writer, customer])
    db.session.flush()
    owner = User(
        username=f"_cp_owner_{suffix}",
        role="client",
        customer_id=customer.id,
    )
    owner.set_password("secret")
    unbound = User(username=f"_cp_unbound_{suffix}", role="client")
    unbound.set_password("secret")
    db.session.add_all([owner, unbound])
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_cp_proj_{suffix}")
    db.session.add(project)
    db.session.flush()
    visible = Case(
        project_id=project.id,
        title=f"_cp_visible_{suffix}",
        application_no=f"CV{suffix}",
    )
    internal = Case(
        project_id=project.id,
        title=f"_cp_internal_{suffix}",
        application_no=f"CI{suffix}",
        attribution=Case.ATTRIBUTION_INTERNAL,
    )
    db.session.add_all([visible, internal])
    db.session.flush()
    db.session.add_all(
        [
            Task(case_id=visible.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=writer.id),
            Task(case_id=internal.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=writer.id),
        ]
    )
    db.session.commit()
    return {
        "admin": admin.username,
        "writer": writer.username,
        "owner": owner.username,
        "unbound": unbound.username,
        "visible_id": visible.id,
        "internal_id": internal.id,
        "visible_title": visible.title,
        "internal_title": internal.title,
        "writer_id": writer.id,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def test_staff_and_admin_cannot_open_client_portal():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed_client_world(suffix)

    for username in (seeded["writer"], seeded["admin"]):
        http = _login(app, username)
        assert http.get("/client/dashboard").status_code == 403
        assert http.get("/client/cases").status_code == 403
        assert http.get(f"/client/case-detail/{seeded['visible_id']}").status_code == 403


def test_client_material_version_filter_and_hides_internal_case():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed_client_world(suffix)
        visible_id = seeded["visible_id"]

    writer = _login(app, seeded["writer"])
    for tag, name, body in (
        ("draft", "draft.txt", b"draft-bytes"),
        ("final", "final.txt", b"final-bytes"),
    ):
        uploaded = writer.post(
            f"/staff/case-detail/{visible_id}",
            data={
                "form_action": "upload_material",
                "material_file": (BytesIO(body), name),
                "material_version_tag": tag,
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert uploaded.status_code == 200

    owner = _login(app, seeded["owner"])
    listing = owner.get("/client/cases")
    assert listing.status_code == 200
    assert seeded["visible_title"].encode() in listing.data
    assert seeded["internal_title"].encode() not in listing.data

    all_page = owner.get(f"/client/case-detail/{visible_id}")
    html = all_page.get_data(as_text=True)
    assert "draft.txt" in html
    assert "final.txt" in html

    draft_page = owner.get(f"/client/case-detail/{visible_id}?material_version=draft")
    draft_html = draft_page.get_data(as_text=True)
    assert "draft.txt" in draft_html
    assert "final.txt" not in draft_html

    final_page = owner.get(f"/client/case-detail/{visible_id}?material_version=final")
    final_html = final_page.get_data(as_text=True)
    assert "final.txt" in final_html
    assert "draft.txt" not in final_html

    assert owner.get(f"/client/case-detail/{seeded['internal_id']}").status_code == 403

    unbound = _login(app, seeded["unbound"])
    empty = unbound.get("/client/cases")
    assert empty.status_code == 200
    assert seeded["visible_title"].encode() not in empty.data
    assert unbound.get(f"/client/case-detail/{visible_id}").status_code == 403

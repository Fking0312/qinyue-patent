from uuid import uuid4

from app import create_app
from app.case_types import (
    CASE_TYPE_LEAVES,
    case_type_display,
    codes_for_primary,
    legacy_case_type_code,
    validate_case_type_code,
)
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def test_case_type_taxonomy_contains_only_complete_valid_paths():
    codes = {leaf.code for leaf in CASE_TYPE_LEAVES}
    assert len(codes) == 42
    assert len(CASE_TYPE_LEAVES) == 42
    assert all(validate_case_type_code(code)[1] is None for code in codes)
    assert len([leaf for leaf in CASE_TYPE_LEAVES if leaf.primary == "invention"]) == 36
    assert len(codes_for_primary("invention")) == 39
    assert case_type_display(
        "invention_risk_precheck_mechanical"
    ) == "发明 / 风险发明 / 预审通道 / 机械类"
    assert case_type_display(
        "invention_nonrisk_priority_software"
    ) == "发明 / 非风险发明 / 优审通道 / 软通类"
    assert case_type_display(
        "invention_high_quality_unknown_chemical"
    ) == "发明 / 高质量发明 / 不确定 / 化工类"
    assert case_type_display(
        "invention_nonrisk_software"
    ) == "发明 / 非风险发明 / 不确定 / 软通类"
    assert validate_case_type_code("invention_risk_precheck_mechanical")[1] is None
    assert validate_case_type_code("invention_risk_mechanical")[1] is not None
    assert legacy_case_type_code("外观专利") == "utility_design"
    assert legacy_case_type_code("发明") is None
    legacy_case = Case(project_type="发明")
    assert legacy_case.case_type_display == "发明"
    assert legacy_case.legacy_project_type_note == "发明"
    assert legacy_case.case_type_needs_completion is True
    generated_legacy = Case(
        project_type="实用新型",
        case_type_code="utility_utility_model",
    )
    assert generated_legacy.legacy_project_type_note == ""


def test_case_type_create_filter_and_role_visibility():
    app = create_app()
    suffix = uuid4().hex[:8]
    title = f"_case_type_{suffix}"
    invalid_title = f"_case_type_invalid_{suffix}"
    note = "备注内容较长时应在案件列表悬停查看完整内容"

    with app.app_context():
        admin = User(username=f"_type_admin_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_type_staff_{suffix}", role="staff")
        staff.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_type_customer_{suffix}")
        db.session.add_all([admin, staff, customer])
        db.session.flush()
        client_user = User(
            username=f"_type_client_{suffix}",
            role="client",
            customer_id=customer.id,
        )
        client_user.set_password("secret")
        project = Project(customer_id=customer.id, name=f"_type_project_{suffix}")
        db.session.add_all([client_user, project])
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        client_name = client_user.username
        staff_id = staff.id
        project_id = project.id

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": admin_name, "password": "secret"},
        follow_redirects=True,
    )
    create_page = client.get("/admin/case-create")
    assert create_page.status_code == 200
    assert b'name="case_type_code"' in create_page.data
    assert b"invention_high_quality_unknown_chemical" in create_page.data

    invalid = client.post(
        "/admin/case-create",
        data={"project_id": str(project_id), "title": invalid_title},
        follow_redirects=False,
    )
    assert invalid.status_code in (302, 303)
    with app.app_context():
        assert Case.query.filter_by(title=invalid_title).first() is None

    created = client.post(
        "/admin/case-create",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "invention_risk_precheck_mechanical",
            "case_note": note,
            "business_owner_id": str(staff_id),
            "phase_status": TaskPhase.IN_PROGRESS,
        },
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "风险发明".encode() in created.data
    assert "预审通道".encode() in created.data

    with app.app_context():
        case = Case.query.filter_by(title=title).one()
        case_id = case.id
        assert case.case_type_code == "invention_risk_precheck_mechanical"
        assert case.project_type is None
        assert case.case_note == note
        assert Task.query.filter_by(case_id=case_id).one().assignee_id == staff_id

    invention_list = client.get("/admin/cases?case_type_primary=invention")
    assert title.encode() in invention_list.data
    assert note.encode() in invention_list.data
    assert b"qy-case-note-tooltip" in invention_list.data
    utility_list = client.get("/admin/cases?case_type_primary=utility")
    assert title.encode() not in utility_list.data

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": staff_name, "password": "secret"},
        follow_redirects=True,
    )
    staff_detail = client.get(f"/staff/case-detail/{case_id}")
    assert staff_detail.status_code == 200
    assert "风险发明".encode() in staff_detail.data

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": client_name, "password": "secret"},
        follow_redirects=True,
    )
    client_detail = client.get(f"/client/case-detail/{case_id}")
    assert client_detail.status_code == 200
    assert title.encode() in client_detail.data
    assert "预审通道".encode() not in client_detail.data

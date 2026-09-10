from io import BytesIO
from re import search
from uuid import uuid4

from app import create_app
from app.assignment_advisor import pending_assignment_cases
from app.case_material_upload import fetch_case_materials_grouped
from app.extensions import db
from app.models import Case, CaseMaterial, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _seed(*, suffix: str):
    admin = User(username=f"_bo_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_bo_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    business = User(
        username=f"_bo_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    business.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_bo_customer_{suffix}")
    db.session.add_all([admin, writer, business, customer])
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_bo_project_{suffix}")
    db.session.add(project)
    db.session.flush()
    hidden = Case(
        project_id=project.id,
        title=f"_bo_hidden_case_{suffix}",
        application_no=f"BH{suffix}",
        case_type_code="utility_utility_model",
        business_owner_id=writer.id,
    )
    db.session.add(hidden)
    db.session.flush()
    db.session.add(
        Task(
            case_id=hidden.id,
            assignee_id=writer.id,
            phase_status=TaskPhase.IN_PROGRESS,
        )
    )
    db.session.commit()
    return {
        "admin_name": admin.username,
        "writer_name": writer.username,
        "business_name": business.username,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "project_id": project.id,
        "project_name": project.name,
        "hidden_title": hidden.title,
        "hidden_serial": hidden.application_no,
    }


def test_business_order_page_shows_catalog_but_not_project_cases():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)

    client = app.test_client()
    client.post("/auth/login", data={"username": seeded["business_name"], "password": "secret"})
    page = client.get("/staff/business-orders")
    assert page.status_code == 200
    text = page.data.decode("utf-8")
    assert "下单".encode() in page.data
    assert "提交下单".encode() in page.data
    assert "新建客户".encode() in page.data
    assert "新建项目".encode() in page.data
    assert "案件交底材料上传端口".encode() in page.data
    assert "上传交底材料".encode() in page.data
    assert 'name="attachments"' in text
    assert 'enctype="multipart/form-data"' in text
    assert "初始任务状态".encode() not in page.data
    assert 'name="phase_status"' not in text
    assert seeded["customer_name"] in text
    assert seeded["project_name"] in text
    assert f'value="{seeded["project_id"]}" data-customer-id="{seeded["customer_id"]}"' in text
    assert seeded["hidden_title"] not in text
    assert seeded["hidden_serial"] not in text
    assert "功能建设中" not in text
    assert "/admin/cases" not in text
    assert "/admin/case-detail" not in text

    writer = app.test_client()
    writer.post("/auth/login", data={"username": seeded["writer_name"], "password": "secret"})
    assert writer.get("/staff/business-orders").status_code == 403
    assert writer.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": seeded["customer_id"],
            "project_id": seeded["project_id"],
            "title": "should-not-create",
            "case_type_code": "utility_utility_model",
        },
    ).status_code == 403


def test_business_can_create_customer_project_and_submit_case():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        hidden_title = seeded["hidden_title"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]

    client = app.test_client()
    client.post("/auth/login", data={"username": business_name, "password": "secret"})

    new_customer_name = f"_bo_new_customer_{suffix}"
    created_customer = client.post(
        "/staff/business-orders/customers",
        data={
            "name": new_customer_name,
            "kind": CustomerKind.COMPANY,
            "contact_name": "王联系",
        },
        follow_redirects=False,
    )
    assert created_customer.status_code in (302, 303)

    with app.app_context():
        new_customer = Customer.query.filter_by(name=new_customer_name).one()
        new_customer_id = new_customer.id

    new_project_name = f"_bo_new_project_{suffix}"
    created_project = client.post(
        "/staff/business-orders/projects",
        data={
            "customer_id": str(new_customer_id),
            "name": new_project_name,
        },
        follow_redirects=False,
    )
    assert created_project.status_code in (302, 303)
    with app.app_context():
        new_project = Project.query.filter_by(name=new_project_name).one()
        assert new_project.customer_id == new_customer_id
        assert new_project.code

    missing_project = client.post(
        "/staff/business-orders/projects",
        data={"name": f"_bo_orphan_{suffix}"},
        follow_redirects=False,
    )
    assert missing_project.status_code in (302, 303)
    with app.app_context():
        assert Project.query.filter_by(name=f"_bo_orphan_{suffix}").count() == 0

    mismatch = client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(new_customer_id),
            "project_id": str(project_id),
            "title": f"_bo_mismatch_{suffix}",
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert mismatch.status_code in (302, 303)

    order_title = f"_bo_submitted_{suffix}"
    submitted = client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": order_title,
            "case_type_code": "utility_utility_model",
            "case_note": "合同已签",
            "material_upload_port": "https://example.com/disclosure",
            "phase_status": "completed",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)

    with app.app_context():
        case = Case.query.filter_by(title=order_title).one()
        assert case.project_id == project_id
        assert case.project.customer_id == customer_id
        assert case.intake_owner_id is not None
        assert case.business_owner_id is None
        assert case.task.phase_status == TaskPhase.PENDING_ORDER_REVIEW
        assert case.task.assignee_id is None
        assert case.material_upload_port == "https://example.com/disclosure"
        assert case.id not in [item.id for item in pending_assignment_cases()]
        assert CaseReviewLog.query.filter_by(case_id=case.id, action="intake_submit").count() == 1
        assert Case.query.filter_by(title=f"_bo_mismatch_{suffix}").count() == 0

    mine = client.get("/staff/business-orders")
    mine_text = mine.data.decode("utf-8")
    assert order_title in mine_text
    assert "打开端口" in mine_text
    assert hidden_title not in mine_text
    assert new_customer_name in mine_text
    assert new_project_name in mine_text

    admin = app.test_client()
    admin.post("/auth/login", data={"username": admin_name, "password": "secret"})
    inbox = admin.get("/admin/order-intake")
    assert order_title.encode() in inbox.data
    assert b"https://example.com/disclosure" in inbox.data
    assert "打开材料上传端口".encode() in inbox.data
    assert hidden_title.encode() not in inbox.data


def test_business_order_rejects_duplicate_title_in_same_project():
    from urllib.parse import unquote

    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        business_name = seeded["business_name"]
        hidden_title = seeded["hidden_title"]

    client = app.test_client()
    client.post("/auth/login", data={"username": business_name, "password": "secret"})
    taken = client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": f"  {hidden_title}  ",
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert taken.status_code in (302, 303)
    assert Case.DUPLICATE_TITLE_IN_PROJECT_MSG in unquote(taken.headers.get("Location", ""))
    with app.app_context():
        assert Case.query.filter_by(project_id=project_id, title=hidden_title).count() == 1
        assert Case.query.filter_by(title=f"  {hidden_title}  ").count() == 0


def test_business_message_center_shows_intake_review_and_opens_orders():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]
        writer_name = seeded["writer_name"]

    business = app.test_client()
    business.post("/auth/login", data={"username": business_name, "password": "secret"})
    empty = business.get("/staff/notifications")
    assert empty.status_code == 200
    empty_text = empty.data.decode("utf-8")
    assert "消息中心" in empty_text
    assert "去下单" in empty_text
    assert "去案件列表" not in empty_text
    assert "去任务看板" not in empty_text

    order_title = f"_bo_notice_{suffix}"
    submitted = business.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": order_title,
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)

    with app.app_context():
        case = Case.query.filter_by(title=order_title).one()
        case_id = case.id

    admin = app.test_client()
    admin.post("/auth/login", data={"username": admin_name, "password": "secret"})
    rejected = admin.post(
        f"/admin/order-intake/{case_id}/action",
        data={"review_action": "reject", "reject_note": "客户名称与合同不一致"},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)

    with app.app_context():
        log = CaseReviewLog.query.filter_by(case_id=case_id, action="intake_reject").one()
        log_id = log.id

    inbox = business.get("/staff/notifications")
    assert inbox.status_code == 200
    inbox_text = inbox.data.decode("utf-8")
    assert order_title in inbox_text
    assert "下单被打回" in inbox_text
    assert "客户名称与合同不一致" in inbox_text
    status = business.get("/staff/notifications/status").get_json()
    assert status["ok"] is True
    assert status["unread"] >= 1

    opened = business.get(f"/staff/notifications/{log_id}/read", follow_redirects=False)
    assert opened.status_code in (302, 303)
    assert "/staff/business-orders" in opened.headers["Location"]
    assert f"edit={case_id}" in opened.headers["Location"]
    after = business.get("/staff/notifications/status").get_json()
    assert after["unread"] == 0

    writer = app.test_client()
    writer.post("/auth/login", data={"username": writer_name, "password": "secret"})
    writer_page = writer.get("/staff/notifications")
    assert writer_page.status_code == 200
    assert order_title.encode() not in writer_page.data
    assert writer.get("/staff/business-orders").status_code == 403


def test_business_can_resubmit_order_revision_on_same_case():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]

    client = app.test_client()
    client.post("/auth/login", data={"username": business_name, "password": "secret"})
    order_title = f"_bo_revise_{suffix}"
    submitted = client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": order_title,
            "case_type_code": "utility_utility_model",
            "case_note": "初稿",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)

    with app.app_context():
        case = Case.query.filter_by(title=order_title).one()
        case_id = case.id
        serial = case.application_no

    admin = app.test_client()
    admin.post("/auth/login", data={"username": admin_name, "password": "secret"})
    rejected = admin.post(
        f"/admin/order-intake/{case_id}/action",
        data={"review_action": "reject", "reject_note": "客户名称与合同不一致"},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)

    waiting = client.get("/staff/business-orders")
    waiting_text = waiting.data.decode("utf-8")
    assert "修改再提交" in waiting_text
    assert f"edit={case_id}" in waiting_text
    assert "重新提交" not in waiting_text

    edit_page = client.get(f"/staff/business-orders?edit={case_id}")
    edit_text = edit_page.data.decode("utf-8")
    assert edit_page.status_code == 200
    assert "修改后重新提交" in edit_text
    assert "客户名称与合同不一致" in edit_text
    assert serial in edit_text
    assert f'name="case_id" value="{case_id}"' in edit_text
    assert f'value="{order_title}"' in edit_text
    assert "重新提交" in edit_text
    assert "取消修改" in edit_text

    resubmitted = client.post(
        "/staff/business-orders/cases",
        data={
            "case_id": str(case_id),
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": order_title,
            "case_type_code": "utility_utility_model",
            "case_note": "已按合同更正客户名称",
        },
        follow_redirects=False,
    )
    assert resubmitted.status_code in (302, 303)

    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.title == order_title
        assert case.application_no == serial
        assert case.case_note == "已按合同更正客户名称"
        assert case.task.phase_status == TaskPhase.PENDING_ORDER_REVIEW
        assert Case.query.filter_by(title=order_title).count() == 1
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="intake_submit").count() == 2
        assert Case.query.filter(Case.application_no == serial).count() == 1

    inbox = admin.get("/admin/order-intake")
    assert order_title.encode() in inbox.data

    blocked = client.post(
        "/staff/business-orders/cases",
        data={
            "case_id": str(case_id),
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": order_title,
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert blocked.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Case, case_id).task.phase_status == TaskPhase.PENDING_ORDER_REVIEW
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="intake_submit").count() == 2


def test_business_order_can_upload_disclosure_material():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]
        writer_name = seeded["writer_name"]

    client = app.test_client()
    client.post("/auth/login", data={"username": business_name, "password": "secret"})
    order_title = f"_bo_file_{suffix}"
    submitted = client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": order_title,
            "case_type_code": "utility_utility_model",
            "attachments": (BytesIO(b"disclosure-bytes"), "交底说明书.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)

    with app.app_context():
        case = Case.query.filter_by(title=order_title).one()
        case_id = case.id
        material = CaseMaterial.query.filter_by(case_id=case.id).one()
        material_id = material.id
        assert material.original_name == "交底说明书.pdf"
        assert material.note == "交底材料"
        assert material.uploaded_by_role == User.STAFF_FUNCTION_BUSINESS
        disclosure, writing = fetch_case_materials_grouped(case.id, "all")
        assert [item.id for item in disclosure] == [material.id]
        assert writing == []

    mine = client.get("/staff/business-orders")
    assert "1 个文件".encode() in mine.data
    downloaded = client.get(f"/staff/business-orders/materials/{case_id}/{material_id}")
    assert downloaded.status_code == 200
    assert downloaded.data == b"disclosure-bytes"

    admin = app.test_client()
    admin.post("/auth/login", data={"username": admin_name, "password": "secret"})
    inbox = admin.get("/admin/order-intake")
    assert "交底说明书.pdf".encode() in inbox.data
    assert f"/admin/case-material/{case_id}/{material_id}".encode() in inbox.data
    admin_download = admin.get(f"/admin/case-material/{case_id}/{material_id}")
    assert admin_download.status_code == 200
    assert admin_download.data == b"disclosure-bytes"

    writer = app.test_client()
    writer.post("/auth/login", data={"username": writer_name, "password": "secret"})
    assert writer.get(f"/staff/business-orders/materials/{case_id}/{material_id}").status_code == 403


def _dashboard_stat(html: str, label: str) -> int:
    match = search(
        rf'{label}</div>\s*<div class="qy-dash-stat-value">(\d+)</div>',
        html,
    )
    assert match, f"missing dashboard stat {label}"
    return int(match.group(1))


def test_business_dashboard_is_followup_of_own_orders():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        other = User(
            username=f"_bo_biz2_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_BUSINESS,
        )
        other.set_password("secret")
        db.session.add(other)
        db.session.commit()
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]
        other_name = other.username
        writer_name = seeded["writer_name"]

    owner = app.test_client()
    owner.post("/auth/login", data={"username": business_name, "password": "secret"})
    empty = owner.get("/staff/business-dashboard")
    empty_text = empty.data.decode("utf-8")
    assert empty.status_code == 200
    assert _dashboard_stat(empty_text, "待我修改") == 0
    assert _dashboard_stat(empty_text, "待管理员确认") == 0
    assert _dashboard_stat(empty_text, "未读消息") == 0
    assert _dashboard_stat(empty_text, "本月已提交") == 0
    assert "目前没有需要修改的下单" in empty_text
    assert "去下单" in empty_text
    assert 'href="/staff/business-orders"' in empty_text
    assert "/staff/notifications?status=unread" in empty_text
    assert "功能建设中" not in empty_text
    assert "撰写中" not in empty_text
    assert "登记与核对客户款项" not in empty_text
    assert "期限关注" not in empty_text

    pending_title = f"_bo_dash_pending_{suffix}"
    revision_title = f"_bo_dash_revise_{suffix}"
    other_title = f"_bo_dash_other_{suffix}"
    for title in (pending_title, revision_title):
        submitted = owner.post(
            "/staff/business-orders/cases",
            data={
                "customer_id": str(customer_id),
                "project_id": str(project_id),
                "title": title,
                "case_type_code": "utility_utility_model",
            },
            follow_redirects=False,
        )
        assert submitted.status_code in (302, 303)

    other_client = app.test_client()
    other_client.post("/auth/login", data={"username": other_name, "password": "secret"})
    other_submitted = other_client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": other_title,
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert other_submitted.status_code in (302, 303)

    with app.app_context():
        revision_id = Case.query.filter_by(title=revision_title).one().id

    admin = app.test_client()
    admin.post("/auth/login", data={"username": admin_name, "password": "secret"})
    rejected = admin.post(
        f"/admin/order-intake/{revision_id}/action",
        data={"review_action": "reject", "reject_note": "交底材料缺附图"},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)

    home = owner.get("/staff/business-dashboard")
    home_text = home.data.decode("utf-8")
    assert _dashboard_stat(home_text, "待我修改") == 1
    assert _dashboard_stat(home_text, "待管理员确认") == 1
    assert _dashboard_stat(home_text, "未读消息") == 1
    assert _dashboard_stat(home_text, "本月已提交") == 2
    assert revision_title in home_text
    assert pending_title not in home_text
    assert other_title not in home_text
    assert "交底材料缺附图" in home_text
    assert "修改再提交" in home_text
    assert f"edit={revision_id}" in home_text

    other_home = other_client.get("/staff/business-dashboard").data.decode("utf-8")
    assert _dashboard_stat(other_home, "待我修改") == 0
    assert _dashboard_stat(other_home, "待管理员确认") == 1
    assert _dashboard_stat(other_home, "本月已提交") == 1
    assert other_title not in other_home
    assert revision_title not in other_home
    assert "目前没有需要修改的下单" in other_home

    writer = app.test_client()
    writer.post("/auth/login", data={"username": writer_name, "password": "secret"})
    assert writer.get("/staff/business-dashboard").status_code == 403


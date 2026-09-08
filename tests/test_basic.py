import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from app import create_app
from app.extensions import db
from app.models import Case, CaseMaterial, CaseMaterialDownloadLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase
from uuid import uuid4


def test_homepage():
    app = create_app()
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200


def test_staff_spa_fragment_and_full_page():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username="_spa_staff").first()
        if user is None:
            user = User(username="_spa_staff", role="staff")
            user.set_password("secret")
            db.session.add(user)
            db.session.commit()

    client = app.test_client()
    client.post("/auth/login", data={"username": "_spa_staff", "password": "secret"}, follow_redirects=True)

    frag = client.get("/staff/task-board", headers={"X-Qy-Spa": "1"})
    assert frag.status_code == 200
    assert b'id="qy-spa-main"' in frag.data
    assert b"data-spa-endpoint=\"staff.task_board\"" in frag.data

    full = client.get("/staff/dashboard")
    assert full.status_code == 200
    assert b'id="qy-spa-layout"' in full.data
    assert b'id="qySpaTabsBar"' in full.data
    assert b"staff-spa-body" in full.data
    assert b'id="qy-spa-main"' in full.data


def test_auth_register_is_disabled():
    app = create_app()
    client = app.test_client()
    r = client.get("/auth/register")
    assert r.status_code == 404

    login_page = client.get("/auth/login")
    assert login_page.status_code == 200
    assert "立即注册".encode("utf-8") not in login_page.data
    assert "联系管理员分发账号".encode("utf-8") in login_page.data


def test_admin_customers_can_list_and_create():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_customers").first()
        if admin is None:
            admin = User(username="_admin_customers", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        seed = Customer.query.filter_by(name="_seed_customer").first()
        if seed is None:
            seed = Customer(kind=CustomerKind.COMPANY, name="_seed_customer")
            db.session.add(seed)
            db.session.commit()

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_customers", "password": "secret"}, follow_redirects=True)

    r = client.get("/admin/customers?q=_seed_customer&per_page=50")
    assert r.status_code == 200
    assert "客户档案".encode("utf-8") in r.data
    assert "_seed_customer".encode("utf-8") in r.data

    r2 = client.post(
        "/admin/customers/create",
        json={
            "name": "_new_customer",
            "kind": CustomerKind.COMPANY,
            "note": "test note",
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.is_json
    assert r2.get_json().get("ok") is True
    r3 = client.get("/admin/customers?q=_new_customer&per_page=50")
    assert r3.status_code == 200
    assert "_new_customer".encode("utf-8") in r3.data


def test_admin_accounts_can_create_staff_and_client():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_accounts_{suffix}", role="admin")
        admin.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_acc_customer_{suffix}")
        db.session.add_all([admin, customer])
        db.session.commit()
        admin_name = admin.username
        customer_id = customer.id
        customer_name = customer.name

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_page = client.get("/admin/accounts")
    assert r_page.status_code == 200
    assert "账号分发".encode("utf-8") in r_page.data

    staff_name = f"_staff_distributed_{suffix}"
    r_staff = client.post(
        "/admin/accounts",
        data={
            "username": staff_name,
            "password": "secret123",
            "role": "staff",
            "staff_kind": "outsource",
            "customer_id": "",
        },
        follow_redirects=True,
    )
    assert r_staff.status_code == 200
    r_staff_search = client.get(f"/admin/accounts?q={staff_name}")
    assert staff_name.encode("utf-8") in r_staff_search.data
    assert "员工".encode("utf-8") in r_staff_search.data
    assert "外包员工".encode("utf-8") in r_staff_search.data
    with app.app_context():
        created = User.query.filter_by(username=staff_name).one()
        assert created.staff_kind == "outsource"
        assert created.staff_kind_label == "外包"

    client_name = f"_client_distributed_{suffix}"
    r_client = client.post(
        "/admin/accounts",
        data={
            "username": client_name,
            "password": "secret123",
            "role": "client",
            "customer_id": str(customer_id),
        },
        follow_redirects=True,
    )
    assert r_client.status_code == 200
    r_client_search = client.get(f"/admin/accounts?q={client_name}")
    assert client_name.encode("utf-8") in r_client_search.data
    assert customer_name.encode("utf-8") in r_client_search.data


def test_admin_accounts_can_reset_password():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_reset_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_reset_{suffix}", role="staff")
        staff.set_password("oldpass")
        db.session.add_all([admin, staff])
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        staff_id = staff.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_reset = client.post(
        "/admin/accounts",
        data={"form_action": "reset_password", "user_id": str(staff_id), "new_password": "newpass123"},
        follow_redirects=False,
    )
    assert r_reset.status_code in (302, 303)
    assert "qy_toast=" in r_reset.headers.get("Location", "")

    client.get("/auth/logout", follow_redirects=True)
    r_login_old = client.post("/auth/login", data={"username": staff_name, "password": "oldpass"}, follow_redirects=True)
    assert "账号/手机号或密码错误".encode("utf-8") in r_login_old.data

    r_login_new = client.post("/auth/login", data={"username": staff_name, "password": "newpass123"}, follow_redirects=True)
    assert r_login_new.status_code == 200
    assert "员工工作台".encode("utf-8") in r_login_new.data


def test_deactivate_staff_removes_from_assignment_pool_and_unassigns():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_deact_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_deact_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_deact_c_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_deact_p_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_deact_case_{suffix}",
            application_no=f"CN99{suffix}",
            business_owner_id=staff.id,
        )
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        staff_id = staff.id
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    r_board = client.get("/admin/task-board")
    assert r_board.status_code == 200
    assert staff_name.encode("utf-8") in r_board.data

    r_del = client.post(
        "/admin/accounts/bulk-delete",
        json={"ids": [staff_id]},
        headers={"Content-Type": "application/json"},
    )
    assert r_del.status_code == 200
    data = r_del.get_json()
    assert data["ok"] is True
    assert staff_id in data["deleted"]

    r_board2 = client.get("/admin/task-board")
    assert r_board2.status_code == 200
    # 分配下拉里不应再出现该员工
    assert f'value="{staff_id}"'.encode("utf-8") not in r_board2.data

    with app.app_context():
        staff = db.session.get(User, staff_id)
        assert staff is not None
        assert staff.is_active is False
        case = db.session.get(Case, case_id)
        assert case.business_owner_id is None
        assert case.task.assignee_id is None
        assert case.task.phase_status == TaskPhase.PENDING_ASSIGNMENT

    client.get("/auth/logout", follow_redirects=True)
    r_login = client.post(
        "/auth/login",
        data={"username": staff_name, "password": "secret"},
        follow_redirects=True,
    )
    assert "账号已停用".encode("utf-8") in r_login.data


def test_staff_and_client_can_login_with_phone_or_username():
    app = create_app()
    suffix = uuid4().hex[:8]
    phone_n = int(suffix[:7], 16) % 10**8
    phone_staff = f"138{phone_n:08d}"
    phone_client = f"139{(phone_n + 1) % 10**8:08d}"
    with app.app_context():
        admin = User(username=f"_admin_phone_login_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_phone_{suffix}", role="staff", phone=phone_staff)
        staff.set_password("secret123")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_phone_login_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        client_u = User(
            username=f"_client_phone_{suffix}",
            role="client",
            customer_id=customer.id,
            phone=phone_client,
        )
        client_u.set_password("secret123")
        db.session.add_all([admin, staff, client_u])
        db.session.commit()
        staff_name = staff.username
        client_name = client_u.username

    client = app.test_client()
    r_staff_phone = client.post(
        "/auth/login",
        data={"username": phone_staff, "password": "secret123"},
        follow_redirects=True,
    )
    assert r_staff_phone.status_code == 200
    assert "员工工作台".encode("utf-8") in r_staff_phone.data

    client.get("/auth/logout", follow_redirects=True)
    r_staff_user = client.post(
        "/auth/login",
        data={"username": staff_name, "password": "secret123"},
        follow_redirects=True,
    )
    assert r_staff_user.status_code == 200
    assert "员工工作台".encode("utf-8") in r_staff_user.data

    client.get("/auth/logout", follow_redirects=True)
    r_client_phone = client.post(
        "/auth/login",
        data={"username": phone_client, "password": "secret123"},
        follow_redirects=True,
    )
    assert r_client_phone.status_code == 200
    assert "客户工作台".encode("utf-8") in r_client_phone.data

    client.get("/auth/logout", follow_redirects=True)
    r_client_user = client.post(
        "/auth/login",
        data={"username": client_name, "password": "secret123"},
        follow_redirects=True,
    )
    assert r_client_user.status_code == 200
    assert "客户工作台".encode("utf-8") in r_client_user.data


def test_admin_project_initiation_can_list_and_create():
    app = create_app()
    suffix = uuid4().hex[:8]
    project_name = f"_new_project_{suffix}"
    with app.app_context():
        admin = User.query.filter_by(username="_admin_projects").first()
        if admin is None:
            admin = User(username="_admin_projects", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_project_customer").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_project_customer")
            db.session.add(customer)
            db.session.commit()
        customer_id = customer.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_projects", "password": "secret"}, follow_redirects=True)

    r = client.get("/admin/project-initiation")
    assert r.status_code == 200
    assert "项目立项".encode("utf-8") in r.data
    assert "_project_customer".encode("utf-8") in r.data

    r2 = client.post(
        "/admin/projects/create",
        json={
            "customer_id": customer_id,
            "name": project_name,
            "due_at": "2030-01-01T12:00",
            "description": "project note",
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.is_json
    body = r2.get_json()
    assert body.get("ok") is True
    auto_code = body.get("code") or ""
    assert len(auto_code) == 6 and auto_code.isdigit()

    r3 = client.get("/admin/project-initiation")
    assert r3.status_code == 200
    assert project_name.encode("utf-8") in r3.data
    assert auto_code.encode("utf-8") in r3.data


def test_project_code_auto_increments_within_month():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_pcode_{suffix}", role="admin")
        admin.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_pcode_c_{suffix}")
        db.session.add_all([admin, customer])
        db.session.commit()
        admin_name = admin.username
        customer_id = customer.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    codes = []
    for i in range(2):
        r = client.post(
            "/admin/projects/create",
            json={"customer_id": customer_id, "name": f"_pcode_p{i}_{suffix}"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        codes.append(r.get_json()["code"])
    assert codes[0][:4] == codes[1][:4]
    assert int(codes[1][4:]) == int(codes[0][4:]) + 1
    assert codes[0] != codes[1]


def test_admin_project_edit_can_update_fields():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_project_edit").first()
        if admin is None:
            admin = User(username="_admin_project_edit", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        c1 = Customer.query.filter_by(name="_project_edit_c1").first()
        if c1 is None:
            c1 = Customer(kind=CustomerKind.COMPANY, name="_project_edit_c1")
            db.session.add(c1)
            db.session.commit()
        c2 = Customer.query.filter_by(name="_project_edit_c2").first()
        if c2 is None:
            c2 = Customer(kind=CustomerKind.COMPANY, name="_project_edit_c2")
            db.session.add(c2)
            db.session.commit()

        from app.models import Project

        p = Project.query.filter_by(name="_project_to_edit").first()
        if p is None:
            p = Project(customer_id=c1.id, name="_project_to_edit", code="OLD")
            db.session.add(p)
            db.session.commit()
        project_id = p.id
        c2_id = c2.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_project_edit", "password": "secret"}, follow_redirects=True)

    r = client.get(f"/admin/project-edit/{project_id}")
    assert r.status_code == 200
    assert "编辑项目".encode("utf-8") in r.data

    r2 = client.post(
        f"/admin/project-edit/{project_id}",
        data={
            "customer_id": str(c2_id),
            "name": "_project_edited",
            "initiated_at": "2020-01-15T10:00",
            "due_at": "2031-05-01T09:30",
            "description": "edited note",
        },
        follow_redirects=True,
    )
    assert r2.status_code == 200
    assert "_project_edited".encode("utf-8") in r2.data

    with app.app_context():
        from app.models import Project

        updated = db.session.get(Project, project_id)
        assert updated is not None
        assert updated.initiated_at is not None
        assert updated.initiated_at.year == 2020
        assert updated.code == "OLD"  # 编辑不可改编码


def test_admin_project_edit_rejects_future_initiated_at():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_project_edit_future").first()
        if admin is None:
            admin = User(username="_admin_project_edit_future", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_project_edit_future_c").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_project_edit_future_c")
            db.session.add(customer)
            db.session.commit()

        from app.models import Project

        project = Project.query.filter_by(name="_project_future_init").first()
        if project is None:
            project = Project(customer_id=customer.id, name="_project_future_init")
            db.session.add(project)
            db.session.commit()
        project_id = project.id
        customer_id = customer.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_project_edit_future", "password": "secret"}, follow_redirects=True)
    r = client.post(
        f"/admin/project-edit/{project_id}",
        data={
            "customer_id": str(customer_id),
            "name": "_project_future_init",
            "initiated_at": "2099-01-01T00:00",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    from urllib.parse import unquote

    assert "立项时间不能晚于当前时间" in unquote(r.headers.get("Location", ""))


def test_admin_project_detail_shows_case_and_recent_tasks():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_project_detail").first()
        if admin is None:
            admin = User(username="_admin_project_detail", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_project_detail_customer").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_project_detail_customer")
            db.session.add(customer)
            db.session.commit()

        project = Project.query.filter_by(name="_project_detail").first()
        if project is None:
            project = Project(customer_id=customer.id, name="_project_detail")
            db.session.add(project)
            db.session.commit()

        case = Case.query.filter_by(application_no="CN999900000001").first()
        if case is None:
            case = Case(project_id=project.id, title="_detail_case", application_no="CN999900000001")
            db.session.add(case)
            db.session.commit()

        task = Task.query.filter_by(case_id=case.id).first()
        if task is None:
            task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
            db.session.add(task)
            db.session.commit()
        project_id = project.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_project_detail", "password": "secret"}, follow_redirects=True)

    r = client.get(f"/admin/project-detail/{project_id}")
    assert r.status_code == 200
    assert "项目详情".encode("utf-8") in r.data
    assert "_project_detail".encode("utf-8") in r.data
    assert "_detail_case".encode("utf-8") in r.data


def test_admin_customer_detail_shows_projects():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_customer_detail").first()
        if admin is None:
            admin = User(username="_admin_customer_detail", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_customer_detail").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_customer_detail")
            db.session.add(customer)
            db.session.commit()

        project = Project.query.filter_by(name="_customer_detail_project").first()
        if project is None:
            project = Project(customer_id=customer.id, name="_customer_detail_project")
            db.session.add(project)
            db.session.commit()
        customer_id = customer.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_customer_detail", "password": "secret"}, follow_redirects=True)

    r = client.get(f"/admin/customer-detail/{customer_id}")
    assert r.status_code == 200
    assert "客户详情".encode("utf-8") in r.data
    assert "_customer_detail".encode("utf-8") in r.data
    assert "_customer_detail_project".encode("utf-8") in r.data


def test_admin_customer_edit_can_update_fields():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_customer_edit").first()
        if admin is None:
            admin = User(username="_admin_customer_edit", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_customer_to_edit").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_customer_to_edit", note="old")
            db.session.add(customer)
            db.session.commit()
        customer_id = customer.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_customer_edit", "password": "secret"}, follow_redirects=True)

    r = client.get(f"/admin/customer-edit/{customer_id}")
    assert r.status_code == 200
    assert "编辑客户".encode("utf-8") in r.data

    r2 = client.post(
        f"/admin/customer-edit/{customer_id}",
        data={"name": "_customer_edited", "kind": CustomerKind.INDIVIDUAL, "note": "new note"},
        follow_redirects=True,
    )
    assert r2.status_code == 200
    assert "_customer_edited".encode("utf-8") in r2.data


def test_admin_case_create_list_and_detail():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_case_ops").first()
        if admin is None:
            admin = User(username="_admin_case_ops", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_case_customer").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_case_customer")
            db.session.add(customer)
            db.session.commit()

        project = Project.query.filter_by(name="_case_project").first()
        if project is None:
            project = Project(customer_id=customer.id, name="_case_project")
            db.session.add(project)
            db.session.commit()
        project_id = project.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_case_ops", "password": "secret"}, follow_redirects=True)
    suffix = uuid4().hex[:8]
    title = f"_case_title_{suffix}"

    r = client.post(
        "/admin/case-create",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "other",
            "formal_status": "已受理",
            "patent_application_no": "CN202410123456.7",
            "phase_status": TaskPhase.IN_PROGRESS,
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "案件详情".encode("utf-8") in r.data
    assert title.encode("utf-8") in r.data
    assert "CN202410123456.7".encode("utf-8") in r.data
    with app.app_context():
        created_case = Case.query.filter_by(title=title).one()
        assert re.fullmatch(r"\d{6}", created_case.application_no)
        assert created_case.application_no[:4] == datetime.now(timezone(timedelta(hours=8))).strftime("%y%m")
        assert created_case.patent_application_no == "CN202410123456.7"
        assert created_case.business_owner_id is None
        assert created_case.task.phase_status == TaskPhase.PENDING_ASSIGNMENT

    r2 = client.get("/admin/cases")
    assert r2.status_code == 200
    assert title.encode("utf-8") in r2.data


def test_unassigned_case_uses_pending_assignment_and_assignee_clears_it():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_pending_assign_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_pending_assign_{suffix}", role="staff")
        staff.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_pending_assign_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_pending_assign_project_{suffix}")
        db.session.add_all([admin, staff, project])
        db.session.commit()
        admin_name = admin.username
        staff_id = staff.id
        project_id = project.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    title = f"_pending_assign_case_{suffix}"
    r_create = client.post(
        "/admin/case-create",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "other",
            "phase_status": TaskPhase.IN_PROGRESS,
        },
        follow_redirects=True,
    )
    assert r_create.status_code == 200
    with app.app_context():
        case = Case.query.filter_by(title=title).one()
        case_id = case.id
        assert case.task.phase_status == TaskPhase.PENDING_ASSIGNMENT

    r_assign = client.post(
        f"/admin/case-edit/{case_id}",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "other",
            "business_owner_id": str(staff_id),
            "phase_status": TaskPhase.PENDING_REVIEW,
        },
        follow_redirects=True,
    )
    assert r_assign.status_code == 200
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.business_owner_id == staff_id
        assert case.task.phase_status == TaskPhase.PENDING_REVIEW

    r_clear = client.post(
        f"/admin/case-edit/{case_id}",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "other",
            "business_owner_id": "",
            "phase_status": TaskPhase.IN_PROGRESS,
        },
        follow_redirects=True,
    )
    assert r_clear.status_code == 200
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.business_owner_id is None
        assert case.task.phase_status == TaskPhase.PENDING_ASSIGNMENT


def test_admin_case_edit_can_update_case_and_task_phase():
    app = create_app()
    with app.app_context():
        admin = User.query.filter_by(username="_admin_case_edit").first()
        if admin is None:
            admin = User(username="_admin_case_edit", role="admin")
            admin.set_password("secret")
            db.session.add(admin)
            db.session.commit()

        customer = Customer.query.filter_by(name="_case_edit_customer").first()
        if customer is None:
            customer = Customer(kind=CustomerKind.COMPANY, name="_case_edit_customer")
            db.session.add(customer)
            db.session.commit()

        project = Project.query.filter_by(name="_case_edit_project").first()
        if project is None:
            project = Project(customer_id=customer.id, name="_case_edit_project")
            db.session.add(project)
            db.session.commit()

        suffix = uuid4().hex[:8]
        staff = User(username=f"_case_edit_staff_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add(staff)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_case_edit_{suffix}",
            application_no=f"CN8888{suffix}",
        )
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.DRAFT)
        db.session.add(task)
        db.session.commit()
        case_id = case.id
        project_id = project.id
        staff_id = staff.id

    client = app.test_client()
    client.post("/auth/login", data={"username": "_admin_case_edit", "password": "secret"}, follow_redirects=True)

    r = client.get(f"/admin/case-edit/{case_id}")
    assert r.status_code == 200
    assert "编辑案件".encode("utf-8") in r.data

    r2 = client.post(
        f"/admin/case-edit/{case_id}",
        data={
            "project_id": str(project_id),
            "title": "_case_edited",
            "application_no": f"CN7777{uuid4().hex[:8]}",
            "formal_status": "审查中",
            "case_type_code": "utility_design",
            "patent_application_no": "CN202499887766.1",
            "business_owner_id": str(staff_id),
            "phase_status": TaskPhase.PENDING_REVIEW,
        },
        follow_redirects=True,
    )
    assert r2.status_code == 200
    assert "_case_edited".encode("utf-8") in r2.data
    assert "待审核".encode("utf-8") in r2.data
    with app.app_context():
        edited_case = db.session.get(Case, case_id)
        assert edited_case.patent_application_no == "CN202499887766.1"
        assert edited_case.task.phase_status == TaskPhase.PENDING_REVIEW


def test_task_board_admin_and_staff_use_unified_list_scope():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_board_admin_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_board_staff_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_board_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()

        project = Project(customer_id=customer.id, name=f"_board_project_{suffix}")
        db.session.add(project)
        db.session.flush()

        case_mine = Case(
            project_id=project.id,
            title=f"_board_mine_{suffix}",
            application_no=f"CN66{suffix}",
            case_type_code="invention_nonrisk_software",
            case_note=f"_board_note_{suffix}",
        )
        case_other = Case(
            project_id=project.id,
            title=f"_board_other_{suffix}",
            application_no=f"CN77{suffix}",
            case_type_code="other",
        )
        case_completed = Case(
            project_id=project.id,
            title=f"_board_completed_{suffix}",
            application_no=f"CN65{suffix}",
            case_type_code="utility_utility_model",
        )
        case_bad_pending = Case(
            project_id=project.id,
            title=f"_board_bad_pending_{suffix}",
            application_no=f"CN64{suffix}",
            case_type_code="other",
        )
        db.session.add_all([case_mine, case_other, case_completed, case_bad_pending])
        db.session.flush()

        task_mine = Task(case_id=case_mine.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        task_other = Task(case_id=case_other.id, phase_status=TaskPhase.PENDING_REVIEW)
        task_completed = Task(
            case_id=case_completed.id,
            phase_status=TaskPhase.COMPLETED,
            assignee_id=staff.id,
        )
        task_bad_pending = Task(
            case_id=case_bad_pending.id,
            phase_status=TaskPhase.PENDING_ASSIGNMENT,
            assignee_id=staff.id,
        )
        db.session.add_all([task_mine, task_other, task_completed, task_bad_pending])
        db.session.commit()

        admin_name = admin.username
        staff_name = staff.username
        task_bad_pending_id = task_bad_pending.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_admin = client.get("/admin/task-board")
    assert r_admin.status_code == 200
    assert f"_board_mine_{suffix}".encode("utf-8") in r_admin.data
    assert f"_board_other_{suffix}".encode("utf-8") in r_admin.data
    assert f"_board_bad_pending_{suffix}".encode("utf-8") in r_admin.data
    assert "待分配".encode("utf-8") in r_admin.data
    with app.app_context():
        assert db.session.get(Task, task_bad_pending_id).phase_status == TaskPhase.IN_PROGRESS
    assert b"/admin/case-detail/" in r_admin.data

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff = client.get("/staff/task-board")
    assert r_staff.status_code == 200
    assert f"_board_mine_{suffix}".encode("utf-8") in r_staff.data
    assert f"_board_other_{suffix}".encode("utf-8") not in r_staff.data
    assert f"_board_bad_pending_{suffix}".encode("utf-8") in r_staff.data
    assert "待分配".encode("utf-8") not in r_staff.data
    assert b"/staff/case-detail/" in r_staff.data

    r_case_list = client.get("/staff/case-detail")
    assert r_case_list.status_code == 200
    assert "案件列表".encode("utf-8") in r_case_list.data
    assert f"_board_mine_{suffix}".encode("utf-8") in r_case_list.data
    assert f"_board_note_{suffix}".encode("utf-8") in r_case_list.data
    assert b"qy-case-note-tooltip" in r_case_list.data
    assert f"_board_project_{suffix}".encode("utf-8") not in r_case_list.data
    assert f"_board_completed_{suffix}".encode("utf-8") in r_case_list.data
    assert f"_board_other_{suffix}".encode("utf-8") not in r_case_list.data
    assert f"_board_bad_pending_{suffix}".encode("utf-8") in r_case_list.data

    r_utility = client.get("/staff/case-detail?case_type_primary=utility")
    assert f"_board_completed_{suffix}".encode("utf-8") in r_utility.data
    assert f"_board_mine_{suffix}".encode("utf-8") not in r_utility.data
    assert "待分配".encode("utf-8") not in r_utility.data


def test_staff_upload_then_submit_for_review():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_staff_case_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add(staff)
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_staff_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()

        project = Project(customer_id=customer.id, name=f"_staff_project_{suffix}")
        db.session.add(project)
        db.session.flush()

        case = Case(project_id=project.id, title=f"_staff_case_title_{suffix}", application_no=f"CN55{suffix}")
        db.session.add(case)
        db.session.flush()

        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)

    r_phase = client.post(
        f"/staff/case-detail/{case_id}",
        data={"phase_status": TaskPhase.PENDING_REVIEW},
        follow_redirects=False,
    )
    assert r_phase.status_code in (302, 303)
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.IN_PROGRESS

    r = client.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"draft body"), f"writing_{suffix}.txt"),
            "material_version_tag": "draft",
            "material_note": "提交审核",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r.status_code == 200
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.IN_PROGRESS
        assert CaseMaterial.query.filter_by(case_id=case_id).count() == 1

    unlocked_page = client.get(f"/staff/case-detail/{case_id}")
    assert b'data-upload-locked="true"' not in unlocked_page.data
    assert b'form_action" value="submit_for_review"' in unlocked_page.data

    submitted = client.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.PENDING_REVIEW

    locked_page = client.get(f"/staff/case-detail/{case_id}")
    assert b'data-upload-locked="true"' in locked_page.data
    assert b'id="staffMaterialFileInput"' in locked_page.data
    assert "材料上传已锁定".encode("utf-8") in locked_page.data

    blocked_upload = client.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"should not save"), f"locked_{suffix}.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "材料上传已锁定".encode("utf-8") in blocked_upload.data
    with app.app_context():
        assert CaseMaterial.query.filter_by(case_id=case_id).count() == 1
        task = Task.query.filter_by(case_id=case_id).one()
        task.phase_status = TaskPhase.IN_PROGRESS
        db.session.commit()

    unlocked_page = client.get(f"/staff/case-detail/{case_id}")
    assert b'data-upload-locked="true"' not in unlocked_page.data
    resubmitted = client.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"revised body"), f"revised_{suffix}.txt"),
            "material_note": "打回后重新提交",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resubmitted.status_code == 200
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.IN_PROGRESS
        assert CaseMaterial.query.filter_by(case_id=case_id).count() == 2

    client.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=True,
    )
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.PENDING_REVIEW

    r2 = client.get("/staff/task-board?status=pending_review")
    assert r2.status_code == 200
    assert f"_staff_case_title_{suffix}".encode("utf-8") in r2.data


def test_task_board_pending_review_includes_overdue_pending_review():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_pr_overdue_{suffix}", role="admin")
        admin.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_pr_overdue_customer_{suffix}")
        db.session.add_all([admin, customer])
        db.session.flush()
        project = Project(
            customer_id=customer.id,
            name=f"_pr_overdue_project_{suffix}",
            due_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        db.session.add(project)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_pr_overdue_case_{suffix}",
            application_no=f"CN88{suffix}",
        )
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        case_title = case.title

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    r = client.get("/admin/task-board?status=pending_review")
    assert r.status_code == 200
    assert case_title.encode("utf-8") in r.data

    r2 = client.get("/admin/task-board?status=overdue")
    assert r2.status_code == 200
    assert case_title.encode("utf-8") not in r2.data


def test_staff_cannot_skip_review_by_setting_pending_submit():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_staff_noskip_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add(staff)
        db.session.flush()
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_noskip_cust_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_noskip_proj_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_noskip_case_{suffix}", application_no=f"CN99{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_bad = client.post(
        f"/staff/case-detail/{case_id}",
        data={"phase_status": TaskPhase.PENDING_SUBMIT},
        follow_redirects=False,
    )
    assert r_bad.status_code in (302, 303)
    assert "qy_toast=" in r_bad.headers.get("Location", "")
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.IN_PROGRESS


def test_staff_cannot_change_phase_while_pending_review():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_staff_locked_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add(staff)
        db.session.flush()
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_locked_cust_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_locked_proj_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_locked_case_{suffix}", application_no=f"CN98{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_bad = client.post(
        f"/staff/case-detail/{case_id}",
        data={"phase_status": TaskPhase.IN_PROGRESS},
        follow_redirects=False,
    )
    assert r_bad.status_code in (302, 303)
    assert "qy_toast=" in r_bad.headers.get("Location", "")
    with app.app_context():
        assert Task.query.filter_by(case_id=case_id).one().phase_status == TaskPhase.PENDING_REVIEW


def test_admin_case_review_actions_approve_and_reject():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_review_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_review_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()

        project = Project(customer_id=customer.id, name=f"_review_project_{suffix}")
        db.session.add(project)
        db.session.flush()

        case = Case(project_id=project.id, title=f"_review_case_{suffix}", application_no=f"CN44{suffix}")
        db.session.add(case)
        db.session.flush()

        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    r = client.post(
        f"/admin/case-detail/{case_id}",
        data={"review_action": "approve"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "待递交".encode("utf-8") in r.data

    with app.app_context():
        task = Task.query.filter_by(case_id=case_id).first()
        assert task is not None
        task.phase_status = TaskPhase.PENDING_REVIEW
        db.session.commit()

    r2 = client.post(
        f"/admin/case-detail/{case_id}",
        data={"review_action": "reject", "reject_note": "请补充权利要求依据"},
        follow_redirects=True,
    )
    assert r2.status_code == 200
    assert "撰写中".encode("utf-8") in r2.data
    assert "请补充权利要求依据".encode("utf-8") in r2.data


def test_admin_case_reject_requires_reason_and_staff_can_see_latest_reason():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_rej_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_rej_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_rej_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_rej_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_rej_case_{suffix}", application_no=f"CN33{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    r = client.post(
        f"/admin/case-detail/{case_id}",
        data={"review_action": "reject", "reject_note": ""},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "qy_toast=" in r.headers.get("Location", "")
    r_after = client.get(r.headers["Location"])
    assert "待审核".encode("utf-8") in r_after.data

    note = "补充说明书实施例后再提交"
    r2 = client.post(
        f"/admin/case-detail/{case_id}",
        data={"review_action": "reject", "reject_note": note},
        follow_redirects=True,
    )
    assert r2.status_code == 200
    assert "撰写中".encode("utf-8") in r2.data

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r3 = client.get(f"/staff/case-detail/{case_id}")
    assert r3.status_code == 200
    assert note.encode("utf-8") in r3.data


def test_admin_and_staff_case_detail_show_review_history_list():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_hist_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_hist_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_hist_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_hist_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_hist_case_{suffix}", application_no=f"CN22{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.flush()

        from app.models import CaseReviewLog

        db.session.add_all(
            [
                CaseReviewLog(case_id=case.id, operator_id=admin.id, action="approve"),
                CaseReviewLog(case_id=case.id, operator_id=admin.id, action="reject", note="历史打回备注"),
            ]
        )
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_admin = client.get(f"/admin/case-detail/{case_id}")
    assert r_admin.status_code == 200
    assert "审核历史".encode("utf-8") in r_admin.data
    assert "通过".encode("utf-8") in r_admin.data
    assert "打回".encode("utf-8") in r_admin.data
    assert "历史打回备注".encode("utf-8") in r_admin.data

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff = client.get(f"/staff/case-detail/{case_id}")
    assert r_staff.status_code == 200
    assert "审核历史".encode("utf-8") in r_staff.data
    assert "通过".encode("utf-8") in r_staff.data
    assert "打回".encode("utf-8") in r_staff.data
    assert "历史打回备注".encode("utf-8") in r_staff.data


def test_case_detail_review_history_supports_pagination():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_hist_page_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_hist_page_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_hist_page_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_hist_page_case_{suffix}", application_no=f"CN11{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        db.session.flush()

        from app.models import CaseReviewLog

        for idx in range(11):
            db.session.add(
                CaseReviewLog(
                    case_id=case.id,
                    operator_id=admin.id,
                    action="reject" if idx % 2 else "approve",
                    note=f"分页备注-{idx}",
                )
            )
        db.session.commit()
        admin_name = admin.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r = client.get(f"/admin/case-detail/{case_id}?review_page=2")
    assert r.status_code == 200
    assert "分页备注-0".encode("utf-8") in r.data


def test_admin_and_staff_case_detail_review_history_filter_by_action():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_hist_filter_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_hist_filter_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_hist_filter_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_hist_filter_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_hist_filter_case_{suffix}", application_no=f"CN10{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.flush()

        from app.models import CaseReviewLog

        db.session.add_all(
            [
                CaseReviewLog(case_id=case.id, operator_id=admin.id, action="approve", note="仅通过备注"),
                CaseReviewLog(case_id=case.id, operator_id=admin.id, action="reject", note="仅打回备注"),
            ]
        )
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_admin_approve = client.get(f"/admin/case-detail/{case_id}?review_action=approve")
    assert r_admin_approve.status_code == 200
    approve_history = r_admin_approve.data.split("审核历史".encode("utf-8"), 1)[1]
    assert "仅通过备注".encode("utf-8") in approve_history
    assert "仅打回备注".encode("utf-8") not in approve_history

    r_admin_reject = client.get(f"/admin/case-detail/{case_id}?review_action=reject")
    assert r_admin_reject.status_code == 200
    reject_history = r_admin_reject.data.split("审核历史".encode("utf-8"), 1)[1]
    assert "仅打回备注".encode("utf-8") in reject_history
    assert "仅通过备注".encode("utf-8") not in reject_history

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff_approve = client.get(f"/staff/case-detail/{case_id}?review_action=approve")
    assert r_staff_approve.status_code == 200
    staff_approve_history = r_staff_approve.data.split("审核历史".encode("utf-8"), 1)[1]
    assert "仅通过备注".encode("utf-8") in staff_approve_history
    assert "仅打回备注".encode("utf-8") not in staff_approve_history

    r_staff_reject = client.get(f"/staff/case-detail/{case_id}?review_action=reject")
    assert r_staff_reject.status_code == 200
    staff_reject_history = r_staff_reject.data.split("审核历史".encode("utf-8"), 1)[1]
    assert "仅打回备注".encode("utf-8") in staff_reject_history
    assert "仅通过备注".encode("utf-8") not in staff_reject_history


def test_admin_and_staff_case_detail_review_history_filter_by_operator():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_hist_op_{suffix}", role="admin")
        admin.set_password("secret")
        reviewer = User(username=f"_reviewer_hist_op_{suffix}", role="admin")
        reviewer.set_password("secret")
        staff = User(username=f"_staff_hist_op_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, reviewer, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_hist_op_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_hist_op_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_hist_op_case_{suffix}", application_no=f"CN09{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.flush()

        from app.models import CaseReviewLog

        db.session.add_all(
            [
                CaseReviewLog(case_id=case.id, operator_id=admin.id, action="approve", note="管理员A备注"),
                CaseReviewLog(case_id=case.id, operator_id=reviewer.id, action="reject", note="管理员B备注"),
            ]
        )
        db.session.commit()
        admin_name = admin.username
        reviewer_name = reviewer.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_admin = client.get(f"/admin/case-detail/{case_id}?review_operator={admin_name}")
    assert r_admin.status_code == 200
    admin_history = r_admin.data.split("审核历史".encode("utf-8"), 1)[1]
    assert "管理员A备注".encode("utf-8") in admin_history
    assert "管理员B备注".encode("utf-8") not in admin_history

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff = client.get(f"/staff/case-detail/{case_id}?review_operator={reviewer_name}")
    assert r_staff.status_code == 200
    staff_history = r_staff.data.split("审核历史".encode("utf-8"), 1)[1]
    assert "管理员B备注".encode("utf-8") in staff_history
    assert "管理员A备注".encode("utf-8") not in staff_history


def test_admin_case_create_and_edit_support_required_business_fields():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_case_fields_{suffix}", role="admin")
        admin.set_password("secret")
        staff_zhang = User(username=f"_staff_zhang_{suffix}", role="staff")
        staff_zhang.set_password("secret")
        staff_li = User(username=f"_staff_li_{suffix}", role="staff")
        staff_li.set_password("secret")
        db.session.add_all([admin, staff_zhang, staff_li])
        db.session.flush()
        staff_zhang_id = staff_zhang.id
        staff_li_id = staff_li.id

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_case_fields_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_case_fields_project_{suffix}")
        db.session.add(project)
        db.session.commit()
        admin_name = admin.username
        project_id = project.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_create = client.post(
        "/admin/case-create",
        data={
            "project_id": str(project_id),
            "title": "_case_fields_title",
            "formal_status": "已受理",
            "case_type_code": "invention_risk_precheck_mechanical",
            "business_owner_id": str(staff_zhang_id),
            "order_at": "2032-01-01T10:00",
            "expected_return_at": "2032-01-10T10:00",
            "actual_return_at": "2032-01-09T09:00",
            "case_note": "创建时备注",
            "material_upload_port": "https://example.com/upload",
            "phase_status": TaskPhase.IN_PROGRESS,
        },
        follow_redirects=True,
    )
    assert r_create.status_code == 200
    assert "甲方要求字段".encode("utf-8") in r_create.data
    assert "发明".encode("utf-8") in r_create.data
    assert f"_staff_zhang_{suffix}".encode("utf-8") in r_create.data
    assert "创建时备注".encode("utf-8") in r_create.data
    assert "https://example.com/upload".encode("utf-8") in r_create.data

    with app.app_context():
        case = Case.query.filter_by(title="_case_fields_title").first()
        assert case is not None
        case_id = case.id
        serial = case.application_no
        assert re.fullmatch(r"\d{6}", serial)

    r_edit = client.post(
        f"/admin/case-edit/{case_id}",
        data={
            "project_id": str(project_id),
            "title": "_case_fields_title_updated",
            "formal_status": "审查中",
            "case_type_code": "utility_utility_model",
            "business_owner_id": str(staff_li_id),
            "order_at": "2032-02-01T10:00",
            "expected_return_at": "2032-02-10T10:00",
            "actual_return_at": "2032-02-11T09:00",
            "case_note": "编辑后备注",
            "material_upload_port": "https://example.com/new-upload",
            "phase_status": TaskPhase.PENDING_REVIEW,
        },
        follow_redirects=True,
    )
    assert r_edit.status_code == 200
    assert "实用新型".encode("utf-8") in r_edit.data
    assert f"_staff_li_{suffix}".encode("utf-8") in r_edit.data
    assert "编辑后备注".encode("utf-8") in r_edit.data
    assert "https://example.com/new-upload".encode("utf-8") in r_edit.data
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case is not None
        assert case.application_no == serial


def test_case_material_upload_and_download_for_admin_and_staff():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_file_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_file_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_file_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_file_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_file_case_{suffix}", application_no=f"CN07{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_upload = client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"hello patent"), "material.txt"),
            "material_version_tag": "final",
            "material_note": "定稿上传",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r_upload.status_code == 200
    assert "已上传交底材料".encode("utf-8") in r_upload.data
    assert "定稿".encode("utf-8") in r_upload.data
    assert "定稿上传".encode("utf-8") in r_upload.data

    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        assert material.version_tag == "final"
        assert material.note == "定稿上传"
        material_id = material.id
        original_name = material.original_name

    r_admin_download = client.get(f"/admin/case-material/{case_id}/{material_id}")
    assert r_admin_download.status_code == 200
    assert r_admin_download.data == b"hello patent"

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff_detail = client.get(f"/staff/case-detail/{case_id}")
    assert r_staff_detail.status_code == 200
    assert original_name.encode("utf-8") in r_staff_detail.data

    r_staff_download = client.get(f"/staff/case-material/{case_id}/{material_id}")
    assert r_staff_download.status_code == 200
    assert r_staff_download.data == b"hello patent"


def test_case_material_upload_chinese_zip_filename():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_cnzip_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_cnzip_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_cnzip_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_cnzip_case_{suffix}", application_no=f"CN07Z{suffix}")
        db.session.add(case)
        db.session.commit()
        admin_name = admin.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_upload = client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"zip-bytes"), "交底材料.zip"),
            "material_version_tag": "draft",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r_upload.status_code == 200
    assert "已上传交底材料".encode("utf-8") in r_upload.data

    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        assert material.original_name == "交底材料.zip"
        assert material.stored_name.endswith(".zip")
        assert "upload_" in material.stored_name or material.stored_name.startswith("upload_")


def test_staff_uploaded_material_filename_hidden_from_admin():
    app = create_app()
    suffix = uuid4().hex[:8]
    staff_filename = f"staff_secret_{suffix}.txt"
    with app.app_context():
        admin = User(username=f"_admin_hide_file_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_hide_file_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_hide_file_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_hide_file_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_hide_file_case_{suffix}", application_no=f"CN07H{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff_upload = client.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"staff only"), staff_filename),
            "material_version_tag": "final",
            "material_note": "员工私密上传",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert r_staff_upload.status_code == 200
    writing_section = r_staff_upload.data.split("已上传撰写材料".encode("utf-8"), 1)[1]
    assert staff_filename.encode("utf-8") not in writing_section
    assert "定稿".encode("utf-8") not in writing_section
    assert "员工私密上传".encode("utf-8") in writing_section

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_admin_detail = client.get(f"/admin/case-detail/{case_id}")
    assert r_admin_detail.status_code == 200
    materials_section = r_admin_detail.data.split("已上传撰写材料".encode("utf-8"), 1)[1]
    assert staff_filename.encode("utf-8") not in materials_section
    assert "员工私密上传".encode("utf-8") in materials_section


def test_case_material_filter_and_admin_delete():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_file_delete_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_file_delete_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_file_delete_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_file_delete_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_file_delete_case_{suffix}", application_no=f"CN06{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"draft"), "draft.txt"),
            "material_version_tag": "draft",
            "material_note": "初稿备注",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"final"), "final.txt"),
            "material_version_tag": "final",
            "material_note": "定稿备注",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    r_final_only = client.get(f"/admin/case-detail/{case_id}?material_version=final")
    assert r_final_only.status_code == 200
    assert "定稿备注".encode("utf-8") in r_final_only.data
    assert "初稿备注".encode("utf-8") not in r_final_only.data

    with app.app_context():
        final_material = (
            CaseMaterial.query.filter_by(case_id=case_id, version_tag="final")
            .order_by(CaseMaterial.id.desc())
            .first()
        )
        assert final_material is not None
        final_material_id = final_material.id

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff_final_only = client.get(f"/staff/case-detail/{case_id}?material_version=final")
    assert r_staff_final_only.status_code == 200
    assert "定稿备注".encode("utf-8") in r_staff_final_only.data
    assert "初稿备注".encode("utf-8") not in r_staff_final_only.data

    # Staff has no delete action; posting delete payload should not remove materials.
    client.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "delete_material", "material_id": str(final_material_id)},
        follow_redirects=True,
    )
    with app.app_context():
        assert db.session.get(CaseMaterial, final_material_id) is not None

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_delete = client.post(
        f"/admin/case-detail/{case_id}",
        data={"form_action": "delete_material", "material_id": str(final_material_id)},
        follow_redirects=False,
    )
    assert r_delete.status_code in (302, 303)
    assert "qy_toast=" in r_delete.headers.get("Location", "")

    with app.app_context():
        assert db.session.get(CaseMaterial, final_material_id) is None

    r_deleted_download = client.get(f"/admin/case-material/{case_id}/{final_material_id}")
    assert r_deleted_download.status_code == 404


def test_case_material_download_creates_audit_logs():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_file_log_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_file_log_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_file_log_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_file_log_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_file_log_case_{suffix}", application_no=f"CN05{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"audit"), "audit.txt"),
            "material_version_tag": "draft",
            "material_note": "日志测试",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        material_id = material.id

    r_admin_download = client.get(f"/admin/case-material/{case_id}/{material_id}")
    assert r_admin_download.status_code == 200

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff_download = client.get(f"/staff/case-material/{case_id}/{material_id}")
    assert r_staff_download.status_code == 200

    with app.app_context():
        logs = (
            CaseMaterialDownloadLog.query.filter_by(case_id=case_id, material_id=material_id)
            .order_by(CaseMaterialDownloadLog.id.asc())
            .all()
        )
        assert len(logs) == 2
        assert logs[0].operator_role == "admin"
        assert logs[1].operator_role == "staff"

    r_staff_detail = client.get(f"/staff/case-detail/{case_id}")
    assert r_staff_detail.status_code == 200
    assert "最近下载记录".encode("utf-8") in r_staff_detail.data
    assert admin_name.encode("utf-8") in r_staff_detail.data
    assert staff_name.encode("utf-8") in r_staff_detail.data


def test_case_material_download_logs_filter_and_pagination():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_file_page_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_file_page_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_file_page_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_file_page_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_file_page_case_{suffix}", application_no=f"CN04{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"page"), "page.txt"),
            "material_version_tag": "draft",
            "material_note": "分页日志测试",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        material_id = material.id

    # 11 admin logs
    for _ in range(11):
        r = client.get(f"/admin/case-material/{case_id}/{material_id}")
        assert r.status_code == 200

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    r_staff_download = client.get(f"/staff/case-material/{case_id}/{material_id}")
    assert r_staff_download.status_code == 200

    r_staff_only = client.get(f"/staff/case-detail/{case_id}?download_role=staff")
    assert r_staff_only.status_code == 200
    download_section = r_staff_only.data.split("最近下载记录".encode("utf-8"), 1)[1]
    table_body = download_section.split(b"<tbody>", 1)[1].split(b"</tbody>", 1)[0]
    assert staff_name.encode("utf-8") in table_body
    assert admin_name.encode("utf-8") not in table_body

    r_admin_page2 = client.get(f"/staff/case-detail/{case_id}?download_role=admin&download_page=2")
    assert r_admin_page2.status_code == 200
    assert "download_page=2".encode("utf-8") in r_admin_page2.data


def test_staff_cannot_access_other_assignee_case_materials():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        owner_staff = User(username=f"_staff_owner_{suffix}", role="staff")
        owner_staff.set_password("secret")
        other_staff = User(username=f"_staff_other_{suffix}", role="staff")
        other_staff.set_password("secret")
        db.session.add_all([owner_staff, other_staff])
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_staff_guard_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_staff_guard_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_staff_guard_case_{suffix}", application_no=f"CN03{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=owner_staff.id)
        db.session.add(task)
        db.session.commit()
        owner_name = owner_staff.username
        other_name = other_staff.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": owner_name, "password": "secret"}, follow_redirects=True)
    client.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"guard"), "guard.txt"),
            "material_version_tag": "draft",
            "material_note": "权限校验",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        material_id = material.id

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": other_name, "password": "secret"}, follow_redirects=True)

    r_detail = client.get(f"/staff/case-detail/{case_id}")
    assert r_detail.status_code == 403

    r_download = client.get(f"/staff/case-material/{case_id}/{material_id}")
    assert r_download.status_code == 403


def test_download_logs_can_export_csv_with_role_filter():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_admin_csv_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_staff_csv_{suffix}", role="staff")
        staff.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_csv_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        client_u = User(username=f"_client_csv_{suffix}", role="client", customer_id=customer.id)
        client_u.set_password("secret")
        db.session.add_all([admin, staff, client_u])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_csv_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(project_id=project.id, title=f"_csv_case_{suffix}", application_no=f"CN02{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        client_name = client_u.username
        case_id = case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    client.post(
        f"/admin/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"csv"), "csv.txt"),
            "material_version_tag": "draft",
            "material_note": "csv",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        material_id = material.id

    client.get(f"/admin/case-material/{case_id}/{material_id}")
    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    client.get(f"/staff/case-material/{case_id}/{material_id}")
    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": client_name, "password": "secret"}, follow_redirects=True)
    client.get(f"/client/case-material/{case_id}/{material_id}")

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r_csv = client.get(f"/admin/case-material-download-logs-export/{case_id}?download_role=staff")
    assert r_csv.status_code == 200
    assert b"text/csv" in r_csv.content_type.encode("utf-8")
    assert b"operator,role,file_name" in r_csv.data
    assert staff_name.encode("utf-8") in r_csv.data
    assert admin_name.encode("utf-8") not in r_csv.data

    r_csv_client = client.get(f"/admin/case-material-download-logs-export/{case_id}?download_role=client")
    assert r_csv_client.status_code == 200
    assert client_name.encode("utf-8") in r_csv_client.data
    assert staff_name.encode("utf-8") not in r_csv_client.data


def test_client_can_only_read_own_cases_and_download_materials():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_staff_client_{suffix}", role="staff")
        staff.set_password("secret")
        client_owner = User(username=f"_client_owner_{suffix}", role="client")
        client_owner.set_password("secret")
        client_other = User(username=f"_client_other_{suffix}", role="client")
        client_other.set_password("secret")
        db.session.add_all([staff, client_owner, client_other])
        db.session.flush()

        c_owner = Customer(kind=CustomerKind.COMPANY, name=f"_client_owner_cust_{suffix}")
        c_other = Customer(kind=CustomerKind.COMPANY, name=f"_client_other_cust_{suffix}")
        db.session.add_all([c_owner, c_other])
        db.session.flush()
        client_owner.customer_id = c_owner.id
        client_other.customer_id = c_other.id

        p_owner = Project(customer_id=c_owner.id, name=f"_client_owner_proj_{suffix}")
        p_other = Project(customer_id=c_other.id, name=f"_client_other_proj_{suffix}")
        db.session.add_all([p_owner, p_other])
        db.session.flush()

        case_owner = Case(project_id=p_owner.id, title=f"_client_owner_case_{suffix}", application_no=f"CN01{suffix}")
        case_other = Case(project_id=p_other.id, title=f"_client_other_case_{suffix}", application_no=f"CN00{suffix}")
        db.session.add_all([case_owner, case_other])
        db.session.flush()
        task_owner = Task(case_id=case_owner.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        task_other = Task(case_id=case_other.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id)
        db.session.add_all([task_owner, task_other])
        db.session.commit()

        staff_name = staff.username
        owner_name = client_owner.username
        case_owner_id = case_owner.id
        case_other_id = case_other.id

    client = app.test_client()
    client.post("/auth/login", data={"username": staff_name, "password": "secret"}, follow_redirects=True)
    client.post(
        f"/staff/case-detail/{case_owner_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"client"), "client.txt"),
            "material_version_tag": "final",
            "material_note": "给客户",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        material = CaseMaterial.query.filter_by(case_id=case_owner_id).order_by(CaseMaterial.id.desc()).first()
        assert material is not None
        material_id = material.id

    client.get("/auth/logout", follow_redirects=True)
    client.post("/auth/login", data={"username": owner_name, "password": "secret"}, follow_redirects=True)

    r_cases = client.get("/client/cases")
    assert r_cases.status_code == 200
    assert f"_client_owner_case_{suffix}".encode("utf-8") in r_cases.data
    assert f"_client_other_case_{suffix}".encode("utf-8") not in r_cases.data

    r_detail = client.get(f"/client/case-detail/{case_owner_id}")
    assert r_detail.status_code == 200
    assert "材料下载".encode("utf-8") in r_detail.data
    assert "client.txt".encode("utf-8") in r_detail.data

    r_download = client.get(f"/client/case-material/{case_owner_id}/{material_id}")
    assert r_download.status_code == 200
    assert r_download.data == b"client"

    r_forbidden = client.get(f"/client/case-detail/{case_other_id}")
    assert r_forbidden.status_code == 403

    r_post_forbidden = client.post(
        f"/client/case-detail/{case_owner_id}",
        data={"form_action": "upload_material"},
        follow_redirects=True,
    )
    assert r_post_forbidden.status_code == 405

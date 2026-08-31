"""员工职能：默认回填、账号维护、登录分流与越权边界。"""

from urllib.parse import unquote
from uuid import uuid4

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, User


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _make_user(*, username: str, role: str, password: str = "secret", **kwargs) -> User:
    user = User(username=username, role=role, **kwargs)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def test_staff_function_read_fallback_and_labels():
    app = create_app()
    with app.app_context():
        empty = User(username=_unique("_fn_empty"), role="staff", staff_function=None)
        invalid = User(username=_unique("_fn_bad"), role="staff", staff_function="unknown")
        writer = User(username=_unique("_fn_writer"), role="staff", staff_function="writer")
        process = User(username=_unique("_fn_process"), role="staff", staff_function="process")
        business = User(username=_unique("_fn_biz"), role="staff", staff_function="business")
        client = User(username=_unique("_fn_client"), role="client", staff_function="process")

        assert empty.staff_function_normalized == "writer"
        assert empty.staff_function_label == "撰写师"
        assert empty.is_assignable_writer is True
        assert empty.home_endpoint == "staff.dashboard"
        assert invalid.staff_function_normalized == "writer"
        assert writer.staff_function_label == "撰写师"
        assert writer.is_assignable_writer is True
        assert process.staff_function_normalized == "process"
        assert process.is_assignable_writer is False
        assert process.staff_function_label == "流程人员"
        assert process.home_endpoint == "staff.process_dashboard"
        assert business.home_endpoint == "staff.business_dashboard"
        assert business.staff_function_label == "业务人员"
        assert business.is_assignable_writer is False
        assert client.staff_function_normalized == ""
        assert client.staff_function_label == ""
        assert client.home_endpoint == "client.dashboard"
        assert User(username="a", role="admin").home_endpoint == "admin.dashboard"


def test_staff_function_migration_backfills_empty_staff_only():
    app = create_app()
    with app.app_context():
        staff = _make_user(username=_unique("_fn_mig_staff"), role="staff", staff_function=None)
        client_user = _make_user(
            username=_unique("_fn_mig_client"),
            role="client",
            staff_function=None,
        )
        already = _make_user(
            username=_unique("_fn_mig_proc"),
            role="staff",
            staff_function="process",
        )
        db.session.execute(
            text(
                "UPDATE users SET staff_function = 'writer' "
                "WHERE role = 'staff' AND (staff_function IS NULL OR staff_function = '')"
            )
        )
        db.session.commit()
        db.session.refresh(staff)
        db.session.refresh(client_user)
        db.session.refresh(already)
        assert staff.staff_function == "writer"
        assert client_user.staff_function is None
        assert already.staff_function == "process"


def test_admin_accounts_staff_function_create_update_filter_and_client_ignored():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = _make_user(username=f"_admin_fn_{suffix}", role="admin")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_fn_cust_{suffix}")
        db.session.add(customer)
        db.session.commit()
        admin_name = admin.username
        customer_id = customer.id

    http = app.test_client()
    http.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    page = http.get("/admin/accounts")
    assert page.status_code == 200
    assert "员工职能".encode("utf-8") in page.data
    assert "撰写师".encode("utf-8") in page.data
    assert b'name="form_action" value="update_staff_function"' not in page.data
    assert "角色权限".encode("utf-8") in page.data

    roles_page = http.get("/admin/role-permissions")
    assert roles_page.status_code == 200
    assert "角色权限".encode("utf-8") in roles_page.data
    assert b'name="staff_function"' in roles_page.data

    writer_name = f"_staff_fn_writer_{suffix}"
    created_writer = http.post(
        "/admin/accounts",
        data={
            "username": writer_name,
            "password": "secret123",
            "role": "staff",
        },
        follow_redirects=True,
    )
    assert created_writer.status_code == 200

    process_name = f"_staff_fn_process_{suffix}"
    created_process = http.post(
        "/admin/accounts",
        data={
            "username": process_name,
            "password": "secret123",
            "role": "staff",
            "staff_function": "process",
            "staff_kind": "formal",
        },
        follow_redirects=True,
    )
    assert created_process.status_code == 200

    client_name = f"_client_fn_{suffix}"
    created_client = http.post(
        "/admin/accounts",
        data={
            "username": client_name,
            "password": "secret123",
            "role": "client",
            "customer_id": str(customer_id),
            "staff_function": "business",
        },
        follow_redirects=True,
    )
    assert created_client.status_code == 200

    with app.app_context():
        writer = User.query.filter_by(username=writer_name).one()
        process = User.query.filter_by(username=process_name).one()
        client_user = User.query.filter_by(username=client_name).one()
        assert writer.staff_function == "writer"
        assert process.staff_function == "process"
        assert client_user.staff_function is None
        writer_id = writer.id
        client_id = client_user.id

    updated = http.post(
        "/admin/role-permissions",
        data={
            "user_id": str(writer_id),
            "staff_function": "business",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    assert "角色权限".encode("utf-8") in updated.data
    with app.app_context():
        assert db.session.get(User, writer_id).staff_function == "business"

    ignored = http.post(
        "/admin/role-permissions",
        data={
            "user_id": str(client_id),
            "staff_function": "writer",
        },
    )
    assert ignored.status_code == 302
    assert "仅员工账号可设置职能" in unquote(ignored.headers.get("Location", ""))
    with app.app_context():
        assert db.session.get(User, client_id).staff_function is None

    filtered = http.get("/admin/accounts?staff_function=process")
    assert filtered.status_code == 200
    assert process_name.encode("utf-8") in filtered.data
    assert writer_name.encode("utf-8") not in filtered.data
    assert "流程人员".encode("utf-8") in filtered.data

    writer_filter = http.get("/admin/accounts?staff_function=writer")
    assert writer_filter.status_code == 200
    assert process_name.encode("utf-8") not in writer_filter.data


def test_staff_function_login_redirects_and_forbidden_writer_pages():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        writer = _make_user(
            username=f"_fn_login_writer_{suffix}",
            role="staff",
            staff_function="writer",
        )
        process = _make_user(
            username=f"_fn_login_process_{suffix}",
            role="staff",
            staff_function="process",
        )
        business = _make_user(
            username=f"_fn_login_biz_{suffix}",
            role="staff",
            staff_function="business",
        )
        writer_name = writer.username
        process_name = process.username
        business_name = business.username

    writer_client = app.test_client()
    writer_login = writer_client.post(
        "/auth/login",
        data={"username": writer_name, "password": "secret"},
    )
    assert writer_login.status_code == 302
    assert "/staff/dashboard" in writer_login.headers["Location"]
    assert writer_client.get("/staff/dashboard").status_code == 200
    assert writer_client.get("/staff/process-dashboard").status_code == 403
    assert writer_client.get("/staff/business-dashboard").status_code == 403

    process_client = app.test_client()
    process_login = process_client.post(
        "/auth/login",
        data={"username": process_name, "password": "secret"},
    )
    assert process_login.status_code == 302
    assert "/staff/process-dashboard" in process_login.headers["Location"]
    home = process_client.get("/staff/process-dashboard")
    assert home.status_code == 200
    assert "流程工作台".encode("utf-8") in home.data
    assert "审核案件跟进".encode("utf-8") in home.data
    assert "功能建设中".encode("utf-8") in home.data
    assert "任务看板".encode("utf-8") not in home.data
    followup = process_client.get("/staff/process-followup")
    assert followup.status_code == 200
    for path in (
        "/staff/dashboard",
        "/staff/task-board",
        "/staff/case-detail",
        "/staff/worklog",
        "/staff/notifications",
        "/staff/business-dashboard",
    ):
        assert process_client.get(path).status_code == 403
    root = process_client.get("/")
    assert root.status_code == 302
    assert "/staff/process-dashboard" in root.headers["Location"]

    business_client = app.test_client()
    business_login = business_client.post(
        "/auth/login",
        data={"username": business_name, "password": "secret"},
    )
    assert business_login.status_code == 302
    assert "/staff/business-dashboard" in business_login.headers["Location"]
    biz_home = business_client.get("/staff/business-dashboard")
    assert biz_home.status_code == 200
    assert "业务工作台".encode("utf-8") in biz_home.data
    assert "下单".encode("utf-8") in biz_home.data
    assert "收账".encode("utf-8") in biz_home.data
    assert business_client.get("/staff/business-orders").status_code == 200
    assert business_client.get("/staff/business-collections").status_code == 200
    assert business_client.get("/staff/dashboard").status_code == 403
    assert business_client.get("/staff/process-dashboard").status_code == 403
    assert business_client.get("/staff/case-detail").status_code == 403


def test_spa_home_tab_follows_staff_function_and_toast_host_exists():
    """标签栏「首页」按职能取，标签缓存按账号隔离，Toast 容器各端都在。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        writer = _make_user(
            username=f"_fn_home_writer_{suffix}",
            role="staff",
            staff_function="writer",
        )
        process = _make_user(
            username=f"_fn_home_proc_{suffix}",
            role="staff",
            staff_function="process",
        )
        business = _make_user(
            username=f"_fn_home_biz_{suffix}",
            role="staff",
            staff_function="business",
        )
        admin = _make_user(username=f"_fn_home_admin_{suffix}", role="admin")
        cases = [
            (writer.username, "/staff/dashboard", writer.id),
            (process.username, "/staff/process-dashboard", process.id),
            (business.username, "/staff/business-dashboard", business.id),
            (admin.username, "/admin/dashboard", admin.id),
        ]

    for name, home_path, user_id in cases:
        http = app.test_client()
        login = http.post("/auth/login", data={"username": name, "password": "secret"})
        assert login.status_code == 302
        assert home_path in login.headers["Location"]

        page = http.get(home_path)
        assert page.status_code == 200
        html = page.data.decode("utf-8")
        # 前端据此渲染固定在最左的「首页」标签；写死 /dashboard 会让流程/业务点出 403。
        assert f'data-spa-home="{home_path}"' in html
        # 标签缓存 key 掺入账号，避免同一浏览器换人登录后继承上一个人的标签。
        assert f'data-spa-scope="{user_id}"' in html
        # 缺少容器时 qyShowToast 会静默返回，所有操作提示都看不到。
        assert html.count('id="qyToastContainer"') == 1

    # 流程与业务人员的侧栏不应出现任何撰写师专属链接。
    for name, own_home, _user_id in cases[1:3]:
        http = app.test_client()
        http.post("/auth/login", data={"username": name, "password": "secret"})
        html = http.get(own_home).data.decode("utf-8")
        assert 'href="/staff/dashboard"' not in html
        assert 'href="/staff/task-board"' not in html


def test_only_writers_enter_case_assignment_pool():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = _make_user(username=f"_admin_assign_pool_{suffix}", role="admin")
        writer = _make_user(
            username=f"_writer_pool_{suffix}",
            role="staff",
            staff_function="writer",
        )
        process = _make_user(
            username=f"_process_pool_{suffix}",
            role="staff",
            staff_function="process",
        )
        business = _make_user(
            username=f"_biz_pool_{suffix}",
            role="staff",
            staff_function="business",
        )
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_pool_cust_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_pool_proj_{suffix}")
        db.session.add(project)
        db.session.commit()
        admin_name = admin.username
        writer_id = writer.id
        process_id = process.id
        business_id = business.id
        writer_name = writer.username
        process_name = process.username
        business_name = business.username
        project_id = project.id

    http = app.test_client()
    http.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    create_page = http.get("/admin/case-create")
    assert create_page.status_code == 200
    html = create_page.data.decode("utf-8")
    assert writer_name in html
    assert f'value="{writer_id}"' in html
    assert f'value="{process_id}"' not in html
    assert f'value="{business_id}"' not in html
    assert process_name not in html
    assert business_name not in html

    title = f"_pool_case_{suffix}"
    created = http.post(
        "/admin/case-create",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "other",
            "business_owner_id": str(process_id),
        },
    )
    assert created.status_code == 302
    assert "仅撰写师可进入案件分配池" in unquote(created.headers.get("Location", ""))

    ok = http.post(
        "/admin/case-create",
        data={
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "other",
            "business_owner_id": str(writer_id),
        },
        follow_redirects=True,
    )
    assert ok.status_code == 200
    with app.app_context():
        case = Case.query.filter_by(title=title).one()
        assert case.business_owner_id == writer_id


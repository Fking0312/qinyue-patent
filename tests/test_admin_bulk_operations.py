"""管理端批量删除 / 恢复 / 导出的护栏。"""

from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _login_admin(app):
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_bulk_admin_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.commit()
        admin_name = admin.username
        admin_id = admin.id
    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    return client, suffix, admin_id


def test_bulk_delete_customers_skips_those_with_projects_and_unbinds_clients():
    app = create_app()
    client, suffix, _admin_id = _login_admin(app)
    with app.app_context():
        empty = Customer(kind=CustomerKind.COMPANY, name=f"_bulk_empty_{suffix}")
        busy = Customer(kind=CustomerKind.COMPANY, name=f"_bulk_busy_{suffix}")
        db.session.add_all([empty, busy])
        db.session.flush()
        project = Project(customer_id=busy.id, name=f"_bulk_busy_proj_{suffix}")
        bound = User(
            username=f"_bulk_client_{suffix}",
            role="client",
            customer_id=empty.id,
        )
        bound.set_password("secret")
        db.session.add_all([project, bound])
        db.session.commit()
        empty_id = empty.id
        busy_id = busy.id
        bound_id = bound.id

    missing = client.post("/admin/customers/bulk-delete", json={"ids": []})
    assert missing.status_code == 400
    not_json = client.post("/admin/customers/bulk-delete", data={"ids": "1"})
    assert not_json.status_code == 415

    result = client.post(
        "/admin/customers/bulk-delete",
        json={"ids": [empty_id, busy_id, "x", empty_id]},
        headers={"Content-Type": "application/json"},
    )
    assert result.status_code == 200
    payload = result.get_json()
    assert payload["ok"] is True
    assert empty_id in payload["deleted"]
    assert busy_id not in payload["deleted"]
    reasons = {item["id"]: item["reason"] for item in payload["skipped"]}
    assert reasons[busy_id] == "存在关联项目，无法删除"
    assert reasons["x"] == "非法的编号"

    with app.app_context():
        assert db.session.get(Customer, empty_id) is None
        assert db.session.get(Customer, busy_id) is not None
        assert db.session.get(User, bound_id).customer_id is None


def test_bulk_delete_projects_and_cases_follow_relation_rules():
    app = create_app()
    client, suffix, _admin_id = _login_admin(app)
    with app.app_context():
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_bulk_pc_{suffix}")
        db.session.add(customer)
        db.session.flush()
        empty_proj = Project(customer_id=customer.id, name=f"_bulk_empty_p_{suffix}")
        busy_proj = Project(customer_id=customer.id, name=f"_bulk_busy_p_{suffix}")
        db.session.add_all([empty_proj, busy_proj])
        db.session.flush()
        case = Case(
            project_id=busy_proj.id,
            title=f"_bulk_case_{suffix}",
            application_no=f"BD{suffix}",
        )
        db.session.add(case)
        db.session.flush()
        db.session.add(Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS))
        db.session.commit()
        empty_id = empty_proj.id
        busy_id = busy_proj.id
        case_id = case.id

    blocked = client.post(
        "/admin/projects/bulk-delete",
        json={"ids": [empty_id, busy_id]},
    )
    payload = blocked.get_json()
    assert empty_id in payload["deleted"]
    assert any(item["id"] == busy_id and "关联案件" in item["reason"] for item in payload["skipped"])

    gone = client.post("/admin/cases/bulk-delete", json={"ids": [case_id, 0, 99999999]})
    case_payload = gone.get_json()
    assert case_id in case_payload["deleted"]
    skipped_ids = {item["id"] for item in case_payload["skipped"]}
    assert 99999999 in skipped_ids

    with app.app_context():
        assert db.session.get(Case, case_id) is None
        assert Task.query.filter_by(case_id=case_id).count() == 0
        assert db.session.get(Project, busy_id) is not None

    now_empty = client.post("/admin/projects/bulk-delete", json={"ids": [busy_id]})
    assert busy_id in now_empty.get_json()["deleted"]


def test_accounts_bulk_delete_skips_admin_and_self_then_reactivate_works():
    app = create_app()
    client, suffix, admin_id = _login_admin(app)
    with app.app_context():
        staff = User(username=f"_bulk_staff_{suffix}", role="staff")
        staff.set_password("secret")
        frozen = User(username=f"_bulk_frozen_{suffix}", role="client")
        frozen.set_password("secret")
        frozen.is_active = False
        db.session.add_all([staff, frozen])
        db.session.commit()
        staff_id = staff.id
        frozen_id = frozen.id

    result = client.post(
        "/admin/accounts/bulk-delete",
        json={"ids": [admin_id, staff_id, frozen_id]},
    )
    payload = result.get_json()
    assert staff_id in payload["deleted"]
    skipped = {item["id"]: item["reason"] for item in payload["skipped"]}
    assert skipped[admin_id] == "仅可删除员工/客户账号"
    assert skipped[frozen_id] == "账号已是离职/冻结状态"

    with app.app_context():
        assert db.session.get(User, staff_id).is_active is False
        assert db.session.get(User, admin_id).is_active is True

    revived = client.post(
        "/admin/accounts/bulk-reactivate",
        json={"ids": [staff_id, frozen_id, admin_id]},
    )
    revived_payload = revived.get_json()
    assert staff_id in revived_payload["reactivated"]
    assert frozen_id in revived_payload["reactivated"]
    assert any(item["id"] == admin_id for item in revived_payload["skipped"])
    with app.app_context():
        assert db.session.get(User, staff_id).is_active is True
        assert db.session.get(User, frozen_id).is_active is True

    already = client.post("/admin/accounts/bulk-reactivate", json={"ids": [staff_id]})
    assert any(
        item["id"] == staff_id and "正常状态" in item["reason"]
        for item in already.get_json()["skipped"]
    )


def test_customers_export_xlsx_and_staff_cannot_bulk_delete():
    app = create_app()
    client, suffix, _admin_id = _login_admin(app)
    with app.app_context():
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_export_c_{suffix}")
        staff = User(username=f"_export_staff_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([customer, staff])
        db.session.commit()
        customer_id = customer.id
        staff_name = staff.username

    empty = client.post("/admin/customers/export", json={"ids": []})
    assert empty.status_code == 400
    exported = client.post("/admin/customers/export", json={"ids": [customer_id]})
    assert exported.status_code == 200
    assert exported.data.startswith(b"PK")
    assert "spreadsheetml" in exported.mimetype
    assert "customers_export_" in exported.headers.get("Content-Disposition", "")

    staff_client = app.test_client()
    staff_client.post("/auth/login", data={"username": staff_name, "password": "secret"})
    forbidden = staff_client.post(
        "/admin/customers/bulk-delete",
        json={"ids": [customer_id]},
    )
    assert forbidden.status_code == 403

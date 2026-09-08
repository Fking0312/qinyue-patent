"""专利局退稿 → 内部案件：归属改写、客户端隐藏、统计口径与重做链路。"""

from datetime import datetime, timezone
from uuid import uuid4

from app import create_app
from app.case_statistics import case_statistics_data
from app.extensions import db
from app.models import (
    Case,
    CaseMaterial,
    CaseReviewLog,
    Customer,
    CustomerKind,
    Project,
    Task,
    User,
)
from app.workflow import TaskPhase


def _seed_case(suffix: str, *, serial: str, attribution: str = Case.ATTRIBUTION_CUSTOMER):
    """建一套客户/项目/案件/任务，返回各 id 与登录名。"""
    admin = User(username=f"_rej_admin_{suffix}", role="admin")
    admin.set_password("secret")
    staff = User(username=f"_rej_staff_{suffix}", role="staff")
    staff.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_rej_customer_{suffix}")
    db.session.add_all([admin, staff, customer])
    db.session.flush()
    client_user = User(username=f"_rej_client_{suffix}", role="client", customer_id=customer.id)
    client_user.set_password("secret")
    project = Project(customer_id=customer.id, name=f"_rej_project_{suffix}")
    db.session.add_all([client_user, project])
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_rej_case_{suffix}",
        application_no=serial,
        case_type_code="invention_nonrisk_unknown_software",
        business_owner_id=staff.id,
        attribution=attribution,
    )
    db.session.add(case)
    db.session.flush()
    task = Task(case_id=case.id, assignee_id=staff.id, phase_status=TaskPhase.IN_PROGRESS)
    material = CaseMaterial(
        case_id=case.id,
        uploaded_by_id=staff.id,
        original_name=f"draft_{suffix}.txt",
        stored_name=f"stored_{suffix}.txt",
        version_tag="final",
    )
    db.session.add_all([task, material])
    db.session.commit()
    return {
        "admin_name": admin.username,
        "client_name": client_user.username,
        "case_id": case.id,
        "task_id": task.id,
        "material_id": material.id,
        "title": case.title,
    }


def _login(app, username):
    http = app.test_client()
    resp = http.post("/auth/login", data={"username": username, "password": "secret"})
    assert resp.status_code == 302
    return http


def test_new_case_defaults_to_customer_attribution():
    """默认归属必须是客户案件，否则现有案件一上线全部变内部。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed_case(suffix, serial=f"RJ0{suffix[:5]}")
        case = db.session.get(Case, seeded["case_id"])
        assert case.attribution == Case.ATTRIBUTION_CUSTOMER
        assert case.is_internal is False
        assert case.is_rejected is False
        assert case.attribution_label == "客户案件"


def test_office_reject_turns_case_internal_and_hides_it_from_client():
    """标记退稿：归属转内部并留痕；客户端列表、详情、材料下载一律不可达。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed_case(suffix, serial=f"RJ1{suffix[:5]}")
    case_id = seeded["case_id"]

    # 退稿前客户看得到自己的案件
    client_http = _login(app, seeded["client_name"])
    before = client_http.get("/client/cases")
    assert before.status_code == 200
    assert seeded["title"].encode() in before.data
    assert client_http.get(f"/client/case-detail/{case_id}").status_code == 200

    admin_http = _login(app, seeded["admin_name"])
    # 原因必填，否则退稿理由无从追溯
    blank = admin_http.post(
        f"/admin/case-detail/{case_id}",
        data={"form_action": "mark_rejected", "case_reject_note": "   "},
    )
    assert blank.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Case, case_id).is_rejected is False

    marked = admin_http.post(
        f"/admin/case-detail/{case_id}",
        data={"form_action": "mark_rejected", "case_reject_note": "形式审查未通过"},
    )
    assert marked.status_code in (302, 303)
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.rejected_at is not None
        assert case.reject_note == "形式审查未通过"
        assert case.attribution == Case.ATTRIBUTION_INTERNAL
        assert case.is_internal is True
        assert case.attribution_label == "内部案件"
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="office_reject").count() == 1

    # 客户端三条路径都要断：列表过滤掉，详情与材料下载 403（挡收藏夹里的旧链接）
    after = client_http.get("/client/cases")
    assert after.status_code == 200
    assert seeded["title"].encode() not in after.data
    assert client_http.get(f"/client/case-detail/{case_id}").status_code == 403
    assert (
        client_http.get(
            f"/client/case-material/{case_id}/{seeded['material_id']}"
        ).status_code
        == 403
    )

    # 管理端仍要能按归属把退稿案件筛出来，否则标记完就找不到了
    internal_list = admin_http.get(f"/admin/cases?attribution={Case.ATTRIBUTION_INTERNAL}")
    assert internal_list.status_code == 200
    assert seeded["title"].encode() in internal_list.data
    customer_list = admin_http.get(f"/admin/cases?attribution={Case.ATTRIBUTION_CUSTOMER}")
    assert seeded["title"].encode() not in customer_list.data


def test_rejected_case_stays_reworkable_and_reject_mark_can_be_reverted():
    """退稿不是终态：可改回撰写中自行重做；误标可撤销并恢复客户归属。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed_case(suffix, serial=f"RJ2{suffix[:5]}")
    case_id = seeded["case_id"]
    task_id = seeded["task_id"]

    admin_http = _login(app, seeded["admin_name"])
    admin_http.post(
        f"/admin/case-detail/{case_id}",
        data={"form_action": "mark_rejected", "case_reject_note": "实审未通过"},
    )

    # 自己重新做：状态回到撰写中，说明退稿没把案件锁死
    reworked = admin_http.post(
        f"/admin/case-detail/{case_id}",
        data={"form_action": "update_phase", "phase_status": TaskPhase.IN_PROGRESS},
    )
    assert reworked.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.IN_PROGRESS
        # 重做期间仍是内部案件，不能悄悄回到客户名下
        assert db.session.get(Case, case_id).is_internal is True

    reverted = admin_http.post(f"/admin/case-detail/{case_id}", data={"form_action": "revert_rejected"})
    assert reverted.status_code in (302, 303)
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.rejected_at is None
        assert case.reject_note is None
        assert case.attribution == Case.ATTRIBUTION_CUSTOMER
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="reject_undo").count() == 1

    # 撤销后客户重新可见
    client_http = _login(app, seeded["client_name"])
    assert client_http.get(f"/client/case-detail/{case_id}").status_code == 200


def test_internal_cases_are_counted_separately_from_customer_total():
    """内部案件单独一栏：不进总数、不进类型占比，但要有自己的计数。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    stamp = datetime(2026, 3, 12, 4, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    with app.app_context():
        keep = _seed_case(suffix, serial=f"RJ3{suffix[:5]}")
        drop = _seed_case(f"{suffix}b", serial=f"RJ4{suffix[:5]}")
        for case_id in (keep["case_id"], drop["case_id"]):
            db.session.get(Case, case_id).created_at = stamp
        db.session.commit()

        baseline = case_statistics_data(2026, 3, "created")
        assert baseline["total"] == 2
        assert baseline["internal_total"] == 0

        internal_case = db.session.get(Case, drop["case_id"])
        internal_case.attribution = Case.ATTRIBUTION_INTERNAL
        internal_case.rejected_at = stamp
        db.session.commit()

        after = case_statistics_data(2026, 3, "created")
        assert after["total"] == 1
        assert after["internal_total"] == 1
        # 类型占比也必须跟着掉，否则总数与明细对不上
        assert sum(row["count"] for row in after["rows"]) + after["unclassified"] == 1

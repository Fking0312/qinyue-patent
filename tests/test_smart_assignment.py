"""智能派单：撰写师建议顺序的排序口径，以及页面与派单落库。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.assignment_advisor import (
    case_workload_weight,
    departed_assignee_cases,
    original_writer,
    pending_assignment_cases,
    writer_load_rows,
)
from app.extensions import db
from app.models import Case, CaseMaterial, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase

INVENTION = "invention_nonrisk_normal_mechanical"
UTILITY = "utility_utility_model"
DESIGN = "utility_design"


def _seed():
    """三名撰写师：free 的在办案件都在窗口外，mid 一件实用新型，busy 两件发明。"""
    suffix = uuid4().hex[:8]
    admin = User(username=f"_sa_admin_{suffix}", role="admin")
    admin.set_password("secret")
    free = User(username=f"_sa_w_free_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_WRITER)
    mid = User(username=f"_sa_w_mid_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_WRITER)
    busy = User(username=f"_sa_w_busy_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_WRITER)
    process = User(username=f"_sa_process_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_PROCESS)
    for user in (free, mid, busy, process):
        user.set_password("secret")
    db.session.add_all([admin, free, mid, busy, process])
    db.session.flush()

    customer = Customer(kind=CustomerKind.COMPANY, name=f"_sa_customer_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_sa_project_{suffix}")
    db.session.add(project)
    db.session.flush()

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=10)
    inside = now + timedelta(days=5)
    outside = now + timedelta(days=90)

    counter = {"n": 0}
    ids: dict[str, int] = {}

    def add_case(name, *, code, due, assignee=None, phase=TaskPhase.IN_PROGRESS):
        counter["n"] += 1
        case = Case(
            project_id=project.id,
            title=f"_sa_{name}_{suffix}",
            application_no=f"SA{counter['n']:02d}{suffix}",
            case_type_code=code,
            expected_return_at=due,
            business_owner_id=assignee.id if assignee else None,
        )
        db.session.add(case)
        db.session.flush()
        db.session.add(
            Task(
                case_id=case.id,
                phase_status=phase,
                assignee_id=assignee.id if assignee else None,
            )
        )
        ids[name] = case.id
        return case

    # 窗口外的负载不该把 free 压下去。
    add_case("free_far_a", code=INVENTION, due=outside, assignee=free)
    add_case("free_far_b", code=INVENTION, due=outside, assignee=free)
    # 已完成的不算负载。
    add_case("free_done", code=INVENTION, due=inside, assignee=free, phase=TaskPhase.COMPLETED)
    add_case("mid_one", code=UTILITY, due=inside, assignee=mid)
    add_case("busy_one", code=INVENTION, due=inside, assignee=busy)
    add_case("busy_two", code=INVENTION, due=inside, assignee=busy)

    target = add_case(
        "target", code=INVENTION, due=window_end, phase=TaskPhase.PENDING_ASSIGNMENT
    )
    undated = add_case(
        "undated", code=UTILITY, due=None, phase=TaskPhase.PENDING_ASSIGNMENT
    )
    db.session.commit()

    return {
        "admin": admin.username,
        "writers": [free, mid, busy],
        "process_id": process.id,
        "process_name": process.username,
        "free_name": free.username,
        "mid_name": mid.username,
        "busy_name": busy.username,
        "target_id": target.id,
        "target_title": target.title,
        "undated_id": undated.id,
        "window_end": window_end,
        "case_ids": ids,
    }


def test_case_workload_weight_follows_primary_type_with_design_override():
    app = create_app()
    with app.app_context():
        invention = Case(project_id=None, title="i", application_no="w1", case_type_code=INVENTION)
        utility = Case(project_id=None, title="u", application_no="w2", case_type_code=UTILITY)
        design = Case(project_id=None, title="d", application_no="w3", case_type_code=DESIGN)
        unknown = Case(project_id=None, title="x", application_no="w4", case_type_code="")
        assert case_workload_weight(invention) == 3.0
        assert case_workload_weight(utility) == 1.0
        assert case_workload_weight(design) == 0.5
        assert case_workload_weight(unknown) == 1.0
        assert case_workload_weight(None) == 1.0


def test_writer_ranking_counts_only_load_due_inside_the_window():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        rows = writer_load_rows(
            seeded["writers"],
            window_end=seeded["window_end"],
            exclude_case_id=seeded["target_id"],
        )
        order = [row["user"].username for row in rows]
        assert order[0] == seeded["free_name"], "窗口内没有负载的撰写师应排最前"
        assert order[-1] == seeded["busy_name"], "窗口内两件发明的撰写师应排最后"

        by_name = {row["user"].username: row for row in rows}
        free_row = by_name[seeded["free_name"]]
        # 两件发明在窗口外、一件已完成：在办算 2 件，窗口内负载为 0。
        assert free_row["window_workload"] == 0.0
        assert free_row["window_count"] == 0
        assert free_row["open_count"] == 2
        assert free_row["busy_percent"] == 0
        # 发明按 3 折算，两件即 6。
        assert by_name[seeded["busy_name"]]["window_workload"] == 6.0
        assert by_name[seeded["busy_name"]]["busy_percent"] == 100


def test_already_overdue_target_still_counts_existing_backlog():
    """待派案件本身超期时窗口会退化到「现在」，积压的超期案件必须照样计入负载。"""
    app = create_app()
    with app.app_context():
        seeded = _seed()
        rows = writer_load_rows(
            seeded["writers"],
            window_end=datetime.now(timezone.utc) - timedelta(days=30),
            exclude_case_id=seeded["target_id"],
        )
        by_name = {row["user"].username: row for row in rows}
        # busy 手上两件发明尚未到期，不该算进这个已经收缩的窗口。
        assert by_name[seeded["busy_name"]]["window_workload"] == 0.0
        assert by_name[seeded["free_name"]]["window_workload"] == 0.0

        # 把 busy 的一件改成已超期后，它应当重新占据负载。
        stale = db.session.get(Case, seeded["case_ids"]["busy_one"])
        stale.expected_return_at = datetime.now(timezone.utc) - timedelta(days=5)
        db.session.commit()
        rows = writer_load_rows(
            seeded["writers"],
            window_end=datetime.now(timezone.utc) - timedelta(days=30),
            exclude_case_id=seeded["target_id"],
        )
        by_name = {row["user"].username: row for row in rows}
        assert by_name[seeded["busy_name"]]["window_workload"] == 3.0
        assert by_name[seeded["busy_name"]]["overdue_count"] == 1
        # free 与 mid 窗口内都是 0，平手时按在办总工作量排：
        # mid 只有 1 件实用新型（1.0），free 有 2 件窗口外的发明（6.0）。
        assert rows[0]["user"].username == seeded["mid_name"]
        assert rows[-1]["user"].username == seeded["busy_name"]


def test_pending_pool_excludes_assigned_cases():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        ids = [case.id for case in pending_assignment_cases()]
        assert seeded["target_id"] in ids
        assert seeded["undated_id"] in ids
        assert seeded["case_ids"]["busy_one"] not in ids
        # 无截止时间的案件排在有截止时间的之后。
        assert ids.index(seeded["target_id"]) < ids.index(seeded["undated_id"])


def test_smart_assignment_page_ranks_writers_and_hides_other_functions():
    app = create_app()
    with app.app_context():
        seeded = _seed()

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.get(f"/admin/smart-assignment?case_id={seeded['target_id']}")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "撰写师建议顺序" in text
    assert seeded["target_title"] in text
    assert "派单窗口" in text
    # 分配池只含在职撰写师。
    assert seeded["process_name"] not in text
    assert text.index(seeded["free_name"]) < text.index(seeded["busy_name"])


def test_case_without_due_prompts_for_expected_return_instead_of_ranking():
    app = create_app()
    with app.app_context():
        seeded = _seed()

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.get(f"/admin/smart-assignment?case_id={seeded['undated_id']}")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "还没有应返稿时间" in text
    assert "撰写师建议顺序" in text
    assert "窗口内负载" not in text


def test_assigning_from_smart_page_takes_case_out_of_the_pool():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        target_id = seeded["target_id"]
        free_id = next(u.id for u in seeded["writers"] if u.username == seeded["free_name"])

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.post(
        "/admin/smart-assignment/assign",
        data={"case_id": target_id, "assignee_id": free_id},
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        case = db.session.get(Case, target_id)
        assert case.business_owner_id == free_id
        assert case.task.assignee_id == free_id
        assert case.task.phase_status == TaskPhase.IN_PROGRESS
        assert target_id not in [c.id for c in pending_assignment_cases()]


def test_smart_page_refuses_to_assign_non_writer_staff():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        target_id = seeded["target_id"]
        process_id = seeded["process_id"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.post(
        "/admin/smart-assignment/assign",
        data={"case_id": target_id, "assignee_id": process_id},
    )
    assert r.status_code == 302
    # Toast 文案随查询参数回传，由前端消费，因此断言重定向地址。
    assert "仅撰写师可进入案件分配池" in unquote(r.headers["Location"])

    with app.app_context():
        case = db.session.get(Case, target_id)
        assert case.business_owner_id is None
        assert case.task.phase_status == TaskPhase.PENDING_ASSIGNMENT


def _seed_rejected(*, writer_active=True, phase=TaskPhase.COMPLETED):
    """一件已退稿案件，承办人可设为在职或已离职。"""
    suffix = uuid4().hex[:8]
    admin = User(username=f"_sar_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_sar_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
        is_active=writer_active,
    )
    writer.set_password("secret")
    spare = User(
        username=f"_sar_spare_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_WRITER
    )
    spare.set_password("secret")
    db.session.add_all([admin, writer, spare])
    db.session.flush()

    customer = Customer(kind=CustomerKind.COMPANY, name=f"_sar_customer_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_sar_project_{suffix}")
    db.session.add(project)
    db.session.flush()

    case = Case(
        project_id=project.id,
        title=f"_sar_case_{suffix}",
        application_no=f"SAR{suffix}",
        case_type_code=INVENTION,
        expected_return_at=datetime.now(timezone.utc) + timedelta(days=20),
        business_owner_id=writer.id,
        rejected_at=datetime.now(timezone.utc),
        reject_note="实审未通过",
        attribution=Case.ATTRIBUTION_INTERNAL,
    )
    db.session.add(case)
    db.session.flush()
    db.session.add(Task(case_id=case.id, phase_status=phase, assignee_id=writer.id))
    db.session.add(
        CaseReviewLog(
            case_id=case.id,
            operator_id=admin.id,
            recipient_id=writer.id,
            action="office_reject",
            note="实审未通过",
        )
    )
    db.session.commit()
    return {
        "admin": admin.username,
        "case_id": case.id,
        "case_title": case.title,
        "writer_id": writer.id,
        "writer_name": writer.username,
        "spare_name": spare.username,
    }


def test_original_writer_falls_back_through_available_clues():
    app = create_app()
    with app.app_context():
        seeded = _seed_rejected()
        case = db.session.get(Case, seeded["case_id"])
        user, source = original_writer(case)
        assert user.id == seeded["writer_id"]
        assert source == "退稿时的承办人"

        # 去掉退稿日志的承办人后，退到最后一次指派记录。
        CaseReviewLog.query.filter_by(case_id=case.id, action="office_reject").update(
            {CaseReviewLog.recipient_id: None}
        )
        db.session.add(
            CaseReviewLog(
                case_id=case.id,
                operator_id=case.business_owner_id,
                recipient_id=seeded["writer_id"],
                action="assigned",
            )
        )
        db.session.commit()
        assert original_writer(case)[1] == "最后一次指派记录"

        # 再去掉指派记录，退到案件业务负责人。
        CaseReviewLog.query.filter_by(case_id=case.id, action="assigned").delete()
        db.session.commit()
        assert original_writer(case)[1] == "案件业务负责人"

        # 业务负责人也空了，退到撰写稿上传人。
        case.business_owner_id = None
        db.session.add(
            CaseMaterial(
                case_id=case.id,
                uploaded_by_id=seeded["writer_id"],
                original_name="draft.docx",
                stored_name=f"stored_{uuid4().hex}.docx",
                version_tag="draft",
            )
        )
        db.session.commit()
        user, source = original_writer(case)
        assert user.id == seeded["writer_id"]
        assert source == "撰写稿上传人"

        # 全部线索都没了就明确返回无记录，不要瞎猜。
        CaseMaterial.query.filter_by(case_id=case.id).delete()
        db.session.commit()
        assert original_writer(case) == (None, "")


def test_original_writer_is_pinned_first_without_stealing_the_least_busy_badge():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        busy = next(u for u in seeded["writers"] if u.username == seeded["busy_name"])
        rows = writer_load_rows(
            seeded["writers"],
            window_end=seeded["window_end"],
            exclude_case_id=seeded["target_id"],
            pin_user_id=busy.id,
        )
        # 最忙的人是原撰写师时也要排第一，但「最闲」仍属于真正负载最低的人。
        assert rows[0]["user"].username == seeded["busy_name"]
        assert rows[0]["is_original_writer"] is True
        assert rows[0]["is_least_busy"] is False
        assert rows[0]["pinned_but_busy"] is True
        least = next(row for row in rows if row["is_least_busy"])
        assert least["user"].username == seeded["free_name"]
        assert least["is_original_writer"] is False


def test_rejected_case_page_pins_original_writer_when_still_employed():
    app = create_app()
    with app.app_context():
        seeded = _seed_rejected(phase=TaskPhase.PENDING_ASSIGNMENT)

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    text = client.get(f"/admin/smart-assignment?case_id={seeded['case_id']}").data.decode("utf-8")
    assert "退稿重做" in text
    assert "原撰写师" in text
    assert "在职 · 已置顶" in text
    assert "派回原撰写师" in text
    assert "退稿时的承办人" in text
    # 置顶的原撰写师排在备用撰写师之前。
    assert text.index(seeded["writer_name"]) < text.index(seeded["spare_name"])


def test_departed_original_writer_prompts_reassignment():
    app = create_app()
    with app.app_context():
        seeded = _seed_rejected(writer_active=False, phase=TaskPhase.PENDING_ASSIGNMENT)

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    text = client.get(f"/admin/smart-assignment?case_id={seeded['case_id']}").data.decode("utf-8")
    assert "已离职" in text
    assert "原撰写师已不在分配池，需要重新分配" in text
    assert "派回原撰写师" not in text
    # 离职的人不进分配池，不能出现在推荐表里。
    assert seeded["spare_name"] in text


def test_rejected_case_stuck_with_departed_writer_is_surfaced_not_auto_released():
    """离职退单只退回「撰写中」，已完成的退稿案件会挂在离职者名下——必须能被看见。"""
    app = create_app()
    with app.app_context():
        seeded = _seed_rejected(writer_active=False, phase=TaskPhase.COMPLETED)
        case_id = seeded["case_id"]
        # 没有自动退回：仍挂在离职者名下，也不在待分配池里。
        assert db.session.get(Case, case_id).task.assignee_id == seeded["writer_id"]
        assert case_id not in [c.id for c in pending_assignment_cases()]
        assert case_id in [c.id for c in departed_assignee_cases()]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    text = client.get("/admin/smart-assignment").data.decode("utf-8")
    assert "承办人已离职" in text
    assert seeded["case_title"] in text

    released = client.post(
        "/admin/smart-assignment/release", data={"case_id": case_id}
    )
    assert released.status_code == 302
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.task.assignee_id is None
        assert case.task.phase_status == TaskPhase.PENDING_ASSIGNMENT
        assert case_id in [c.id for c in pending_assignment_cases()]
        assert case_id not in [c.id for c in departed_assignee_cases()]


def test_completed_case_with_departed_writer_is_not_flagged_unless_rejected():
    app = create_app()
    with app.app_context():
        seeded = _seed_rejected(writer_active=False, phase=TaskPhase.COMPLETED)
        case = db.session.get(Case, seeded["case_id"])
        # 撤销退稿标记后，这就是一件正常的历史已完成案件，不该再要求重新分配。
        case.rejected_at = None
        case.attribution = Case.ATTRIBUTION_CUSTOMER
        db.session.commit()
        assert seeded["case_id"] not in [c.id for c in departed_assignee_cases()]


def test_release_refuses_cases_whose_writer_is_still_employed():
    app = create_app()
    with app.app_context():
        seeded = _seed_rejected(writer_active=True, phase=TaskPhase.COMPLETED)
        case_id = seeded["case_id"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.post("/admin/smart-assignment/release", data={"case_id": case_id})
    assert r.status_code == 302
    assert "承办人仍在职" in unquote(r.headers["Location"])
    with app.app_context():
        assert db.session.get(Case, case_id).task.assignee_id == seeded["writer_id"]


def test_marking_reject_records_who_was_writing():
    """退稿时必须把当时的承办人记进日志，否则那个人离职后就查不到了。"""
    app = create_app()
    with app.app_context():
        seeded = _seed()
        writer_id = next(u.id for u in seeded["writers"] if u.username == seeded["busy_name"])
        case_id = seeded["case_ids"]["busy_one"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.post(
        f"/admin/case-detail/{case_id}",
        data={"form_action": "mark_rejected", "case_reject_note": "实审未通过"},
    )
    assert r.status_code in (302, 303)

    with app.app_context():
        log = CaseReviewLog.query.filter_by(case_id=case_id, action="office_reject").one()
        assert log.recipient_id == writer_id
        user, source = original_writer(db.session.get(Case, case_id))
        assert user.id == writer_id
        assert source == "退稿时的承办人"


def test_smart_assignment_page_denies_non_admin():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        writer_name = seeded["free_name"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": writer_name, "password": "secret"},
        follow_redirects=True,
    )
    assert client.get("/admin/smart-assignment").status_code == 403
    assert client.post("/admin/smart-assignment/assign", data={}).status_code == 403

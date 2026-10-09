"""本地默认开 CSRF 的表单：漏 token 必须 400，带上才能提交。"""

import re
from uuid import uuid4

from app import create_app
from app.case_trace import stamp_assignee, stamp_process_owner
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "page is missing csrf_token"
    return match.group(1)


def _login_with_csrf(client, username: str, password: str = "secret") -> None:
    login_html = client.get("/auth/login").get_data(as_text=True)
    client.post(
        "/auth/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": _csrf_token(login_html),
        },
        follow_redirects=True,
    )


def test_case_create_requires_csrf_token():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = True
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_csrf_admin_{suffix}", role="admin")
        admin.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_csrf_c_{suffix}")
        db.session.add_all([admin, customer])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_csrf_p_{suffix}")
        db.session.add(project)
        db.session.commit()
        admin_name = admin.username
        project_id = project.id

    client = app.test_client()
    _login_with_csrf(client, admin_name)
    page = client.get("/admin/case-create")
    token = _csrf_token(page.get_data(as_text=True))
    payload = {
        "project_id": str(project_id),
        "title": f"_csrf_case_{suffix}",
        "case_type_code": "other",
        "phase_status": TaskPhase.PENDING_ASSIGNMENT,
    }
    blocked = client.post("/admin/case-create", data=payload, follow_redirects=False)
    assert blocked.status_code == 400
    assert b"CSRF" in blocked.data

    created = client.post(
        "/admin/case-create",
        data={**payload, "csrf_token": token},
        follow_redirects=False,
    )
    assert created.status_code in (302, 303)
    with app.app_context():
        assert Case.query.filter_by(title=payload["title"]).one() is not None


def test_billing_assign_requires_csrf_token():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = True
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_csrf_bill_admin_{suffix}", role="admin")
        admin.set_password("secret")
        process = User(
            username=f"_csrf_bill_proc_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_PROCESS,
        )
        process.set_password("secret")
        business = User(
            username=f"_csrf_bill_biz_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_BUSINESS,
        )
        business.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_csrf_bill_c_{suffix}")
        db.session.add_all([admin, process, business, customer])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_csrf_bill_p_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_csrf_bill_case_{suffix}",
            application_no=f"CB{suffix}",
        )
        stamp_process_owner(case, process)
        db.session.add(case)
        db.session.flush()
        db.session.add(Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS))
        db.session.commit()
        admin_name = admin.username
        case_id = case.id
        business_id = business.id

    client = app.test_client()
    _login_with_csrf(client, admin_name)
    page = client.get(f"/admin/billing-assignment?case_id={case_id}")
    token = _csrf_token(page.get_data(as_text=True))
    blocked = client.post(
        "/admin/billing-assignment/assign",
        data={"case_id": str(case_id), "billing_owner_id": str(business_id)},
        follow_redirects=False,
    )
    assert blocked.status_code == 400
    assert b"CSRF" in blocked.data

    assigned = client.post(
        "/admin/billing-assignment/assign",
        data={
            "case_id": str(case_id),
            "billing_owner_id": str(business_id),
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert assigned.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Case, case_id).billing_owner_id == business_id


def test_staff_refactor_post_flows_require_and_accept_csrf_token():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = True
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_csrf_flow_admin_{suffix}", role="admin")
        admin.set_password("secret")
        writer = User(
            username=f"_csrf_flow_writer_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_WRITER,
        )
        writer.set_password("secret")
        process = User(
            username=f"_csrf_flow_process_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_PROCESS,
        )
        process.set_password("secret")
        business = User(
            username=f"_csrf_flow_biz_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_BUSINESS,
        )
        business.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_csrf_flow_c_{suffix}")
        db.session.add_all([admin, writer, process, business, customer])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_csrf_flow_p_{suffix}")
        db.session.add(project)
        db.session.flush()

        assignment_case = Case(
            project_id=project.id,
            title=f"_csrf_assign_{suffix}",
            application_no=f"CSA{suffix}",
            case_type_code="other",
        )
        process_case = Case(
            project_id=project.id,
            title=f"_csrf_process_{suffix}",
            application_no=f"CSP{suffix}",
            case_type_code="other",
        )
        stamp_process_owner(process_case, process)
        db.session.add_all([assignment_case, process_case])
        db.session.flush()
        assignment_task = Task(
            case_id=assignment_case.id,
            phase_status=TaskPhase.PENDING_ASSIGNMENT,
        )
        process_task = Task(
            case_id=process_case.id,
            phase_status=TaskPhase.PENDING_SUBMIT,
        )
        stamp_assignee(process_task, writer)
        db.session.add_all([assignment_task, process_task])
        db.session.commit()
        admin_name = admin.username
        writer_id = writer.id
        process_id = process.id
        process_name = process.username
        business_name = business.username
        customer_id = customer.id
        project_id = project.id
        assignment_case_id = assignment_case.id
        process_case_id = process_case.id

    admin_http = app.test_client()
    _login_with_csrf(admin_http, admin_name)
    admin_token = _csrf_token(
        admin_http.get("/admin/case-create").get_data(as_text=True)
    )

    smart_payload = {
        "case_id": str(assignment_case_id),
        "assignee_id": str(writer_id),
    }
    assert admin_http.post(
        "/admin/smart-assignment/assign",
        data=smart_payload,
        follow_redirects=False,
    ).status_code == 400
    assert admin_http.post(
        "/admin/smart-assignment/assign",
        data={**smart_payload, "csrf_token": admin_token},
        follow_redirects=False,
    ).status_code in (302, 303)

    process_assignment_payload = {
        "case_id": str(assignment_case_id),
        "process_owner_id": str(process_id),
    }
    assert admin_http.post(
        "/admin/process-assignment/assign",
        data=process_assignment_payload,
        follow_redirects=False,
    ).status_code == 400
    assert admin_http.post(
        "/admin/process-assignment/assign",
        data={**process_assignment_payload, "csrf_token": admin_token},
        follow_redirects=False,
    ).status_code in (302, 303)

    business_http = app.test_client()
    _login_with_csrf(business_http, business_name)
    business_token = _csrf_token(
        business_http.get("/staff/business-orders").get_data(as_text=True)
    )
    order_payload = {
        "customer_id": str(customer_id),
        "project_id": str(project_id),
        "title": f"_csrf_order_{suffix}",
        "case_type_code": "other",
    }
    assert business_http.post(
        "/staff/business-orders/cases",
        data=order_payload,
        follow_redirects=False,
    ).status_code == 400
    assert business_http.post(
        "/staff/business-orders/cases",
        data={**order_payload, "csrf_token": business_token},
        follow_redirects=False,
    ).status_code in (302, 303)
    with app.app_context():
        order_case_id = Case.query.filter_by(title=order_payload["title"]).one().id

    intake_payload = {
        "review_action": "approve",
        "writer_id": str(writer_id),
        "process_owner_id": str(process_id),
    }
    assert admin_http.post(
        f"/admin/order-intake/{order_case_id}/action",
        data=intake_payload,
        follow_redirects=False,
    ).status_code == 400
    assert admin_http.post(
        f"/admin/order-intake/{order_case_id}/action",
        data={**intake_payload, "csrf_token": admin_token},
        follow_redirects=False,
    ).status_code in (302, 303)

    process_http = app.test_client()
    _login_with_csrf(process_http, process_name)
    process_token = _csrf_token(
        process_http.get(
            f"/staff/process-cases/{process_case_id}"
        ).get_data(as_text=True)
    )
    assert process_http.post(
        f"/staff/process-cases/{process_case_id}",
        data={"form_action": "process_filed"},
        follow_redirects=False,
    ).status_code == 400
    assert process_http.post(
        f"/staff/process-cases/{process_case_id}",
        data={
            "form_action": "process_filed",
            "csrf_token": process_token,
        },
        follow_redirects=False,
    ).status_code in (302, 303)

from datetime import datetime
from io import BytesIO
from uuid import uuid4

from openpyxl import load_workbook

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def test_admin_case_statistics_month_type_filter_and_export():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_stats_admin_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_stats_staff_{suffix}", role="staff")
        staff.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_stats_customer_{suffix}")
        db.session.add_all([admin, staff, customer])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_stats_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        invention = Case(
            project_id=project.id,
            title=f"_stats_invention_{suffix}",
            application_no=f"STI{suffix}",
            case_type_code="invention_risk_precheck_mechanical",
            created_at=datetime(2088, 7, 10, 3, 0),
            actual_return_at=datetime(2088, 7, 16, 3, 0),
            business_owner_id=None,
        )
        utility = Case(
            project_id=project.id,
            title=f"_stats_utility_{suffix}",
            application_no=f"STU{suffix}",
            case_type_code="utility_utility_model",
            created_at=datetime(2088, 7, 20, 3, 0),
            actual_return_at=datetime(2088, 6, 28, 3, 0),
        )
        # 创建时间夹在实用新型之后，用于验证明细导出按类型聚拢而非纯按时间
        invention_later = Case(
            project_id=project.id,
            title=f"_stats_invention_later_{suffix}",
            application_no=f"STI2{suffix}",
            case_type_code="invention_risk_precheck_mechanical",
            created_at=datetime(2088, 7, 25, 3, 0),
            actual_return_at=datetime(2088, 7, 26, 3, 0),
        )
        previous = Case(
            project_id=project.id,
            title=f"_stats_previous_{suffix}",
            application_no=f"STP{suffix}",
            case_type_code="trademark_trademark",
            created_at=datetime(2088, 6, 20, 3, 0),
        )
        db.session.add_all([invention, utility, invention_later, previous])
        db.session.flush()
        invention.business_owner_id = staff.id
        db.session.add(
            Task(
                case_id=invention.id,
                phase_status=TaskPhase.COMPLETED,
                assignee_id=staff.id,
            )
        )
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})

    page = client.get("/admin/flow-map?year=2088&month=7")
    assert page.status_code == 200
    assert "案件统计".encode() in page.data
    assert "发明".encode() in page.data
    assert "实用新型".encode() in page.data
    assert b">3<" in page.data
    assert b"created_year=2088" in page.data
    assert "近 6 个月趋势".encode() in page.data
    assert page.data.count(b"qy-monthly-trend-point") == 6
    assert "2088年7月：3 件".encode() in page.data

    filtered = client.get(
        "/admin/cases?case_type_primary=invention&created_year=2088&created_month=7"
    )
    assert filtered.status_code == 200
    assert f"_stats_invention_{suffix}".encode() in filtered.data
    assert f"_stats_utility_{suffix}".encode() not in filtered.data
    assert f"_stats_previous_{suffix}".encode() not in filtered.data

    exported = client.get("/admin/flow-map/export?year=2088&month=7")
    assert exported.status_code == 200
    assert exported.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "7月案件统计.xlsx" in exported.headers.get("Content-Disposition", "") or (
        "7%E6%9C%88%E6%A1%88%E4%BB%B6%E7%BB%9F%E8%AE%A1.xlsx" in exported.headers.get("Content-Disposition", "")
    )
    workbook = load_workbook(BytesIO(exported.data), read_only=True)
    rows = list(workbook.active.values)
    assert ("案件总数", 3, None, None, None) in rows
    assert any(row[0] == "发明" and row[1] == 2 for row in rows)
    assert "案件明细" in workbook.sheetnames
    detail_rows = list(workbook["案件明细"].values)
    assert detail_rows[0][:4] == ("案件名称", "序列号", "客户", "项目")
    invention_detail = next(row for row in detail_rows if row[0] == f"_stats_invention_{suffix}")
    assert invention_detail[1] == f"STI{suffix}"
    assert staff_name in (invention_detail[6] or "")
    assert invention_detail[9]  # 实际返稿时间
    detail_titles = [row[0] for row in detail_rows[1:]]
    assert detail_titles == [
        f"_stats_invention_{suffix}",
        f"_stats_invention_later_{suffix}",
        f"_stats_utility_{suffix}",
    ]
    assert all(row[0] != f"_stats_previous_{suffix}" for row in detail_rows[1:])

    completed_page = client.get("/admin/flow-map?year=2088&month=7&basis=completed")
    assert completed_page.status_code == 200
    assert "按实际返稿时间统计".encode() in completed_page.data
    assert b"actual_return_year=2088" in completed_page.data

    completed_filtered = client.get(
        "/admin/cases?case_type_primary=invention"
        "&actual_return_year=2088&actual_return_month=7"
    )
    assert completed_filtered.status_code == 200
    assert f"_stats_invention_{suffix}".encode() in completed_filtered.data
    assert f"_stats_utility_{suffix}".encode() not in completed_filtered.data

    completed_export = client.get(
        "/admin/flow-map/export?year=2088&month=7&basis=completed"
    )
    assert completed_export.status_code == 200
    completed_workbook = load_workbook(BytesIO(completed_export.data), read_only=True)
    completed_rows = list(completed_workbook.active.values)
    assert any(row[0] == "统计口径" and row[1] == "实际返稿时间" for row in completed_rows)
    assert any(row[0] == "案件总数" and row[1] == 2 for row in completed_rows)
    completed_detail_titles = [row[0] for row in list(completed_workbook["案件明细"].values)[1:]]
    assert completed_detail_titles == [
        f"_stats_invention_{suffix}",
        f"_stats_invention_later_{suffix}",
    ]

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": staff_name, "password": "secret"})
    forbidden = client.get("/admin/flow-map")
    assert forbidden.status_code == 403

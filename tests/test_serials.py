"""案件序列号与项目编码：按东八区年月递增，当月最多 99 个。"""

from datetime import datetime, timezone

import pytest

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project
from app.serials import next_case_serial, next_project_code, ym_prefix_cn


def _project(suffix: str) -> Project:
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_serial_cust_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_serial_proj_{suffix}")
    db.session.add(project)
    db.session.flush()
    return project


def test_ym_prefix_follows_beijing_date_not_utc():
    # 2026-06-30 16:00 UTC = 2026-07-01 00:00 北京时间 → 2607
    assert ym_prefix_cn(datetime(2026, 6, 30, 16, 0, tzinfo=timezone.utc)) == "2607"
    # 2026-06-30 15:59 UTC 仍是 6 月 30 日北京时间 → 2606
    assert ym_prefix_cn(datetime(2026, 6, 30, 15, 59, tzinfo=timezone.utc)) == "2606"


def test_case_serial_increments_within_month_and_resets_next_month():
    app = create_app()
    jan = datetime(1999, 1, 15, 4, 0, tzinfo=timezone.utc)
    feb = datetime(1999, 2, 1, 4, 0, tzinfo=timezone.utc)
    with app.app_context():
        project = _project("jan")
        db.session.add(
            Case(
                project_id=project.id,
                title="_serial_first",
                application_no="990101",
            )
        )
        db.session.add(
            Case(
                project_id=project.id,
                title="_serial_noise",
                application_no="CN19990101.8",
            )
        )
        db.session.commit()
        assert next_case_serial(jan) == "990102"
        assert next_case_serial(feb) == "990201"


def test_case_serial_uses_highest_existing_sequence():
    app = create_app()
    mar = datetime(1998, 3, 10, tzinfo=timezone.utc)
    with app.app_context():
        project = _project("gap")
        db.session.add_all(
            [
                Case(project_id=project.id, title="_s1", application_no="980301"),
                Case(project_id=project.id, title="_s5", application_no="980305"),
            ]
        )
        db.session.commit()
        assert next_case_serial(mar) == "980306"


def test_next_case_serial_raises_when_month_exhausted():
    app = create_app()
    apr = datetime(1997, 4, 1, tzinfo=timezone.utc)
    with app.app_context():
        project = _project("full")
        db.session.add(
            Case(
                project_id=project.id,
                title="_serial_full",
                application_no="970499",
            )
        )
        db.session.commit()
        with pytest.raises(ValueError, match="当月案件序列号已用尽"):
            next_case_serial(apr)


def test_project_code_increments_and_exhausts_like_case_serial():
    app = create_app()
    may = datetime(1996, 5, 2, tzinfo=timezone.utc)
    jun = datetime(1996, 6, 2, tzinfo=timezone.utc)
    with app.app_context():
        customer = Customer(kind=CustomerKind.COMPANY, name="_pcode_cust")
        db.session.add(customer)
        db.session.flush()
        first = Project(customer_id=customer.id, name="_pcode_a", code="960501")
        skip = Project(customer_id=customer.id, name="_pcode_skip", code="P-9605")
        db.session.add_all([first, skip])
        db.session.commit()
        assert next_project_code(may) == "960502"
        assert next_project_code(jun) == "960601"

        full = Project(customer_id=customer.id, name="_pcode_full", code="960599")
        db.session.add(full)
        db.session.commit()
        with pytest.raises(ValueError, match="当月项目编码序号已用尽"):
            next_project_code(may)

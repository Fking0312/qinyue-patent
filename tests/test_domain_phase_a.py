"""阶段 A：客户 / 项目 / 案件 / 任务与超期状态规则。"""
from datetime import datetime, timezone
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task
from app.workflow import TaskPhase, apply_task_overdue_status, effective_task_due_at


def test_effective_due_inherits_project():
    app = create_app()
    sfx = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"C1-{sfx}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"P1-{sfx}", due_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case", application_no=f"CN209912345678.{sfx}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        db.session.commit()

        assert effective_task_due_at(task) == proj.due_at


def test_pending_review_stays_when_overdue():
    app = create_app()
    sfx = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"C2b-{sfx}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"P2b-{sfx}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case2b", application_no=f"CN209912345679b.{sfx}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW)
        db.session.add(task)
        db.session.commit()

        now = datetime(2020, 2, 1, tzinfo=timezone.utc)
        assert apply_task_overdue_status(task, now=now) is False
        assert task.phase_status == TaskPhase.PENDING_REVIEW


def test_overdue_flips_status():
    app = create_app()
    sfx = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"C2-{sfx}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"P2-{sfx}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case2", application_no=f"CN209912345679.{sfx}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        db.session.commit()

        now = datetime(2020, 2, 1, tzinfo=timezone.utc)
        assert apply_task_overdue_status(task, now=now) is True
        assert task.phase_status == TaskPhase.OVERDUE_IN_PROGRESS

        assert apply_task_overdue_status(task, now=now) is False


def test_not_overdue_clears_flag():
    app = create_app()
    sfx = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"C3-{sfx}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"P3-{sfx}", due_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case3", application_no=f"CN209912345670.{sfx}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.OVERDUE_IN_PROGRESS)
        db.session.add(task)
        db.session.commit()

        now = datetime(2029, 1, 1, tzinfo=timezone.utc)
        assert apply_task_overdue_status(task, now=now) is True
        assert task.phase_status == TaskPhase.IN_PROGRESS


def test_pending_assignment_does_not_flip_overdue():
    app = create_app()
    sfx = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"C-pa-{sfx}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"P-pa-{sfx}", due_at=datetime(2010, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case-pa", application_no=f"CN209912340000.{sfx}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_ASSIGNMENT)
        db.session.add(task)
        db.session.commit()

        assert apply_task_overdue_status(task, now=datetime(2020, 1, 1, tzinfo=timezone.utc)) is False
        assert task.phase_status == TaskPhase.PENDING_ASSIGNMENT


def test_terminal_task_not_overdue():
    app = create_app()
    sfx = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"C4-{sfx}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"P4-{sfx}", due_at=datetime(2010, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case4", application_no=f"CN209912345671.{sfx}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.COMPLETED)
        db.session.add(task)
        db.session.commit()

        assert apply_task_overdue_status(task, now=datetime(2020, 1, 1, tzinfo=timezone.utc)) is False

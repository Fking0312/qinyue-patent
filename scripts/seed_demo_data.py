"""生成本地演示数据：客户、项目、案件、员工及不同任务状态。

禁止在生产环境运行。新建演示账号口令为 123456；已存在账号不会被改密。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


DEMO_MARKER = "演示数据："


def refuse_if_production(env: str | None = None) -> None:
    """生产环境一律拒绝；无覆盖开关。"""
    cfg_env = str(env or os.getenv("FLASK_ENV") or "development").lower()
    if cfg_env == "production":
        print(
            "错误：生产环境禁止执行 seed_demo_data.py。"
            "该脚本会写入演示数据；新建账号口令为 123456。",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _ensure_user(
    username: str,
    role: str,
    *,
    customer_id: int | None = None,
    staff_kind: str | None = None,
    staff_function: str | None = None,
) -> User:
    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(
            username=username,
            role=role,
            customer_id=customer_id,
            staff_kind=staff_kind,
            staff_function=staff_function,
        )
        user.set_password("123456")
        db.session.add(user)
        db.session.flush()
        return user

    user.role = role
    user.customer_id = customer_id
    user.staff_kind = staff_kind
    user.staff_function = staff_function
    # 已存在账号不改密，避免把 writer / process / business 等口令重置为 123456。
    return user


STAFF_FUNCTION_TEST_ACCOUNTS = (
    ("writer", User.STAFF_FUNCTION_WRITER, "撰写师"),
    ("process", User.STAFF_FUNCTION_PROCESS, "流程人员"),
    ("business", User.STAFF_FUNCTION_BUSINESS, "业务人员"),
)


def ensure_staff_function_test_accounts() -> list[tuple[str, str]]:
    """确保三个职能各有一个可登录测试账号；仅新建时口令为 123456。"""
    created: list[tuple[str, str]] = []
    for username, function, label in STAFF_FUNCTION_TEST_ACCOUNTS:
        _ensure_user(
            username,
            "staff",
            staff_kind=User.STAFF_KIND_FORMAL,
            staff_function=function,
        )
        created.append((username, label))
    return created


def main() -> None:
    app = create_app()
    now = datetime.now(timezone.utc)

    with app.app_context():
        refuse_if_production(str(app.config.get("ENV")))
        function_accounts = ensure_staff_function_test_accounts()
        db.session.commit()
        print("职能测试账号已就绪（仅新建账号密码为 123456，已存在账号未改密）：")
        for username, label in function_accounts:
            print(f"  {username}（{label}）")

        if Customer.query.filter(Customer.name.startswith(DEMO_MARKER, autoescape=True)).first():
            print("演示数据已存在，未重复生成。")
            return

        admin = _ensure_user("admin", "admin")
        staff = _ensure_user(
            "staff",
            "staff",
            staff_kind=User.STAFF_KIND_FORMAL,
            staff_function=User.STAFF_FUNCTION_WRITER,
        )
        writer_li = _ensure_user(
            "writer_li",
            "staff",
            staff_kind=User.STAFF_KIND_FORMAL,
            staff_function=User.STAFF_FUNCTION_WRITER,
        )
        partner_zhang = _ensure_user(
            "partner_zhang",
            "staff",
            staff_kind=User.STAFF_KIND_OUTSOURCE,
            staff_function=User.STAFF_FUNCTION_WRITER,
        )

        customer_specs = (
            (CustomerKind.COMPANY, "演示数据：上海智造科技有限公司", "王经理", "13800001001"),
            (CustomerKind.COMPANY, "演示数据：杭州云帆软件有限公司", "陈女士", "13800001002"),
            (CustomerKind.COMPANY, "演示数据：广州绿源新材料有限公司", "刘总", "13800001003"),
            (CustomerKind.INDIVIDUAL, "演示数据：赵先生", "赵先生", "13800001004"),
        )
        customers: list[Customer] = []
        for kind, name, contact_name, contact_phone in customer_specs:
            customer = Customer(
                kind=kind,
                name=name,
                contact_name=contact_name,
                contact_phone=contact_phone,
                note="本地功能演示数据，可通过清理脚本删除。",
                fee_standard="按案件类型与复杂度报价",
                created_by_id=admin.id,
            )
            db.session.add(customer)
            customers.append(customer)
        db.session.flush()

        for index, customer in enumerate(customers, start=1):
            _ensure_user(
                f"client_demo_{index}",
                "client",
                customer_id=customer.id,
            )

        project_specs = (
            (0, "演示数据：智能制造专利布局", 23, "围绕工业机器人与视觉检测系统进行专利布局。"),
            (0, "演示数据：自动化产线升级", 9, "自动化产线设备及控制方法的专利申请。"),
            (1, "演示数据：云服务产品保护", 16, "软件平台核心功能、界面与品牌保护。"),
            (2, "演示数据：环保材料研发", 5, "新型环保复合材料相关专利组合。"),
            (2, "演示数据：新材料商标注册", 31, "品牌及产品线商标注册申请。"),
            (3, "演示数据：个人创新成果", 12, "个人发明与外观设计申请。"),
        )
        projects: list[Project] = []
        for index, (customer_index, name, due_days, description) in enumerate(project_specs, start=1):
            created_at = now - timedelta(days=30 - index * 3)
            project = Project(
                customer_id=customers[customer_index].id,
                name=name,
                code=f"{created_at:%y%m}{index:02d}",
                description=description,
                initiated_at=created_at,
                due_at=now + timedelta(days=due_days),
                created_by_id=admin.id,
                created_at=created_at,
            )
            db.session.add(project)
            projects.append(project)
        db.session.flush()

        case_specs = (
            (0, "工业机器人关节模组及控制方法", "invention_risk_precheck_mechanical", TaskPhase.IN_PROGRESS, staff, 15),
            (0, "视觉检测装置及缺陷识别方法", "invention_risk_priority_software", TaskPhase.PENDING_REVIEW, writer_li, 8),
            (1, "柔性装配线定位机构", "utility_utility_model", TaskPhase.PENDING_ASSIGNMENT, None, 20),
            (1, "装配线控制终端外观设计", "utility_design", TaskPhase.PENDING_SUBMIT, partner_zhang, 4),
            (2, "云端数据分析系统", "invention_nonrisk_unknown_software", TaskPhase.IN_PROGRESS, writer_li, 12),
            (2, "云帆品牌文字商标", "trademark_trademark", TaskPhase.COMPLETED, staff, -6),
            (2, "云帆业务管理平台软件著作权", "trademark_software_copyright", TaskPhase.PENDING_REVIEW, partner_zhang, 7),
            (3, "可降解复合材料及其制备方法", "invention_risk_normal_chemical", TaskPhase.OFFICE_ACTION, writer_li, 3),
            (3, "环保材料成型设备", "invention_nonrisk_unknown_mechanical", TaskPhase.ON_HOLD, staff, 18),
            (4, "绿源新材料图形商标", "trademark_trademark", TaskPhase.PENDING_SUBMIT, partner_zhang, 9),
            (5, "便携式工具收纳盒", "utility_design", TaskPhase.OVERDUE_IN_PROGRESS, staff, -3),
            (5, "芯片版图布局设计", "ic_layout", TaskPhase.IN_PROGRESS, writer_li, 14),
        )

        month_sequences: dict[str, int] = {}
        for index, (project_index, title, type_code, phase, assignee, due_days) in enumerate(case_specs, start=1):
            created_at = now - timedelta(days=35 - index * 3)
            prefix = created_at.strftime("%y%m")
            month_sequences[prefix] = month_sequences.get(prefix, 0) + 1
            case = Case(
                project_id=projects[project_index].id,
                title=title,
                application_no=f"{prefix}{month_sequences[prefix]:02d}",
                case_type_code=type_code,
                business_owner_id=admin.id,
                order_at=created_at - timedelta(days=2),
                expected_return_at=now + timedelta(days=due_days),
                case_note="本地演示案件，用于查看案件类型标签、备注和任务状态。",
                created_at=created_at,
            )
            db.session.add(case)
            db.session.flush()
            db.session.add(
                Task(
                    case_id=case.id,
                    assignee_id=assignee.id if assignee else None,
                    phase_status=phase,
                    due_at=now + timedelta(days=due_days),
                    created_at=created_at,
                )
            )

        db.session.commit()
        print(
            f"已生成演示数据：客户 {len(customers)} 个，项目 {len(projects)} 个，案件 {len(case_specs)} 件。"
        )
        print("本次新建的演示员工/客户账号密码为 123456；已存在账号未改密。")


if __name__ == "__main__":
    main()

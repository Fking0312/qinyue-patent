"""补充本地演示案件，覆盖全部案件类型及多个月份统计。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app import create_app
from app.case_types import CASE_TYPE_LEAVES
from app.extensions import db
from app.models import Case, Project, Task, User
from app.workflow import TaskPhase


TITLE_PREFIX = "扩展示例："
SERIAL_RE = re.compile(r"^(\d{4})(\d{2})$")

TITLES = (
    "工业机器人自适应抓取控制方法",
    "复杂曲面零件视觉定位系统",
    "耐高温复合涂层及其制备工艺",
    "智能仓储设备故障诊断方法",
    "多源数据融合分析平台",
    "低挥发环保胶黏剂组合物",
    "精密传动机构间隙补偿装置",
    "面向园区的能耗优化系统",
    "高强度轻量化合金处理工艺",
    "自动换刀机械臂及控制方法",
    "企业知识库智能检索系统",
    "抗腐蚀功能材料制备方法",
    "模块化工装快速定位机构",
    "设备远程运维数据处理平台",
    "多孔吸附材料及再生方法",
    "便携式检测仪器外观设计",
    "清岳智造图形商标",
    "集成电路版图结构设计",
)

PHASES = (
    TaskPhase.PENDING_ASSIGNMENT,
    TaskPhase.DRAFT,
    TaskPhase.IN_PROGRESS,
    TaskPhase.PENDING_REVIEW,
    TaskPhase.PENDING_SUBMIT,
    TaskPhase.AUTHORIZED_PENDING_PAYMENT,
    TaskPhase.OFFICE_ACTION,
    TaskPhase.ON_HOLD,
    TaskPhase.COMPLETED,
)


def _next_serial(created_at: datetime) -> str:
    prefix = created_at.strftime("%y%m")
    max_sequence = 0
    for (value,) in db.session.query(Case.application_no).all():
        match = SERIAL_RE.fullmatch((value or "").strip())
        if match and match.group(1) == prefix:
            max_sequence = max(max_sequence, int(match.group(2)))
    if max_sequence >= 99:
        raise RuntimeError(f"{prefix} 月份案件序列号已达到 99。")
    return f"{prefix}{max_sequence + 1:02d}"


def main() -> None:
    app = create_app()
    with app.app_context():
        projects = (
            Project.query.filter(Project.name.startswith("演示数据：", autoescape=True))
            .order_by(Project.id.asc())
            .all()
        )
        if not projects:
            raise RuntimeError("请先运行 scripts/seed_demo_data.py 生成基础演示项目。")

        staff_users = (
            User.query.filter(User.role == "staff", User.is_active.is_(True))
            .order_by(User.id.asc())
            .all()
        )
        if not staff_users:
            raise RuntimeError("没有可分配的演示员工。")

        now = datetime.now(timezone.utc)
        current_month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        previous_month_end = current_month_start - timedelta(days=1)
        previous_month_start = datetime(
            previous_month_end.year,
            previous_month_end.month,
            1,
            tzinfo=timezone.utc,
        )

        specs: list[tuple[str, str, datetime]] = []
        for index, leaf in enumerate(CASE_TYPE_LEAVES):
            max_day_offset = max(0, (now - current_month_start).days)
            created_at = current_month_start + timedelta(
                days=index % (max_day_offset + 1),
                hours=2 + index % 12,
            )
            specs.append((TITLES[index], leaf.code, created_at))

        # 上个月额外放入 6 件，方便观察本月/上月对比。
        for index, leaf in enumerate(CASE_TYPE_LEAVES[:6]):
            created_at = previous_month_start + timedelta(days=3 + index * 3, hours=4)
            specs.append((f"历史对比{index + 1}号案件", leaf.code, created_at))

        created_count = 0
        for index, (title, type_code, created_at) in enumerate(specs):
            full_title = f"{TITLE_PREFIX}{title}"
            if Case.query.filter_by(title=full_title).first() is not None:
                continue
            assignee = staff_users[index % len(staff_users)]
            due_at = created_at + timedelta(days=14 + index % 10)
            case_item = Case(
                project_id=projects[index % len(projects)].id,
                title=full_title,
                application_no=_next_serial(created_at),
                case_type_code=type_code,
                business_owner_id=assignee.id,
                order_at=created_at - timedelta(days=1),
                expected_return_at=due_at,
                case_note=f"案件统计演示数据；类型：{next(leaf.display for leaf in CASE_TYPE_LEAVES if leaf.code == type_code)}。",
                created_at=created_at,
            )
            db.session.add(case_item)
            db.session.flush()
            db.session.add(
                Task(
                    case_id=case_item.id,
                    assignee_id=assignee.id,
                    phase_status=PHASES[index % len(PHASES)],
                    due_at=due_at,
                    created_at=created_at,
                )
            )
            created_count += 1

        db.session.commit()
        print(f"已新增 {created_count} 件扩展演示案件；重复数据已自动跳过。")
        print(f"当前月新增目标 18 件，上个月新增目标 6 件。")


if __name__ == "__main__":
    main()

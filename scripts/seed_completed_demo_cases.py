"""补充已完成的本地演示案件，并设置任务为已完成状态。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models import Case, Project, Task, User
from app.workflow import TaskPhase


TITLE_PREFIX = "已完成示例："
SERIAL_RE = re.compile(r"^(\d{4})(\d{2})$")

COMPLETED_CASES = (
    ("机器人末端执行器快速更换装置", "utility_utility_model"),
    ("生产线异常检测与预警方法", "invention_nonrisk_unknown_software"),
    ("耐磨机械零部件表面处理工艺", "invention_risk_precheck_chemical"),
    ("智能设备控制面板外观设计", "utility_design"),
    ("清岳云管家文字商标", "trademark_trademark"),
    ("设备巡检管理平台软件著作权", "trademark_software_copyright"),
    ("多轴联动加工误差补偿方法", "invention_risk_priority_mechanical"),
    ("低能耗数据采集芯片版图设计", "ic_layout"),
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
        staff_users = (
            User.query.filter(User.role == "staff", User.is_active.is_(True))
            .order_by(User.id.asc())
            .all()
        )
        if not projects or not staff_users:
            raise RuntimeError("请先运行 scripts/seed_demo_data.py 生成基础演示数据。")

        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        created_count = 0

        for index, (title, type_code) in enumerate(COMPLETED_CASES):
            full_title = f"{TITLE_PREFIX}{title}"
            existing = Case.query.filter_by(title=full_title).first()
            if existing is not None:
                if existing.actual_return_at is None:
                    existing.actual_return_at = existing.created_at + timedelta(days=4)
                if existing.task:
                    existing.task.phase_status = TaskPhase.COMPLETED
                continue

            created_at = month_start + timedelta(days=1 + index * 2, hours=2 + index)
            # 防止演示数据出现未来时间。
            if created_at > now - timedelta(hours=6):
                created_at = now - timedelta(days=2 + index, hours=2)
            expected_return_at = created_at + timedelta(days=7 + index % 3)
            actual_return_at = created_at + timedelta(days=3 + index % 4, hours=2)
            if actual_return_at > now:
                actual_return_at = now - timedelta(hours=2 + index)

            assignee = staff_users[index % len(staff_users)]
            case_item = Case(
                project_id=projects[index % len(projects)].id,
                title=full_title,
                application_no=_next_serial(created_at),
                case_type_code=type_code,
                business_owner_id=assignee.id,
                order_at=created_at - timedelta(days=1),
                expected_return_at=expected_return_at,
                actual_return_at=actual_return_at,
                formal_status="已完成",
                case_note="本地已完成案件演示数据，包含实际返稿时间。",
                created_at=created_at,
            )
            db.session.add(case_item)
            db.session.flush()
            db.session.add(
                Task(
                    case_id=case_item.id,
                    assignee_id=assignee.id,
                    phase_status=TaskPhase.COMPLETED,
                    due_at=expected_return_at,
                    created_at=created_at,
                    updated_at=actual_return_at,
                )
            )
            created_count += 1

        db.session.commit()
        print(f"已新增 {created_count} 件已完成演示案件，均已设置实际返稿时间。")


if __name__ == "__main__":
    main()

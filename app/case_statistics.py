"""案件月度统计：管理端与员工端共用。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import joinedload

from app.case_types import (
    PRIMARY_LABELS as CASE_TYPE_PRIMARY_LABELS,
    PRIMARY_OPTIONS as CASE_TYPE_PRIMARY_OPTIONS,
    get_case_type,
    legacy_case_type_code,
    legacy_values_for_primary,
)
from app.extensions import db
from app.models import Case, Project, Task
from app.workflow import task_phase_label

_CN_TZ = timezone(timedelta(hours=8))


def _export_dt_text(value: datetime | None) -> str:
    """导出用北京时间字符串；空值为 —。"""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def case_statistics_month(raw_year: str, raw_month: str) -> tuple[int, int]:
    """解析统计年月；非法值回落到东八区当前月份。"""
    now_cn = datetime.now(timezone.utc).astimezone(_CN_TZ)
    year = int(raw_year) if raw_year.isdigit() else now_cn.year
    month = int(raw_month) if raw_month.isdigit() else now_cn.month
    if year < 2000 or year > 2100:
        year = now_cn.year
    if month < 1 or month > 12:
        month = now_cn.month
    return year, month


def case_statistics_month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """返回东八区自然月对应的 UTC 无时区边界，兼容 SQLite 现有 DATETIME 存储。"""
    start_cn = datetime(year, month, 1, tzinfo=_CN_TZ)
    if month == 12:
        end_cn = datetime(year + 1, 1, 1, tzinfo=_CN_TZ)
    else:
        end_cn = datetime(year, month + 1, 1, tzinfo=_CN_TZ)
    return (
        start_cn.astimezone(timezone.utc).replace(tzinfo=None),
        end_cn.astimezone(timezone.utc).replace(tzinfo=None),
    )


def case_statistics_basis(raw_basis: str) -> str:
    """统计口径：created=创建时间；completed=实际返稿时间。"""
    return "completed" if raw_basis == "completed" else "created"


def _case_primary_from_legacy(value: str | None) -> str | None:
    """旧 project_type 仅在能归入明确一级类型时参与统计。"""
    normalized = (value or "").strip()
    if not normalized:
        return None
    mapped_code = legacy_case_type_code(normalized)
    mapped_leaf = get_case_type(mapped_code)
    if mapped_leaf:
        return mapped_leaf.primary
    for primary, _label in CASE_TYPE_PRIMARY_OPTIONS:
        if normalized in legacy_values_for_primary(primary):
            return primary
    return None


def _cases_in_statistics_month(
    year: int,
    month: int,
    basis: str,
    *,
    assignee_id: int | None = None,
    with_details: bool = False,
) -> list[Case]:
    start_at, end_at = case_statistics_month_bounds(year, month)
    date_column = Case.actual_return_at if basis == "completed" else Case.created_at
    query = Case.query.filter(date_column >= start_at, date_column < end_at)
    if assignee_id is not None:
        query = query.join(Task, Task.case_id == Case.id).filter(Task.assignee_id == assignee_id)
    if with_details:
        query = query.options(
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.business_owner_user),
            joinedload(Case.task).joinedload(Task.assignee),
        )
    return query.order_by(date_column.asc(), Case.id.asc()).all()


def _case_statistics_type_sort_key(case_item: Case, *, basis: str) -> tuple:
    """明细导出排序：一级类型 → 完整分类路径 → 统计时间 → id。"""
    primary_order = {value: index for index, (value, _) in enumerate(CASE_TYPE_PRIMARY_OPTIONS)}
    leaf = get_case_type(case_item.case_type_code)
    if leaf:
        primary = leaf.primary
        detail_label = leaf.display
    else:
        primary = _case_primary_from_legacy(case_item.project_type)
        detail_label = (case_item.project_type or "").strip() or (case_item.case_type_display or "")
    if primary:
        primary_rank = primary_order.get(primary, len(primary_order))
    else:
        primary_rank = len(primary_order) + 1
    path_parts = tuple(segment.strip() for segment in (detail_label or "").split("/"))
    date_value = case_item.actual_return_at if basis == "completed" else case_item.created_at
    return (primary_rank, path_parts, date_value or datetime.min, case_item.id)


def case_statistics_case_details(
    year: int,
    month: int,
    basis: str = "created",
    *,
    assignee_id: int | None = None,
) -> list[dict]:
    """返回统计月份内案件明细行，供 Excel 导出（同类型优先相邻）。"""
    basis = case_statistics_basis(basis)
    cases = _cases_in_statistics_month(
        year,
        month,
        basis,
        assignee_id=assignee_id,
        with_details=True,
    )
    cases = sorted(cases, key=lambda item: _case_statistics_type_sort_key(item, basis=basis))
    rows: list[dict] = []
    for case_item in cases:
        task = case_item.task
        staff = None
        if task is not None and task.assignee is not None:
            staff = task.assignee
        elif case_item.business_owner_user is not None:
            staff = case_item.business_owner_user
        project = case_item.project
        customer = project.customer if project is not None else None
        rows.append(
            {
                "title": case_item.title,
                "application_no": case_item.application_no,
                "customer_name": customer.name if customer is not None else "—",
                "project_name": project.name if project is not None else "—",
                "case_type": case_item.case_type_display or "—",
                "status": task_phase_label(task.phase_status) if task is not None else "—",
                "staff_label": staff.display_label if staff is not None else "—",
                "created_at": _export_dt_text(case_item.created_at),
                "expected_return_at": _export_dt_text(case_item.expected_return_at),
                "actual_return_at": _export_dt_text(case_item.actual_return_at),
            }
        )
    return rows


def case_statistics_data(
    year: int,
    month: int,
    basis: str = "created",
    *,
    assignee_id: int | None = None,
) -> dict:
    """按指定时间口径汇总案件总数、一级类型及完整分类路径。"""
    basis = case_statistics_basis(basis)
    cases_in_month = _cases_in_statistics_month(year, month, basis, assignee_id=assignee_id)

    primary_counts = {value: 0 for value, _label in CASE_TYPE_PRIMARY_OPTIONS}
    detail_counts: dict[tuple[str, str], int] = {}
    unclassified = 0
    for case_item in cases_in_month:
        leaf = get_case_type(case_item.case_type_code)
        if leaf:
            primary = leaf.primary
            detail_label = leaf.display
        else:
            primary = _case_primary_from_legacy(case_item.project_type)
            detail_label = (case_item.project_type or "").strip()
        if not primary:
            unclassified += 1
            continue
        primary_counts[primary] += 1
        detail_key = (primary, detail_label or CASE_TYPE_PRIMARY_LABELS[primary])
        detail_counts[detail_key] = detail_counts.get(detail_key, 0) + 1

    total = len(cases_in_month)
    rows = []
    for primary, label in CASE_TYPE_PRIMARY_OPTIONS:
        count = primary_counts[primary]
        details = [
            {"label": detail_label, "count": detail_count}
            for (detail_primary, detail_label), detail_count in detail_counts.items()
            if detail_primary == primary
        ]
        # 优先按分类路径逐级排列，让风险属性、通道和技术领域相近的案件相邻；
        # 数量仅用于展示，不再把同类路径打散。
        details.sort(
            key=lambda item: tuple(
                segment.strip() for segment in item["label"].split("/")
            )
        )
        rows.append(
            {
                "code": primary,
                "label": label,
                "count": count,
                "percent": round(count * 100 / total, 1) if total else 0,
                "details": details,
            }
        )
    return {
        "year": year,
        "month": month,
        "basis": basis,
        "basis_label": "实际返稿时间" if basis == "completed" else "创建时间",
        "total": total,
        "rows": rows,
        "unclassified": unclassified,
    }


def case_statistics_trend_data(
    year: int,
    month: int,
    basis: str = "created",
    *,
    assignee_id: int | None = None,
    months: int = 6,
) -> dict:
    """返回截至所选月份的月度案件趋势，用于轻量柱状图。"""
    points = []
    selected_index = year * 12 + month - 1
    for offset in range(-(months - 1), 1):
        month_index = selected_index + offset
        point_year, month_zero_based = divmod(month_index, 12)
        point_month = month_zero_based + 1
        point_statistics = case_statistics_data(
            point_year,
            point_month,
            basis,
            assignee_id=assignee_id,
        )
        points.append(
            {
                "year": point_year,
                "month": point_month,
                "label": f"{point_month}月",
                "full_label": f"{point_year}年{point_month}月",
                "count": point_statistics["total"],
                "is_selected": offset == 0,
            }
        )
    maximum = max((point["count"] for point in points), default=0)
    for point in points:
        point["bar_percent"] = (
            max(5, round(point["count"] * 100 / maximum))
            if maximum and point["count"]
            else 0
        )
    total = sum(point["count"] for point in points)
    return {
        "points": points,
        "maximum": maximum,
        "total": total,
        "average": round(total / len(points), 1) if points else 0,
        "change": points[-1]["count"] - points[-2]["count"] if len(points) > 1 else 0,
    }


def case_statistics_available_years(
    basis: str,
    *,
    assignee_id: int | None = None,
    selected_year: int | None = None,
) -> list[int]:
    """返回有统计数据的年份列表（降序）。"""
    basis = case_statistics_basis(basis)
    date_column = Case.actual_return_at if basis == "completed" else Case.created_at
    query = db.session.query(date_column).filter(date_column.isnot(None))
    if assignee_id is not None:
        query = query.join(Task, Task.case_id == Case.id).filter(Task.assignee_id == assignee_id)
    available_year_values: set[int] = set()
    for (value,) in query.all():
        if value is None:
            continue
        value_utc = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        available_year_values.add(value_utc.astimezone(_CN_TZ).year)
    available_years = sorted(available_year_values, reverse=True)
    if selected_year is not None and selected_year not in available_years:
        available_years.insert(0, selected_year)
    return available_years


def case_statistics_workbook_bytes(
    statistics: dict,
    *,
    sheet_title_prefix: str = "案件统计",
    assignee_id: int | None = None,
) -> bytes:
    """将统计汇总与案件明细导出为 Excel 二进制内容。"""
    from io import BytesIO

    from openpyxl import Workbook

    year = statistics["year"]
    month = statistics["month"]
    wb = Workbook()
    ws = wb.active
    ws.title = f"{year}-{month:02d}{sheet_title_prefix}"
    ws.append(["统计月份", f"{year}-{month:02d}"])
    ws.append(["统计口径", statistics["basis_label"]])
    ws.append(["案件总数", statistics["total"]])
    ws.append([])
    ws.append(["一级案件类型", "数量", "占比", "完整分类路径", "路径数量"])
    for row in statistics["rows"]:
        if row["details"]:
            for index, detail in enumerate(row["details"]):
                ws.append(
                    [
                        row["label"] if index == 0 else "",
                        row["count"] if index == 0 else "",
                        f'{row["percent"]}%' if index == 0 else "",
                        detail["label"],
                        detail["count"],
                    ]
                )
        else:
            ws.append([row["label"], 0, "0%", "", ""])
    ws.append(["未分类", statistics["unclassified"], "", "未设置有效案件类型", statistics["unclassified"]])
    for column, width in {"A": 18, "B": 12, "C": 12, "D": 48, "E": 12}.items():
        ws.column_dimensions[column].width = width

    detail_ws = wb.create_sheet("案件明细")
    detail_headers = [
        "案件名称",
        "序列号",
        "客户",
        "项目",
        "案件类型",
        "状态",
        "负责员工",
        "创建时间",
        "应返稿时间",
        "实际返稿时间",
    ]
    detail_ws.append(detail_headers)
    for detail in case_statistics_case_details(
        year,
        month,
        statistics["basis"],
        assignee_id=assignee_id,
    ):
        detail_ws.append(
            [
                detail["title"],
                detail["application_no"],
                detail["customer_name"],
                detail["project_name"],
                detail["case_type"],
                detail["status"],
                detail["staff_label"],
                detail["created_at"],
                detail["expected_return_at"],
                detail["actual_return_at"],
            ]
        )
    for column, width in {
        "A": 28,
        "B": 14,
        "C": 18,
        "D": 18,
        "E": 36,
        "F": 14,
        "G": 22,
        "H": 20,
        "I": 20,
        "J": 20,
    }.items():
        detail_ws.column_dimensions[column].width = width

    output = BytesIO()
    wb.save(output)
    return output.getvalue()

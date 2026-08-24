"""管理端视图：客户/项目/案件全链路管理与各类只读统计页占位。

本文件较长，按业务区域顺序排列：
- 工具函数（_ensure_admin、查询/导出/校验等私有 helper）
- 客户：列表 / 创建 / 编辑 / 详情 / 导出 / 批量删除
- 项目：列表 / JSON 创建 / 批量删除 / 勾选导出 Excel / 编辑 / 详情
- 案件：列表 / 创建 / 详情（含审核与材料） / 编辑 / 下载留痕导出
- 批量操作：入口与规则说明（跳转客户 / 项目 / 案件列表）
- 其余分析与管理工具页（多为后续扩展的占位）。
"""

import csv
import re
import shutil
from itertools import groupby
from io import BytesIO, StringIO
from math import ceil
from datetime import date, datetime, time, timedelta, timezone
from flask import Response, abort, current_app, jsonify, redirect, request, send_file, send_from_directory, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from flask_login import current_user, login_required
from app.case_material_upload import case_material_dir, fetch_case_materials_grouped, save_case_material_upload
from app.case_statistics import (
    case_statistics_available_years,
    case_statistics_basis,
    case_statistics_data,
    case_statistics_month,
    case_statistics_month_bounds,
    case_statistics_trend_data,
    case_statistics_workbook_bytes,
)
from app.case_types import (
    CASE_TYPE_UI_CONFIG,
    PRIMARY_LABELS as CASE_TYPE_PRIMARY_LABELS,
    PRIMARY_OPTIONS as CASE_TYPE_PRIMARY_OPTIONS,
    codes_for_primary,
    legacy_case_type_code,
    legacy_values_for_primary,
    normalize_case_type_code,
    validate_case_type_code,
)
from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models import Case, CaseMaterial, CaseMaterialDownloadLog, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.dashboard_stats import dashboard_page_kwargs
from app.overdue_reminder import reminder_template_kwargs
from app.spa_helpers import redirect_with_qy_toast, render_spa_or_full
from app.task_board import task_board_data_for_user
from app.workflow import (
    TaskPhase,
    ADMIN_CASE_PHASE_OPTIONS,
    apply_task_overdue_status,
    is_pending_review_phase,
    is_terminal_phase,
    phase_for_workflow,
    resolve_case_task_phase,
    set_actual_return_at_from_latest_approved_writing,
)


def _ensure_admin():
    """权限闸：仅角色为 admin 的用户可继续访问视图，否则 403。"""
    if current_user.role != "admin":
        abort(403)


def _review_inbox_query():
    """全部待审核任务；兼容尚未归一化的历史 overdue_pending_review。"""
    return (
        Task.query.join(Task.case)
        .filter(
            Task.phase_status.in_(
                [TaskPhase.PENDING_REVIEW, TaskPhase.OVERDUE_PENDING_REVIEW]
            )
        )
    )


def _review_inbox_counts(_user: User | None = None) -> tuple[int, int]:
    """返回（待审核总数，待处理数）；仅审核通过/打回后待处理数才会减少。"""
    total = _review_inbox_query().count()
    return total, total


@admin_bp.app_context_processor
def _admin_review_nav_context():
    """给管理端侧边栏注入待审核总数与待处理数。"""
    if not current_user.is_authenticated or current_user.role != "admin":
        return {}
    total, unread = _review_inbox_counts(current_user)
    return {
        "review_nav_total": total,
        "review_nav_unread": unread,
    }


def _apply_admin_review(case: Case, task: Task | None, action: str, reject_note: str) -> tuple[bool, str, str]:
    """统一执行审核通过/打回，供案件详情与审核中心复用。"""
    if task is None:
        return False, "该案件暂无任务，无法审核。", "warning"
    if not is_pending_review_phase(task.phase_status):
        return False, "当前状态不是待审核，无法执行审核动作。", "warning"
    if action == "approve":
        task.phase_status = TaskPhase.PENDING_SUBMIT
        db.session.add(
            CaseReviewLog(
                case_id=case.id,
                operator_id=current_user.id,
                recipient_id=task.assignee_id,
                action="approve",
                note=None,
            )
        )
        db.session.commit()
        return True, "审核通过，案件已进入待递交。", "success"
    if action == "reject":
        if not reject_note:
            return False, "打回时请填写原因。", "warning"
        task.phase_status = TaskPhase.IN_PROGRESS
        db.session.add(
            CaseReviewLog(
                case_id=case.id,
                operator_id=current_user.id,
                recipient_id=task.assignee_id,
                action="reject",
                note=reject_note,
            )
        )
        db.session.commit()
        return True, "已打回，案件状态改为撰写中。", "info"
    return False, "审核动作无效。", "warning"


def _notify_case_assigned(case: Case, old_assignee_id, new_assignee_id) -> None:
    """员工被指派新案件时写入通知；仅在指派目标为员工且发生变化时记录。

    调用方需在 commit 之前调用；本函数只 add，不 commit。
    """
    if new_assignee_id is None or new_assignee_id == old_assignee_id:
        return
    assignee = db.session.get(User, new_assignee_id)
    if assignee is None or assignee.role != "staff":
        return
    db.session.add(
        CaseReviewLog(
            case_id=case.id,
            operator_id=current_user.id,
            recipient_id=new_assignee_id,
            action="assigned",
            note=None,
        )
    )


def _staff_users_ordered() -> list[User]:
    """返回可派单的在职撰写师：正式在前、外包在后，再按用户名升序。流程/业务人员不进入分配池。"""
    users = (
        User.query.filter(User.role == "staff", User.is_active.is_(True))
        .order_by(User.username.asc(), User.id.asc())
        .all()
    )
    users = [u for u in users if u.is_assignable_writer]
    formal = [u for u in users if u.staff_kind_normalized == User.STAFF_KIND_FORMAL]
    outsource = [u for u in users if u.staff_kind_normalized == User.STAFF_KIND_OUTSOURCE]
    return formal + outsource


def _staff_users_partitioned() -> tuple[list[User], list[User]]:
    """在职员工按正式 / 外包分区，供管理端下拉与列表使用。"""
    users = _staff_users_ordered()
    formal = [u for u in users if u.staff_kind_normalized == User.STAFF_KIND_FORMAL]
    outsource = [u for u in users if u.staff_kind_normalized == User.STAFF_KIND_OUTSOURCE]
    return formal, outsource


_CASE_SERIAL_RE = re.compile(r"^(\d{4})(\d{2})$")
_PROJECT_CODE_RE = re.compile(r"^(\d{4})(\d{2})$")
_CN_TZ = timezone(timedelta(hours=8))


def _ym_prefix_cn(created_at: datetime | None = None) -> str:
    """年月前缀（东八区）：如 2026-07 → 2607。"""
    dt = created_at or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CN_TZ).strftime("%y%m")


def _next_case_serial(created_at: datetime | None = None) -> str:
    """
    自动生成 6 位案件序列号：YYMM + 当月序号（两位）。
    例：2026 年 7 月第一个案件 → 260701；8 月重新从 01 起。
    """
    prefix = _ym_prefix_cn(created_at)
    max_seq = 0
    for (serial,) in db.session.query(Case.application_no).all():
        match = _CASE_SERIAL_RE.fullmatch((serial or "").strip())
        if not match:
            continue
        if match.group(1) != prefix:
            continue
        max_seq = max(max_seq, int(match.group(2)))
    next_seq = max_seq + 1
    if next_seq > 99:
        raise ValueError("当月案件序列号已用尽（最多 99 个）。")
    return f"{prefix}{next_seq:02d}"


def _project_code_ym_prefix(created_at: datetime | None = None) -> str:
    """项目编码前四位：创建时间（东八区）的年月，如 2026-04 → 2604。"""
    return _ym_prefix_cn(created_at)


def _next_project_code(created_at: datetime | None = None) -> str:
    """
    自动生成 6 位项目编码：YYMM + 当月序号（两位）。
    例：2026 年 4 月第一个项目 → 260401。
    序号按当月已有自动编号的最大值递增。
    """
    prefix = _project_code_ym_prefix(created_at)
    max_seq = 0
    for (code,) in db.session.query(Project.code).filter(Project.code.isnot(None)).all():
        match = _PROJECT_CODE_RE.fullmatch((code or "").strip())
        if not match:
            continue
        if match.group(1) != prefix:
            continue
        max_seq = max(max_seq, int(match.group(2)))
    next_seq = max_seq + 1
    if next_seq > 99:
        raise ValueError("当月项目编码序号已用尽（最多 99 个）。")
    return f"{prefix}{next_seq:02d}"


def backfill_empty_project_codes() -> list[tuple[int, str, str]]:
    """
    仅为空编码补发自动编号；已有自动或手工编码一律保持不动。
    返回发生变化的 [(project_id, name, new_code), ...]。
    """
    updated: list[tuple[int, str, str]] = []
    projects = Project.query.order_by(Project.created_at.asc(), Project.id.asc()).all()
    next_sequences: dict[str, int] = {}

    for project in projects:
        code = (project.code or "").strip()
        match = _PROJECT_CODE_RE.fullmatch(code)
        if match:
            prefix = match.group(1)
            next_sequences[prefix] = max(next_sequences.get(prefix, 1), int(match.group(2)) + 1)

    for project in projects:
        if (project.code or "").strip():
            continue
        prefix = _project_code_ym_prefix(project.created_at)
        sequence = next_sequences.get(prefix, 1)
        if sequence > 99:
            raise ValueError(f"{prefix} 当月项目编码序号已用尽（最多 99 个）。")
        new_code = f"{prefix}{sequence:02d}"
        project.code = new_code
        next_sequences[prefix] = sequence + 1
        updated.append((project.id, project.name, new_code))

    if updated:
        db.session.commit()
    return updated


def _normalize_user_phone(raw: str) -> tuple[str | None, str | None]:
    """规范化手机号：空串视为清除；非法格式返回 (None, 错误说明)。"""
    phone = User.normalize_phone(raw)
    if phone is None:
        return None, None
    if not User.is_valid_phone(phone):
        return None, "手机号格式不正确，请输入 11 位中国大陆手机号。"
    return phone, None


def _phone_taken(phone: str, *, exclude_user_id: int | None = None) -> bool:
    q = User.query.filter_by(phone=phone)
    if exclude_user_id is not None:
        q = q.filter(User.id != exclude_user_id)
    return q.first() is not None


def _business_owner_id_from_form(raw: str | None) -> tuple[int | None, str | None]:
    """指派员工选填；空为未分配（待派单）。错误时第二项为说明。"""
    s = (raw or "").strip()
    if not s:
        return None, None
    if not s.isdigit():
        return None, "指派员工参数不合法。"
    uid = int(s)
    u = db.session.get(User, uid)
    if u is None or u.role != "staff":
        return None, "请选择有效的员工账号进行指派。"
    if not u.is_assignable_writer:
        return None, "仅撰写师可进入案件分配池。"
    return uid, None


def _customer_list_display_name(customer: Customer) -> str:
    """列表展示名：CSV 等系统占位名优先用备注首行作为对外显示名称。"""
    raw = (customer.name or "").strip()
    if raw.startswith("_csv_customer_"):
        note = (customer.note or "").strip()
        if note:
            first = note.splitlines()[0].strip()
            if first:
                return first[:200]
    return raw or "—"


def _parse_iso_date_str(raw: str | None) -> date | None:
    """把 'YYYY-MM-DD' 字符串解析为 date；空值或非法返回 None。"""
    if not raw or not str(raw).strip():
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _customer_list_rows(customers: list[Customer]) -> list[dict]:
    """构造客户列表行视图模型：展示名、联系人摘要、登录账号、创建人。"""
    if not customers:
        return []
    ids = [c.id for c in customers]
    by_cid: dict[int, list[User]] = {}
    for u in User.query.filter(User.customer_id.in_(ids), User.role == "client").order_by(User.id.asc()).all():
        by_cid.setdefault(u.customer_id, []).append(u)

    creator_ids = {c.created_by_id for c in customers if c.created_by_id}
    creator_names: dict[int, str] = {}
    if creator_ids:
        for u in User.query.filter(User.id.in_(creator_ids)).all():
            creator_names[u.id] = u.username

    rows: list[dict] = []
    for c in customers:
        clients = by_cid.get(c.id, [])
        if not clients:
            client_summary = None
            client_accounts = None
        elif len(clients) == 1:
            uname = clients[0].username
            client_summary = uname
            client_accounts = f"账号 {uname}"
        else:
            client_summary = f"{clients[0].username} 等{len(clients)}人"
            client_accounts = "、".join(u.username for u in clients)
        cn = (c.contact_name or "").strip()
        cp = (c.contact_phone or "").strip()
        contact_summary = cn or client_summary
        contact_accounts = cp or client_accounts
        disp = _customer_list_display_name(c)
        raw_name = (c.name or "").strip()
        rows.append(
            {
                "customer": c,
                "display_name": disp,
                "system_name_hint": raw_name if disp != raw_name else None,
                "contact_summary": contact_summary,
                "contact_accounts": contact_accounts,
                "created_by_username": creator_names.get(c.created_by_id) if c.created_by_id else None,
            }
        )
    return rows


_CUSTOMER_PHONE_RE = re.compile(r"^[\d+\s\-()（）]{5,40}$")


def _validate_customer_create_payload(data: object) -> tuple[dict | None, dict[str, str]]:
    """校验 JSON 创建客户；返回 (payload, errors)，errors 非空时 payload 为 None。"""
    errors: dict[str, str] = {}
    if not isinstance(data, dict):
        return None, {"_": "请求体格式不正确。"}
    name = (data.get("name") or "").strip()
    if not name:
        errors["name"] = "客户名称不能为空"
    elif len(name) > 200:
        errors["name"] = "客户名称不能超过 200 个字符"
    kind = (data.get("kind") or CustomerKind.COMPANY)
    if isinstance(kind, str):
        kind = kind.strip()
    if kind not in {CustomerKind.COMPANY, CustomerKind.INDIVIDUAL}:
        errors["kind"] = "客户类型不合法"
    contact_name = (data.get("contact_name") or "").strip()
    if len(contact_name) > 120:
        errors["contact_name"] = "联系人不能超过 120 个字符"
    contact_phone = (data.get("contact_phone") or "").strip()
    if contact_phone and not _CUSTOMER_PHONE_RE.match(contact_phone):
        errors["contact_phone"] = "电话格式不正确（5–40 位数字、+、空格、括号及短横线）"
    note = (data.get("note") or "").strip() or None
    fee_standard = (data.get("fee_standard") or "").strip() or None
    if note and len(note) > 5000:
        errors["note"] = "备注不能超过 5000 字"
    if fee_standard and len(fee_standard) > 5000:
        errors["fee_standard"] = "收费标准不能超过 5000 字"
    if errors:
        return None, errors
    return (
        {
            "name": name,
            "kind": kind,
            "contact_name": contact_name or None,
            "contact_phone": contact_phone or None,
            "note": note,
            "fee_standard": fee_standard,
        },
        {},
    )


def _customers_filter_url_kwargs(
    *,
    name_q: str,
    note_q: str,
    kind_filter: str,
    created_from: str,
    created_to: str,
    created_by: str,
    per_page: int,
) -> dict:
    """供客户列表分页/模板生成查询串（不含 page），便于翻页保留筛选。"""
    d: dict = {}
    if name_q:
        d["q"] = name_q
    if note_q:
        d["note_q"] = note_q
    if kind_filter in {CustomerKind.COMPANY, CustomerKind.INDIVIDUAL}:
        d["kind"] = kind_filter
    if created_from:
        d["created_from"] = created_from
    if created_to:
        d["created_to"] = created_to
    if created_by:
        d["created_by"] = created_by
    if per_page != 20:
        d["per_page"] = str(per_page)
    return d


def _parse_due_at(value: str) -> datetime | None:
    """将用户输入的北京时间转换为 UTC 存储；空值返回 None。"""
    raw = value.strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_initiated_at(value: str) -> datetime:
    """解析立项时间：必填，且不得晚于当前时刻。"""
    parsed = _parse_due_at(value)
    if parsed is None:
        raise ValueError("empty")
    now = datetime.now(timezone.utc)
    if parsed > now:
        raise ValueError("future")
    return parsed


def _project_initiated_at(project: Project) -> datetime:
    """展示/编辑用立项时间：优先 initiated_at，否则回落 created_at。"""
    return project.initiated_at or project.created_at


def _dt_input_value(value: datetime | None) -> str:
    """把数据库 UTC 时间转换为北京时间的 datetime-local 字符串。"""
    if value is None:
        return ""
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    dt = utc_value.astimezone(_CN_TZ)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _resolve_actual_return_for_completed(
    case: Case,
    *,
    actual_return_at_raw: str = "",
    previous_actual_return_at: datetime | None = None,
) -> tuple[bool, str]:
    """已完成案件必须有实际返稿时间：表单可显式修改，否则保留已有值，再尝试补算。"""
    raw = (actual_return_at_raw or "").strip()
    if raw:
        try:
            parsed = _parse_due_at(raw)
        except ValueError:
            return False, "实际返稿时间格式不正确。"
        if parsed is None:
            return False, "改为已完成时请填写实际返稿时间。"
        case.actual_return_at = parsed
        return True, ""

    existing = previous_actual_return_at or case.actual_return_at
    if existing is not None:
        case.actual_return_at = existing
        return True, ""

    set_actual_return_at_from_latest_approved_writing(case)
    if case.actual_return_at is not None:
        return True, ""
    return False, "改为已完成时请填写实际返稿时间（系统未能根据审核/材料自动推算）。"


def _case_material_download_rows(case_id: int, role_filter: str, page: int):
    """分页返回某案件的材料下载留痕（可按角色过滤）。"""
    q = CaseMaterialDownloadLog.query.filter_by(case_id=case_id)
    if role_filter in {"admin", "staff", "client"}:
        q = q.filter(CaseMaterialDownloadLog.operator_role == role_filter)
    return q.order_by(CaseMaterialDownloadLog.created_at.desc(), CaseMaterialDownloadLog.id.desc()).paginate(
        page=page,
        per_page=10,
        error_out=False,
    )


def _case_material_download_query(case_id: int, role_filter: str):
    """返回不分页的留痕查询，便于导出 CSV 复用同一筛选规则。"""
    q = CaseMaterialDownloadLog.query.filter_by(case_id=case_id)
    if role_filter in {"admin", "staff", "client"}:
        q = q.filter(CaseMaterialDownloadLog.operator_role == role_filter)
    return q.order_by(CaseMaterialDownloadLog.created_at.desc(), CaseMaterialDownloadLog.id.desc())


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    """管理工作台：以提醒摘要 + 工作通知为主入口。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/dashboard.html",
        inner_template="admin/snippets/dashboard_inner.html",
        spa_endpoint="admin.dashboard",
        spa_document_title="管理工作台 — 琴岳专利管理系统",
        **dashboard_page_kwargs(current_user),
    )


@admin_bp.route("/task-board")
@login_required
def task_board():
    """管理员任务看板：聚合全员任务，支持状态筛选与排序。"""
    _ensure_admin()
    status_filter = request.args.get("status", "all").strip()
    sort_by = request.args.get("sort", "deadline").strip()
    if status_filter not in {"all", "in_progress", "pending_review", "pending_assignment", "overdue"}:
        status_filter = "all"
    if sort_by not in {"deadline", "customer"}:
        sort_by = "deadline"
    board = task_board_data_for_user(current_user, status_filter=status_filter, sort_by=sort_by)
    staff_formal, staff_outsource = _staff_users_partitioned()
    return render_spa_or_full(
        full_template="admin/task_board.html",
        inner_template="partials/task_board_inner.html",
        spa_endpoint="admin.task_board",
        spa_document_title="任务看板 — 琴岳专利管理系统",
        page_title="任务看板",
        page_desc="查看进行中、待审核与超期任务，并支持统一筛选与排序。",
        task_rows=board["rows"],
        status_filter=status_filter,
        sort_by=sort_by,
        count_in_progress=board["counts"]["in_progress"],
        count_pending_review=board["counts"]["pending_review"],
        count_pending_assignment=board["counts"]["pending_assignment"],
        count_overdue=board["counts"]["overdue"],
        case_detail_endpoint="admin.case_detail",
        staff_users=_staff_users_ordered(),
        staff_users_formal=staff_formal,
        staff_users_outsource=staff_outsource,
    )


def _admin_customers_filtered_query():
    """根据 request.args 构建筛选后的 Customer 查询（未排序、未分页）。"""
    name_q = request.args.get("q", "").strip()
    note_q = request.args.get("note_q", "").strip()
    kind_filter = request.args.get("kind", "").strip()
    created_from_raw = request.args.get("created_from", "").strip()
    created_to_raw = request.args.get("created_to", "").strip()
    created_by_raw = request.args.get("created_by", "").strip()

    q = Customer.query
    if name_q:
        q = q.filter(Customer.name.contains(name_q))
    if note_q:
        q = q.filter(Customer.note.contains(note_q))
    if kind_filter in {CustomerKind.COMPANY, CustomerKind.INDIVIDUAL}:
        q = q.filter(Customer.kind == kind_filter)

    df = _parse_iso_date_str(created_from_raw)
    dt = _parse_iso_date_str(created_to_raw)
    if df and dt and df > dt:
        df, dt = dt, df
    if df:
        start_dt = datetime.combine(df, time.min, tzinfo=_CN_TZ).astimezone(timezone.utc).replace(tzinfo=None)
        q = q.filter(Customer.created_at >= start_dt)
    if dt:
        end_dt = (
            datetime.combine(dt, time(23, 59, 59, 999999), tzinfo=_CN_TZ)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        q = q.filter(Customer.created_at <= end_dt)
    if created_by_raw.isdigit():
        q = q.filter(Customer.created_by_id == int(created_by_raw))
    return q, name_q, note_q, kind_filter, created_from_raw, created_to_raw, created_by_raw


def _customer_kind_label_cn(kind: str) -> str:
    """客户类型枚举翻译为中文标签，便于导出与展示。"""
    if kind == CustomerKind.COMPANY:
        return "公司"
    if kind == CustomerKind.INDIVIDUAL:
        return "个人"
    return kind or "—"


def _customer_created_at_export_utc(c: Customer) -> str:
    """导出客户时统一为北京时间字符串。"""
    v = c.created_at
    if v is None:
        return "—"
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _customers_export_xlsx_bytes(customers: list[Customer]) -> bytes:
    """把传入客户列表渲染为 .xlsx 字节串（不落盘，直接返回给前端下载）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "客户"
    ws.append(["客户名称", "展示名称", "类型", "联系人", "电话", "备注", "创建时间(北京时间)"])
    for c in customers:
        disp = _customer_list_display_name(c)
        cn = (c.contact_name or "").strip()
        cp = (c.contact_phone or "").strip()
        note = (c.note or "").strip()
        ws.append(
            [
                c.name,
                disp,
                _customer_kind_label_cn(c.kind),
                cn or "—",
                cp or "—",
                note,
                _customer_created_at_export_utc(c),
            ]
        )
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _project_datetime_export_utc(v: datetime | None) -> str:
    """导出项目时日期时间统一为北京时间字符串；空值为 —。"""
    if v is None:
        return "—"
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _projects_export_xlsx_bytes(projects: list[Project]) -> bytes:
    """把传入项目列表渲染为 .xlsx 字节串（不落盘，直接返回给前端下载）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "项目"
    ws.append(["项目名称", "归属客户", "编码", "截止时间(北京时间)", "说明", "创建人", "创建时间(北京时间)"])
    for p in projects:
        cust_name = (p.customer.name if p.customer else None) or "—"
        code_s = (p.code or "").strip()
        desc = (p.description or "").strip() or "—"
        creator = (p.created_by.username if p.created_by else None) or "—"
        ws.append(
            [
                p.name,
                cust_name,
                code_s if code_s else "—",
                _project_datetime_export_utc(p.due_at),
                desc,
                creator,
                _project_datetime_export_utc(p.created_at),
            ]
        )
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _bulk_delete_customers_by_ids(ids: list[int]) -> tuple[list[int], list[dict]]:
    """
    批量删除客户：无关联项目方可删；先解除绑定该客户的客户端账号 customer_id。
    返回 (已删除 id 列表, 跳过项 [{id, reason}, ...])。
    """
    deleted: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in ids:
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            skipped.append({"id": str(raw), "reason": "非法的编号"})
            continue
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)

    deletable: list[Customer] = []
    for cid in ordered:
        c = db.session.get(Customer, cid)
        if c is None:
            skipped.append({"id": cid, "reason": "记录不存在"})
            continue
        if c.projects.count() > 0:
            skipped.append({"id": cid, "reason": "存在关联项目，无法删除"})
            continue
        deletable.append(c)

    if not deletable:
        return deleted, skipped

    id_list = [c.id for c in deletable]
    User.query.filter(User.customer_id.in_(id_list)).update({User.customer_id: None}, synchronize_session=False)
    for c in deletable:
        db.session.delete(c)
        deleted.append(c.id)
    return deleted, skipped


def _bulk_delete_projects_by_ids(ids: list[int]) -> tuple[list[int], list[dict]]:
    """
    批量删除项目：无任何关联案件方可删。
    Case.project_id 为 NOT NULL；存在案件则跳过（无需也无法「断绑」案件）。
    Project.created_by_id 指向 User 且可空：删除项目行即可，不必事先清理创建人外键。
    返回 (已删除 id 列表, 跳过项 [{id, reason}, ...])。
    """
    deleted: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in ids:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            skipped.append({"id": str(raw), "reason": "非法的编号"})
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        ordered.append(pid)

    deletable: list[Project] = []
    for pid in ordered:
        p = db.session.get(Project, pid)
        if p is None:
            skipped.append({"id": pid, "reason": "记录不存在"})
            continue
        if p.cases.count() > 0:
            skipped.append({"id": pid, "reason": "存在关联案件，无法删除"})
            continue
        deletable.append(p)

    if not deletable:
        return deleted, skipped

    for p in deletable:
        db.session.delete(p)
        deleted.append(p.id)
    return deleted, skipped


def _bulk_delete_cases_by_ids(ids: list[int]) -> tuple[list[int], list[dict]]:
    """
    批量删除案件：案件从属任务、审核记录、材料记录与下载日志通过模型级联删除。
    数据库提交成功后再由路由清理 instance/uploads/case_<id>，避免提交失败但文件已丢失。
    返回 (已删除 id 列表, 跳过项 [{id, reason}, ...])。
    """
    deleted: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in ids:
        try:
            case_id = int(raw)
        except (TypeError, ValueError):
            skipped.append({"id": str(raw), "reason": "非法的编号"})
            continue
        if case_id <= 0 or case_id in seen:
            continue
        seen.add(case_id)
        ordered.append(case_id)

    for case_id in ordered:
        case = db.session.get(Case, case_id)
        if case is None:
            skipped.append({"id": case_id, "reason": "记录不存在"})
            continue
        db.session.delete(case)
        deleted.append(case.id)
    return deleted, skipped


@admin_bp.route("/customers", methods=["GET"])
@login_required
def customers():
    """客户列表页：渲染筛选条件、分页表格和新增/导出工具条。"""
    _ensure_admin()
    q, name_q, note_q, kind_filter, created_from_raw, created_to_raw, created_by_raw = _admin_customers_filtered_query()

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    per_page_raw = request.args.get("per_page", "20").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() else 20
    if per_page not in (10, 20, 50):
        per_page = 20

    pagination = q.order_by(Customer.created_at.desc(), Customer.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    customer_rows = _customer_list_rows(pagination.items)

    creator_options = User.query.filter(User.role.in_(["admin", "staff"])).order_by(User.username.asc()).all()

    filter_url_kwargs = _customers_filter_url_kwargs(
        name_q=name_q,
        note_q=note_q,
        kind_filter=kind_filter,
        created_from=created_from_raw,
        created_to=created_to_raw,
        created_by=created_by_raw,
        per_page=per_page,
    )

    return render_spa_or_full(
        full_template="admin/customers.html",
        inner_template="admin/snippets/customers_inner.html",
        spa_endpoint="admin.customers",
        spa_document_title="客户档案 — 琴岳专利管理系统",
        page_title="客户档案",
        page_desc="管理客户联系人、合同期限与收费标准。",
        customer_rows=customer_rows,
        pagination=pagination,
        q=name_q,
        note_q=note_q,
        kind_filter=kind_filter,
        created_from=created_from_raw,
        created_to=created_to_raw,
        created_by_filter=created_by_raw,
        per_page=per_page,
        filter_url_kwargs=filter_url_kwargs,
        creator_options=creator_options,
        customer_kind_company=CustomerKind.COMPANY,
        customer_kind_individual=CustomerKind.INDIVIDUAL,
    )


@admin_bp.route("/customers/bulk-delete", methods=["POST"])
@login_required
def customers_bulk_delete():
    """JSON 批量删除客户；返回 deleted / skipped。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要删除的客户。"), 400
    try:
        deleted, skipped = _bulk_delete_customers_by_ids(ids)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("customers_bulk_delete")
        return jsonify(ok=False, message="删除失败：存在未处理的外键约束。"), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception("customers_bulk_delete")
        return jsonify(ok=False, message="删除失败，请稍后重试。"), 500
    return jsonify(ok=True, deleted=deleted, skipped=skipped, message=f"已删除 {len(deleted)} 条。")


@admin_bp.route("/customers/export", methods=["POST"])
@login_required
def customers_export():
    """按勾选 id 导出 Excel（.xlsx）。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要导出的客户。"), 400
    parsed: list[int] = []
    for raw in ids:
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            continue
        if cid > 0 and cid not in parsed:
            parsed.append(cid)
    if not parsed:
        return jsonify(ok=False, message="没有有效的客户编号。"), 400

    by_id = {c.id: c for c in Customer.query.filter(Customer.id.in_(parsed)).all()}
    ordered: list[Customer] = [by_id[i] for i in parsed if i in by_id]
    if not ordered:
        return jsonify(ok=False, message="所选客户均不存在。"), 400

    data_bytes = _customers_export_xlsx_bytes(ordered)
    stamp = datetime.now(timezone.utc).astimezone(_CN_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"customers_export_{stamp}.xlsx"
    return Response(
        data_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data_bytes)),
        },
    )


@admin_bp.route("/customers/create", methods=["POST"])
@login_required
def customers_create():
    """JSON 创建客户（管理端弹窗）；成功返回 { ok, message, customer_id }，校验失败 422。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, errors={"_": "请使用 JSON 格式提交（Content-Type: application/json）。"}), 415
    payload, errors = _validate_customer_create_payload(request.get_json(silent=True))
    if errors:
        return jsonify(ok=False, errors=errors), 422
    assert payload is not None
    try:
        customer = Customer(
            name=payload["name"],
            kind=payload["kind"],
            contact_name=payload["contact_name"],
            contact_phone=payload["contact_phone"],
            note=payload["note"],
            fee_standard=payload["fee_standard"],
            created_by_id=current_user.id,
        )
        db.session.add(customer)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("customers_create")
        return jsonify(ok=False, errors={"_": "保存失败，请稍后重试或联系管理员。"}), 500
    return jsonify(ok=True, message="新增成功", customer_id=customer.id)


@admin_bp.route("/accounts", methods=["GET", "POST"])
@login_required
def accounts():
    """账号分发页：管理员统一创建员工/客户账号，并支持密码重置。"""
    _ensure_admin()
    if request.method == "POST":
        form_action = request.form.get("form_action", "create_account").strip()
        if form_action == "reset_password":
            user_id_raw = request.form.get("user_id", "").strip()
            new_password = request.form.get("new_password", "")
            if not user_id_raw.isdigit():
                return redirect_with_qy_toast("admin.accounts", "账号参数不合法。", "warning")
            if len(new_password) < 6:
                return redirect_with_qy_toast("admin.accounts", "新密码长度不能少于 6 位。", "warning")
            user = db.session.get(User, int(user_id_raw))
            if user is None or user.role not in {"staff", "client"}:
                return redirect_with_qy_toast("admin.accounts", "目标账号不存在。", "warning")
            user.set_password(new_password)
            db.session.commit()
            return redirect_with_qy_toast("admin.accounts", "密码已重置。", "success")

        if form_action == "update_phone":
            user_id_raw = request.form.get("user_id", "").strip()
            if not user_id_raw.isdigit():
                return redirect_with_qy_toast("admin.accounts", "账号参数不合法。", "warning")
            user = db.session.get(User, int(user_id_raw))
            if user is None or user.role not in {"staff", "client"}:
                return redirect_with_qy_toast("admin.accounts", "目标账号不存在。", "warning")
            phone, phone_err = _normalize_user_phone(request.form.get("phone", ""))
            if phone_err:
                return redirect_with_qy_toast("admin.accounts", phone_err, "warning")
            if phone and _phone_taken(phone, exclude_user_id=user.id):
                return redirect_with_qy_toast("admin.accounts", "该手机号已被其他账号使用。", "danger")
            user.phone = phone
            db.session.commit()
            return redirect_with_qy_toast("admin.accounts", "手机号已更新。", "success")

        if form_action == "update_staff_kind":
            user_id_raw = request.form.get("user_id", "").strip()
            if not user_id_raw.isdigit():
                return redirect_with_qy_toast("admin.accounts", "账号参数不合法。", "warning")
            user = db.session.get(User, int(user_id_raw))
            if user is None or user.role != "staff":
                return redirect_with_qy_toast("admin.accounts", "仅员工账号可设置编制。", "warning")
            user.staff_kind = User.normalize_staff_kind(request.form.get("staff_kind", ""))
            db.session.commit()
            return redirect_with_qy_toast("admin.accounts", "员工编制已更新。", "success")

        if form_action == "reactivate_account":
            # 预留：列表仅展示在职/有效账号，恢复入口未开放；管理员仍可通过 POST 调用。
            user_id_raw = request.form.get("user_id", "").strip()
            if not user_id_raw.isdigit():
                return redirect_with_qy_toast("admin.accounts", "账号参数不合法。", "warning")
            user = db.session.get(User, int(user_id_raw))
            if user is None or user.role not in {"staff", "client"}:
                return redirect_with_qy_toast("admin.accounts", "目标账号不存在。", "warning")
            if getattr(user, "is_active", True):
                return redirect_with_qy_toast(
                    "admin.accounts",
                    "该账号已是正常状态。",
                    "secondary",
                )
            user.is_active = True
            db.session.commit()
            if user.role == "staff":
                msg = f"账号「{user.username}」已恢复在职。"
            elif user.role == "client":
                msg = f"账号「{user.username}」已恢复有效。"
            else:
                msg = f"账号「{user.username}」已恢复启用。"
            return redirect_with_qy_toast(
                "admin.accounts",
                msg,
                "success",
            )

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "client").strip()
        customer_id_raw = request.form.get("customer_id", "").strip()
        phone, phone_err = _normalize_user_phone(request.form.get("phone", ""))
        if phone_err:
            return redirect_with_qy_toast("admin.accounts", phone_err, "warning")

        if not username:
            return redirect_with_qy_toast("admin.accounts", "账号名不能为空。", "warning")
        if len(password) < 6:
            return redirect_with_qy_toast("admin.accounts", "密码长度不能少于 6 位。", "warning")
        if role not in {"staff", "client"}:
            return redirect_with_qy_toast("admin.accounts", "账号类型不合法。", "warning")
        if User.query.filter_by(username=username).first() is not None:
            return redirect_with_qy_toast("admin.accounts", "账号名已存在，请更换后重试。", "danger")
        if phone and _phone_taken(phone):
            return redirect_with_qy_toast("admin.accounts", "该手机号已被其他账号使用。", "danger")

        customer_id = None
        staff_kind = None
        staff_function = None
        if role == "client":
            if not customer_id_raw.isdigit():
                return redirect_with_qy_toast("admin.accounts", "客户账号必须绑定客户。", "warning")
            customer = db.session.get(Customer, int(customer_id_raw))
            if customer is None:
                return redirect_with_qy_toast("admin.accounts", "绑定客户不存在，请刷新后重试。", "danger")
            customer_id = customer.id
        else:
            staff_kind = User.normalize_staff_kind(request.form.get("staff_kind", ""))
            staff_function = User.normalize_staff_function(request.form.get("staff_function", ""))

        user = User(
            username=username,
            role=role,
            customer_id=customer_id,
            phone=phone,
            staff_kind=staff_kind,
            staff_function=staff_function,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return redirect_with_qy_toast("admin.accounts", "账号已创建。", "success")

    keyword = request.args.get("q", "").strip()
    role_filter = request.args.get("role", "all").strip()
    staff_kind_filter = request.args.get("staff_kind", "all").strip()
    staff_function_filter = request.args.get("staff_function", "all").strip()
    users_q = User.query.filter(User.role.in_(["staff", "client"]), User.is_active.is_(True))
    if keyword:
        users_q = users_q.filter(
            db.or_(
                User.username.contains(keyword),
                User.phone.contains(keyword),
            )
        )
    if role_filter in {"staff", "client"}:
        users_q = users_q.filter(User.role == role_filter)
    if staff_kind_filter == User.STAFF_KIND_OUTSOURCE:
        users_q = users_q.filter(User.role == "staff", User.staff_kind == User.STAFF_KIND_OUTSOURCE)
    elif staff_kind_filter == User.STAFF_KIND_FORMAL:
        users_q = users_q.filter(
            User.role == "staff",
            db.or_(
                User.staff_kind == User.STAFF_KIND_FORMAL,
                User.staff_kind.is_(None),
                User.staff_kind == "",
            ),
        )
    if staff_function_filter == User.STAFF_FUNCTION_WRITER:
        users_q = users_q.filter(
            User.role == "staff",
            db.or_(
                User.staff_function == User.STAFF_FUNCTION_WRITER,
                User.staff_function.is_(None),
                User.staff_function == "",
            ),
        )
    elif staff_function_filter in User.STAFF_FUNCTIONS:
        users_q = users_q.filter(User.role == "staff", User.staff_function == staff_function_filter)

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    per_page_raw = request.args.get("per_page", "20").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() else 20
    if per_page not in (10, 20, 50):
        per_page = 20

    # 分区排序：正式员工 → 外包员工 → 客户；同组内新账号在前。
    kind_rank = db.case(
        (User.role == "client", 2),
        (
            db.or_(
                User.staff_kind == User.STAFF_KIND_OUTSOURCE,
            ),
            1,
        ),
        else_=0,
    )
    pagination = users_q.order_by(kind_rank.asc(), User.created_at.desc(), User.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    customer_options = Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all()

    filter_url_kwargs: dict = {}
    if keyword:
        filter_url_kwargs["q"] = keyword
    if role_filter in {"staff", "client"}:
        filter_url_kwargs["role"] = role_filter
    if staff_kind_filter in User.STAFF_KINDS:
        filter_url_kwargs["staff_kind"] = staff_kind_filter
    if staff_function_filter in User.STAFF_FUNCTIONS:
        filter_url_kwargs["staff_function"] = staff_function_filter
    if per_page != 20:
        filter_url_kwargs["per_page"] = str(per_page)

    return render_spa_or_full(
        full_template="admin/accounts.html",
        inner_template="admin/snippets/accounts_inner.html",
        spa_endpoint="admin.accounts",
        spa_document_title="账号分发 — 琴岳专利管理系统",
        page_title="账号分发",
        page_desc="由管理端统一创建员工与客户账号，可设置手机号用于登录与辨认；员工职能请到「角色权限」调整。当前版本不开放自助注册。",
        users=pagination.items,
        pagination=pagination,
        customers=customer_options,
        q=keyword,
        role_filter=role_filter,
        staff_kind_filter=staff_kind_filter if staff_kind_filter in User.STAFF_KINDS else "all",
        staff_function_filter=(
            staff_function_filter if staff_function_filter in User.STAFF_FUNCTIONS else "all"
        ),
        per_page=per_page,
        filter_url_kwargs=filter_url_kwargs,
    )


def _unassign_staff_workload(user: User) -> None:
    """离职停用：仅撰写中（含已超期）退回待分配并解除承办；其余状态保留承办人与业务负责人留痕。"""
    for task in Task.query.filter_by(assignee_id=user.id).all():
        if phase_for_workflow(task.phase_status) != TaskPhase.IN_PROGRESS:
            continue
        task.assignee_id = None
        task.phase_status = TaskPhase.PENDING_ASSIGNMENT


def _bulk_delete_users_by_ids(ids: list[int]) -> tuple[list[int], list[dict]]:
    """批量停用员工/客户账号（软删除）；撰写中案件退回待分配，其余留痕保留。"""
    deleted: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in ids:
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            skipped.append({"id": str(raw), "reason": "非法的编号"})
            continue
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        ordered.append(uid)

    for uid in ordered:
        user = db.session.get(User, uid)
        if user is None:
            skipped.append({"id": uid, "reason": "记录不存在"})
            continue
        if user.role not in {"staff", "client"}:
            skipped.append({"id": uid, "reason": "仅可删除员工/客户账号"})
            continue
        if user.id == current_user.id:
            skipped.append({"id": uid, "reason": "不能删除当前账号"})
            continue
        if not getattr(user, "is_active", True):
            skipped.append({"id": uid, "reason": "账号已是离职/冻结状态"})
            continue
        if user.role == "staff":
            _unassign_staff_workload(user)
        user.is_active = False
        deleted.append(user.id)
    return deleted, skipped


def _bulk_reactivate_users_by_ids(ids: list[int]) -> tuple[list[int], list[dict]]:
    """批量恢复已停用的员工/客户账号。"""
    reactivated: list[int] = []
    skipped: list[dict] = []
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in ids:
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            skipped.append({"id": str(raw), "reason": "非法的编号"})
            continue
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        ordered.append(uid)

    for uid in ordered:
        user = db.session.get(User, uid)
        if user is None:
            skipped.append({"id": uid, "reason": "记录不存在"})
            continue
        if user.role not in {"staff", "client"}:
            skipped.append({"id": uid, "reason": "仅可恢复员工/客户账号"})
            continue
        if getattr(user, "is_active", True):
            skipped.append({"id": uid, "reason": "账号已是正常状态"})
            continue
        user.is_active = True
        reactivated.append(user.id)
    return reactivated, skipped


@admin_bp.route("/accounts/bulk-delete", methods=["POST"])
@login_required
def accounts_bulk_delete():
    """JSON 批量停用员工/客户账号；停用后不再出现在分配池，也无法登录。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要删除的账号。"), 400
    try:
        deleted, skipped = _bulk_delete_users_by_ids(ids)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("accounts_bulk_delete")
        return jsonify(ok=False, message="删除失败：存在未处理的外键约束。"), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception("accounts_bulk_delete")
        return jsonify(ok=False, message="删除失败，请稍后重试。"), 500
    return jsonify(
        ok=True,
        deleted=deleted,
        skipped=skipped,
        message=f"已处理 {len(deleted)} 个账号（员工离职 / 客户冻结）。",
    )


@admin_bp.route("/accounts/bulk-reactivate", methods=["POST"])
@login_required
def accounts_bulk_reactivate():
    """JSON 批量恢复已停用的员工/客户账号（预留，当前管理端未开放入口）。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要恢复的账号。"), 400
    try:
        reactivated, skipped = _bulk_reactivate_users_by_ids(ids)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("accounts_bulk_reactivate")
        return jsonify(ok=False, message="恢复失败：存在未处理的数据约束。"), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception("accounts_bulk_reactivate")
        return jsonify(ok=False, message="恢复失败，请稍后重试。"), 500
    return jsonify(
        ok=True,
        reactivated=reactivated,
        skipped=skipped,
        message=f"已恢复 {len(reactivated)} 个账号（员工在职 / 客户有效）。",
    )


def _admin_projects_filtered_query():
    """根据 request.args 构建筛选后的 Project 查询（未排序、未分页）。"""
    name_q = request.args.get("q", "").strip()
    code_q = request.args.get("code_q", "").strip()
    customer_id_raw = request.args.get("customer_id", "").strip()
    created_from_raw = request.args.get("created_from", "").strip()
    created_to_raw = request.args.get("created_to", "").strip()
    created_by_raw = request.args.get("created_by", "").strip()

    q = Project.query.join(Project.customer)
    if name_q:
        q = q.filter(Project.name.contains(name_q))
    if code_q:
        q = q.filter(Project.code.contains(code_q))
    customer_id_filter = ""
    if customer_id_raw.isdigit():
        customer_id_filter = customer_id_raw
        q = q.filter(Project.customer_id == int(customer_id_raw))

    df = _parse_iso_date_str(created_from_raw)
    dt = _parse_iso_date_str(created_to_raw)
    if df and dt and df > dt:
        df, dt = dt, df
    if df:
        start_dt = datetime.combine(df, time.min, tzinfo=_CN_TZ).astimezone(timezone.utc).replace(tzinfo=None)
        q = q.filter(Project.created_at >= start_dt)
    if dt:
        end_dt = (
            datetime.combine(dt, time(23, 59, 59, 999999), tzinfo=_CN_TZ)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        q = q.filter(Project.created_at <= end_dt)
    if created_by_raw.isdigit():
        q = q.filter(Project.created_by_id == int(created_by_raw))
    return q, name_q, code_q, customer_id_filter, created_from_raw, created_to_raw, created_by_raw


def _projects_filter_url_kwargs(
    *,
    name_q: str,
    code_q: str,
    customer_id_filter: str,
    created_from: str,
    created_to: str,
    created_by: str,
    per_page: int,
) -> dict:
    """供项目列表分页/模板生成查询串（不含 page），便于翻页保留筛选。"""
    d: dict = {}
    if name_q:
        d["q"] = name_q
    if code_q:
        d["code_q"] = code_q
    if customer_id_filter:
        d["customer_id"] = customer_id_filter
    if created_from:
        d["created_from"] = created_from
    if created_to:
        d["created_to"] = created_to
    if created_by:
        d["created_by"] = created_by
    if per_page != 20:
        d["per_page"] = str(per_page)
    return d


def _validate_project_create_payload(data: object) -> tuple[dict | None, dict[str, str]]:
    """校验 JSON 创建项目；返回 (payload, errors)，errors 非空时 payload 为 None。"""
    errors: dict[str, str] = {}
    if not isinstance(data, dict):
        return None, {"_": "请求体格式不正确。"}

    name = (data.get("name") or "")
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        errors["name"] = "项目名称不能为空"
    elif len(name) > 200:
        errors["name"] = "项目名称不能超过 200 个字符"

    customer_id_raw = data.get("customer_id")
    customer_id_int: int | None = None
    if customer_id_raw is None or (isinstance(customer_id_raw, str) and not customer_id_raw.strip()):
        errors["customer_id"] = "请选择归属客户"
    else:
        try:
            customer_id_int = int(str(customer_id_raw).strip())
        except (TypeError, ValueError):
            errors["customer_id"] = "归属客户参数不合法"
        else:
            if customer_id_int <= 0:
                errors["customer_id"] = "归属客户参数不合法"
            elif db.session.get(Customer, customer_id_int) is None:
                errors["customer_id"] = "客户不存在，请刷新后重试"

    description = (data.get("description") or "")
    description = description.strip() if isinstance(description, str) else ""
    if description and len(description) > 1000:
        errors["description"] = "项目说明不能超过 1000 字"

    due_at_raw = data.get("due_at")
    due_at: datetime | None = None
    if due_at_raw is None or (isinstance(due_at_raw, str) and not due_at_raw.strip()):
        due_at = None
    elif isinstance(due_at_raw, str):
        try:
            due_at = _parse_due_at(due_at_raw)
        except ValueError:
            errors["due_at"] = "截止时间格式不正确"
    else:
        errors["due_at"] = "截止时间格式不正确"

    if errors:
        return None, errors
    return (
        {
            "name": name,
            "customer_id": customer_id_int,
            "description": description or None,
            "due_at": due_at,
        },
        {},
    )


@admin_bp.route("/projects/create", methods=["POST"])
@login_required
def projects_create():
    """JSON 创建项目（管理端弹窗）；成功返回 { ok, message, project_id }，校验失败 422。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, errors={"_": "请使用 JSON 格式提交（Content-Type: application/json）。"}), 415
    payload, errors = _validate_project_create_payload(request.get_json(silent=True))
    if errors:
        return jsonify(ok=False, errors=errors), 422
    assert payload is not None
    try:
        project = Project(
            customer_id=payload["customer_id"],
            name=payload["name"],
            description=payload["description"],
            due_at=payload["due_at"],
            created_by_id=current_user.id,
        )
        db.session.add(project)
        db.session.flush()
        project.code = _next_project_code(project.created_at)
        project.initiated_at = project.created_at
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify(ok=False, errors={"_": str(exc)}), 422
    except Exception:
        db.session.rollback()
        current_app.logger.exception("projects_create")
        return jsonify(ok=False, errors={"_": "保存失败，请稍后重试或联系管理员。"}), 500
    return jsonify(ok=True, message="项目已创建", project_id=project.id, code=project.code)


@admin_bp.route("/projects/bulk-delete", methods=["POST"])
@login_required
def projects_bulk_delete():
    """JSON 批量删除项目；有关联案件的跳过。返回 deleted / skipped。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要删除的项目。"), 400
    try:
        deleted, skipped = _bulk_delete_projects_by_ids(ids)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("projects_bulk_delete")
        return jsonify(ok=False, message="删除失败：存在未处理的外键约束。"), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception("projects_bulk_delete")
        return jsonify(ok=False, message="删除失败，请稍后重试。"), 500
    return jsonify(ok=True, deleted=deleted, skipped=skipped, message=f"已删除 {len(deleted)} 条。")


@admin_bp.route("/projects/export", methods=["POST"])
@login_required
def projects_export():
    """按勾选 id 导出项目 Excel（.xlsx）。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要导出的项目。"), 400
    parsed: list[int] = []
    for raw in ids:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in parsed:
            parsed.append(pid)
    if not parsed:
        return jsonify(ok=False, message="没有有效的项目编号。"), 400

    rows = (
        Project.query.options(joinedload(Project.customer), joinedload(Project.created_by))
        .filter(Project.id.in_(parsed))
        .all()
    )
    by_id = {p.id: p for p in rows}
    ordered: list[Project] = [by_id[i] for i in parsed if i in by_id]
    if not ordered:
        return jsonify(ok=False, message="所选项目均不存在。"), 400

    data_bytes = _projects_export_xlsx_bytes(ordered)
    stamp = datetime.now(timezone.utc).astimezone(_CN_TZ).strftime("%Y%m%d_%H%M%S")
    filename = f"projects_export_{stamp}.xlsx"
    return Response(
        data_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data_bytes)),
        },
    )


@admin_bp.route("/project-initiation", methods=["GET"])
@login_required
def project_initiation():
    """项目立项列表页（GET-only）：创建走 JSON 接口 admin.projects_create。"""
    _ensure_admin()
    q_obj, name_q, code_q, customer_id_filter, created_from_raw, created_to_raw, created_by_raw = (
        _admin_projects_filtered_query()
    )

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    per_page_raw = request.args.get("per_page", "20").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() else 20
    if per_page not in (10, 20, 50):
        per_page = 20

    pagination = (
        q_obj.options(joinedload(Project.customer), joinedload(Project.created_by))
        .order_by(Project.created_at.desc(), Project.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    page_project_ids = [p.id for p in pagination.items]
    project_case_counts: dict[int, int] = dict.fromkeys(page_project_ids, 0)
    if page_project_ids:
        for pid, cnt in (
            db.session.query(Case.project_id, func.count(Case.id))
            .filter(Case.project_id.in_(page_project_ids))
            .group_by(Case.project_id)
            .all()
        ):
            project_case_counts[int(pid)] = int(cnt)

    customer_options = Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all()
    creator_options = User.query.filter(User.role.in_(["admin", "staff"])).order_by(User.username.asc()).all()

    filter_url_kwargs = _projects_filter_url_kwargs(
        name_q=name_q,
        code_q=code_q,
        customer_id_filter=customer_id_filter,
        created_from=created_from_raw,
        created_to=created_to_raw,
        created_by=created_by_raw,
        per_page=per_page,
    )

    return render_spa_or_full(
        full_template="admin/project_initiation.html",
        inner_template="admin/snippets/project_initiation_inner.html",
        spa_endpoint="admin.project_initiation",
        spa_document_title="项目立项 — 琴岳专利管理系统",
        page_title="项目立项",
        page_desc="创建项目、分配负责人并设置预算时间与里程碑。",
        projects=pagination.items,
        pagination=pagination,
        project_case_counts=project_case_counts,
        customers=customer_options,
        q=name_q,
        code_q=code_q,
        customer_id_filter=customer_id_filter,
        created_from=created_from_raw,
        created_to=created_to_raw,
        created_by_filter=created_by_raw,
        per_page=per_page,
        filter_url_kwargs=filter_url_kwargs,
        creator_options=creator_options,
    )


@admin_bp.route("/customer-detail/<int:customer_id>")
@login_required
def customer_detail(customer_id: int):
    """客户详情页：展示基础信息及该客户名下项目列表。"""
    _ensure_admin()
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)

    projects = (
        Project.query.filter(Project.customer_id == customer.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
        .all()
    )
    return render_spa_or_full(
        full_template="admin/customer_detail.html",
        inner_template="admin/snippets/customer_detail_inner.html",
        spa_endpoint="admin.customer_detail",
        spa_document_title="客户详情 — 琴岳专利管理系统",
        page_title="客户详情",
        page_desc="查看客户基础信息及其名下项目。",
        customer=customer,
        projects=projects,
        customer_kind_company=CustomerKind.COMPANY,
    )


@admin_bp.route("/customer-edit/<int:customer_id>", methods=["GET", "POST"])
@login_required
def customer_edit(customer_id: int):
    """编辑客户：GET 渲染表单；POST 校验并写入名称/类型/备注。"""
    _ensure_admin()
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        kind = request.form.get("kind", CustomerKind.COMPANY)
        note = request.form.get("note", "").strip()
        if not name:
            return redirect(
                url_for(
                    "admin.customer_edit",
                    customer_id=customer.id,
                    qy_toast="客户名称不能为空。",
                    qy_toast_variant="warning",
                )
            )
        if kind not in {CustomerKind.COMPANY, CustomerKind.INDIVIDUAL}:
            kind = CustomerKind.COMPANY
        customer.name = name
        customer.kind = kind
        customer.note = note or None
        db.session.commit()
        return redirect(
            url_for(
                "admin.customer_detail",
                customer_id=customer.id,
                qy_toast="客户信息已保存。",
                qy_toast_variant="success",
            )
        )

    return render_spa_or_full(
        full_template="admin/customer_edit.html",
        inner_template="admin/snippets/customer_edit_inner.html",
        spa_endpoint="admin.customer_edit",
        spa_document_title="编辑客户 — 琴岳专利管理系统",
        page_title="编辑客户",
        page_desc="维护客户名称、类型和备注。",
        customer=customer,
        customer_kind_company=CustomerKind.COMPANY,
        customer_kind_individual=CustomerKind.INDIVIDUAL,
    )


@admin_bp.route("/project-edit/<int:project_id>", methods=["GET", "POST"])
@login_required
def project_edit(project_id: int):
    """编辑项目：GET 渲染表单；POST 校验客户、名称、截止时间后保存。"""
    _ensure_admin()
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)

    if request.method == "POST":
        customer_id_raw = request.form.get("customer_id", "").strip()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        initiated_at_raw = request.form.get("initiated_at", "")
        due_at_raw = request.form.get("due_at", "")

        if not name:
            return redirect_with_qy_toast("admin.project_edit", "项目名称不能为空。", "warning", project_id=project.id)
        if len(name) > 200:
            return redirect_with_qy_toast(
                "admin.project_edit", "项目名称不能超过 200 个字符", "warning", project_id=project.id
            )
        if description and len(description) > 1000:
            return redirect_with_qy_toast(
                "admin.project_edit", "项目说明不能超过 1000 字", "warning", project_id=project.id
            )
        if not customer_id_raw.isdigit():
            return redirect_with_qy_toast("admin.project_edit", "请选择归属客户。", "warning", project_id=project.id)
        customer = db.session.get(Customer, int(customer_id_raw))
        if customer is None:
            return redirect_with_qy_toast(
                "admin.project_edit", "客户不存在，请刷新后重试。", "danger", project_id=project.id
            )
        try:
            due_at = _parse_due_at(due_at_raw)
        except ValueError:
            return redirect_with_qy_toast("admin.project_edit", "截止时间格式不正确。", "warning", project_id=project.id)
        try:
            initiated_at = _parse_initiated_at(initiated_at_raw)
        except ValueError as exc:
            if str(exc) == "future":
                return redirect_with_qy_toast(
                    "admin.project_edit", "立项时间不能晚于当前时间。", "warning", project_id=project.id
                )
            return redirect_with_qy_toast("admin.project_edit", "立项时间格式不正确。", "warning", project_id=project.id)

        project.customer_id = customer.id
        project.name = name
        project.description = description or None
        project.due_at = due_at
        project.initiated_at = initiated_at
        db.session.commit()
        return redirect_with_qy_toast("admin.project_initiation", "项目已更新。", "success")

    due_at_input = _dt_input_value(project.due_at)
    initiated_at_input = _dt_input_value(_project_initiated_at(project))
    initiated_at_max_input = _dt_input_value(datetime.now(timezone.utc))
    customer_options = Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all()
    return render_spa_or_full(
        full_template="admin/project_edit.html",
        inner_template="admin/snippets/project_edit_inner.html",
        spa_endpoint="admin.project_edit",
        spa_document_title="编辑项目 — 琴岳专利管理系统",
        page_title="编辑项目",
        page_desc="维护项目基础信息、归属客户和时间节点。",
        project=project,
        customers=customer_options,
        due_at_input=due_at_input,
        initiated_at_input=initiated_at_input,
        initiated_at_max_input=initiated_at_max_input,
    )


@admin_bp.route("/project-detail/<int:project_id>")
@login_required
def project_detail(project_id: int):
    """项目详情：展示项目基础信息、关联案件与最近任务动态。"""
    _ensure_admin()
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)

    case_count = project.cases.count()
    cases = project.cases.order_by(Case.created_at.desc(), Case.id.desc()).all()
    recent_tasks = (
        Task.query.join(Task.case)
        .filter(Case.project_id == project.id)
        .order_by(Task.updated_at.desc(), Task.id.desc())
        .limit(5)
        .all()
    )
    return render_spa_or_full(
        full_template="admin/project_detail.html",
        inner_template="admin/snippets/project_detail_inner.html",
        spa_endpoint="admin.project_detail",
        spa_document_title="项目详情 — 琴岳专利管理系统",
        page_title="项目详情",
        page_desc="查看项目信息、关联案件和最近任务动态。",
        project=project,
        case_count=case_count,
        cases=cases,
        recent_tasks=recent_tasks,
    )


class _SimplePagination:
    """轻量分页对象，供模板复用 iter_pages / has_prev 等属性。"""

    def __init__(self, *, page: int, per_page: int, total: int) -> None:
        self.per_page = per_page
        self.total = total
        self.pages = max(1, ceil(total / per_page)) if total else 1
        self.page = min(max(page, 1), self.pages)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int | None:
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self) -> int | None:
        return self.page + 1 if self.has_next else None

    def iter_pages(
        self,
        *,
        left_edge: int = 1,
        left_current: int = 2,
        right_current: int = 2,
        right_edge: int = 1,
    ):
        if self.pages <= 0:
            return
        last = 0
        for num in range(1, self.pages + 1):
            if (
                num <= left_edge
                or (self.page - left_current - 1 < num < self.page + right_current)
                or num > self.pages - right_edge
            ):
                if last + 1 != num:
                    yield None
                yield num
                last = num


@admin_bp.route("/cases")
@login_required
def cases():
    """案件列表：管理员维度的案件总览（按客户/项目/关键词筛选）。"""
    _ensure_admin()
    keyword = request.args.get("q", "").strip()
    customer_id_raw = request.args.get("customer_id", "").strip()
    project_id_raw = request.args.get("project_id", "").strip()
    assignee_filter = request.args.get("assignee", "").strip()
    case_type_primary = request.args.get("case_type_primary", "").strip()
    created_year_raw = request.args.get("created_year", "").strip()
    created_month_raw = request.args.get("created_month", "").strip()
    actual_return_year_raw = request.args.get("actual_return_year", "").strip()
    actual_return_month_raw = request.args.get("actual_return_month", "").strip()

    q = (
        Case.query.options(
            joinedload(Case.business_owner_user),
            joinedload(Case.task),
            joinedload(Case.project).joinedload(Project.customer),
        )
        .join(Case.project)
        .join(Project.customer)
    )
    if keyword:
        q = q.filter(
            Case.title.contains(keyword)
            | Case.application_no.contains(keyword)
            | Case.patent_application_no.contains(keyword)
        )
    project_id_filter = ""
    if project_id_raw.isdigit():
        project_id_filter = project_id_raw
        q = q.filter(Case.project_id == int(project_id_raw))
    customer_id_filter = ""
    if customer_id_raw.isdigit():
        customer_id_filter = customer_id_raw
        q = q.filter(Project.customer_id == int(customer_id_raw))
    if assignee_filter == "unassigned":
        q = q.filter(Case.business_owner_id.is_(None))
    elif assignee_filter == "assigned":
        q = q.filter(Case.business_owner_id.isnot(None))
    else:
        assignee_filter = ""
    valid_primary_codes = {value for value, _label in CASE_TYPE_PRIMARY_OPTIONS}
    if case_type_primary in valid_primary_codes:
        q = q.filter(
            db.or_(
                Case.case_type_code.in_(codes_for_primary(case_type_primary)),
                Case.project_type.in_(legacy_values_for_primary(case_type_primary)),
            )
        )
    else:
        case_type_primary = ""
    created_year = ""
    created_month = ""
    actual_return_year = ""
    actual_return_month = ""
    if created_year_raw.isdigit() and created_month_raw.isdigit():
        parsed_year, parsed_month = case_statistics_month(created_year_raw, created_month_raw)
        if str(parsed_year) == created_year_raw and str(parsed_month) == str(int(created_month_raw)):
            start_at, end_at = case_statistics_month_bounds(parsed_year, parsed_month)
            q = q.filter(Case.created_at >= start_at, Case.created_at < end_at)
            created_year = str(parsed_year)
            created_month = str(parsed_month)
    elif actual_return_year_raw.isdigit() and actual_return_month_raw.isdigit():
        parsed_year, parsed_month = case_statistics_month(
            actual_return_year_raw,
            actual_return_month_raw,
        )
        if (
            str(parsed_year) == actual_return_year_raw
            and str(parsed_month) == str(int(actual_return_month_raw))
        ):
            start_at, end_at = case_statistics_month_bounds(parsed_year, parsed_month)
            q = q.filter(
                Case.actual_return_at >= start_at,
                Case.actual_return_at < end_at,
            )
            actual_return_year = str(parsed_year)
            actual_return_month = str(parsed_month)

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    per_page_raw = request.args.get("per_page", "10").strip()
    per_page = int(per_page_raw) if per_page_raw.isdigit() else 10
    if per_page not in (5, 10, 20):
        per_page = 10

    cases_total = q.count()
    project_rows = (
        q.with_entities(Case.project_id, Customer.name, Project.name)
        .group_by(Case.project_id, Customer.name, Project.name)
        .order_by(Customer.name.asc(), Project.name.asc(), Case.project_id.asc())
        .all()
    )
    ordered_project_ids = [row[0] for row in project_rows]
    projects_total = len(ordered_project_ids)
    pagination = _SimplePagination(page=page, per_page=per_page, total=projects_total)
    page = pagination.page
    start = (page - 1) * per_page
    page_project_ids = ordered_project_ids[start : start + per_page]

    if page_project_ids:
        case_list = (
            q.filter(Case.project_id.in_(page_project_ids))
            .order_by(Case.created_at.desc(), Case.id.desc())
            .all()
        )
    else:
        case_list = []

    cases_by_project: dict[int, list[Case]] = {}
    for case_item in case_list:
        cases_by_project.setdefault(case_item.project_id, []).append(case_item)

    case_groups: list[dict] = []
    for project_id in page_project_ids:
        items = cases_by_project.get(project_id, [])
        if items:
            items.sort(key=lambda c: (c.created_at is not None, c.created_at or datetime.min, c.id), reverse=True)
            case_groups.append({"project": items[0].project, "cases": items})
    page_cases_total = len(case_list)

    filter_url_kwargs: dict = {}
    if keyword:
        filter_url_kwargs["q"] = keyword
    if project_id_filter:
        filter_url_kwargs["project_id"] = project_id_filter
    if customer_id_filter:
        filter_url_kwargs["customer_id"] = customer_id_filter
    if assignee_filter:
        filter_url_kwargs["assignee"] = assignee_filter
    if case_type_primary:
        filter_url_kwargs["case_type_primary"] = case_type_primary
    if created_year and created_month:
        filter_url_kwargs["created_year"] = created_year
        filter_url_kwargs["created_month"] = created_month
    if actual_return_year and actual_return_month:
        filter_url_kwargs["actual_return_year"] = actual_return_year
        filter_url_kwargs["actual_return_month"] = actual_return_month
    if per_page != 10:
        filter_url_kwargs["per_page"] = str(per_page)

    project_options_q = Project.query.order_by(Project.name.asc(), Project.id.asc())
    all_project_options = project_options_q.all()
    if customer_id_filter:
        project_options = [p for p in all_project_options if str(p.customer_id) == customer_id_filter]
    else:
        project_options = all_project_options
    project_filter_options = [
        {"id": p.id, "name": p.name, "customer_id": p.customer_id}
        for p in all_project_options
    ]
    customer_options = Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all()
    filtered_project = None
    filtered_customer = None
    if customer_id_filter:
        filtered_customer = db.session.get(Customer, int(customer_id_filter))
    if project_id_raw.isdigit():
        filtered_project = db.session.get(Project, int(project_id_raw))
    return render_spa_or_full(
        full_template="admin/cases.html",
        inner_template="admin/snippets/cases_inner.html",
        spa_endpoint="admin.cases",
        spa_document_title="案件列表 — 琴岳专利管理系统",
        page_title="案件列表",
        page_desc="查看和检索全部案件，追踪所属项目与任务状态。按所属项目分条展示，便于检索。",
        cases=case_list,
        case_groups=case_groups,
        projects=project_options,
        project_filter_options=project_filter_options,
        customers=customer_options,
        q=keyword,
        customer_id_filter=customer_id_filter,
        project_id_filter=project_id_filter,
        assignee_filter=assignee_filter,
        case_type_primary=case_type_primary,
        case_type_primary_options=CASE_TYPE_PRIMARY_OPTIONS,
        created_year=created_year,
        created_month=created_month,
        actual_return_year=actual_return_year,
        actual_return_month=actual_return_month,
        filtered_project=filtered_project,
        filtered_customer=filtered_customer,
        cases_total=cases_total,
        projects_total=projects_total,
        page_cases_total=page_cases_total,
        pagination=pagination,
        per_page=per_page,
        filter_url_kwargs=filter_url_kwargs,
    )


@admin_bp.route("/cases/bulk-delete", methods=["POST"])
@login_required
def cases_bulk_delete():
    """JSON 批量删除案件；从属任务、材料与留痕记录随案件删除。"""
    _ensure_admin()
    if not request.is_json:
        return jsonify(ok=False, message="请使用 JSON 提交。"), 415
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, message="请选择要删除的案件。"), 400
    try:
        deleted, skipped = _bulk_delete_cases_by_ids(ids)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("cases_bulk_delete")
        return jsonify(ok=False, message="删除失败：存在未处理的外键约束。"), 500
    except Exception:
        db.session.rollback()
        current_app.logger.exception("cases_bulk_delete")
        return jsonify(ok=False, message="删除失败，请稍后重试。"), 500
    for case_id in deleted:
        try:
            shutil.rmtree(case_material_dir(case_id), ignore_errors=True)
        except OSError:
            current_app.logger.exception("cases_bulk_delete_file_cleanup case_id=%s", case_id)
    return jsonify(ok=True, deleted=deleted, skipped=skipped, message=f"已删除 {len(deleted)} 条。")


@admin_bp.route("/case-create", methods=["GET", "POST"])
@login_required
def case_create():
    """新建案件：GET 渲染表单；POST 自动生成序列号并创建对应任务。"""
    _ensure_admin()
    project_id_raw = request.args.get("project_id", "").strip()
    if request.method == "POST":
        project_id_raw = request.form.get("project_id", "").strip()
        title = request.form.get("title", "").strip()
        formal_status = request.form.get("formal_status", "").strip()
        case_type_code = request.form.get("case_type_code", "").strip()
        business_owner_id_raw = request.form.get("business_owner_id", "").strip()
        case_note = request.form.get("case_note", "").strip()
        material_upload_port = request.form.get("material_upload_port", "").strip()
        patent_application_no = request.form.get("patent_application_no", "").strip()
        order_at_raw = request.form.get("order_at", "")
        expected_return_at_raw = request.form.get("expected_return_at", "")
        actual_return_at_raw = request.form.get("actual_return_at", "")
        phase_status = request.form.get("phase_status", TaskPhase.IN_PROGRESS)

        if not project_id_raw.isdigit():
            return redirect_with_qy_toast("admin.case_create", "请选择所属项目。", "warning")
        project = db.session.get(Project, int(project_id_raw))
        if project is None:
            return redirect_with_qy_toast("admin.case_create", "项目不存在，请刷新后重试。", "danger")
        if not title:
            return redirect_with_qy_toast("admin.case_create", "案件标题不能为空。", "warning", project_id=project.id)
        case_type_leaf, case_type_err = validate_case_type_code(case_type_code)
        if case_type_err:
            return redirect_with_qy_toast(
                "admin.case_create", case_type_err, "warning", project_id=project.id
            )
        bo_err: str | None
        business_owner_id, bo_err = _business_owner_id_from_form(business_owner_id_raw)
        if bo_err:
            return redirect_with_qy_toast("admin.case_create", bo_err, "warning", project_id=project.id)
        phase_status = resolve_case_task_phase(business_owner_id, phase_status)
        try:
            order_at = _parse_due_at(order_at_raw)
            expected_return_at = _parse_due_at(expected_return_at_raw)
            actual_return_at = _parse_due_at(actual_return_at_raw)
        except ValueError:
            return redirect_with_qy_toast(
                "admin.case_create", "下单/返稿时间格式不正确。", "warning", project_id=project.id
            )

        created_at = datetime.now(timezone.utc)
        try:
            application_no = _next_case_serial(created_at)
        except ValueError as exc:
            return redirect_with_qy_toast("admin.case_create", str(exc), "warning", project_id=project.id)
        case = Case(
            project_id=project.id,
            title=title,
            application_no=application_no,
            formal_status=formal_status or None,
            case_type_code=case_type_leaf.code,
            business_owner_id=business_owner_id,
            order_at=order_at,
            expected_return_at=expected_return_at,
            actual_return_at=actual_return_at,
            case_note=case_note or None,
            material_upload_port=material_upload_port or None,
            patent_application_no=patent_application_no or None,
            created_at=created_at,
        )
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=phase_status, assignee_id=business_owner_id)
        if phase_status == TaskPhase.COMPLETED:
            ok, err = _resolve_actual_return_for_completed(
                case,
                actual_return_at_raw=actual_return_at_raw,
            )
            if not ok:
                db.session.rollback()
                return redirect_with_qy_toast(
                    "admin.case_create", err, "warning", project_id=project.id
                )
        apply_task_overdue_status(task)
        db.session.add(task)
        _notify_case_assigned(case, None, business_owner_id)
        db.session.commit()

        saved_count = 0
        attach_errors: list[str] = []
        for file_storage in request.files.getlist("attachments"):
            if file_storage is None or not (file_storage.filename or "").strip():
                continue
            material, upload_err = save_case_material_upload(case.id, file_storage, current_user.id, "draft", "")
            if material is not None:
                saved_count += 1
            else:
                attach_errors.append(f"{file_storage.filename}：{upload_err or '上传失败'}")

        msg = "案件已创建。"
        variant = "success"
        if saved_count:
            msg += f" 已上传 {saved_count} 个附件。"
        if attach_errors:
            msg += " 以下附件未上传：" + "；".join(attach_errors[:3]) + ("…" if len(attach_errors) > 3 else "")
            variant = "secondary"
        return redirect_with_qy_toast("admin.case_detail", msg, variant, case_id=case.id)

    project_options = Project.query.order_by(Project.name.asc(), Project.id.asc()).all()
    initial_project_id = ""
    if project_id_raw.isdigit():
        initial_project_id = project_id_raw
    phase_options = list(ADMIN_CASE_PHASE_OPTIONS)
    staff_formal, staff_outsource = _staff_users_partitioned()
    return render_spa_or_full(
        full_template="admin/case_create.html",
        inner_template="admin/snippets/case_create_inner.html",
        spa_endpoint="admin.case_create",
        spa_document_title="新建案件 — 琴岳专利管理系统",
        page_title="新建案件",
        page_desc="选择项目并录入案件基础信息，系统自动创建任务。",
        projects=project_options,
        staff_users=staff_formal + staff_outsource,
        staff_users_formal=staff_formal,
        staff_users_outsource=staff_outsource,
        initial_project_id=initial_project_id,
        phase_options=phase_options,
        case_type_config=CASE_TYPE_UI_CONFIG,
        case_type_selected="",
        default_phase=TaskPhase.PENDING_ASSIGNMENT,
        order_at_input="",
        expected_return_at_input="",
        actual_return_at_input="",
    )


@admin_bp.route("/case-detail/<int:case_id>", methods=["GET", "POST"])
@login_required
def case_detail(case_id: int):
    """案件详情：GET 渲染审核/材料/留痕；POST 处理审核动作与材料上传。"""
    _ensure_admin()
    case = Case.query.options(joinedload(Case.business_owner_user)).filter_by(id=case_id).first()
    if case is None:
        abort(404)
    task = case.task
    latest_reject_log = (
        CaseReviewLog.query.filter_by(case_id=case.id, action="reject")
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .first()
    )
    review_action_filter = request.args.get("review_action", "all").strip()
    if review_action_filter not in {"all", "approve", "reject"}:
        review_action_filter = "all"
    review_operator_filter = request.args.get("review_operator", "all").strip()
    material_version_filter = request.args.get("material_version", "all").strip()
    if material_version_filter not in {"all", "draft", "final"}:
        material_version_filter = "all"
    download_role_filter = request.args.get("download_role", "all").strip()
    if download_role_filter not in {"all", "admin", "staff", "client"}:
        download_role_filter = "all"
    download_page_raw = request.args.get("download_page", "1").strip()
    download_page = int(download_page_raw) if download_page_raw.isdigit() and int(download_page_raw) > 0 else 1
    review_page_raw = request.args.get("review_page", "1").strip()
    review_page = int(review_page_raw) if review_page_raw.isdigit() and int(review_page_raw) > 0 else 1
    review_logs_query = CaseReviewLog.query.filter(
        CaseReviewLog.case_id == case.id,
        CaseReviewLog.action.in_(["approve", "reject"]),
    )
    if review_action_filter in {"approve", "reject"}:
        review_logs_query = review_logs_query.filter(CaseReviewLog.action == review_action_filter)
    operator_options = [
        row[0]
        for row in db.session.query(User.username)
        .join(CaseReviewLog, CaseReviewLog.operator_id == User.id)
        .filter(
            CaseReviewLog.case_id == case.id,
            CaseReviewLog.action.in_(["approve", "reject"]),
        )
        .distinct()
        .order_by(User.username.asc())
        .all()
    ]
    if review_operator_filter != "all":
        if review_operator_filter in operator_options:
            operator_user = User.query.filter_by(username=review_operator_filter).first()
            if operator_user is not None:
                review_logs_query = review_logs_query.filter(CaseReviewLog.operator_id == operator_user.id)
        else:
            review_operator_filter = "all"
    review_logs_pagination = (
        review_logs_query
        .order_by(CaseReviewLog.created_at.desc(), CaseReviewLog.id.desc())
        .paginate(page=review_page, per_page=10, error_out=False)
    )
    download_logs_pagination = _case_material_download_rows(case.id, download_role_filter, download_page)
    if request.method == "POST":
        if request.form.get("form_action", "") == "upload_material":
            material_file = request.files.get("material_file")
            version_tag = request.form.get("material_version_tag", "draft").strip()
            if version_tag not in {"draft", "final"}:
                version_tag = "draft"
            material_note = request.form.get("material_note", "").strip() or "交底材料"
            material_saved, upload_err = save_case_material_upload(
                case.id, material_file, current_user.id, version_tag, material_note
            )
            if material_saved is not None:
                return redirect_with_qy_toast("admin.case_detail", "交底材料已上传。", "success", case_id=case.id)
            return redirect_with_qy_toast(
                "admin.case_detail", upload_err or "上传失败。", "warning", case_id=case.id
            )
        if request.form.get("form_action", "") == "delete_material":
            material_id_raw = request.form.get("material_id", "").strip()
            if not material_id_raw.isdigit():
                return redirect_with_qy_toast("admin.case_detail", "删除参数不合法。", "warning", case_id=case.id)
            material = db.session.get(CaseMaterial, int(material_id_raw))
            if material is None or material.case_id != case.id:
                return redirect_with_qy_toast("admin.case_detail", "目标材料不存在。", "warning", case_id=case.id)
            file_path = case_material_dir(case.id) / material.stored_name
            db.session.delete(material)
            db.session.commit()
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                current_app.logger.exception(
                    "case_material_file_cleanup case_id=%s material_id=%s",
                    case.id,
                    material_id_raw,
                )
            return redirect_with_qy_toast("admin.case_detail", "材料已删除。", "success", case_id=case.id)
        if request.form.get("form_action", "") == "update_phase":
            if task is None:
                return redirect_with_qy_toast("admin.case_detail", "该案件暂无任务，无法修改状态。", "warning", case_id=case.id)
            phase_status = request.form.get("phase_status", "").strip()
            if phase_status not in ADMIN_CASE_PHASE_OPTIONS:
                return redirect_with_qy_toast("admin.case_detail", "状态不合法。", "warning", case_id=case.id)
            previous_actual = case.actual_return_at
            new_phase = resolve_case_task_phase(case.business_owner_id, phase_status)
            if new_phase == TaskPhase.COMPLETED:
                ok, err = _resolve_actual_return_for_completed(
                    case,
                    actual_return_at_raw=request.form.get("actual_return_at", ""),
                    previous_actual_return_at=previous_actual,
                )
                if not ok:
                    db.session.rollback()
                    return redirect_with_qy_toast("admin.case_detail", err, "warning", case_id=case.id)
            task.phase_status = new_phase
            db.session.commit()
            return redirect_with_qy_toast("admin.case_detail", "案件状态已更新。", "success", case_id=case.id)
        action = request.form.get("review_action", "").strip()
        if not action:
            return redirect_with_qy_toast("admin.case_detail", "不支持的操作。", "warning", case_id=case.id)
        _ok, message, variant = _apply_admin_review(
            case,
            task,
            action,
            request.form.get("reject_note", "").strip(),
        )
        return redirect_with_qy_toast(
            "admin.case_detail",
            message,
            variant,
            case_id=case.id,
        )
    disclosure_material_files, writing_material_files = fetch_case_materials_grouped(
        case.id, material_version_filter
    )
    phase_options = list(ADMIN_CASE_PHASE_OPTIONS)
    phase_selected_value = None
    if task is not None:
        phase_selected_value = (
            task.phase_status if is_terminal_phase(task.phase_status) else phase_for_workflow(task.phase_status)
        )
    return render_spa_or_full(
        full_template="admin/case_detail.html",
        inner_template="admin/snippets/case_detail_inner.html",
        spa_endpoint="admin.case_detail",
        spa_document_title="案件详情 — 琴岳专利管理系统",
        page_title="案件详情",
        page_desc="查看案件基础信息与当前任务状态。",
        case=case,
        task=task,
        phase_options=phase_options,
        phase_selected_value=phase_selected_value,
        actual_return_at_input=_dt_input_value(case.actual_return_at),
        latest_reject_log=latest_reject_log,
        review_logs=review_logs_pagination.items,
        review_page=review_page,
        review_total_pages=review_logs_pagination.pages,
        review_action_filter=review_action_filter,
        review_operator_filter=review_operator_filter,
        review_operator_options=operator_options,
        disclosure_material_files=disclosure_material_files,
        writing_material_files=writing_material_files,
        material_version_filter=material_version_filter,
        material_download_logs=download_logs_pagination.items,
        download_role_filter=download_role_filter,
        download_page=download_page,
        download_total_pages=download_logs_pagination.pages,
    )


@admin_bp.route("/case-material/<int:case_id>/<int:material_id>")
@login_required
def case_material_download(case_id: int, material_id: int):
    """管理员下载案件材料：写入下载留痕后返回文件流。"""
    _ensure_admin()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    material = db.session.get(CaseMaterial, material_id)
    if material is None or material.case_id != case.id:
        abort(404)
    db.session.add(
        CaseMaterialDownloadLog(
            case_id=case.id,
            material_id=material.id,
            operator_id=current_user.id,
            operator_role=current_user.role,
        )
    )
    db.session.commit()
    return send_from_directory(
        case_material_dir(case_id),
        material.stored_name,
        as_attachment=True,
        download_name=material.original_name,
    )


@admin_bp.route("/case-material-download-logs-export/<int:case_id>")
@login_required
def case_material_download_logs_export(case_id: int):
    """导出案件材料下载留痕为 CSV，可按角色（admin/staff/client）过滤。"""
    _ensure_admin()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    download_role_filter = request.args.get("download_role", "all").strip()
    if download_role_filter not in {"all", "admin", "staff", "client"}:
        download_role_filter = "all"
    rows = _case_material_download_query(case.id, download_role_filter).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["time_beijing", "operator", "role", "file_name"])
    for item in rows:
        writer.writerow(
            [
                _project_datetime_export_utc(item.created_at) if item.created_at else "",
                item.operator.username if item.operator else "",
                item.operator_role,
                item.material.original_name if item.material else "",
            ]
        )
    filename = f"case_{case.id}_download_logs_{download_role_filter}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@admin_bp.route("/case-edit/<int:case_id>", methods=["GET", "POST"])
@login_required
def case_edit(case_id: int):
    """编辑案件：GET 渲染表单；POST 校验时间字段并保存。"""
    _ensure_admin()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    task = case.task

    if request.method == "POST":
        project_id_raw = request.form.get("project_id", "").strip()
        title = request.form.get("title", "").strip()
        formal_status = request.form.get("formal_status", "").strip()
        case_type_code = request.form.get("case_type_code", "").strip()
        business_owner_id_raw = request.form.get("business_owner_id", "").strip()
        case_note = request.form.get("case_note", "").strip()
        material_upload_port = request.form.get("material_upload_port", "").strip()
        patent_application_no = request.form.get("patent_application_no", "").strip()
        order_at_raw = request.form.get("order_at", "")
        expected_return_at_raw = request.form.get("expected_return_at", "")
        actual_return_at_raw = request.form.get("actual_return_at", "")
        phase_status = request.form.get("phase_status", TaskPhase.IN_PROGRESS)

        if not project_id_raw.isdigit():
            return redirect_with_qy_toast("admin.case_edit", "请选择所属项目。", "warning", case_id=case.id)
        project = db.session.get(Project, int(project_id_raw))
        if project is None:
            return redirect_with_qy_toast("admin.case_edit", "所属项目不存在，请刷新后重试。", "danger", case_id=case.id)
        if not title:
            return redirect_with_qy_toast("admin.case_edit", "案件标题不能为空。", "warning", case_id=case.id)
        case_type_leaf, case_type_err = validate_case_type_code(case_type_code)
        if case_type_err:
            return redirect_with_qy_toast(
                "admin.case_edit", case_type_err, "warning", case_id=case.id
            )
        business_owner_id, bo_err = _business_owner_id_from_form(business_owner_id_raw)
        if bo_err:
            return redirect_with_qy_toast("admin.case_edit", bo_err, "warning", case_id=case.id)
        phase_status = resolve_case_task_phase(business_owner_id, phase_status)
        try:
            order_at = _parse_due_at(order_at_raw)
            expected_return_at = _parse_due_at(expected_return_at_raw)
            actual_return_at = _parse_due_at(actual_return_at_raw)
        except ValueError:
            return redirect_with_qy_toast("admin.case_edit", "下单/返稿时间格式不正确。", "warning", case_id=case.id)

        previous_actual = case.actual_return_at
        case.project_id = project.id
        case.title = title
        case.formal_status = formal_status or None
        case.case_type_code = case_type_leaf.code
        case.business_owner_id = business_owner_id
        case.order_at = order_at
        case.expected_return_at = expected_return_at
        # 留空表示保留已有值；清空业务时间必须通过专门、带确认的操作完成。
        if actual_return_at_raw.strip():
            case.actual_return_at = actual_return_at
        case.case_note = case_note or None
        case.material_upload_port = material_upload_port or None
        case.patent_application_no = patent_application_no or None
        old_assignee_id = task.assignee_id if task is not None else None
        if task is None:
            task = Task(case_id=case.id, phase_status=phase_status, assignee_id=business_owner_id)
            db.session.add(task)
        else:
            task.phase_status = phase_status
            task.assignee_id = business_owner_id
        if task.phase_status == TaskPhase.COMPLETED:
            ok, err = _resolve_actual_return_for_completed(
                case,
                actual_return_at_raw=actual_return_at_raw,
                previous_actual_return_at=previous_actual,
            )
            if not ok:
                db.session.rollback()
                return redirect_with_qy_toast("admin.case_edit", err, "warning", case_id=case.id)
        apply_task_overdue_status(task)
        _notify_case_assigned(case, old_assignee_id, business_owner_id)
        db.session.commit()

        saved_count = 0
        attach_errors: list[str] = []
        for file_storage in request.files.getlist("attachments"):
            if file_storage is None or not (file_storage.filename or "").strip():
                continue
            material, upload_err = save_case_material_upload(case.id, file_storage, current_user.id, "draft", "")
            if material is not None:
                saved_count += 1
            else:
                attach_errors.append(f"{file_storage.filename}：{upload_err or '上传失败'}")

        msg = "案件已更新。"
        variant = "success"
        if saved_count:
            msg += f" 新增 {saved_count} 个附件。"
        if attach_errors:
            msg += " 以下附件未上传：" + "；".join(attach_errors[:3]) + ("…" if len(attach_errors) > 3 else "")
            variant = "secondary"
        return redirect_with_qy_toast("admin.case_detail", msg, variant, case_id=case.id)

    project_options = Project.query.order_by(Project.name.asc(), Project.id.asc()).all()
    phase_options = list(ADMIN_CASE_PHASE_OPTIONS)
    current_phase = task.phase_status if task else TaskPhase.PENDING_ASSIGNMENT
    staff_formal, staff_outsource = _staff_users_partitioned()
    return render_spa_or_full(
        full_template="admin/case_edit.html",
        inner_template="admin/snippets/case_edit_inner.html",
        spa_endpoint="admin.case_edit",
        spa_document_title="编辑案件 — 琴岳专利管理系统",
        page_title="编辑案件",
        page_desc="维护案件基础信息并同步任务状态。",
        case=case,
        task=task,
        projects=project_options,
        staff_users=staff_formal + staff_outsource,
        staff_users_formal=staff_formal,
        staff_users_outsource=staff_outsource,
        phase_options=phase_options,
        case_type_config=CASE_TYPE_UI_CONFIG,
        case_type_selected=normalize_case_type_code(
            case.case_type_code or legacy_case_type_code(case.project_type) or ""
        ),
        current_phase=current_phase,
        order_at_input=_dt_input_value(case.order_at),
        expected_return_at_input=_dt_input_value(case.expected_return_at),
        actual_return_at_input=_dt_input_value(case.actual_return_at),
    )


@admin_bp.route("/case-reassign/<int:case_id>", methods=["POST"])
@login_required
def case_reassign(case_id: int):
    """快速转派：仅更新案件指派员工与任务承办人，便于在看板上直接分单。"""
    _ensure_admin()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    status = request.form.get("status", "all").strip()
    sort = request.form.get("sort", "deadline").strip()
    assignee_id, err = _business_owner_id_from_form(request.form.get("assignee_id"))
    if err:
        return redirect_with_qy_toast("admin.task_board", err, "warning", status=status, sort=sort)
    case.business_owner_id = assignee_id
    task = case.task
    old_assignee_id = task.assignee_id if task is not None else None
    if task is None:
        phase_status = TaskPhase.PENDING_ASSIGNMENT if assignee_id is None else TaskPhase.IN_PROGRESS
        task = Task(case_id=case.id, phase_status=phase_status, assignee_id=assignee_id)
        db.session.add(task)
    else:
        task.assignee_id = assignee_id
        if assignee_id is None:
            task.phase_status = TaskPhase.PENDING_ASSIGNMENT
        elif task.phase_status == TaskPhase.PENDING_ASSIGNMENT:
            task.phase_status = TaskPhase.IN_PROGRESS
    _notify_case_assigned(case, old_assignee_id, assignee_id)
    db.session.commit()
    msg = "已转派给指定员工。" if assignee_id else "已取消指派，案件进入待分配。"
    return redirect_with_qy_toast("admin.task_board", msg, "success", status=status, sort=sort)


@admin_bp.route("/batch-operations")
@login_required
def batch_operations():
    """批量操作说明与入口：指向已支持勾选批量动作的列表页。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/batch_operations.html",
        inner_template="admin/snippets/batch_operations_inner.html",
        spa_endpoint="admin.batch_operations",
        spa_document_title="批量操作 — 琴岳专利管理系统",
        page_title="批量操作",
        page_desc="统一说明批量删除与导出规则，并跳转至客户档案、项目立项与案件列表。",
    )


@admin_bp.route("/smart-assignment")
@login_required
def smart_assignment():
    """智能分案占位页：后续接入员工负载与历史接手数据。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.smart_assignment",
        spa_document_title="智能派单 — 琴岳专利管理系统",
        page_title="智能派单",
        page_desc="按员工负载、技术领域与案件难度推荐分派人选。",
    )


@admin_bp.route("/flow-map")
@login_required
def flow_map():
    """案件统计：按创建时间或实际返稿时间汇总案件类型。"""
    _ensure_admin()
    basis = case_statistics_basis(request.args.get("basis", "").strip())
    year, month = case_statistics_month(
        request.args.get("year", "").strip(),
        request.args.get("month", "").strip(),
    )
    statistics = case_statistics_data(year, month, basis)
    previous_month_end = datetime(year, month, 1) - timedelta(days=1)
    previous = case_statistics_data(
        previous_month_end.year,
        previous_month_end.month,
        basis,
    )
    statistics["previous_total"] = previous["total"]
    statistics["month_change"] = statistics["total"] - previous["total"]
    statistics["month_change_percent"] = (
        round(statistics["month_change"] * 100 / previous["total"], 1)
        if previous["total"]
        else None
    )
    statistics["previous_year"] = previous_month_end.year
    statistics["previous_month"] = previous_month_end.month
    now_cn = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    statistics["current_year"] = now_cn.year
    statistics["current_month"] = now_cn.month
    trend = case_statistics_trend_data(year, month, basis)
    available_years = case_statistics_available_years(basis, selected_year=year)

    return render_spa_or_full(
        full_template="admin/case_statistics.html",
        inner_template="admin/snippets/case_statistics_inner.html",
        spa_endpoint="admin.flow_map",
        spa_document_title="案件统计 — 琴岳专利管理系统",
        page_title="案件统计",
        page_desc="可按创建时间或实际返稿时间统计案件数量与类型构成。",
        statistics=statistics,
        trend=trend,
        available_years=available_years,
    )


@admin_bp.route("/flow-map/export")
@login_required
def case_statistics_export():
    """导出指定月份的案件类型统计 Excel。"""
    _ensure_admin()
    basis = case_statistics_basis(request.args.get("basis", "").strip())
    year, month = case_statistics_month(
        request.args.get("year", "").strip(),
        request.args.get("month", "").strip(),
    )
    statistics = case_statistics_data(year, month, basis)
    return send_file(
        BytesIO(case_statistics_workbook_bytes(statistics)),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{month}月案件统计.xlsx",
    )


@admin_bp.route("/overtime-warning")
@login_required
def overtime_warning():
    """超期预警页：基于 reminder_template_kwargs 提供的已超期/临期清单。"""
    _ensure_admin()
    from app.deadline_calendar import parse_calendar_month

    cal_year, cal_month = parse_calendar_month(
        request.args.get("cal_year", "").strip(),
        request.args.get("cal_month", "").strip(),
    )
    return render_spa_or_full(
        full_template="admin/overtime_warning_page.html",
        inner_template="partials/deadline_reminder_inner.html",
        spa_endpoint="admin.overtime_warning",
        spa_document_title="超时预警看板 — 琴岳专利管理系统",
        **reminder_template_kwargs(
            current_user,
            reminder_page_title="超时预警看板",
            calendar_year=cal_year,
            calendar_month=cal_month,
            selected_day=request.args.get("day", "").strip(),
            calendar_page_endpoint="admin.overtime_warning",
        ),
    )


@admin_bp.route("/review-quality")
@login_required
def review_quality():
    """待审核案件中心：集中查看、筛选、通过或打回案件。"""
    _ensure_admin()
    keyword = request.args.get("q", "").strip()
    case_type_primary = request.args.get("case_type_primary", "").strip()
    assignee_raw = request.args.get("assignee_id", "").strip()
    waiting_filter = request.args.get("waiting", "all").strip()
    urgency_filter = request.args.get("urgency", "all").strip()

    valid_primary_codes = {value for value, _label in CASE_TYPE_PRIMARY_OPTIONS}
    if case_type_primary not in valid_primary_codes:
        case_type_primary = ""
    if waiting_filter not in {"all", "24h", "3d", "7d"}:
        waiting_filter = "all"
    if urgency_filter not in {"all", "overdue"}:
        urgency_filter = "all"

    query = (
        _review_inbox_query()
        .join(Case.project)
        .options(
            joinedload(Task.case).joinedload(Case.project).joinedload(Project.customer),
            joinedload(Task.case).joinedload(Case.business_owner_user),
            joinedload(Task.assignee),
        )
    )
    if keyword:
        query = query.filter(
            db.or_(
                Case.title.contains(keyword),
                Case.application_no.contains(keyword),
                Case.patent_application_no.contains(keyword),
            )
        )
    if case_type_primary:
        query = query.filter(
            db.or_(
                Case.case_type_code.in_(codes_for_primary(case_type_primary)),
                Case.project_type.in_(legacy_values_for_primary(case_type_primary)),
            )
        )
    assignee_id = ""
    if assignee_raw.isdigit():
        assignee_id = assignee_raw
        query = query.filter(Task.assignee_id == int(assignee_raw))

    now_utc = datetime.now(timezone.utc)
    now_naive = now_utc.replace(tzinfo=None)
    waiting_deltas = {
        "24h": timedelta(hours=24),
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
    }
    if waiting_filter in waiting_deltas:
        query = query.filter(
            func.coalesce(Task.updated_at, Task.created_at)
            <= now_naive - waiting_deltas[waiting_filter]
        )
    if urgency_filter == "overdue":
        query = query.filter(
            func.coalesce(Task.due_at, Case.expected_return_at, Project.due_at) < now_naive
        )

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    pagination = query.order_by(
        func.coalesce(Task.updated_at, Task.created_at).desc(),
        Task.id.desc(),
    ).paginate(page=page, per_page=20, error_out=False)

    rows = []
    for task in pagination.items:
        entered_at = task.updated_at or task.created_at
        if entered_at is not None:
            entered_utc = (
                entered_at.replace(tzinfo=timezone.utc)
                if entered_at.tzinfo is None
                else entered_at.astimezone(timezone.utc)
            )
            waiting_seconds = max(0, int((now_utc - entered_utc).total_seconds()))
        else:
            waiting_seconds = 0
        waiting_days, remainder = divmod(waiting_seconds, 86400)
        waiting_hours = remainder // 3600
        waiting_label = (
            f"{waiting_days} 天 {waiting_hours} 小时"
            if waiting_days
            else f"{waiting_hours} 小时"
        )
        due_at = task.due_at or task.case.expected_return_at or task.case.project.due_at
        if due_at is not None:
            due_aware = (
                due_at.replace(tzinfo=timezone.utc)
                if due_at.tzinfo is None
                else due_at.astimezone(timezone.utc)
            )
            is_overdue = due_aware < now_utc
        else:
            is_overdue = False
        rows.append(
            {
                "task": task,
                "case": task.case,
                "entered_at": entered_at,
                "waiting_label": waiting_label,
                "due_at": due_at,
                "is_overdue": is_overdue,
            }
        )

    filter_url_kwargs = {}
    if keyword:
        filter_url_kwargs["q"] = keyword
    if case_type_primary:
        filter_url_kwargs["case_type_primary"] = case_type_primary
    if assignee_id:
        filter_url_kwargs["assignee_id"] = assignee_id
    if waiting_filter != "all":
        filter_url_kwargs["waiting"] = waiting_filter
    if urgency_filter != "all":
        filter_url_kwargs["urgency"] = urgency_filter

    total_pending, _pending_count = _review_inbox_counts(current_user)
    return render_spa_or_full(
        full_template="admin/review_quality.html",
        inner_template="admin/snippets/review_quality_inner.html",
        spa_endpoint="admin.review_quality",
        spa_document_title="多级审核 — 琴岳专利管理系统",
        page_title="多级审核",
        review_rows=rows,
        pagination=pagination,
        total_pending=total_pending,
        q=keyword,
        case_type_primary=case_type_primary,
        case_type_primary_options=CASE_TYPE_PRIMARY_OPTIONS,
        assignee_id=assignee_id,
        assignee_options=_staff_users_ordered(),
        waiting_filter=waiting_filter,
        urgency_filter=urgency_filter,
        filter_url_kwargs=filter_url_kwargs,
    )


@admin_bp.route("/review-quality/status")
@login_required
def review_quality_status():
    """侧边栏轮询：返回待审核总数与待处理数（未审核完前红点不消失）。"""
    _ensure_admin()
    total, unread = _review_inbox_counts(current_user)
    return jsonify(ok=True, total=total, unread=unread)


@admin_bp.route("/review-quality/<int:case_id>/action", methods=["POST"])
@login_required
def review_quality_action(case_id: int):
    """从待审核中心执行通过或打回；提交时再次校验状态防止重复审核。"""
    _ensure_admin()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    _ok, message, variant = _apply_admin_review(
        case,
        case.task,
        request.form.get("review_action", "").strip(),
        request.form.get("reject_note", "").strip(),
    )
    return redirect_with_qy_toast(
        "admin.review_quality",
        message,
        variant,
        **{
            key: request.form.get(key, "").strip()
            for key in ("q", "case_type_primary", "assignee_id", "waiting", "urgency")
            if request.form.get(key, "").strip()
        },
    )


@admin_bp.route("/statutory-deadline")
@login_required
def statutory_deadline():
    """法定期限占位页：后续与官方时间节点联动并触发自动提醒。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.statutory_deadline",
        spa_document_title="官方期限管理 — 琴岳专利管理系统",
        page_title="官方期限管理",
        page_desc="配置法定绝限期并自动生成期限任务。",
    )


@admin_bp.route("/fee-monitor")
@login_required
def fee_monitor():
    """费用监控占位页：后续展示客户/项目维度的费用与回款情况。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.fee_monitor",
        spa_document_title="费用监控 — 琴岳专利管理系统",
        page_title="费用监控",
        page_desc="监控官费与代办费，超预算任务自动标红。",
    )


@admin_bp.route("/official-doc-dispatch")
@login_required
def official_doc_dispatch():
    """官方文件派发占位页：后续承载 OA 文件分发与回执跟踪。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.official_doc_dispatch",
        spa_document_title="官文分发 — 琴岳专利管理系统",
        page_title="官文分发",
        page_desc="接收官方通知并自动匹配案件提醒员工处理。",
    )


@admin_bp.route("/staff-performance")
@login_required
def staff_performance():
    """员工绩效占位页：后续聚合工时、案件量与质量指标。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.staff_performance",
        spa_document_title="员工绩效 — 琴岳专利管理系统",
        page_title="员工绩效",
        page_desc="统计每人完成量、驳回量、授权量与平均处理时长。",
    )


@admin_bp.route("/client-reports")
@login_required
def client_reports():
    """客户报表占位页：后续按客户维度生成进度与结算报表。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.client_reports",
        spa_document_title="客户报表 — 琴岳专利管理系统",
        page_title="客户报表",
        page_desc="按客户展示申请量、授权率与年度花费。",
    )


@admin_bp.route("/profit-analysis")
@login_required
def profit_analysis():
    """利润分析占位页：后续接入费用与工时数据进行毛利测算。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.profit_analysis",
        spa_document_title="利润核算 — 琴岳专利管理系统",
        page_title="利润核算",
        page_desc="按项目计算收入、成本与毛利。",
    )


def _role_permissions_filter_kwargs() -> dict:
    """角色权限页筛选参数：保存后带回当前列表状态（不用表单里的职能字段）。"""
    kwargs: dict = {}
    keyword = (request.form.get("list_q") or request.args.get("q") or "").strip()
    staff_function_filter = (
        request.form.get("list_staff_function") or request.args.get("staff_function") or "all"
    ).strip()
    if keyword:
        kwargs["q"] = keyword
    if staff_function_filter in User.STAFF_FUNCTIONS:
        kwargs["staff_function"] = staff_function_filter
    page_raw = (request.form.get("list_page") or request.args.get("page") or "").strip()
    if page_raw.isdigit() and int(page_raw) > 1:
        kwargs["page"] = page_raw
    return kwargs


@admin_bp.route("/role-permissions", methods=["GET", "POST"])
@login_required
def role_permissions():
    """员工职能维护：为员工账号指定撰写师 / 流程人员 / 业务人员。"""
    _ensure_admin()
    if request.method == "POST":
        user_id_raw = request.form.get("user_id", "").strip()
        redirect_kwargs = _role_permissions_filter_kwargs()
        if not user_id_raw.isdigit():
            return redirect_with_qy_toast(
                "admin.role_permissions", "账号参数不合法。", "warning", **redirect_kwargs
            )
        user = db.session.get(User, int(user_id_raw))
        if user is None or user.role != "staff":
            return redirect_with_qy_toast(
                "admin.role_permissions", "仅员工账号可设置职能。", "warning", **redirect_kwargs
            )
        user.staff_function = User.normalize_staff_function(request.form.get("staff_function", ""))
        db.session.commit()
        return redirect_with_qy_toast(
            "admin.role_permissions",
            f"已将「{user.username}」的职能更新为{user.staff_function_label}。",
            "success",
            **redirect_kwargs,
        )

    keyword = request.args.get("q", "").strip()
    staff_function_filter = request.args.get("staff_function", "all").strip()
    users_q = User.query.filter(User.role == "staff", User.is_active.is_(True))
    if keyword:
        users_q = users_q.filter(
            db.or_(
                User.username.contains(keyword),
                User.phone.contains(keyword),
            )
        )
    if staff_function_filter == User.STAFF_FUNCTION_WRITER:
        users_q = users_q.filter(
            db.or_(
                User.staff_function == User.STAFF_FUNCTION_WRITER,
                User.staff_function.is_(None),
                User.staff_function == "",
            )
        )
    elif staff_function_filter in User.STAFF_FUNCTIONS:
        users_q = users_q.filter(User.staff_function == staff_function_filter)

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    pagination = users_q.order_by(User.created_at.desc(), User.id.desc()).paginate(
        page=page,
        per_page=20,
        error_out=False,
    )
    filter_url_kwargs = _role_permissions_filter_kwargs()
    filter_url_kwargs.pop("page", None)
    return render_spa_or_full(
        full_template="admin/role_permissions.html",
        inner_template="admin/snippets/role_permissions_inner.html",
        spa_endpoint="admin.role_permissions",
        spa_document_title="角色权限 — 琴岳专利管理系统",
        page_title="角色权限",
        page_desc="为员工指定撰写师、流程人员或业务人员；职能决定登录后进入的工作台。账号创建仍在「账号分发」。",
        users=pagination.items,
        pagination=pagination,
        q=keyword,
        staff_function_filter=(
            staff_function_filter if staff_function_filter in User.STAFF_FUNCTIONS else "all"
        ),
        filter_url_kwargs=filter_url_kwargs,
    )


@admin_bp.route("/notification-templates")
@login_required
def notification_templates():
    """通知模板占位页：后续支持邮件/站内消息模板的统一管理。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.notification_templates",
        spa_document_title="通知书模板 — 琴岳专利管理系统",
        page_title="通知书模板",
        page_desc="自定义官方转达函和时限提醒邮件模板。",
    )


@admin_bp.route("/active-cases-library")
@login_required
def active_cases_library():
    """办结案件库 · 在办案件：未完成且已分配，可按截止日排序、员工/客户/项目筛选与分类。"""
    _ensure_admin()
    from app.active_cases_library import active_cases_library_data

    keyword = request.args.get("q", "").strip()
    staff_id_raw = request.args.get("staff_id", "").strip()
    customer_id_raw = request.args.get("customer_id", "").strip()
    project_id_raw = request.args.get("project_id", "").strip()
    sort_by = request.args.get("sort", "deadline").strip()
    group_by = request.args.get("group_by", "none").strip()

    staff_id = int(staff_id_raw) if staff_id_raw.isdigit() else None
    customer_id = int(customer_id_raw) if customer_id_raw.isdigit() else None
    project_id = int(project_id_raw) if project_id_raw.isdigit() else None

    data = active_cases_library_data(
        keyword=keyword,
        staff_id=staff_id,
        customer_id=customer_id,
        project_id=project_id,
        sort_by=sort_by,
        group_by=group_by,
    )

    all_projects = Project.query.order_by(Project.name.asc(), Project.id.asc()).all()
    if customer_id is not None:
        project_options = [p for p in all_projects if p.customer_id == customer_id]
    else:
        project_options = all_projects
    project_filter_options = [
        {"id": p.id, "name": p.name, "customer_id": p.customer_id} for p in all_projects
    ]

    return render_spa_or_full(
        full_template="admin/active_cases_library.html",
        inner_template="admin/snippets/active_cases_library_inner.html",
        spa_endpoint="admin.active_cases_library",
        spa_document_title="在办案件 — 琴岳专利管理系统",
        page_title="在办案件",
        page_desc="展示所有未完成且已分配的案件；默认按截止日期排序，可按负责员工筛选，并按客户或项目分类查看。",
        q=keyword,
        staff_id_filter=str(staff_id) if staff_id is not None else "",
        customer_id_filter=str(customer_id) if customer_id is not None else "",
        project_id_filter=str(project_id) if project_id is not None else "",
        sort_by=data["sort_by"],
        group_by=data["group_by"],
        groups=data["groups"],
        cases_total=data["total"],
        staff_options=_staff_users_ordered(),
        customers=Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all(),
        projects=project_options,
        project_filter_options=project_filter_options,
    )


@admin_bp.route("/completed-cases-library")
@login_required
def completed_cases_library():
    """办结案件库 · 办结案件：全部已完成案件，可按返稿时间排序、员工/客户/项目筛选与分类。"""
    _ensure_admin()
    from app.active_cases_library import completed_cases_library_data

    keyword = request.args.get("q", "").strip()
    staff_id_raw = request.args.get("staff_id", "").strip()
    customer_id_raw = request.args.get("customer_id", "").strip()
    project_id_raw = request.args.get("project_id", "").strip()
    sort_by = request.args.get("sort", "actual_return_desc").strip()
    group_by = request.args.get("group_by", "none").strip()
    missing_return_only = request.args.get("missing_return", "").strip() in {"1", "true", "yes"}

    staff_id = int(staff_id_raw) if staff_id_raw.isdigit() else None
    customer_id = int(customer_id_raw) if customer_id_raw.isdigit() else None
    project_id = int(project_id_raw) if project_id_raw.isdigit() else None

    data = completed_cases_library_data(
        keyword=keyword,
        staff_id=staff_id,
        customer_id=customer_id,
        project_id=project_id,
        sort_by=sort_by,
        group_by=group_by,
        missing_return_only=missing_return_only,
    )

    from app.workflow import LEGACY_TERMINAL_PHASES

    # 未筛选缺漏时，额外统计库内缺返稿时间数量（不受当前列表过滤影响）
    if missing_return_only:
        missing_return_total = data["missing_return_count"]
    else:
        missing_return_total = (
            Task.query.join(Case, Task.case_id == Case.id)
            .filter(
                Task.phase_status.in_(tuple(TaskPhase.TERMINAL | LEGACY_TERMINAL_PHASES)),
                Case.actual_return_at.is_(None),
            )
            .count()
        )

    all_projects = Project.query.order_by(Project.name.asc(), Project.id.asc()).all()
    if customer_id is not None:
        project_options = [p for p in all_projects if p.customer_id == customer_id]
    else:
        project_options = all_projects
    project_filter_options = [
        {"id": p.id, "name": p.name, "customer_id": p.customer_id} for p in all_projects
    ]

    return render_spa_or_full(
        full_template="admin/completed_cases_library.html",
        inner_template="admin/snippets/completed_cases_library_inner.html",
        spa_endpoint="admin.completed_cases_library",
        spa_document_title="办结案件 — 琴岳专利管理系统",
        page_title="办结案件",
        page_desc="展示全部已完成案件；可补填缺失的实际返稿时间。后续将案件改为已完成时必须填写实际返稿时间。",
        q=keyword,
        staff_id_filter=str(staff_id) if staff_id is not None else "",
        customer_id_filter=str(customer_id) if customer_id is not None else "",
        project_id_filter=str(project_id) if project_id is not None else "",
        sort_by=data["sort_by"],
        group_by=data["group_by"],
        missing_return_only=missing_return_only,
        missing_return_total=missing_return_total,
        groups=data["groups"],
        cases_total=data["total"],
        staff_options=_staff_users_ordered(),
        customers=Customer.query.order_by(Customer.name.asc(), Customer.id.asc()).all(),
        projects=project_options,
        project_filter_options=project_filter_options,
    )


@admin_bp.route("/completed-cases-library/set-actual-return", methods=["POST"])
@login_required
def completed_case_set_actual_return():
    """办结案件库：为已完成案件补填或按规则补算实际返稿时间。"""
    _ensure_admin()
    case_id_raw = request.form.get("case_id", "").strip()
    mode = request.form.get("mode", "manual").strip()
    if not case_id_raw.isdigit():
        return redirect_with_qy_toast("admin.completed_cases_library", "案件参数不合法。", "warning")
    case = db.session.get(Case, int(case_id_raw))
    if case is None:
        return redirect_with_qy_toast("admin.completed_cases_library", "案件不存在。", "warning")
    task = case.task
    if task is None or not is_terminal_phase(task.phase_status):
        return redirect_with_qy_toast(
            "admin.completed_cases_library",
            "仅已完成案件可在此补填实际返稿时间。",
            "warning",
        )

    redirect_kwargs = {}
    for key in ("q", "staff_id", "customer_id", "project_id", "sort", "group_by", "missing_return"):
        value = request.form.get(key, "").strip()
        if value:
            redirect_kwargs[key] = value

    if mode == "auto":
        if case.actual_return_at is not None:
            return redirect_with_qy_toast(
                "admin.completed_cases_library",
                "该案件已有实际返稿时间，已保留原值；如需修改请手动填写。",
                "info",
                **redirect_kwargs,
            )
        set_actual_return_at_from_latest_approved_writing(case)
        if case.actual_return_at is None:
            return redirect_with_qy_toast(
                "admin.completed_cases_library",
                "自动补算失败：无审核/材料依据，请手动填写。",
                "warning",
                **redirect_kwargs,
            )
        db.session.commit()
        return redirect_with_qy_toast(
            "admin.completed_cases_library",
            "已按审核/材料规则补算实际返稿时间。",
            "success",
            **redirect_kwargs,
        )

    ok, err = _resolve_actual_return_for_completed(
        case,
        actual_return_at_raw=request.form.get("actual_return_at", ""),
    )
    if not ok:
        return redirect_with_qy_toast("admin.completed_cases_library", err, "warning", **redirect_kwargs)
    db.session.commit()
    return redirect_with_qy_toast(
        "admin.completed_cases_library",
        "实际返稿时间已保存。",
        "success",
        **redirect_kwargs,
    )


@admin_bp.route("/historical-archive")
@login_required
def historical_archive():
    """办结案件库 · 历史归档占位页。"""
    _ensure_admin()
    return render_spa_or_full(
        full_template="admin/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="admin.historical_archive",
        spa_document_title="历史归档 — 琴岳专利管理系统",
        page_title="历史归档",
        page_desc="长期归档案件库，后续承接按年/客户检索与只读查阅。",
    )

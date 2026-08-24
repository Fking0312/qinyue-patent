"""员工端视图：任务看板、材料上传与下载留痕导出（案件状态仅管理端可改）。"""

import csv
from io import BytesIO, StringIO
from datetime import datetime, timedelta, timezone

from flask import Response, abort, current_app, jsonify, redirect, request, send_file, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
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
    PRIMARY_OPTIONS as CASE_TYPE_PRIMARY_OPTIONS,
    codes_for_primary,
    legacy_values_for_primary,
)
from app.blueprints.staff import staff_bp
from app.extensions import db
from app.models import Case, CaseMaterial, CaseMaterialDownloadLog, CaseReviewLog, Task, User
from app.dashboard_stats import dashboard_page_kwargs
from app.overdue_reminder import reminder_template_kwargs
from app.spa_helpers import redirect_with_qy_toast, render_spa_or_full
from app.task_board import task_board_data_for_user
from app.workflow import (
    TaskPhase,
    phase_for_workflow,
    staff_submit_case_for_review,
)

_CN_TZ = timezone(timedelta(hours=8))


def _beijing_datetime_text(value: datetime | None) -> str:
    """将数据库 UTC 时间转换为北京时间文本。"""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_staff():
    """权限闸：仅角色为 staff 的用户可继续访问，否则 403。"""
    if current_user.role != "staff":
        abort(403)


def _ensure_staff_function(*allowed: str):
    """权限闸：仅指定职能的员工可继续访问，否则 403。"""
    _ensure_staff()
    if current_user.staff_function_normalized not in allowed:
        abort(403)


def _ensure_writer():
    """撰写相关页面仅撰写师可访问，不能只靠隐藏菜单。"""
    _ensure_staff_function(User.STAFF_FUNCTION_WRITER)


STAFF_NOTIFICATION_ACTIONS = ("approve", "reject", "assigned")
STAFF_NOTIFICATION_ACTIONABLE = ("reject", "assigned")


def _staff_review_notifications_query(user_id: int):
    """当前员工收到的案件通知（分配 / 审核通过 / 打回）。"""
    return CaseReviewLog.query.filter(
        CaseReviewLog.action.in_(STAFF_NOTIFICATION_ACTIONS),
        CaseReviewLog.recipient_id == user_id,
    )


def _mark_staff_notification_read(log_id: int) -> CaseReviewLog | None:
    """将指定通知标记为已读；仅本人收件且尚未已读时生效。"""
    log = (
        _staff_review_notifications_query(current_user.id)
        .filter_by(id=log_id)
        .first()
    )
    if log is None or log.read_at is not None:
        return log
    log.read_at = datetime.now(timezone.utc)
    db.session.commit()
    return log


def _notification_group_label(value: datetime | None, today) -> str:
    """按北京时间将通知归入今天、昨天或更早。"""
    if value is None:
        return "更早"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    notice_date = value.astimezone(_CN_TZ).date()
    if notice_date == today:
        return "今天"
    if notice_date == today - timedelta(days=1):
        return "昨天"
    return "更早"


@staff_bp.app_context_processor
def _staff_notification_nav_context():
    """给员工侧边栏注入未读通知数量。"""
    if not current_user.is_authenticated or current_user.role != "staff":
        return {}
    unread = _staff_review_notifications_query(current_user.id).filter(
        CaseReviewLog.read_at.is_(None)
    ).count()
    return {"staff_notification_unread": unread}


def _ensure_staff_case_assignee(case: Case):
    """要求当前员工是该案件任务的指派人，否则视为越权（403）。"""
    task = case.task
    if task is None or task.assignee_id != current_user.id:
        abort(403)
    return task


def _staff_material_changes_locked(task: Task) -> bool:
    """仅撰写中允许员工增删材料；提交待审后锁定，打回撰写中时自动解锁。"""
    return phase_for_workflow(task.phase_status) != TaskPhase.IN_PROGRESS


def _case_has_staff_writing_material(case_id: int) -> bool:
    """案件是否已有员工上传的撰写材料。"""
    return (
        CaseMaterial.query.join(User, CaseMaterial.uploaded_by_id == User.id)
        .filter(CaseMaterial.case_id == case_id, User.role == "staff")
        .first()
        is not None
    )


def _case_material_download_rows(case_id: int, role_filter: str, page: int):
    """分页返回某案件的下载留痕（按角色筛选）。"""
    q = CaseMaterialDownloadLog.query.filter_by(case_id=case_id)
    if role_filter in {"admin", "staff", "client"}:
        q = q.filter(CaseMaterialDownloadLog.operator_role == role_filter)
    return q.order_by(CaseMaterialDownloadLog.created_at.desc(), CaseMaterialDownloadLog.id.desc()).paginate(
        page=page,
        per_page=10,
        error_out=False,
    )


def _case_material_download_query(case_id: int, role_filter: str):
    """构造下载留痕查询（不分页），供导出 CSV 时复用。"""
    q = CaseMaterialDownloadLog.query.filter_by(case_id=case_id)
    if role_filter in {"admin", "staff", "client"}:
        q = q.filter(CaseMaterialDownloadLog.operator_role == role_filter)
    return q.order_by(CaseMaterialDownloadLog.created_at.desc(), CaseMaterialDownloadLog.id.desc())


@staff_bp.route("/dashboard")
@login_required
def dashboard():
    """员工工作台：以提醒摘要为主，作为日常入口。"""
    _ensure_writer()
    return render_spa_or_full(
        full_template="staff/dashboard.html",
        inner_template="staff/snippets/dashboard_inner.html",
        spa_endpoint="staff.dashboard",
        spa_document_title="员工工作台 — 琴岳专利管理系统",
        **dashboard_page_kwargs(current_user),
    )


@staff_bp.route("/task-board")
@login_required
def task_board():
    """员工任务看板：仅看本人负责的任务，支持状态筛选与排序。"""
    _ensure_writer()
    from flask import request

    status_filter = request.args.get("status", "all").strip()
    sort_by = request.args.get("sort", "deadline").strip()
    if status_filter not in {"all", "in_progress", "pending_review", "overdue"}:
        status_filter = "all"
    if sort_by not in {"deadline", "customer"}:
        sort_by = "deadline"
    board = task_board_data_for_user(current_user, status_filter=status_filter, sort_by=sort_by)
    return render_spa_or_full(
        full_template="staff/task_board.html",
        inner_template="partials/task_board_inner.html",
        spa_endpoint="staff.task_board",
        spa_document_title="任务看板 — 琴岳专利管理系统",
        page_title="任务看板",
        page_desc="查看进行中、待审核与超期任务，并支持统一筛选与排序。",
        task_rows=board["rows"],
        status_filter=status_filter,
        sort_by=sort_by,
        count_in_progress=board["counts"]["in_progress"],
        count_pending_review=board["counts"]["pending_review"],
        count_overdue=board["counts"]["overdue"],
        case_detail_endpoint="staff.case_detail_by_id",
    )


@staff_bp.route("/case-detail")
@login_required
def case_detail():
    """员工案件列表：仅显示任务已分配给当前员工的全部案件。"""
    _ensure_writer()
    keyword = request.args.get("q", "").strip()
    case_type_primary = request.args.get("case_type_primary", "").strip()
    created_year_raw = request.args.get("created_year", "").strip()
    created_month_raw = request.args.get("created_month", "").strip()
    actual_return_year_raw = request.args.get("actual_return_year", "").strip()
    actual_return_month_raw = request.args.get("actual_return_month", "").strip()
    valid_primary_codes = {value for value, _label in CASE_TYPE_PRIMARY_OPTIONS}
    if case_type_primary not in valid_primary_codes:
        case_type_primary = ""

    query = (
        Case.query.join(Case.task)
        .options(
            joinedload(Case.task),
        )
        .filter(
            Task.assignee_id == current_user.id,
            Task.phase_status != "pending_assignment",
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
    created_year = ""
    created_month = ""
    actual_return_year = ""
    actual_return_month = ""
    if created_year_raw.isdigit() and created_month_raw.isdigit():
        parsed_year, parsed_month = case_statistics_month(created_year_raw, created_month_raw)
        if str(parsed_year) == created_year_raw and str(parsed_month) == str(int(created_month_raw)):
            start_at, end_at = case_statistics_month_bounds(parsed_year, parsed_month)
            query = query.filter(Case.created_at >= start_at, Case.created_at < end_at)
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
            query = query.filter(
                Case.actual_return_at >= start_at,
                Case.actual_return_at < end_at,
            )
            actual_return_year = str(parsed_year)
            actual_return_month = str(parsed_month)

    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    pagination = query.order_by(Case.created_at.desc(), Case.id.desc()).paginate(
        page=page,
        per_page=20,
        error_out=False,
    )
    return render_spa_or_full(
        full_template="staff/cases.html",
        inner_template="staff/snippets/cases_inner.html",
        spa_endpoint="staff.case_detail",
        spa_document_title="案件列表 — 琴岳专利管理系统",
        page_title="案件列表",
        cases=pagination.items,
        pagination=pagination,
        q=keyword,
        case_type_primary=case_type_primary,
        case_type_primary_options=CASE_TYPE_PRIMARY_OPTIONS,
        created_year=created_year,
        created_month=created_month,
        actual_return_year=actual_return_year,
        actual_return_month=actual_return_month,
    )


@staff_bp.route("/case-detail/<int:case_id>", methods=["GET", "POST"])
@login_required
def case_detail_by_id(case_id: int):
    """员工案件详情：GET 渲染状态/材料/留痕；POST 处理材料上传（不可手动改状态）。"""
    _ensure_writer()
    case = Case.query.options(joinedload(Case.business_owner_user)).filter_by(id=case_id).first()
    if case is None:
        abort(404)
    task = _ensure_staff_case_assignee(case)
    material_changes_locked = _staff_material_changes_locked(task)
    if request.method == "POST":
        form_action = request.form.get("form_action", "").strip()
        if form_action in {"upload_material", "delete_material"} and material_changes_locked:
            return redirect_with_qy_toast(
                "staff.case_detail_by_id",
                "案件已提交审核，当前材料已锁定；如被打回到撰写中可继续修改。",
                "warning",
                case_id=case.id,
            )
        if form_action == "upload_material":
            material_file = request.files.get("material_file")
            version_tag = request.form.get("material_version_tag", "draft").strip()
            if version_tag not in {"draft", "final"}:
                version_tag = "draft"
            material_note = request.form.get("material_note", "").strip() or "撰写材料"
            material_saved, upload_err = save_case_material_upload(
                case.id, material_file, current_user.id, version_tag, material_note
            )
            if material_saved is not None:
                return redirect_with_qy_toast("staff.case_detail_by_id", "撰写文件已上传。", "success", case_id=case.id)
            return redirect_with_qy_toast(
                "staff.case_detail_by_id", upload_err or "上传失败。", "warning", case_id=case.id
            )
        if form_action == "submit_for_review":
            if material_changes_locked:
                return redirect_with_qy_toast(
                    "staff.case_detail_by_id",
                    "当前状态无法提交审核。",
                    "warning",
                    case_id=case.id,
                )
            if not _case_has_staff_writing_material(case.id):
                return redirect_with_qy_toast(
                    "staff.case_detail_by_id",
                    "请先上传撰写材料后再提交审核。",
                    "warning",
                    case_id=case.id,
                )
            if staff_submit_case_for_review(task, operator_id=current_user.id):
                db.session.commit()
                return redirect_with_qy_toast(
                    "staff.case_detail_by_id",
                    "已提交审核，材料已锁定等待管理员处理。",
                    "success",
                    case_id=case.id,
                )
            return redirect_with_qy_toast(
                "staff.case_detail_by_id",
                "当前状态无法提交审核。",
                "warning",
                case_id=case.id,
            )
        if form_action == "delete_material":
            material_id_raw = request.form.get("material_id", "").strip()
            if not material_id_raw.isdigit():
                return redirect_with_qy_toast("staff.case_detail_by_id", "删除参数不合法。", "warning", case_id=case.id)
            material = db.session.get(CaseMaterial, int(material_id_raw))
            if material is None or material.case_id != case.id:
                return redirect_with_qy_toast("staff.case_detail_by_id", "目标材料不存在。", "warning", case_id=case.id)
            if material.uploaded_by_id != current_user.id:
                return redirect_with_qy_toast(
                    "staff.case_detail_by_id", "只能删除本人上传的附件。", "warning", case_id=case.id
                )
            file_path = case_material_dir(case.id) / material.stored_name
            db.session.delete(material)
            db.session.commit()
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                current_app.logger.exception(
                    "staff_material_file_cleanup case_id=%s material_id=%s",
                    case.id,
                    material_id_raw,
                )
            return redirect_with_qy_toast("staff.case_detail_by_id", "材料已删除。", "success", case_id=case.id)
        return redirect_with_qy_toast("staff.case_detail_by_id", "不支持的操作。", "warning", case_id=case.id)
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
    disclosure_material_files, writing_material_files = fetch_case_materials_grouped(
        case.id, material_version_filter
    )
    _, all_writing_material_files = fetch_case_materials_grouped(case.id, "all")
    can_submit_for_review = (
        not material_changes_locked and len(all_writing_material_files) > 0
    )
    return render_spa_or_full(
        full_template="staff/case_detail.html",
        inner_template="staff/snippets/case_detail_inner.html",
        spa_endpoint="staff.case_detail_by_id",
        spa_document_title="案件详情 — 琴岳专利管理系统",
        page_title="案件详情",
        page_desc="查看案件基础信息与当前任务状态。",
        case=case,
        task=task,
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
        material_changes_locked=material_changes_locked,
        can_submit_for_review=can_submit_for_review,
        has_writing_material=bool(all_writing_material_files),
    )


@staff_bp.route("/case-material/<int:case_id>/<int:material_id>")
@login_required
def case_material_download(case_id: int, material_id: int):
    """员工下载案件材料：写入留痕后以 send_from_directory 返回文件。"""
    _ensure_writer()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    _ensure_staff_case_assignee(case)
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


@staff_bp.route("/case-material-download-logs-export/<int:case_id>")
@login_required
def case_material_download_logs_export(case_id: int):
    """导出当前案件的材料下载留痕为 CSV，可按角色过滤。"""
    _ensure_writer()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    _ensure_staff_case_assignee(case)
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
                _beijing_datetime_text(item.created_at),
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


@staff_bp.route("/progress-update")
@login_required
def progress_update():
    """进度更新占位页：后续将提供一键状态变更与统一通知能力。"""
    _ensure_writer()
    return render_spa_or_full(
        full_template="staff/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="staff.progress_update",
        spa_document_title="进度更新 — 琴岳专利管理系统",
        page_title="进度更新",
        page_desc="这里将支持一键更新案件状态，并向管理者与客户同步提醒。",
    )


@staff_bp.route("/deadline-reminder")
@login_required
def deadline_reminder():
    """期限提醒页：复用 reminder_template_kwargs 的已超期/临期分组。"""
    _ensure_writer()
    from app.deadline_calendar import parse_calendar_month

    cal_year, cal_month = parse_calendar_month(
        request.args.get("cal_year", "").strip(),
        request.args.get("cal_month", "").strip(),
    )
    return render_spa_or_full(
        full_template="staff/deadline_reminder_page.html",
        inner_template="partials/deadline_reminder_inner.html",
        spa_endpoint="staff.deadline_reminder",
        spa_document_title="期限提醒 — 琴岳专利管理系统",
        **reminder_template_kwargs(
            current_user,
            reminder_page_title="期限提醒",
            calendar_year=cal_year,
            calendar_month=cal_month,
            selected_day=request.args.get("day", "").strip(),
            calendar_page_endpoint="staff.deadline_reminder",
        ),
    )


@staff_bp.route("/deliverable-upload")
@login_required
def deliverable_upload():
    """成果上传占位页：后续承载申请文件、OA 答复等带版本标记的上传流程。"""
    _ensure_writer()
    return render_spa_or_full(
        full_template="staff/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="staff.deliverable_upload",
        spa_document_title="成果上传 — 琴岳专利管理系统",
        page_title="成果上传",
        page_desc="这里将上传申请文件、OA 答复与中间文件，并支持版本标记。",
    )


@staff_bp.route("/worklog")
@login_required
def worklog():
    """员工月度统计：按创建时间或实际返稿时间汇总本人负责案件。"""
    _ensure_writer()
    basis = case_statistics_basis(request.args.get("basis", "").strip())
    year, month = case_statistics_month(
        request.args.get("year", "").strip(),
        request.args.get("month", "").strip(),
    )
    statistics = case_statistics_data(year, month, basis, assignee_id=current_user.id)
    previous_month_end = datetime(year, month, 1) - timedelta(days=1)
    previous = case_statistics_data(
        previous_month_end.year,
        previous_month_end.month,
        basis,
        assignee_id=current_user.id,
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
    now_cn = datetime.now(timezone.utc).astimezone(_CN_TZ)
    statistics["current_year"] = now_cn.year
    statistics["current_month"] = now_cn.month
    trend = case_statistics_trend_data(
        year,
        month,
        basis,
        assignee_id=current_user.id,
    )
    available_years = case_statistics_available_years(
        basis,
        assignee_id=current_user.id,
        selected_year=year,
    )
    return render_spa_or_full(
        full_template="staff/monthly_statistics.html",
        inner_template="staff/snippets/monthly_statistics_inner.html",
        spa_endpoint="staff.worklog",
        spa_document_title="月度统计 — 琴岳专利管理系统",
        page_title="月度统计",
        page_desc="查看本人负责案件的月度完成量与类型构成。",
        statistics=statistics,
        trend=trend,
        available_years=available_years,
    )


@staff_bp.route("/worklog/export")
@login_required
def worklog_export():
    """导出员工本人指定月份的案件统计 Excel。"""
    _ensure_writer()
    basis = case_statistics_basis(request.args.get("basis", "").strip())
    year, month = case_statistics_month(
        request.args.get("year", "").strip(),
        request.args.get("month", "").strip(),
    )
    statistics = case_statistics_data(year, month, basis, assignee_id=current_user.id)
    return send_file(
        BytesIO(
            case_statistics_workbook_bytes(
                statistics,
                sheet_title_prefix="月度统计",
                assignee_id=current_user.id,
            )
        ),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{month}月案件统计.xlsx",
    )


@staff_bp.route("/notifications")
@login_required
def notifications():
    """员工消息通知：展示案件分配、审核通过/打回结果；点击「查看」后标记已读。"""
    _ensure_writer()
    status_filter = request.args.get("status", "all").strip()
    if status_filter not in {"all", "unread", "actionable"}:
        status_filter = "all"
    type_filter = request.args.get("type", "all").strip()
    if type_filter not in {"all", "assigned", "approve", "reject"}:
        type_filter = "all"
    query = _staff_review_notifications_query(current_user.id).options(
        joinedload(CaseReviewLog.case).joinedload(Case.project),
        joinedload(CaseReviewLog.case).joinedload(Case.task),
        joinedload(CaseReviewLog.operator),
    )
    if status_filter == "unread":
        query = query.filter(CaseReviewLog.read_at.is_(None))
    elif status_filter == "actionable":
        query = query.filter(
            CaseReviewLog.read_at.is_(None),
            CaseReviewLog.action.in_(STAFF_NOTIFICATION_ACTIONABLE),
        )
    if type_filter != "all":
        query = query.filter(CaseReviewLog.action == type_filter)
    page_raw = request.args.get("page", "1").strip()
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    pagination = query.order_by(
        CaseReviewLog.created_at.desc(),
        CaseReviewLog.id.desc(),
    ).paginate(page=page, per_page=20, error_out=False)
    unread_count = _staff_review_notifications_query(current_user.id).filter(
        CaseReviewLog.read_at.is_(None)
    ).count()
    actionable_count = _staff_review_notifications_query(current_user.id).filter(
        CaseReviewLog.read_at.is_(None),
        CaseReviewLog.action.in_(STAFF_NOTIFICATION_ACTIONABLE),
    ).count()
    now_beijing = datetime.now(timezone.utc).astimezone(_CN_TZ)
    today = now_beijing.date()
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=_CN_TZ).astimezone(timezone.utc)
    today_count = _staff_review_notifications_query(current_user.id).filter(
        CaseReviewLog.created_at >= today_start
    ).count()
    action_counts = {
        action: _staff_review_notifications_query(current_user.id)
        .filter(CaseReviewLog.action == action)
        .count()
        for action in STAFF_NOTIFICATION_ACTIONS
    }
    grouped: dict[str, list[CaseReviewLog]] = {"今天": [], "昨天": [], "更早": []}
    for notification in pagination.items:
        grouped[_notification_group_label(notification.created_at, today)].append(notification)
    notification_groups = [(label, grouped[label]) for label in ("今天", "昨天", "更早") if grouped[label]]
    hour = now_beijing.hour
    greeting = "上午好" if 5 <= hour < 12 else "下午好" if hour < 18 else "晚上好"
    return render_spa_or_full(
        full_template="staff/notifications.html",
        inner_template="staff/snippets/notifications_inner.html",
        spa_endpoint="staff.notifications",
        spa_document_title="消息通知 — 琴岳专利管理系统",
        page_title="消息通知",
        notifications=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        type_filter=type_filter,
        unread_count=unread_count,
        actionable_count=actionable_count,
        today_count=today_count,
        action_counts=action_counts,
        notification_groups=notification_groups,
        greeting=greeting,
    )


@staff_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    """将当前员工的全部未读通知标记为已读。"""
    _ensure_writer()
    updated = (
        _staff_review_notifications_query(current_user.id)
        .filter(CaseReviewLog.read_at.is_(None))
        .update({CaseReviewLog.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.session.commit()
    return redirect_with_qy_toast(
        "staff.notifications",
        f"已将 {updated} 条通知标记为已读。" if updated else "当前没有未读通知。",
        "success" if updated else "secondary",
        status=request.form.get("status", "all"),
        type=request.form.get("type", "all"),
    )


@staff_bp.route("/notifications/<int:log_id>/read")
@login_required
def notification_read(log_id: int):
    """点击「查看」：标记单条通知已读并跳转案件详情。"""
    _ensure_writer()
    log = _mark_staff_notification_read(log_id)
    if log is None:
        abort(404)
    case = log.case
    if case is None:
        abort(404)
    task = case.task
    if task is None or task.assignee_id != current_user.id:
        return redirect_with_qy_toast(
            "staff.notifications",
            "该案件已不再由你负责，无法查看。",
            "warning",
        )
    return redirect(url_for("staff.case_detail_by_id", case_id=case.id))


@staff_bp.route("/notifications/status")
@login_required
def notifications_status():
    """员工侧边栏轮询：返回未读案件审核结果数量。"""
    _ensure_writer()
    unread = _staff_review_notifications_query(current_user.id).filter(
        CaseReviewLog.read_at.is_(None)
    ).count()
    return jsonify(ok=True, unread=unread)


def _render_function_workspace(
    *,
    endpoint: str,
    title: str,
    document_title: str,
    page_desc: str,
    cards: list[dict],
    allowed: str,
):
    """流程/业务占位工作台：独立标题、职责说明与「功能建设中」卡片。"""
    _ensure_staff_function(allowed)
    return render_spa_or_full(
        full_template="staff/function_workspace.html",
        inner_template="staff/snippets/function_workspace_inner.html",
        spa_endpoint=endpoint,
        spa_document_title=document_title,
        page_title=title,
        page_desc=page_desc,
        placeholder_cards=cards,
    )


@staff_bp.route("/process-dashboard")
@login_required
def process_dashboard():
    """流程人员首页：后续承接审核案件跟进。"""
    return _render_function_workspace(
        endpoint="staff.process_dashboard",
        title="流程工作台",
        document_title="流程工作台 — 琴岳专利管理系统",
        page_desc="你的职责是审核案件跟进。本页为独立入口，后续将在此接入审核进度与跟进记录。",
        cards=[
            {
                "title": "审核案件跟进",
                "desc": "查看待审案件、跟进审核意见并回写处理结果。",
                "endpoint": "staff.process_followup",
            }
        ],
        allowed=User.STAFF_FUNCTION_PROCESS,
    )


@staff_bp.route("/process-followup")
@login_required
def process_followup():
    """流程人员后续入口：审核案件跟进（建设中）。"""
    return _render_function_workspace(
        endpoint="staff.process_followup",
        title="审核案件跟进",
        document_title="审核案件跟进 — 琴岳专利管理系统",
        page_desc="后续将在此处理案件审核与跟进，当前仅预留稳定入口。",
        cards=[
            {
                "title": "审核案件跟进",
                "desc": "审核记录、打回意见与跟进状态将在此集中处理。",
            }
        ],
        allowed=User.STAFF_FUNCTION_PROCESS,
    )


@staff_bp.route("/business-dashboard")
@login_required
def business_dashboard():
    """业务人员首页：后续承接下单与收账。"""
    return _render_function_workspace(
        endpoint="staff.business_dashboard",
        title="业务工作台",
        document_title="业务工作台 — 琴岳专利管理系统",
        page_desc="你的职责是下单与收账。本页为独立入口，后续将在此接入委托下单和收款核对。",
        cards=[
            {
                "title": "下单",
                "desc": "为客户创建委托并进入后续办理流程。",
                "endpoint": "staff.business_orders",
            },
            {
                "title": "收账",
                "desc": "登记与核对客户款项，跟踪未收款。",
                "endpoint": "staff.business_collections",
            },
        ],
        allowed=User.STAFF_FUNCTION_BUSINESS,
    )


@staff_bp.route("/business-orders")
@login_required
def business_orders():
    """业务人员后续入口：下单（建设中）。"""
    return _render_function_workspace(
        endpoint="staff.business_orders",
        title="下单",
        document_title="下单 — 琴岳专利管理系统",
        page_desc="后续将在此创建客户委托订单，当前仅预留稳定入口。",
        cards=[
            {
                "title": "下单",
                "desc": "客户委托、案件立项与下单确认将在此处理。",
            }
        ],
        allowed=User.STAFF_FUNCTION_BUSINESS,
    )


@staff_bp.route("/business-collections")
@login_required
def business_collections():
    """业务人员后续入口：收账（建设中）。"""
    return _render_function_workspace(
        endpoint="staff.business_collections",
        title="收账",
        document_title="收账 — 琴岳专利管理系统",
        page_desc="后续将在此登记与核对收款，当前仅预留稳定入口。",
        cards=[
            {
                "title": "收账",
                "desc": "收款登记、未收款跟踪与对账将在此处理。",
            }
        ],
        allowed=User.STAFF_FUNCTION_BUSINESS,
    )

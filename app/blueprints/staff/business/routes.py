"""业务人员视图：下单（建客户/项目/案件）与收账占位。"""

from datetime import datetime, timezone

from flask import abort, current_app, request, send_from_directory
from flask_login import current_user, login_required

from app.blueprints.staff import staff_bp
from app.blueprints.staff.common.routes import render_function_workspace
from app.blueprints.staff.guards import ensure_business
from app.case_material_upload import case_material_dir
from app.case_types import CASE_TYPE_UI_CONFIG
from app.extensions import db
from app.models import Case, CaseMaterial, CaseMaterialDownloadLog, CustomerKind, User
from app.order_intake import (
    case_material_counts,
    case_materials_for_cases,
    create_intake_customer,
    create_intake_project,
    customer_project_catalog,
    datetime_local_value,
    latest_intake_reject_note,
    my_submitted_orders,
    order_revision_for_owner,
    parse_beijing_datetime,
    project_options_payload,
    resubmit_business_order,
    save_intake_attachments,
    submit_business_order,
)
from app.spa_helpers import redirect_with_qy_toast, render_spa_or_full


def _order_redirect_kwargs(customer_id, project_id, case_id=None) -> dict:
    kwargs = {}
    if customer_id:
        kwargs["customer_id"] = customer_id
    if project_id:
        kwargs["project_id"] = project_id
    if case_id:
        kwargs["edit"] = case_id
    return kwargs


def _int_or_none(raw: str) -> int | None:
    value = (raw or "").strip()
    if value.isdigit():
        parsed = int(value)
        if parsed > 0:
            return parsed
    return None


@staff_bp.route("/business-dashboard")
@login_required
def business_dashboard():
    """业务人员首页：下单已接真实流程，收账仍为占位。"""
    return render_function_workspace(
        endpoint="staff.business_dashboard",
        title="业务工作台",
        document_title="业务工作台 — 琴岳专利管理系统",
        page_desc="你的职责是下单与收账。案件归属于项目，项目绑定客户。",
        cards=[
            {
                "title": "下单",
                "desc": "选择或新建客户与项目，创建案件并提交管理员确认。",
                "endpoint": "staff.business_orders",
                "ready": True,
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
    """业务下单：看客户/项目名称、创建案件；看不到项目下的案件。"""
    ensure_business()
    selected_customer_id = (request.args.get("customer_id") or "").strip()
    selected_project_id = (request.args.get("project_id") or "").strip()
    editing_case = None
    reject_note = ""
    edit_id = _int_or_none(request.args.get("edit", ""))
    if edit_id:
        editing_case = order_revision_for_owner(current_user.id, edit_id)
        if editing_case is not None:
            selected_customer_id = str(editing_case.project.customer_id)
            selected_project_id = str(editing_case.project_id)
            reject_note = latest_intake_reject_note(editing_case.id)
    catalog = customer_project_catalog()
    my_orders = my_submitted_orders(current_user.id)
    editing_files = (
        case_materials_for_cases([editing_case.id]).get(editing_case.id, [])
        if editing_case is not None
        else []
    )
    return render_spa_or_full(
        full_template="staff/business_orders.html",
        inner_template="staff/snippets/business_orders_inner.html",
        spa_endpoint="staff.business_orders",
        spa_document_title="下单 — 琴岳专利管理系统",
        page_title="下单",
        page_desc="",
        catalog_rows=catalog,
        customers=[row["customer"] for row in catalog],
        project_options=project_options_payload(),
        selected_customer_id=selected_customer_id,
        selected_project_id=selected_project_id,
        my_orders=my_orders,
        order_file_counts=case_material_counts([task.case_id for task in my_orders]),
        case_type_config=CASE_TYPE_UI_CONFIG,
        case_type_selected=editing_case.case_type_code if editing_case else "",
        customer_kind_company=CustomerKind.COMPANY,
        customer_kind_individual=CustomerKind.INDIVIDUAL,
        order_at_input=datetime_local_value(
            editing_case.order_at if editing_case else datetime.now(timezone.utc)
        ),
        expected_return_at_input=datetime_local_value(
            editing_case.expected_return_at if editing_case else None
        ),
        editing_case=editing_case,
        editing_files=editing_files,
        reject_note=reject_note,
    )


@staff_bp.route("/business-orders/customers", methods=["POST"])
@login_required
def business_create_customer():
    """业务人员新建客户，供后续项目绑定。"""
    ensure_business()
    customer, err = create_intake_customer(
        current_user,
        name=request.form.get("name", ""),
        kind=request.form.get("kind", CustomerKind.COMPANY),
        contact_name=request.form.get("contact_name", ""),
        contact_phone=request.form.get("contact_phone", ""),
        note=request.form.get("note", ""),
    )
    if err:
        return redirect_with_qy_toast("staff.business_orders", err, "warning")
    db.session.commit()
    return redirect_with_qy_toast(
        "staff.business_orders",
        "客户已创建，可以继续新建项目或下单。",
        "success",
        customer_id=customer.id,
    )


@staff_bp.route("/business-orders/projects", methods=["POST"])
@login_required
def business_create_project():
    """业务人员新建项目，必须绑定客户。"""
    ensure_business()
    due_raw = request.form.get("due_at", "")
    try:
        due_at = parse_beijing_datetime(due_raw)
    except ValueError:
        return redirect_with_qy_toast("staff.business_orders", "项目截止时间格式不正确。", "warning")
    customer_id = _int_or_none(request.form.get("customer_id", ""))
    project, err = create_intake_project(
        current_user,
        customer_id=customer_id,
        name=request.form.get("name", ""),
        description=request.form.get("description", ""),
        due_at=due_at,
    )
    if err:
        kwargs = {"customer_id": customer_id} if customer_id else {}
        return redirect_with_qy_toast("staff.business_orders", err, "warning", **kwargs)
    db.session.commit()
    return redirect_with_qy_toast(
        "staff.business_orders",
        "项目已创建并绑定客户，可以继续填写案件。",
        "success",
        customer_id=project.customer_id,
        project_id=project.id,
    )


@staff_bp.route("/business-orders/cases", methods=["POST"])
@login_required
def business_create_case():
    """业务人员创建案件，或把打回的原单改完再提交；不指派撰写师。"""
    ensure_business()
    customer_id = _int_or_none(request.form.get("customer_id", ""))
    project_id = _int_or_none(request.form.get("project_id", ""))
    case_id = _int_or_none(request.form.get("case_id", ""))
    try:
        expected_return_at = parse_beijing_datetime(request.form.get("expected_return_at", ""))
        order_at = parse_beijing_datetime(request.form.get("order_at", ""))
    except ValueError:
        return redirect_with_qy_toast(
            "staff.business_orders",
            "下单或应返稿时间格式不正确。",
            "warning",
            **_order_redirect_kwargs(customer_id, project_id, case_id),
        )
    order_kwargs = dict(
        customer_id=customer_id,
        project_id=project_id,
        title=request.form.get("title", ""),
        case_type_code=request.form.get("case_type_code", ""),
        patent_application_no=request.form.get("patent_application_no", ""),
        expected_return_at=expected_return_at,
        order_at=order_at,
        case_note=request.form.get("case_note", ""),
        material_upload_port=request.form.get("material_upload_port", ""),
    )
    if case_id:
        case, err = resubmit_business_order(current_user, case_id, **order_kwargs)
        success_msg = "已按原单重新提交，等待管理员确认。"
    else:
        case, err = submit_business_order(current_user, **order_kwargs)
        success_msg = "案件已提交，等待管理员确认下单。"
    if err:
        return redirect_with_qy_toast(
            "staff.business_orders",
            err,
            "warning",
            **_order_redirect_kwargs(customer_id, project_id, case_id),
        )
    db.session.commit()
    customer_id_out = case.project.customer_id
    project_id_out = case.project_id
    saved, attach_errors = save_intake_attachments(
        case.id,
        request.files.getlist("attachments"),
        current_user,
    )
    if saved:
        success_msg += f" 已上传 {saved} 个交底材料。"
    if attach_errors:
        success_msg += " 以下文件未上传：" + "；".join(attach_errors[:3])
        if len(attach_errors) > 3:
            success_msg += "…"
    return redirect_with_qy_toast(
        "staff.business_orders",
        success_msg,
        "secondary" if attach_errors else "success",
        customer_id=customer_id_out,
        project_id=project_id_out,
    )


def _intake_owned_case(case_id: int) -> Case:
    case = db.session.get(Case, case_id)
    if case is None or case.intake_owner_id != current_user.id:
        abort(404)
    return case


@staff_bp.route("/business-orders/materials/<int:case_id>/<int:material_id>")
@login_required
def business_order_material_download(case_id: int, material_id: int):
    """业务人员下载自己下单案件的交底材料。"""
    ensure_business()
    case = _intake_owned_case(case_id)
    material = db.session.get(CaseMaterial, material_id)
    if material is None or material.case_id != case.id:
        abort(404)
    db.session.add(
        CaseMaterialDownloadLog(
            case_id=case.id,
            material_id=material.id,
            operator_id=current_user.id,
            operator_label=current_user.display_label,
            operator_role=current_user.role,
        )
    )
    db.session.commit()
    return send_from_directory(
        case_material_dir(case.id),
        material.stored_name,
        as_attachment=True,
        download_name=material.original_name,
    )


@staff_bp.route("/business-orders/materials/<int:case_id>/<int:material_id>/delete", methods=["POST"])
@login_required
def business_order_material_delete(case_id: int, material_id: int):
    """打回修改时，业务人员可删掉自己下单附带的交底材料。"""
    ensure_business()
    case = order_revision_for_owner(current_user.id, case_id)
    if case is None:
        return redirect_with_qy_toast("staff.business_orders", "只能在下单待修改时删除交底材料。", "warning")
    material = db.session.get(CaseMaterial, material_id)
    if material is None or material.case_id != case.id:
        return redirect_with_qy_toast(
            "staff.business_orders",
            "目标材料不存在。",
            "warning",
            edit=case.id,
        )
    file_path = case_material_dir(case.id) / material.stored_name
    db.session.delete(material)
    db.session.commit()
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        current_app.logger.exception(
            "business_order_material_cleanup case_id=%s material_id=%s",
            case.id,
            material_id,
        )
    return redirect_with_qy_toast(
        "staff.business_orders",
        "交底材料已删除。",
        "success",
        edit=case.id,
    )


@staff_bp.route("/business-collections")
@login_required
def business_collections():
    """业务人员后续入口：收账（建设中）。"""
    return render_function_workspace(
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

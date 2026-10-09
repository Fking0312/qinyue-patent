"""业务人员视图：工作台跟进、下单（建客户/项目/案件）与收账。"""

from datetime import datetime, timezone

from flask import abort, current_app, request, send_from_directory
from flask_login import current_user, login_required

from app.dashboard_stats import business_dashboard_page_kwargs
from app.blueprints.staff import staff_bp
from app.blueprints.staff.guards import ensure_business
from app.case_material_upload import case_material_dir
from app.case_types import CASE_TYPE_UI_CONFIG
from app.extensions import db
from app.models import Case, CaseMaterial, CaseMaterialDownloadLog, CustomerKind
from app.collections import (
    billing_queue_counts,
    collections_for_cases,
    delete_collection_proof,
    get_collection,
    get_proof,
    list_billing_cases,
    list_case_collections,
    save_collection_proof,
    submit_collection,
    user_may_submit_collections,
)
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
    """业务人员首页：跟进自己提交的下单，不展示撰写师负载。"""
    ensure_business()
    return render_spa_or_full(
        full_template="staff/business_dashboard.html",
        inner_template="staff/snippets/business_dashboard_inner.html",
        spa_endpoint="staff.business_dashboard",
        spa_document_title="业务工作台 — 琴岳专利管理系统",
        **business_dashboard_page_kwargs(current_user),
    )


def _business_orders_posted_form() -> dict:
    """校验失败时把已填字段带回下单表，避免重定向后清空。"""
    return {
        "selected_customer_id": (request.form.get("customer_id") or "").strip(),
        "selected_project_id": (request.form.get("project_id") or "").strip(),
        "form_title": request.form.get("title", ""),
        "case_type_selected": (request.form.get("case_type_code") or "").strip(),
        "form_patent_application_no": request.form.get("patent_application_no", ""),
        "form_case_note": request.form.get("case_note", ""),
        "form_material_upload_port": request.form.get("material_upload_port", ""),
        "order_at_input": request.form.get("order_at", ""),
        "expected_return_at_input": request.form.get("expected_return_at", ""),
        "editing_case_id": _int_or_none(request.form.get("case_id", "")),
    }


def _render_business_orders(*, form_error: str = "", **form_values):
    """渲染业务下单页；GET 与校验失败共用，失败时保留已填内容。"""
    selected_customer_id = str(form_values.get("selected_customer_id") or "").strip()
    selected_project_id = str(form_values.get("selected_project_id") or "").strip()
    if not selected_customer_id:
        selected_customer_id = (request.args.get("customer_id") or "").strip()
    if not selected_project_id:
        selected_project_id = (request.args.get("project_id") or "").strip()
    editing_case = None
    reject_note = ""
    edit_id = form_values.get("editing_case_id")
    if edit_id is None:
        edit_id = _int_or_none(request.args.get("edit", ""))
    if edit_id:
        editing_case = order_revision_for_owner(current_user.id, edit_id)
        if editing_case is not None and not form_error:
            selected_customer_id = str(editing_case.project.customer_id)
            selected_project_id = str(editing_case.project_id)
        if editing_case is not None:
            reject_note = latest_intake_reject_note(editing_case.id)
    catalog = customer_project_catalog()
    my_orders = my_submitted_orders(current_user.id)
    editing_files = (
        case_materials_for_cases([editing_case.id]).get(editing_case.id, [])
        if editing_case is not None
        else []
    )
    form_title = form_values.get("form_title")
    if form_title is None:
        form_title = editing_case.title if editing_case else ""
    case_type_selected = form_values.get("case_type_selected")
    if case_type_selected is None:
        case_type_selected = editing_case.case_type_code if editing_case else ""
    form_patent_application_no = form_values.get("form_patent_application_no")
    if form_patent_application_no is None:
        form_patent_application_no = (
            editing_case.patent_application_no or "" if editing_case else ""
        )
    form_case_note = form_values.get("form_case_note")
    if form_case_note is None:
        form_case_note = editing_case.case_note or "" if editing_case else ""
    form_material_upload_port = form_values.get("form_material_upload_port")
    if form_material_upload_port is None:
        form_material_upload_port = (
            editing_case.material_upload_port or "" if editing_case else ""
        )
    order_at_input = form_values.get("order_at_input")
    if order_at_input is None:
        order_at_input = datetime_local_value(
            editing_case.order_at if editing_case else datetime.now(timezone.utc)
        )
    expected_return_at_input = form_values.get("expected_return_at_input")
    if expected_return_at_input is None:
        expected_return_at_input = datetime_local_value(
            editing_case.expected_return_at if editing_case else None
        )
    posted_case_id = edit_id if form_error else None
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
        case_type_selected=case_type_selected or "",
        customer_kind_company=CustomerKind.COMPANY,
        customer_kind_individual=CustomerKind.INDIVIDUAL,
        order_at_input=order_at_input or "",
        expected_return_at_input=expected_return_at_input or "",
        editing_case=editing_case,
        editing_files=editing_files,
        reject_note=reject_note,
        form_title=form_title or "",
        form_patent_application_no=form_patent_application_no or "",
        form_case_note=form_case_note or "",
        form_material_upload_port=form_material_upload_port or "",
        posted_case_id=posted_case_id,
        form_error=form_error,
        open_modal=form_values.get("open_modal") or "",
        modal_error=form_values.get("modal_error") or "",
        modal_customer_name=form_values.get("modal_customer_name") or "",
        modal_customer_kind=form_values.get("modal_customer_kind") or CustomerKind.COMPANY,
        modal_contact_name=form_values.get("modal_contact_name") or "",
        modal_contact_phone=form_values.get("modal_contact_phone") or "",
        modal_customer_note=form_values.get("modal_customer_note") or "",
        modal_project_customer_id=form_values.get("modal_project_customer_id") or "",
        modal_project_name=form_values.get("modal_project_name") or "",
        modal_project_due=form_values.get("modal_project_due") or "",
        modal_project_desc=form_values.get("modal_project_desc") or "",
    )


def _business_order_invalid(message: str):
    return _render_business_orders(form_error=message, **_business_orders_posted_form())


@staff_bp.route("/business-orders")
@login_required
def business_orders():
    """业务下单：看客户/项目名称、创建案件；看不到项目下的案件。"""
    ensure_business()
    return _render_business_orders()


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
        return _render_business_orders(
            open_modal="qyOrderNewCustomerModal",
            modal_error=err,
            modal_customer_name=request.form.get("name", ""),
            modal_customer_kind=request.form.get("kind", CustomerKind.COMPANY),
            modal_contact_name=request.form.get("contact_name", ""),
            modal_contact_phone=request.form.get("contact_phone", ""),
            modal_customer_note=request.form.get("note", ""),
        )
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
        return _render_business_orders(
            open_modal="qyOrderNewProjectModal",
            modal_error="项目截止时间格式不正确。",
            modal_project_customer_id=(request.form.get("customer_id") or "").strip(),
            modal_project_name=request.form.get("name", ""),
            modal_project_due=due_raw,
            modal_project_desc=request.form.get("description", ""),
        )
    customer_id = _int_or_none(request.form.get("customer_id", ""))
    project, err = create_intake_project(
        current_user,
        customer_id=customer_id,
        name=request.form.get("name", ""),
        description=request.form.get("description", ""),
        due_at=due_at,
    )
    if err:
        return _render_business_orders(
            open_modal="qyOrderNewProjectModal",
            modal_error=err,
            modal_project_customer_id=(request.form.get("customer_id") or "").strip(),
            modal_project_name=request.form.get("name", ""),
            modal_project_due=due_raw,
            modal_project_desc=request.form.get("description", ""),
        )
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
        return _business_order_invalid("下单或应返稿时间格式不正确。")
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
        return _business_order_invalid(err)
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
    """业务人员收账列表：自己被指定为收账负责人的案件。"""
    ensure_business()
    cases = list_billing_cases(current_user.id)
    collections_by_case = collections_for_cases([case.id for case in cases])
    return render_spa_or_full(
        full_template="staff/business_collections.html",
        inner_template="staff/snippets/business_collections_inner.html",
        spa_endpoint="staff.business_collections",
        spa_document_title="收账 — 琴岳专利管理系统",
        page_title="收账",
        billing_cases=cases,
        collections_by_case=collections_by_case,
        queue_counts=billing_queue_counts(current_user.id),
    )


@staff_bp.route("/business-collections/<int:case_id>", methods=["GET", "POST"])
@login_required
def business_collection_case(case_id: int):
    """单案收账：参考缴费通知金额、上传向客户收款的证明、提交确认。"""
    ensure_business()
    case = db.session.get(Case, case_id)
    if case is None or not user_may_submit_collections(current_user, case):
        abort(404)
    if request.method == "POST":
        form_action = request.form.get("form_action", "").strip()
        collection_id_raw = request.form.get("collection_id", "").strip()
        collection = get_collection(int(collection_id_raw)) if collection_id_raw.isdigit() else None
        if collection is None or collection.case_id != case.id:
            return redirect_with_qy_toast(
                "staff.business_collection_case", "找不到这笔收账。", "warning", case_id=case.id
            )
        if form_action == "upload_proof":
            proof, error = save_collection_proof(
                collection=collection,
                uploader=current_user,
                file_storage=request.files.get("proof_file"),
                note=request.form.get("proof_note", ""),
            )
            if proof is None:
                return redirect_with_qy_toast(
                    "staff.business_collection_case",
                    error or "上传失败。",
                    "warning",
                    case_id=case.id,
                )
            db.session.commit()
            return redirect_with_qy_toast(
                "staff.business_collection_case",
                "收款证明已上传。",
                "success",
                case_id=case.id,
            )
        if form_action == "submit_collection":
            ok, message = submit_collection(
                collection,
                current_user,
                amount_raw=request.form.get("amount", ""),
                note=request.form.get("note", ""),
            )
            if ok:
                db.session.commit()
            return redirect_with_qy_toast(
                "staff.business_collection_case",
                message,
                "success" if ok else "warning",
                case_id=case.id,
            )
        if form_action == "delete_proof":
            proof_id_raw = request.form.get("proof_id", "").strip()
            proof = get_proof(int(proof_id_raw)) if proof_id_raw.isdigit() else None
            if proof is None or proof.collection_id != collection.id:
                return redirect_with_qy_toast(
                    "staff.business_collection_case", "找不到这份证明。", "warning", case_id=case.id
                )
            ok, message = delete_collection_proof(proof, current_user)
            if ok:
                db.session.commit()
            return redirect_with_qy_toast(
                "staff.business_collection_case",
                message,
                "success" if ok else "warning",
                case_id=case.id,
            )
        return redirect_with_qy_toast(
            "staff.business_collection_case", "不支持的操作。", "warning", case_id=case.id
        )

    collections = list_case_collections(case.id)
    notices = [item.notice for item in collections if item.notice is not None]
    return render_spa_or_full(
        full_template="staff/business_collection_case.html",
        inner_template="staff/snippets/business_collection_case_inner.html",
        spa_endpoint="staff.business_collection_case",
        spa_document_title=f"{case.title} — 收账",
        page_title="收账",
        case=case,
        notices=notices,
        collections=collections,
    )

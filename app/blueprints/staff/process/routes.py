"""流程人员：工作台队列、官文跟进与本案官方来文处理。"""

from flask import abort, request, send_from_directory
from flask_login import current_user, login_required

from app.blueprints.staff import staff_bp
from app.blueprints.staff.guards import ensure_process
from app.case_material_upload import fetch_case_materials_grouped
from app.case_trace import pending_revised_writing_hints, review_package_writing_materials
from app.dashboard_stats import process_dashboard_page_kwargs
from app.extensions import db
from app.models import Case, OfficialNotice
from app.official_notices import (
    PROCESS_QUEUES,
    QUEUE_ALL,
    QUEUE_LABELS,
    case_writer,
    date_input_value,
    delete_unforwarded_notice,
    forward_notice,
    get_notice,
    list_case_notices,
    list_followup_cases,
    notice_is_unreceived,
    notice_queue,
    official_notices_dir,
    process_queue_counts,
    save_official_notice,
    stamp_notice_received,
    urge_notice,
    user_may_download_notice,
    user_may_manage_case_notices,
)
from app.collections import (
    case_billing_owner,
    collections_dir,
    confirm_collection,
    final_review_block_reason,
    get_collection,
    get_proof,
    list_case_collections,
    reject_collection,
    user_may_download_proof,
)
from app.spa_helpers import redirect_with_qy_toast, render_spa_or_full
from app.workflow import (
    TaskPhase,
    is_pending_review_phase,
    phase_for_workflow,
    process_accept_writing,
    process_mark_filed,
    process_reject_writing,
    process_submit_final_review,
)


def _ensure_process_case(case_id: int) -> Case:
    ensure_process()
    case = db.session.get(Case, case_id)
    if case is None or not user_may_manage_case_notices(current_user, case):
        abort(404)
    return case


@staff_bp.route("/process-dashboard")
@login_required
def process_dashboard():
    """流程人员首页：官文队列与马上要处理的来文。"""
    ensure_process()
    return render_spa_or_full(
        full_template="staff/process_dashboard.html",
        inner_template="staff/snippets/process_dashboard_inner.html",
        spa_endpoint="staff.process_dashboard",
        spa_document_title="流程工作台 — 琴岳专利管理系统",
        **process_dashboard_page_kwargs(current_user),
    )


@staff_bp.route("/process-followup")
@login_required
def process_followup():
    """官文跟进：按队列查看自己负责的案件。"""
    ensure_process()
    queue = request.args.get("queue", QUEUE_ALL).strip()
    if queue not in PROCESS_QUEUES:
        queue = QUEUE_ALL
    cases = list_followup_cases(current_user.id, queue)
    return render_spa_or_full(
        full_template="staff/process_followup.html",
        inner_template="staff/snippets/process_followup_inner.html",
        spa_endpoint="staff.process_followup",
        spa_document_title="案件跟进 — 琴岳专利管理系统",
        page_title="案件跟进",
        queue=queue,
        queue_labels=QUEUE_LABELS,
        queue_counts=process_queue_counts(current_user.id),
        followup_cases=cases,
    )


@staff_bp.route("/process-cases/<int:case_id>", methods=["GET", "POST"])
@login_required
def process_case(case_id: int):
    """本案：核对撰写材料、递交官方、官文跟进。"""
    case = _ensure_process_case(case_id)
    if request.method == "POST":
        form_action = request.form.get("form_action", "").strip()
        task = case.task
        if form_action in {"process_accept", "process_reject", "process_filed", "submit_final_review"}:
            if task is None:
                return redirect_with_qy_toast(
                    "staff.process_case", "该案件暂无任务。", "warning", case_id=case.id
                )
            if form_action == "process_accept":
                ok, message = process_accept_writing(task, current_user)
            elif form_action == "process_reject":
                ok, message = process_reject_writing(
                    task, current_user, note=request.form.get("reject_note", "")
                )
            elif form_action == "process_filed":
                ok, message = process_mark_filed(task, current_user)
            else:
                ok, message = process_submit_final_review(task, current_user)
            if ok:
                db.session.commit()
            return redirect_with_qy_toast(
                "staff.process_case",
                message,
                "success" if ok else "warning",
                case_id=case.id,
            )
        if form_action == "upload_notice":
            notice, error = save_official_notice(
                case=case,
                uploader=current_user,
                notice_type=request.form.get("notice_type"),
                official_due_raw=request.form.get("official_due_at", ""),
                file_storage=request.files.get("notice_file"),
            )
            if notice is None:
                return redirect_with_qy_toast(
                    "staff.process_case",
                    error or "上传失败。",
                    "warning",
                    case_id=case.id,
                )
            db.session.commit()
            return redirect_with_qy_toast(
                "staff.process_case",
                "官方来文已上传。",
                "success",
                case_id=case.id,
            )
        if form_action in {"forward_notice", "urge_notice", "delete_notice"}:
            notice_id_raw = request.form.get("notice_id", "").strip()
            notice = get_notice(int(notice_id_raw)) if notice_id_raw.isdigit() else None
            if notice is None or notice.case_id != case.id:
                return redirect_with_qy_toast("staff.process_case", "找不到这份官方来文。", "warning", case_id=case.id)
            if form_action == "forward_notice":
                ok, message = forward_notice(
                    notice,
                    current_user,
                    internal_due_raw=request.form.get("internal_due_at", ""),
                )
            elif form_action == "urge_notice":
                ok, message = urge_notice(notice, current_user)
            else:
                ok, message = delete_unforwarded_notice(notice, current_user)
            if ok:
                db.session.commit()
            return redirect_with_qy_toast(
                "staff.process_case",
                message,
                "success" if ok else "warning",
                case_id=case.id,
            )
        if form_action in {"confirm_collection", "reject_collection"}:
            collection_id_raw = request.form.get("collection_id", "").strip()
            collection = get_collection(int(collection_id_raw)) if collection_id_raw.isdigit() else None
            if collection is None or collection.case_id != case.id:
                return redirect_with_qy_toast("staff.process_case", "找不到这笔收账。", "warning", case_id=case.id)
            if form_action == "confirm_collection":
                ok, message = confirm_collection(collection, current_user)
            else:
                ok, message = reject_collection(
                    collection, current_user, note=request.form.get("reject_note", "")
                )
            if ok:
                db.session.commit()
            return redirect_with_qy_toast(
                "staff.process_case",
                message,
                "success" if ok else "warning",
                case_id=case.id,
            )
        return redirect_with_qy_toast("staff.process_case", "不支持的操作。", "warning", case_id=case.id)

    notices = list_case_notices(case.id)
    writer = case_writer(case)
    disclosure_material_files, writing_material_files = fetch_case_materials_grouped(case.id, "all")
    task = case.task
    base_phase = phase_for_workflow(task.phase_status) if task is not None else ""
    revised_writing_hints = (
        pending_revised_writing_hints(case.id) if base_phase == TaskPhase.IN_PROGRESS else []
    )
    can_process_accept = task is not None and is_pending_review_phase(task.phase_status)
    review_hint_files = review_package_writing_materials(case.id) if can_process_accept else []
    can_mark_filed = base_phase == TaskPhase.PENDING_SUBMIT
    can_submit_final = base_phase in {TaskPhase.OFFICE_ACTION, TaskPhase.AUTHORIZED_PENDING_PAYMENT}
    collections = list_case_collections(case.id)
    collection_block = final_review_block_reason(case) if can_submit_final else None
    billing_owner = case_billing_owner(case)
    return render_spa_or_full(
        full_template="staff/process_case.html",
        inner_template="staff/snippets/process_case_inner.html",
        spa_endpoint="staff.process_case",
        spa_document_title=f"{case.title} — 案件跟进",
        page_title="案件跟进",
        case=case,
        task=task,
        writer=writer,
        billing_owner=billing_owner,
        notices=notices,
        collections=collections,
        collection_block=collection_block,
        notice_types=OfficialNotice.TYPES,
        notice_queue=notice_queue,
        notice_is_unreceived=notice_is_unreceived,
        date_input_value=date_input_value,
        disclosure_material_files=disclosure_material_files,
        writing_material_files=writing_material_files,
        review_hint_files=review_hint_files,
        revised_writing_hints=revised_writing_hints,
        can_process_accept=can_process_accept,
        can_mark_filed=can_mark_filed,
        can_submit_final=can_submit_final,
    )


@staff_bp.route("/official-notices/<int:notice_id>")
@login_required
def official_notice_download(notice_id: int):
    """流程人员或已转交的撰写师下载官文；撰写师首次下载记为已接收。"""
    notice = get_notice(notice_id)
    if notice is None or not user_may_download_notice(current_user, notice):
        abort(404)
    if current_user.role == "staff":
        stamp_notice_received(notice, current_user)
        db.session.commit()
    return send_from_directory(
        official_notices_dir(),
        notice.stored_name,
        as_attachment=True,
        download_name=notice.original_name,
    )


@staff_bp.route("/collection-proofs/<int:proof_id>")
@login_required
def collection_proof_download(proof_id: int):
    """流程或收账人员下载收款证明；撰写师不可见。"""
    ensure_staff = current_user.role == "staff"
    if not ensure_staff:
        abort(404)
    proof = get_proof(proof_id)
    if proof is None or not user_may_download_proof(current_user, proof):
        abort(404)
    return send_from_directory(
        collections_dir(),
        proof.stored_name,
        as_attachment=True,
        download_name=proof.original_name,
    )

"""收账：缴费通知转交业务人员，业务交证明，流程按笔确认。

与撰写材料分开存盘。未指定收账负责人时禁止转交缴费通知；全部确认后流程才能交终审。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import current_app
from sqlalchemy.orm import joinedload
from werkzeug.datastructures import FileStorage

from app.case_material_upload import persist_uploaded_file
from app.case_trace import add_review_log, user_trace_label
from app.extensions import db
from app.models import Case, CaseCollection, CaseCollectionProof, OfficialNotice, Project, Task, User
from app.official_notices import user_may_manage_case_notices


def collections_dir() -> Path:
    """收账证明磁盘目录：`instance/uploads/collections`。"""
    return Path(current_app.instance_path) / "uploads" / "collections"


def case_billing_owner(case: Case) -> User | None:
    """本案在职收账负责人：必须仍是可指定的业务人员。"""
    owner = case.billing_owner_user
    if owner is not None and owner.is_assignable_billing:
        return owner
    return None


def user_may_view_collections(user: User, case: Case) -> bool:
    if user.role == "admin":
        return True
    if user.role != "staff":
        return False
    if user_may_manage_case_notices(user, case):
        return True
    return (
        user.staff_function_normalized == User.STAFF_FUNCTION_BUSINESS
        and case.billing_owner_id == user.id
    )


def user_may_submit_collections(user: User, case: Case) -> bool:
    return (
        user.role == "staff"
        and user.staff_function_normalized == User.STAFF_FUNCTION_BUSINESS
        and case.billing_owner_id == user.id
    )


def user_may_download_proof(user: User, proof: CaseCollectionProof) -> bool:
    collection = proof.collection
    if collection is None or collection.case is None:
        return False
    return user_may_view_collections(user, collection.case)


def get_collection(collection_id: int) -> CaseCollection | None:
    return db.session.get(CaseCollection, collection_id)


def get_proof(proof_id: int) -> CaseCollectionProof | None:
    return db.session.get(CaseCollectionProof, proof_id)


def list_case_collections(case_id: int) -> list[CaseCollection]:
    return (
        CaseCollection.query.options(
            joinedload(CaseCollection.notice),
            joinedload(CaseCollection.submitted_by),
            joinedload(CaseCollection.confirmed_by),
            joinedload(CaseCollection.proofs).joinedload(CaseCollectionProof.uploaded_by),
        )
        .filter_by(case_id=case_id)
        .order_by(CaseCollection.created_at.asc(), CaseCollection.id.asc())
        .all()
    )


def collections_for_cases(case_ids: list[int]) -> dict[int, list[CaseCollection]]:
    if not case_ids:
        return {}
    rows = (
        CaseCollection.query.options(
            joinedload(CaseCollection.notice),
            joinedload(CaseCollection.proofs),
        )
        .filter(CaseCollection.case_id.in_(case_ids))
        .order_by(CaseCollection.created_at.asc(), CaseCollection.id.asc())
        .all()
    )
    grouped: dict[int, list[CaseCollection]] = {case_id: [] for case_id in case_ids}
    for row in rows:
        grouped.setdefault(row.case_id, []).append(row)
    return grouped


def collection_for_notice(notice_id: int) -> CaseCollection | None:
    return CaseCollection.query.filter_by(notice_id=notice_id).first()


def ensure_collection_for_notice(notice: OfficialNotice) -> CaseCollection:
    """缴费通知转交时生成或复用收账记录。调用方负责 commit。"""
    existing = collection_for_notice(notice.id)
    if existing is not None:
        return existing
    collection = CaseCollection(
        case_id=notice.case_id,
        notice_id=notice.id,
        status=CaseCollection.STATUS_PENDING_PROOF,
    )
    db.session.add(collection)
    db.session.flush()
    return collection


def list_billing_cases(user_id: int) -> list[Case]:
    return (
        Case.query.filter(Case.billing_owner_id == user_id)
        .options(
            joinedload(Case.task).joinedload(Task.assignee),
            joinedload(Case.project).joinedload(Project.customer),
            joinedload(Case.process_owner_user),
        )
        .order_by(Case.created_at.desc(), Case.id.desc())
        .all()
    )


def billing_queue_counts(user_id: int) -> dict[str, int]:
    rows = CaseCollection.query.join(Case, Case.id == CaseCollection.case_id).filter(
        Case.billing_owner_id == user_id
    ).all()
    pending_proof = 0
    pending_confirm = 0
    rejected = 0
    for row in rows:
        if row.status == CaseCollection.STATUS_PENDING_PROOF:
            pending_proof += 1
        elif row.status == CaseCollection.STATUS_PENDING_CONFIRM:
            pending_confirm += 1
        elif row.status == CaseCollection.STATUS_REJECTED:
            rejected += 1
    return {
        "pending_proof": pending_proof,
        "pending_confirm": pending_confirm,
        "rejected": rejected,
    }


def process_pending_confirm_count(user_id: int) -> int:
    return (
        CaseCollection.query.join(Case, Case.id == CaseCollection.case_id)
        .filter(
            Case.process_owner_id == user_id,
            CaseCollection.status == CaseCollection.STATUS_PENDING_CONFIRM,
        )
        .count()
    )


def list_process_pending_confirm_collections(user_id: int, *, limit: int = 8) -> list[CaseCollection]:
    return (
        CaseCollection.query.join(Case, Case.id == CaseCollection.case_id)
        .options(
            joinedload(CaseCollection.case).joinedload(Case.project).joinedload(Project.customer),
            joinedload(CaseCollection.notice),
        )
        .filter(
            Case.process_owner_id == user_id,
            CaseCollection.status == CaseCollection.STATUS_PENDING_CONFIRM,
        )
        .order_by(CaseCollection.submitted_at.asc(), CaseCollection.id.asc())
        .limit(limit)
        .all()
    )


def list_billing_attention_collections(user_id: int, *, limit: int = 8) -> list[CaseCollection]:
    rows = (
        CaseCollection.query.join(Case, Case.id == CaseCollection.case_id)
        .options(
            joinedload(CaseCollection.case).joinedload(Case.project).joinedload(Project.customer),
            joinedload(CaseCollection.notice),
        )
        .filter(
            Case.billing_owner_id == user_id,
            CaseCollection.status.in_(
                (CaseCollection.STATUS_PENDING_PROOF, CaseCollection.STATUS_REJECTED)
            ),
        )
        .order_by(CaseCollection.created_at.asc(), CaseCollection.id.asc())
        .all()
    )
    ranked = sorted(
        rows,
        key=lambda row: (0 if row.status == CaseCollection.STATUS_REJECTED else 1, row.id),
    )
    return ranked[:limit]


def unforwarded_fee_notices(case_id: int) -> list[OfficialNotice]:
    return [
        notice
        for notice in OfficialNotice.query.filter_by(case_id=case_id).all()
        if notice.needs_billing and notice.forwarded_at is None
    ]


def final_review_block_reason(case: Case | None) -> str | None:
    """流程交终审前：未转交的缴费通知、未确认的收账都挡住。"""
    if case is None:
        return None
    if unforwarded_fee_notices(case.id):
        return "还有未转交的缴费通知，请先转交业务收账并完成确认。"
    open_rows = [
        row
        for row in CaseCollection.query.filter_by(case_id=case.id).all()
        if row.status != CaseCollection.STATUS_CONFIRMED
    ]
    if open_rows:
        return "还有未确认的收账，请先确认全部收款后再提交终审。"
    return None


def parse_amount(raw: str) -> tuple[Decimal | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, "金额格式不正确。"
    if value < 0:
        return None, "金额不能为负数。"
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        return None, "金额最多两位小数。"
    return value.quantize(Decimal("0.01")), None


def _editable_collection(collection: CaseCollection) -> bool:
    return collection.status in {
        CaseCollection.STATUS_PENDING_PROOF,
        CaseCollection.STATUS_REJECTED,
    }


def save_collection_proof(
    *,
    collection: CaseCollection,
    uploader: User,
    file_storage: FileStorage | None,
    note: str = "",
) -> tuple[CaseCollectionProof | None, str | None]:
    case = collection.case
    if case is None or not user_may_submit_collections(uploader, case):
        return None, "只有本案收账人员可以上传收款证明。"
    if not _editable_collection(collection):
        return None, "流程确认中或已确认的收账不能再改证明。"
    display_name, stored_name, error = persist_uploaded_file(collections_dir(), file_storage)
    if error or not display_name or not stored_name:
        return None, error or "请选择要上传的文件。"
    proof = CaseCollectionProof(
        collection_id=collection.id,
        uploaded_by_id=uploader.id,
        uploaded_by_label=user_trace_label(uploader),
        original_name=display_name,
        stored_name=stored_name,
        note=(note or "").strip() or None,
    )
    db.session.add(proof)
    return proof, None


def delete_collection_proof(proof: CaseCollectionProof, operator: User) -> tuple[bool, str]:
    collection = proof.collection
    case = collection.case if collection is not None else None
    if collection is None or case is None or not user_may_submit_collections(operator, case):
        return False, "不能删除这份收款证明。"
    if not _editable_collection(collection):
        return False, "流程确认中或已确认的收账不能删除证明。"
    stored = proof.stored_name
    proof_id = proof.id
    db.session.delete(proof)
    db.session.flush()
    try:
        (collections_dir() / stored).unlink(missing_ok=True)
    except OSError:
        current_app.logger.exception("collection_proof_cleanup proof_id=%s", proof_id)
    return True, "已删除收款证明。"


def submit_collection(
    collection: CaseCollection,
    operator: User,
    *,
    amount_raw: str = "",
    note: str = "",
) -> tuple[bool, str]:
    case = collection.case
    if case is None or not user_may_submit_collections(operator, case):
        return False, "只有本案收账人员可以提交收款证明。"
    if not _editable_collection(collection):
        return False, "这份收账已提交或已确认。"
    if not collection.proofs:
        return False, "请先上传至少一份收款证明。"
    amount, amount_err = parse_amount(amount_raw)
    if amount_err:
        return False, amount_err
    process_owner = case.process_owner_user
    if process_owner is None or not process_owner.is_assignable_process:
        return False, "案件尚未指定在职流程人员，暂时不能提交。"
    now = datetime.now(timezone.utc)
    collection.amount = amount
    collection.note = (note or "").strip() or None
    collection.status = CaseCollection.STATUS_PENDING_CONFIRM
    collection.submitted_by_id = operator.id
    collection.submitted_by_label = user_trace_label(operator)
    collection.submitted_at = now
    collection.confirmed_by_id = None
    collection.confirmed_by_label = None
    collection.confirmed_at = None
    collection.reject_note = None
    notice_label = collection.notice.type_label if collection.notice is not None else "收账"
    add_review_log(
        case_id=case.id,
        action="collection_submit",
        operator=operator,
        recipient=process_owner,
        note=notice_label,
    )
    return True, "已提交收款证明，等待流程人员确认。"


def confirm_collection(collection: CaseCollection, operator: User) -> tuple[bool, str]:
    case = collection.case
    if case is None or not user_may_manage_case_notices(operator, case):
        return False, "只有本案流程负责人可以确认收款。"
    if collection.status != CaseCollection.STATUS_PENDING_CONFIRM:
        return False, "这份收账还不在待确认状态。"
    now = datetime.now(timezone.utc)
    collection.status = CaseCollection.STATUS_CONFIRMED
    collection.confirmed_by_id = operator.id
    collection.confirmed_by_label = user_trace_label(operator)
    collection.confirmed_at = now
    collection.reject_note = None
    billing = case.billing_owner_user
    notice_label = collection.notice.type_label if collection.notice is not None else "收账"
    add_review_log(
        case_id=case.id,
        action="collection_ok",
        operator=operator,
        recipient=billing,
        note=notice_label,
    )
    return True, "已确认这笔收款。"


def reject_collection(collection: CaseCollection, operator: User, *, note: str) -> tuple[bool, str]:
    case = collection.case
    if case is None or not user_may_manage_case_notices(operator, case):
        return False, "只有本案流程负责人可以打回收账。"
    if collection.status != CaseCollection.STATUS_PENDING_CONFIRM:
        return False, "这份收账还不在待确认状态。"
    reason = (note or "").strip()
    if not reason:
        return False, "请填写打回原因。"
    billing = case.billing_owner_user
    if billing is None:
        return False, "找不到收账人员，无法打回。"
    collection.status = CaseCollection.STATUS_REJECTED
    collection.reject_note = reason
    collection.confirmed_by_id = None
    collection.confirmed_by_label = None
    collection.confirmed_at = None
    notice_label = collection.notice.type_label if collection.notice is not None else "收账"
    add_review_log(
        case_id=case.id,
        action="collection_reject",
        operator=operator,
        recipient=billing,
        note=reason if len(reason) <= 200 else f"{notice_label}：{reason[:180]}",
    )
    return True, "已打回，请收账人员补交证明。"

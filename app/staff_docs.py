"""所内资料库：与案件材料分开存储，仅管理员上传，按员工职能控制可见范围。"""

from __future__ import annotations

from pathlib import Path

from flask import current_app
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from werkzeug.datastructures import FileStorage

from app.case_material_upload import persist_uploaded_file
from app.case_trace import user_trace_label
from app.extensions import db
from app.models import StaffDocument, User


def staff_docs_dir() -> Path:
    """资料库磁盘目录：`instance/uploads/staff_docs`。"""
    return Path(current_app.instance_path) / "uploads" / "staff_docs"


def list_staff_documents(*, audience: str | None = None) -> list[StaffDocument]:
    """管理端列表：可选按可见范围筛选，新上传在前。"""
    query = StaffDocument.query.options(joinedload(StaffDocument.uploaded_by))
    normalized = StaffDocument.normalize_audience(audience)
    if normalized:
        query = query.filter(StaffDocument.audience == normalized)
    return query.order_by(StaffDocument.created_at.desc(), StaffDocument.id.desc()).all()


def list_visible_staff_documents(staff_function: str | None) -> list[StaffDocument]:
    """员工端列表：全部员工 + 当前职能可见的资料。空职能按撰写师。"""
    function = User.normalize_staff_function(staff_function)
    return (
        StaffDocument.query.options(joinedload(StaffDocument.uploaded_by))
        .filter(
            or_(
                StaffDocument.audience == StaffDocument.AUDIENCE_ALL,
                StaffDocument.audience == function,
            )
        )
        .order_by(StaffDocument.created_at.desc(), StaffDocument.id.desc())
        .all()
    )


def get_staff_document(doc_id: int) -> StaffDocument | None:
    return db.session.get(StaffDocument, doc_id)


def get_visible_staff_document(doc_id: int, staff_function: str | None) -> StaffDocument | None:
    """员工只能取到自己职能可见的资料；不存在或不可见都返回 None。"""
    document = get_staff_document(doc_id)
    if document is None or not document.visible_to_function(staff_function):
        return None
    return document


def save_staff_document(
    *,
    title: str,
    note: str,
    audience: str | None,
    file_storage: FileStorage | None,
    uploader: User,
) -> tuple[StaffDocument | None, str | None]:
    """管理端上传。成功 (document, None)；失败 (None, Toast 文案)。"""
    cleaned_title = " ".join((title or "").split())
    if not cleaned_title:
        return None, "请填写资料标题。"
    if len(cleaned_title) > 200:
        return None, "标题不得超过 200 字。"

    normalized_audience = StaffDocument.normalize_audience(audience)
    if normalized_audience is None:
        return None, "请选择可见范围。"

    display_name, stored_name, error = persist_uploaded_file(staff_docs_dir(), file_storage)
    if error or not display_name or not stored_name:
        return None, error or "请选择要上传的文件。"

    document = StaffDocument(
        title=cleaned_title,
        note=(note or "").strip() or None,
        original_name=display_name,
        stored_name=stored_name,
        audience=normalized_audience,
        uploaded_by_id=uploader.id,
        uploaded_by_label=user_trace_label(uploader),
    )
    db.session.add(document)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            (staff_docs_dir() / stored_name).unlink(missing_ok=True)
        except OSError:
            pass
        return None, "保存资料失败，请稍后重试。"
    return document, None


def delete_staff_document(document: StaffDocument) -> None:
    """删除库记录并尽量去掉磁盘文件。"""
    stored_name = document.stored_name
    db.session.delete(document)
    db.session.commit()
    try:
        (staff_docs_dir() / stored_name).unlink(missing_ok=True)
    except OSError:
        pass

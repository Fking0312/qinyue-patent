"""案件材料上传：存储路径、扩展名与大小校验。"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import CaseMaterial, User
from app.case_trace import material_uploader_role, stamp_uploader
from sqlalchemy.orm import joinedload

# 专利业务常见格式（均为小写键）：文档、表格、演示、图片、音视频、压缩包等。
ALLOWED_MATERIAL_EXTENSIONS = frozenset(
    {
        # 文档 / 表格 / 演示
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "csv",
        "ppt",
        "pptx",
        "txt",
        "rtf",
        "md",
        # 图片
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "bmp",
        "tif",
        "tiff",
        "svg",
        # 音频 / 视频
        "mp3",
        "wav",
        "m4a",
        "mp4",
        "mov",
        "avi",
        "mkv",
        "webm",
        "wmv",
        "flv",
        "m4v",
        # 压缩包
        "zip",
        "rar",
        "7z",
    },
)


def case_material_dir(case_id: int) -> Path:
    """返回某案件的材料存储目录（`instance/uploads/case_<id>`），按需创建。"""
    return Path(current_app.instance_path) / "uploads" / f"case_{case_id}"


def fetch_case_materials(case_id: int, version_filter: str) -> list[CaseMaterial]:
    """查询案件材料；可按 draft/final 过滤版本，按时间倒序。"""
    q = CaseMaterial.query.options(joinedload(CaseMaterial.uploaded_by)).filter_by(case_id=case_id)
    if version_filter in {"draft", "final"}:
        q = q.filter(CaseMaterial.version_tag == version_filter)
    return q.order_by(CaseMaterial.created_at.desc(), CaseMaterial.id.desc()).all()


def fetch_case_materials_grouped(case_id: int, version_filter: str) -> tuple[list[CaseMaterial], list[CaseMaterial]]:
    """按上传者角色分组：管理员为交底材料，员工为撰写材料。

    角色以账号实时值为准，账号行不在了就用上传时冻结的 uploaded_by_role，
    避免离职或删号后撰写材料从列表里消失。
    """
    materials = fetch_case_materials(case_id, version_filter)
    disclosure = [m for m in materials if material_uploader_role(m) == "admin"]
    writing = [m for m in materials if m not in disclosure]
    return disclosure, writing


def _file_stream_size(fs: FileStorage) -> int:
    """读取上传流的字节长度，并保留原读指针，供大小校验使用。"""
    stream = fs.stream
    pos = stream.tell()
    try:
        stream.seek(0, os.SEEK_END)
        return stream.tell()
    finally:
        stream.seek(pos)


def _safe_display_filename(raw_basename: str) -> str:
    """保留可读文件名（含中文），仅去掉路径与控制字符。"""
    cleaned = "".join(ch for ch in raw_basename if ch.isprintable() and ch not in {"/", "\\", "\x00"})
    cleaned = cleaned.strip().strip(".")
    return cleaned[:255] if cleaned else ""


def save_case_material_upload(
    case_id: int,
    file_storage: FileStorage,
    uploaded_by_id: int,
    version_tag: str,
    note: str,
) -> tuple[CaseMaterial | None, str | None]:
    """
    校验并保存案件材料。
    成功返回 (material, None)；失败返回 (None, 简短中文说明供 Toast 使用)。
    """
    if file_storage is None:
        return None, "请选择要上传的文件。"
    raw_name = (file_storage.filename or "").strip()
    if not raw_name:
        return None, "请选择要上传的文件。"

    raw_basename = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in raw_basename:
        return None, "请上传带扩展名的文件（如 .pdf、.docx）。"

    ext = raw_basename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_MATERIAL_EXTENSIONS:
        return None, "该文件类型不允许上传，请使用 pdf、Office 文档或常见图片/压缩包格式。"

    display_name = _safe_display_filename(raw_basename)
    if not display_name or "." not in display_name:
        display_name = f"upload.{ext}"

    # 磁盘存储名仅用 ASCII，避免中文路径兼容问题；展示名仍保留中文。
    stored_base = secure_filename(raw_basename.rsplit(".", 1)[0]) or "upload"
    suffix = uuid4().hex[:8]
    final_name = f"{stored_base}_{suffix}.{ext}"

    max_bytes = int(current_app.config.get("CASE_MATERIAL_MAX_FILE_BYTES") or (25 * 1024 * 1024))
    try:
        sz = _file_stream_size(file_storage)
    except OSError:
        return None, "无法读取上传文件，请重试。"
    if sz <= 0:
        return None, "文件为空或无法读取，请重试。"
    if sz > max_bytes:
        mb = max(1, max_bytes // (1024 * 1024))
        return None, f"单个文件不得超过 {mb} MB。"

    target_dir = case_material_dir(case_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        file_storage.stream.seek(0)
        file_storage.save(target_dir / final_name)
    except OSError:
        return None, "保存文件失败，请稍后重试。"

    material = CaseMaterial(
        case_id=case_id,
        uploaded_by_id=uploaded_by_id,
        original_name=display_name,
        stored_name=final_name,
        version_tag=version_tag,
        note=note or None,
    )
    uploader = db.session.get(User, uploaded_by_id)
    stamp_uploader(material, uploader)
    db.session.add(material)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            (target_dir / final_name).unlink(missing_ok=True)
        except OSError:
            pass
        return None, "保存附件记录失败，请稍后重试。"
    return material, None

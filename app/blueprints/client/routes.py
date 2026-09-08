"""客户端视图：登录后查看自家案件、下载材料、提交留言（部分占位）。"""

from flask import abort, request, send_from_directory
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.case_material_upload import case_material_dir
from app.blueprints.client import client_bp
from app.extensions import db
from app.models import Case, CaseMaterial, CaseMaterialDownloadLog
from app.overdue_reminder import reminder_template_kwargs
from app.spa_helpers import render_spa_or_full


def _ensure_client():
    """权限闸：仅允许角色为 client 的用户访问后续视图，否则 403。"""
    if current_user.role != "client":
        abort(403)


def _client_case_query():
    """构造仅包含当前客户名下案件的查询；未绑定客户时返回空集合。

    退稿后转为内部案件的不再对客户可见（客户看到的是另行新补的那件）。
    """
    if current_user.customer_id is None:
        return Case.query.filter(False)
    return (
        Case.query.join(Case.project)
        .filter_by(customer_id=current_user.customer_id)
        .filter(Case.attribution_filter(Case.ATTRIBUTION_CUSTOMER))
    )


def _ensure_client_case_visible(case: Case):
    """案件必须属于当前客户且不是内部案件；否则 403（挡直接拼 URL 访问）。"""
    if current_user.customer_id is None or case.project.customer_id != current_user.customer_id:
        abort(403)
    if case.is_internal:
        abort(403)


@client_bp.route("/dashboard")
@login_required
def dashboard():
    """客户工作台：展示提醒摘要与默认入口。"""
    _ensure_client()
    return render_spa_or_full(
        full_template="client/dashboard.html",
        inner_template="client/snippets/dashboard_inner.html",
        spa_endpoint="client.dashboard",
        spa_document_title="客户工作台 — 琴岳专利管理系统",
        **reminder_template_kwargs(current_user),
    )


@client_bp.route("/cases")
@login_required
def cases():
    """客户案件列表：支持按标题/序列号关键字模糊检索。"""
    _ensure_client()
    keyword = request.args.get("q", "").strip()
    q = _client_case_query().options(joinedload(Case.task), joinedload(Case.project))
    if keyword:
        q = q.filter(
            Case.title.contains(keyword)
            | Case.application_no.contains(keyword)
            | Case.patent_application_no.contains(keyword)
        )
    case_list = q.order_by(Case.created_at.desc(), Case.id.desc()).all()
    return render_spa_or_full(
        full_template="client/cases.html",
        inner_template="client/snippets/cases_inner.html",
        spa_endpoint="client.cases",
        spa_document_title="案件列表 — 琴岳专利管理系统",
        page_title="案件列表",
        page_desc="查看本人名下案件，支持按序列号或名称检索。",
        cases=case_list,
        q=keyword,
    )


@client_bp.route("/case-detail/<int:case_id>")
@login_required
def case_detail(case_id: int):
    """客户案件详情：仅展示属于本客户的案件，越权访问返回 403。"""
    _ensure_client()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    _ensure_client_case_visible(case)
    task = case.task
    material_version_filter = request.args.get("material_version", "all").strip()
    if material_version_filter not in {"all", "draft", "final"}:
        material_version_filter = "all"
    materials_q = CaseMaterial.query.filter_by(case_id=case.id)
    if material_version_filter in {"draft", "final"}:
        materials_q = materials_q.filter(CaseMaterial.version_tag == material_version_filter)
    material_files = materials_q.order_by(CaseMaterial.created_at.desc(), CaseMaterial.id.desc()).all()
    return render_spa_or_full(
        full_template="client/case_detail.html",
        inner_template="client/snippets/case_detail_inner.html",
        spa_endpoint="client.case_detail",
        spa_document_title="案件详情 — 琴岳专利管理系统",
        page_title="案件详情",
        page_desc="查看案件状态、时间轴和可下载材料。",
        case=case,
        task=task,
        material_files=material_files,
        material_version_filter=material_version_filter,
    )


@client_bp.route("/case-material/<int:case_id>/<int:material_id>")
@login_required
def case_material_download(case_id: int, material_id: int):
    """客户下载案件材料：写入下载留痕后通过 send_from_directory 返回文件。"""
    _ensure_client()
    case = db.session.get(Case, case_id)
    if case is None:
        abort(404)
    _ensure_client_case_visible(case)
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


@client_bp.route("/timeline")
@login_required
def timeline():
    """状态时间轴占位页：阶段 A 暂以通用占位模板呈现，后续接入节点数据。"""
    _ensure_client()
    return render_spa_or_full(
        full_template="client/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="client.timeline",
        spa_document_title="状态时间轴 — 琴岳专利管理系统",
        page_title="状态时间轴",
        page_desc="查看案件关键节点状态与时间戳变化。",
    )


@client_bp.route("/files")
@login_required
def files():
    """文件下载入口：复用案件列表模板，便于客户从材料维度筛选。"""
    _ensure_client()
    return render_spa_or_full(
        full_template="client/cases.html",
        inner_template="client/snippets/cases_inner.html",
        spa_endpoint="client.files",
        spa_document_title="文件下载 — 琴岳专利管理系统",
        page_title="文件下载",
        page_desc="查看本人名下案件并下载相关材料。",
        cases=_client_case_query()
        .options(joinedload(Case.task), joinedload(Case.project))
        .order_by(Case.created_at.desc(), Case.id.desc())
        .all(),
        q="",
    )


@client_bp.route("/messages")
@login_required
def messages():
    """在线留言占位页：未来将与内部任务消息打通。"""
    _ensure_client()
    return render_spa_or_full(
        full_template="client/page.html",
        inner_template="partials/role_placeholder_inner.html",
        spa_endpoint="client.messages",
        spa_document_title="在线留言 — 琴岳专利管理系统",
        page_title="在线留言",
        page_desc="向管理者补充资料或询问进度，消息将绑定内部任务。",
    )

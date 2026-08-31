"""流程人员视图：当前仅为独立占位入口，后续在此接入审核进度与跟进记录。"""

from flask_login import login_required

from app.blueprints.staff import staff_bp
from app.blueprints.staff.common.routes import render_function_workspace
from app.models import User


@staff_bp.route("/process-dashboard")
@login_required
def process_dashboard():
    """流程人员首页：后续承接审核案件跟进。"""
    return render_function_workspace(
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
    return render_function_workspace(
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

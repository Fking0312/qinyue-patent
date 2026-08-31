"""业务人员视图：当前仅为独立占位入口，后续在此接入委托下单与收款核对。"""

from flask_login import login_required

from app.blueprints.staff import staff_bp
from app.blueprints.staff.common.routes import render_function_workspace
from app.models import User


@staff_bp.route("/business-dashboard")
@login_required
def business_dashboard():
    """业务人员首页：后续承接下单与收账。"""
    return render_function_workspace(
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
    return render_function_workspace(
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

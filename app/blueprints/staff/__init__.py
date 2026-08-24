"""员工端蓝图：员工视角的任务看板、案件状态推进、材料上传等入口。"""

from flask import Blueprint

staff_bp = Blueprint("staff", __name__, template_folder="../../templates/staff")

from app.blueprints.staff import routes

"""员工端蓝图：员工视角的任务看板、案件状态推进、材料上传等入口。

内部按职能拆成 common / writer / process / business 四个子模块，但仍共用同一个
staff_bp，因此 endpoint 始终是 staff.xxx，模板与前端 SPA 的引用不受拆分影响。
新增页面时放进对应职能的 routes.py，不要再往单一文件里堆。
"""

from flask import Blueprint

staff_bp = Blueprint("staff", __name__, template_folder="../../templates/staff")

# 子模块在导入时把路由挂到 staff_bp 上，必须放在蓝图创建之后。
from app.blueprints.staff import common  # noqa: E402,F401
from app.blueprints.staff import writer  # noqa: E402,F401
from app.blueprints.staff import process  # noqa: E402,F401
from app.blueprints.staff import business  # noqa: E402,F401

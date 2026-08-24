"""管理端蓝图：聚合 /admin 下的全部视图（客户/项目/案件/账号等）。

实际视图实现集中在 `routes.py`，此处仅做蓝图注册，避免循环导入。
"""

from flask import Blueprint

admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")

from app.blueprints.admin import routes

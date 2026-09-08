"""鉴权蓝图：仅承载登录/登出（注册当前关闭，自助路径返回 404）。"""

from flask import Blueprint

auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")

from app.blueprints.auth import routes

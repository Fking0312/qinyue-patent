"""客户端蓝图：客户视角下的案件查看、材料下载与时间轴入口。"""

from flask import Blueprint

client_bp = Blueprint("client", __name__, template_folder="../../templates/client")

from app.blueprints.client import routes

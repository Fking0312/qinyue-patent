"""Flask 扩展实例的集中声明。

将扩展独立到此文件，是为了让 `create_app` 与各蓝图都能共享同一组实例，
避免循环导入；具体绑定（init_app）发生在 `app/__init__.py`。
"""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录后再访问。"
csrf = CSRFProtect()

"""鉴权视图：登录、登出与（关闭中的）注册入口。"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth import auth_bp
from app.extensions import db
from app.models import User
from app.spa_helpers import redirect_with_qy_toast


def _role_dashboard(role: str) -> str:
    """按角色返回登录后默认仪表盘 endpoint，未识别角色回落到客户端。"""
    if role == "admin":
        return "admin.dashboard"
    if role == "staff":
        return "staff.dashboard"
    return "client.dashboard"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """登录视图：GET 渲染表单；POST 校验用户名/密码并写入会话。"""
    if current_user.is_authenticated and request.method == "GET":
        return redirect(url_for(_role_dashboard(current_user.role)))
    if current_user.is_authenticated and request.method == "POST":
        logout_user()

    if request.method == "POST":
        login_id = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.find_by_login(login_id)
        if user and not getattr(user, "is_active", True):
            flash(user.inactive_login_message(), "danger")
            return render_template("auth/login.html")
        if user and user.check_password(password):
            login_user(user)
            return redirect_with_qy_toast(_role_dashboard(user.role), "登录成功。", "success")

        flash("账号/手机号或密码错误。", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """登出当前会话并以 Toast 跳回登录页。"""
    logout_user()
    return redirect_with_qy_toast("auth.login", "你已退出登录。", "info")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """自助注册入口：当前版本统一返回 404，账号由管理端分发。"""
    abort(404)

"""会话 Cookie 策略，以及离职/冻结后立刻作废已登录会话。"""

from flask import flash, redirect, request, url_for
from flask_login import current_user, logout_user


def expire_inactive_sessions():
    """员工离职、客户冻结后，已发出的会话在下一次请求即退出并提示。"""
    if request.endpoint == "static":
        return None
    user = current_user
    if getattr(user, "is_anonymous", True):
        return None
    if getattr(user, "is_active", True):
        return None
    message = user.inactive_login_message()
    logout_user()
    flash(message, "danger")
    if request.endpoint in {"auth.login", "auth.logout"}:
        return None
    return redirect(url_for("auth.login"))

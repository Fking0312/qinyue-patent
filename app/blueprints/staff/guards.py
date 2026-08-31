"""员工端权限闸：按角色与职能拦截，供 staff 下各职能子模块共用。"""

from flask import abort
from flask_login import current_user

from app.models import User


def ensure_staff():
    """权限闸：仅角色为 staff 的用户可继续访问，否则 403。"""
    if current_user.role != "staff":
        abort(403)


def ensure_staff_function(*allowed: str):
    """权限闸：仅指定职能的员工可继续访问，否则 403。"""
    ensure_staff()
    if current_user.staff_function_normalized not in allowed:
        abort(403)


def ensure_writer():
    """撰写相关页面仅撰写师可访问，不能只靠隐藏菜单。"""
    ensure_staff_function(User.STAFF_FUNCTION_WRITER)

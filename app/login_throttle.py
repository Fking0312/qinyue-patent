"""登录失败次数限制：同一账号与同一 IP 分别计数，锁定期间不再校验密码。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil

from flask import current_app, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import LoginThrottle

_GENERIC_FAILURE = "账号/手机号或密码错误。"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def client_ip() -> str:
    return ((request.remote_addr or "").strip() or "unknown")[:80]


def normalize_login_key(login_id: str) -> str:
    return (login_id or "").strip()[:80]


def lockout_message(locked_until: datetime, *, now: datetime | None = None) -> str:
    current = now or utc_now()
    seconds = max(1, int((locked_until - current).total_seconds()))
    minutes = max(1, ceil(seconds / 60))
    return f"登录失败次数过多，请 {minutes} 分钟后再试。"


def generic_failure_message() -> str:
    return _GENERIC_FAILURE


def _settings() -> tuple[int, int, int, int]:
    cfg = current_app.config
    return (
        int(cfg.get("LOGIN_MAX_FAILURES") or 5),
        int(cfg.get("LOGIN_FAILURE_WINDOW_MINUTES") or 15),
        int(cfg.get("LOGIN_LOCKOUT_MINUTES") or 15),
        int(cfg.get("LOGIN_IP_MAX_FAILURES") or 20),
    )


def _get_row(scope: str, key: str) -> LoginThrottle | None:
    return LoginThrottle.query.filter_by(scope=scope, key=key).first()


def _locked_until(row: LoginThrottle | None, now: datetime) -> datetime | None:
    if row is None or row.locked_until is None:
        return None
    if row.locked_until > now:
        return row.locked_until
    return None


def _get_or_create(scope: str, key: str, now: datetime) -> LoginThrottle:
    row = _get_row(scope, key)
    if row is not None:
        return row
    row = LoginThrottle(
        scope=scope,
        key=key,
        fail_count=0,
        window_started_at=now,
        locked_until=None,
        updated_at=now,
    )
    db.session.add(row)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        existing = _get_row(scope, key)
        if existing is not None:
            return existing
        raise
    return row


def check_login_allowed(login_id: str, *, now: datetime | None = None) -> str | None:
    """若账号或 IP 已锁定，返回提示文案；否则返回 None。"""
    current = now or utc_now()
    login_key = normalize_login_key(login_id)
    ip_key = client_ip()
    for scope, key in (
        (LoginThrottle.SCOPE_LOGIN, login_key),
        (LoginThrottle.SCOPE_IP, ip_key),
    ):
        if not key:
            continue
        until = _locked_until(_get_row(scope, key), current)
        if until is not None:
            return lockout_message(until, now=current)
    return None


def record_login_failure(login_id: str, *, now: datetime | None = None) -> str | None:
    """记一次失败；若因此锁定，返回锁定提示。"""
    current = now or utc_now()
    max_login, window_minutes, lockout_minutes, max_ip = _settings()
    login_until = _record_scope(
        LoginThrottle.SCOPE_LOGIN,
        normalize_login_key(login_id),
        max_login,
        window_minutes,
        lockout_minutes,
        current,
    )
    ip_until = _record_scope(
        LoginThrottle.SCOPE_IP,
        client_ip(),
        max_ip,
        window_minutes,
        lockout_minutes,
        current,
    )
    until = login_until or ip_until
    if until is None:
        return None
    return lockout_message(until, now=current)


def clear_login_failures(login_id: str) -> None:
    """登录成功后清除该账号的失败计数；IP 计数保留，避免撞库被一次成功清零。"""
    login_key = normalize_login_key(login_id)
    if not login_key:
        return
    row = _get_row(LoginThrottle.SCOPE_LOGIN, login_key)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


def _record_scope(
    scope: str,
    key: str,
    max_failures: int,
    window_minutes: int,
    lockout_minutes: int,
    now: datetime,
) -> datetime | None:
    if not key:
        return None
    row = _get_or_create(scope, key, now)
    locked = _locked_until(row, now)
    if locked is not None:
        return locked

    lock_expired = row.locked_until is not None and row.locked_until <= now
    window_expired = now >= (row.window_started_at + timedelta(minutes=window_minutes))
    if lock_expired or window_expired:
        row.fail_count = 0
        row.window_started_at = now
        row.locked_until = None

    row.fail_count += 1
    row.updated_at = now
    if row.fail_count >= max_failures:
        row.locked_until = now + timedelta(minutes=lockout_minutes)
        db.session.commit()
        return row.locked_until
    db.session.commit()
    return None

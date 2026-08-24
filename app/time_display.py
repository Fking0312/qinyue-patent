"""时间展示辅助：相对时间与完整北京时间。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_CN_TZ = timezone(timedelta(hours=8))


def relative_datetime_text(value, *, now: datetime | None = None) -> str:
    """将 UTC 时间转为中文相对时间；超过 7 天则返回完整北京时间。"""
    if value is None:
        return "—"
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = int((current - value).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if seconds < 172800:
        return f"昨天 {value.astimezone(_CN_TZ).strftime('%H:%M')}"
    if seconds < 604800:
        return f"{seconds // 86400} 天前"
    return value.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M")


def absolute_datetime_text(value) -> str:
    """完整北京时间文本，供 tooltip 使用。"""
    if value is None:
        return "—"
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_CN_TZ).strftime("%Y-%m-%d %H:%M")

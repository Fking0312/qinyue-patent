"""员工端通用工具：北京时区与时间文本格式化。"""

from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def beijing_datetime_text(value: datetime | None) -> str:
    """将数据库 UTC 时间转换为北京时间文本。"""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")

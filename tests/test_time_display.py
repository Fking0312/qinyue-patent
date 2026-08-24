from datetime import datetime, timedelta, timezone

from app.time_display import absolute_datetime_text, relative_datetime_text


def test_relative_datetime_text_buckets():
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    assert relative_datetime_text(now, now=now) == "刚刚"
    assert relative_datetime_text(now - timedelta(minutes=5), now=now) == "5 分钟前"
    assert relative_datetime_text(now - timedelta(hours=2), now=now) == "2 小时前"
    assert relative_datetime_text(now - timedelta(days=1, hours=2), now=now).startswith("昨天 ")
    assert relative_datetime_text(now - timedelta(days=3), now=now) == "3 天前"
    old = relative_datetime_text(now - timedelta(days=10), now=now)
    assert old == "2026-07-09 20:00"


def test_absolute_datetime_text_beijing():
    value = datetime(2026, 1, 1, 16, 30, tzinfo=timezone.utc)
    assert absolute_datetime_text(value) == "2026-01-02 00:30"

from datetime import datetime, timezone

from app import create_app
from app.blueprints.admin.routes import _dt_input_value, _parse_due_at


def test_page_times_and_datetime_inputs_use_beijing_time():
    app = create_app()
    formatter = app.jinja_env.filters["qy_format_dt"]

    utc_value = datetime(2032, 1, 9, 1, 30, tzinfo=timezone.utc)
    assert formatter(utc_value) == "2032-01-09 09:30"
    assert formatter(datetime(2032, 1, 9, 1, 30)) == "2032-01-09 09:30"

    parsed = _parse_due_at("2032-01-09T09:30")
    assert parsed == utc_value
    assert _dt_input_value(parsed) == "2032-01-09T09:30"

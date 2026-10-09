from pathlib import Path

from app import create_app


def test_deploy_version_busts_static_cache_and_disables_html_cache():
    app = create_app()
    client = app.test_client()

    page = client.get("/")
    assert page.status_code == 200
    assert b'name="qy-deploy-version"' in page.data
    assert b"/static/css/style.css?v=" in page.data
    assert b"/static/js/main.js?v=" in page.data
    assert "no-store" in page.headers["Cache-Control"]

    version_response = client.get("/system/deploy-version")
    payload = version_response.get_json()
    assert version_response.status_code == 200
    assert payload["version"]
    assert len(payload["version"]) == 16
    assert "no-store" in version_response.headers["Cache-Control"]


def test_spa_swap_disposes_bootstrap_modal_backdrops():
    src = Path(__file__).resolve().parents[1].joinpath("app", "static", "js", "main.js").read_text(
        encoding="utf-8"
    )
    assert "window.qyDisposeBootstrapOverlays" in src
    assert "replaceSpaMainEl" in src
    assert src.count("replaceSpaMainEl(") >= 2
    assert ".modal-backdrop" in src
    assert "modal-open" in src

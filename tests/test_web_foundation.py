from app.api import web
from app.main import app
from starlette.testclient import TestClient


def test_web_serves_known_routes_only(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "WEB_BUILD", tmp_path)
    (tmp_path / "index.html").write_text("<html lang='he' dir='rtl'></html>")
    # Install a fresh set of routes against the test build.
    from fastapi import FastAPI
    local = FastAPI(redirect_slashes=False)
    web.install_web(local)
    client = TestClient(local)
    assert client.get("/b/example/book/availability").status_code == 200
    assert client.head("/b/example").content == b""
    assert client.get("/b/example/not-a-route").status_code == 404
    assert client.get("/b/Invalid").status_code == 404
    assert client.get("/web-assets/missing.js").status_code == 404


def test_health_and_unknown_api_keep_json():
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/missing").json() == {"error": {"code": "HTTP_ERROR"}}

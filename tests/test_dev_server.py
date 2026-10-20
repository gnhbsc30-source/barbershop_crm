from contextlib import closing
from io import StringIO
import json

import pytest
from starlette.testclient import TestClient

from app import dev_data, dev_server
from app.api import auth_dependencies
from app.core.local_security import DEVELOPMENT_ORIGIN
from app.db import database
from app.main import app


class Terminal(StringIO):
    def isatty(self):
        return True


@pytest.fixture
def provisioned(tmp_path, monkeypatch):
    path = tmp_path / "data" / "dev" / "barbershop.db"
    dev_data.provision_development_data(path, environment="development", input_stream=Terminal("PROVISION\n"), output_stream=Terminal())
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    monkeypatch.setattr(dev_server.settings, "environment", "development")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    return path


def test_launcher_enforces_safe_server_settings(provisioned, monkeypatch):
    calls = []
    monkeypatch.setattr(dev_server.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert dev_server.main() == 0
    args, options = calls[0]
    assert args == ("app.main:app",)
    assert options["host"] == "127.0.0.1" and options["port"] == 8000
    assert options["workers"] == 1 and options["reload"] is False
    assert options["proxy_headers"] is False and options["forwarded_allow_ips"] == ""
    assert options["access_log"] is False


@pytest.mark.parametrize("condition", ["production", "workers", "missing_database", "missing_manifest", "bad_manifest", "inconsistent_database", "partial_commit", "wrong_path"])
def test_startup_and_launcher_refuse_unsafe_or_inconsistent_state(provisioned, monkeypatch, capsys, condition):
    path = provisioned
    if condition == "production":
        monkeypatch.setattr(dev_server.settings, "environment", "production")
    elif condition == "workers":
        monkeypatch.setenv("WEB_CONCURRENCY", "2")
    elif condition == "missing_database":
        path.unlink()  # Isolated test artifact only.
    elif condition == "missing_manifest":
        dev_data._manifest_path(path).unlink()
    elif condition == "bad_manifest":
        dev_data._manifest_path(path).write_text("not-json")
    elif condition == "wrong_path":
        monkeypatch.setattr(database, "DATABASE_PATH", path.parent.parent / "barbershop.db")
    elif condition == "partial_commit":
        with closing(database.open_development_database(path)) as connection:
            connection.execute("DELETE FROM customer_accounts")
            connection.commit()
    else:
        with closing(database.open_development_database(path)) as connection:
            connection.execute("UPDATE services SET default_price = 999 WHERE id = 1")
            connection.commit()
    before = {item.name: item.read_bytes() for item in path.parent.iterdir()}
    monkeypatch.setattr(dev_server.uvicorn, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    assert dev_server.main() == 1
    assert capsys.readouterr().err == "DEVELOPMENT_STARTUP_REFUSED\n"
    with pytest.raises(dev_data.DevelopmentDataError):
        with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)):
            pytest.fail("must not start app")
    assert {item.name: item.read_bytes() for item in path.parent.iterdir()} == before


def test_startup_is_read_only_and_does_not_rotate_credentials(provisioned):
    before = {item.name: item.read_bytes() for item in provisioned.parent.iterdir()}
    with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)) as client:
        assert client.get("/health").status_code == 200
        assert len(app.state.auth_identities) == 2
        with pytest.raises(TypeError):
            app.state.auth_identities["untrusted"] = "bad"
    assert {item.name: item.read_bytes() for item in provisioned.parent.iterdir()} == before

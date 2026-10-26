from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from threading import Barrier, get_ident

from fastapi import Depends
import pytest
from starlette.testclient import TestClient
from starlette.exceptions import HTTPException

from app import dev_data
from app.api import auth_dependencies
from app.api.auth_dependencies import (
    SESSION_COOKIE, ensure_business_access, require_customer, require_owner,
)
from app.core.auth_rate_limit import AuthenticationRateLimiter
from app.core.auth_sessions import InMemorySessionStore
from app.core.local_security import DEVELOPMENT_ORIGIN
from app.db import database, repositories
from app.main import app
from app.services import authentication


AUTH = "/api/v1/auth"
LOGIN_HEADERS = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Protection": "1"}


class Terminal(StringIO):
    def isatty(self):
        return True


@pytest.fixture
def http_data(tmp_path, monkeypatch):
    path = tmp_path / "data" / "dev" / "barbershop.db"
    output = Terminal()
    identities = dev_data.provision_development_data(
        path, environment="development", input_stream=Terminal("PROVISION\n"), output_stream=output,
    )
    codes = {}
    for line in output.getvalue().splitlines():
        if "access code: " in line:
            label, code = line.split(" access code: ")
            codes[label] = code
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    monkeypatch.setattr(auth_dependencies.settings, "environment", "development")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    return path, identities, codes


@pytest.fixture
def client(http_data):
    with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)) as current:
        yield current


def login(client, code, **kwargs):
    return client.post(f"{AUTH}/dev-session", json={"access_code": code}, headers=LOGIN_HEADERS, **kwargs)


def execute(path, statement, parameters=()):
    with closing(database.open_development_database(path)) as connection:
        connection.execute(statement, parameters)
        connection.commit()


@pytest.mark.parametrize("role", ["OWNER", "CUSTOMER"])
def test_login_and_me_use_server_identity_and_cookie_contract(client, http_data, role):
    path, identities, codes = http_data
    response = login(client, codes[role])
    assert response.status_code == 200
    value = response.json()
    principal = value["principal"]
    assert principal["role"] == role and principal["business_id"] == 1
    assert principal["owner_user_id"] == (1 if role == "OWNER" else None)
    assert principal["customer_id"] == (1 if role == "CUSTOMER" else None)
    assert set(value) == {"principal", "expires_at", "csrf_token"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/" in cookie and "Max-Age=3600" in cookie
    assert "Domain=" not in cookie and "Secure" not in cookie
    assert client.cookies[SESSION_COOKIE] not in response.text
    assert codes[role] not in response.text
    assert "$argon2" not in response.text and "fingerprint" not in response.text
    assert response.headers["cache-control"] == "no-store"
    me = client.get(f"{AUTH}/me")
    assert me.status_code == 200 and me.json() == value
    assert "set-cookie" not in me.headers  # No sliding renewal.
    assert me.headers["cache-control"] == "no-store"


def test_successful_login_rotates_cookie_and_old_token(client, http_data):
    codes = http_data[2]
    first = login(client, codes["OWNER"])
    old = client.cookies[SESSION_COOKIE]
    second = login(client, codes["CUSTOMER"])
    assert second.status_code == 200
    assert client.cookies[SESSION_COOKIE] != old
    assert first.json()["csrf_token"] != second.json()["csrf_token"]
    assert client.get(f"{AUTH}/me", headers={"Cookie": f"{SESSION_COOKIE}={old}"}).status_code == 401


def test_failed_login_does_not_clear_or_invalidate_existing_session(client, http_data):
    code = http_data[2]["OWNER"]
    login(client, code)
    old = client.cookies[SESSION_COOKIE]
    invalid = code.split(".")[0] + "." + "w" * 43
    response = login(client, invalid)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "INVALID_CREDENTIALS"}}
    assert "set-cookie" not in response.headers
    assert client.cookies[SESSION_COOKIE] == old
    assert client.get(f"{AUTH}/me").status_code == 200


@pytest.mark.parametrize("statement,role", [
    ("UPDATE users SET role = 'STAFF' WHERE id = 1", "OWNER"),
    ("UPDATE users SET role = 'MANAGER' WHERE id = 1", "OWNER"),
    ("UPDATE users SET is_active = 0 WHERE id = 1", "OWNER"),
    ("UPDATE customer_accounts SET is_active = 0 WHERE id = 1", "CUSTOMER"),
    ("UPDATE customers SET is_active = 0 WHERE id = 1", "CUSTOMER"),
    ("UPDATE businesses SET is_active = 0 WHERE id = 1", "OWNER"),
    ("DELETE FROM customer_accounts WHERE id = 1", "CUSTOMER"),
])
def test_inactive_missing_or_unsupported_identity_fails_generically(client, http_data, statement, role):
    execute(http_data[0], statement)
    response = login(client, http_data[2][role])
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "INVALID_CREDENTIALS"}}


def test_unknown_identity_is_generic_and_dummy_verified(client, monkeypatch):
    calls = []
    original = authentication.verify_access_secret

    def track(encoded, secret):
        calls.append(encoded)
        return original(encoded, secret)

    monkeypatch.setattr(authentication, "verify_access_secret", track)
    response = login(client, "x" * 16 + "." + "s" * 43)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "INVALID_CREDENTIALS"}}
    assert calls == [authentication._DUMMY_HASH]


@pytest.mark.parametrize("seconds,status", [(3599.999, 200), (3600, 401), (3601, 401)])
def test_http_absolute_expiry(client, http_data, seconds, status):
    clock = [datetime(2026, 10, 20, tzinfo=timezone.utc)]
    app.state.auth_sessions = InMemorySessionStore(lambda: clock[0])
    first = login(client, http_data[2]["OWNER"])
    clock[0] += timedelta(seconds=seconds)
    response = client.get(f"{AUTH}/me")
    assert response.status_code == status
    if status == 200:
        assert response.json()["expires_at"] == first.json()["expires_at"]
    else:
        assert "Max-Age=0" in response.headers["set-cookie"]


def test_missing_session_and_logout_lifecycle(client, http_data):
    assert client.get(f"{AUTH}/me").status_code == 401
    result = login(client, http_data[2]["OWNER"])
    token = client.cookies[SESSION_COOKIE]
    headers = {"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": result.json()["csrf_token"]}
    response = client.delete(f"{AUTH}/session", headers=headers)
    assert response.status_code == 204 and not response.content
    cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get(f"{AUTH}/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"}).status_code == 401
    assert client.delete(f"{AUTH}/session", headers=headers).status_code == 401


@pytest.mark.parametrize("token", [None, "wrong"])
def test_logout_csrf_failure_preserves_session(client, http_data, token):
    login(client, http_data[2]["OWNER"])
    headers = {"Origin": DEVELOPMENT_ORIGIN}
    if token is not None:
        headers["X-CSRF-Token"] = token
    assert client.delete(f"{AUTH}/session", headers=headers).status_code == 403
    assert client.get(f"{AUTH}/me").status_code == 200


def test_cross_session_csrf_is_rejected(client, http_data):
    first = login(client, http_data[2]["OWNER"])
    with_second = TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50001))
    try:
        second = login(with_second, http_data[2]["CUSTOMER"])
        assert client.delete(f"{AUTH}/session", headers={"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": second.json()["csrf_token"]}).status_code == 403
        assert client.get(f"{AUTH}/me").status_code == 200
        assert first.json()["csrf_token"] != second.json()["csrf_token"]
    finally:
        with_second.close()


@pytest.mark.parametrize("headers,status", [
    ({"Host": "localhost:8000"}, 400),
    ({"Host": "127.0.0.1"}, 400),
    ({"Host": "127.0.0.1:8001"}, 400),
    ({"Host": "127.0.0.1:8000.attacker.test"}, 400),
    ({"Origin": "http://attacker.test"}, 403),
    ({"Origin": "http://127.0.0.1:8000/"}, 403),
    ({"Origin": "null"}, 403),
])
def test_exact_host_and_origin_restrictions(client, headers, status):
    response = client.get("/health", headers=headers)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/json"},
    {"Content-Type": "application/json", "Origin": DEVELOPMENT_ORIGIN},
    {"Content-Type": "application/json", "X-CSRF-Protection": "1"},
    {"Content-Type": "application/json", "Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Protection": "wrong"},
])
def test_login_csrf_checked_before_credential_work(client, http_data, headers, monkeypatch):
    monkeypatch.setattr(auth_dependencies, "authenticate_access_code_with_fingerprint", lambda *args: pytest.fail("must not verify"))
    assert client.post(f"{AUTH}/dev-session", content=json.dumps({"access_code": http_data[2]["OWNER"]}), headers=headers).status_code == 403


def test_missing_origin_cannot_logout_with_valid_csrf(client, http_data):
    response = login(client, http_data[2]["OWNER"])
    assert client.delete(f"{AUTH}/session", headers={"X-CSRF-Token": response.json()["csrf_token"]}).status_code == 403
    assert client.get(f"{AUTH}/me").status_code == 200


def test_non_loopback_and_forwarded_header_spoofing_rejected(client):
    remote = TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("192.0.2.10", 50001))
    try:
        assert remote.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    finally:
        remote.close()
    assert client.get("https://127.0.0.1:8000/health").status_code == 403


@pytest.mark.parametrize("body", [
    "not-json", "[]", "null", "{}", '{"access_code": 123}',
    '{"access_code": "short"}',
    '{"access_code": "' + "o" * 16 + "." + "s" * 43 + '", "role": "OWNER", "business_id": 2}',
])
def test_invalid_inputs_are_redacted_and_counted(client, body):
    response = client.post(f"{AUTH}/dev-session", content=body, headers={**LOGIN_HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}
    assert len(app.state.auth_limiter._global) == 1
    assert body not in response.text


def test_oversized_body_and_non_json_content_type(client):
    response = client.post(f"{AUTH}/dev-session", content="x" * 5000, headers={**LOGIN_HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 413
    assert len(app.state.auth_limiter._global) == 1
    assert client.post(f"{AUTH}/dev-session", content="not json", headers=LOGIN_HEADERS).status_code == 415


@pytest.mark.parametrize("kind", ["known", "unknown", "malformed"])
def test_integrated_selector_limits_precede_argon(client, http_data, monkeypatch, kind):
    calls = []
    app.state.auth_limiter = AuthenticationRateLimiter(lambda: 0.0)
    actual = auth_dependencies.authenticate_access_code_with_fingerprint

    def track(*args):
        calls.append(True)
        return actual(*args)

    monkeypatch.setattr(auth_dependencies, "authenticate_access_code_with_fingerprint", track)
    code = http_data[2]["OWNER"] if kind == "known" else "x" * 16 + "." + "s" * 43
    for _ in range(5):
        response = login(client, code) if kind != "malformed" else client.post(f"{AUTH}/dev-session", content="bad", headers={**LOGIN_HEADERS, "Content-Type": "application/json"})
        assert response.status_code != 429
    response = login(client, code) if kind != "malformed" else client.post(f"{AUTH}/dev-session", content="bad", headers={**LOGIN_HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert len(calls) == (0 if kind == "malformed" else 5)


def test_different_unknown_selectors_cannot_bypass_global_limit(client):
    app.state.auth_limiter = AuthenticationRateLimiter(lambda: 0.0)
    for index in range(20):
        assert login(client, f"{index:016d}.{'s' * 43}").status_code == 401
    response = login(client, "z" * 16 + "." + "s" * 43)
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


@pytest.mark.parametrize("statement", [
    "UPDATE users SET is_active = 0 WHERE id = 1",
    "UPDATE users SET role = 'STAFF' WHERE id = 1",
    "UPDATE businesses SET is_active = 0 WHERE id = 1",
    "UPDATE users SET username = 'changed' WHERE id = 1",
    "DELETE FROM users WHERE id = 1",
])
def test_authenticated_requests_revalidate_current_identity(client, http_data, statement):
    login(client, http_data[2]["OWNER"])
    execute(http_data[0], statement)
    response = client.get(f"{AUTH}/me")
    assert response.status_code == 401
    assert not app.state.auth_sessions._sessions


@pytest.mark.parametrize("role", ["OWNER", "CUSTOMER"])
def test_cli_rotation_invalidates_http_session(client, http_data, role):
    path, identities, codes = http_data
    login(client, codes[role])
    selector = codes[role].split(".")[0]
    output = Terminal()
    dev_data.rotate_development_credential(path, selector=selector, environment="development", input_stream=Terminal(f"ROTATE {selector}\n"), output_stream=output)
    assert client.get(f"{AUTH}/me").status_code == 401
    assert login(client, codes[role]).status_code == 401
    new_code = next(line.split("access code: ")[1] for line in output.getvalue().splitlines() if "access code: " in line)
    assert login(client, new_code).status_code == 200


def test_rotation_race_cannot_bind_old_secret_to_new_fingerprint(client, http_data, monkeypatch):
    path, identities, codes = http_data
    actual = auth_dependencies.authenticate_access_code_with_fingerprint

    def rotate_after_verification(*args):
        verified = actual(*args)
        execute(path, "UPDATE users SET password_hash = ? WHERE id = 1", (authentication.hash_access_secret("n" * 43),))
        return verified

    monkeypatch.setattr(auth_dependencies, "authenticate_access_code_with_fingerprint", rotate_after_verification)
    assert login(client, codes["OWNER"]).status_code == 200
    assert client.get(f"{AUTH}/me").status_code == 401


def test_role_and_tenant_dependencies(client, http_data):
    # Probe routes exist only in this test and are removed afterwards.
    existing = list(app.router.routes)

    @app.get("/test-owner")
    def owner(context=Depends(require_owner)):
        ensure_business_access(context, 1)
        return {"role": context.principal.role}

    @app.get("/test-customer")
    def customer(context=Depends(require_customer)):
        return {"role": context.principal.role}

    @app.get("/test-foreign")
    def foreign(context=Depends(require_owner)):
        ensure_business_access(context, 2)
        return {"bad": True}

    try:
        login(client, http_data[2]["OWNER"])
        assert client.get("/test-owner").status_code == 200
        assert client.get("/test-customer").status_code == 403
        assert client.get("/test-foreign").status_code == 404
        login(client, http_data[2]["CUSTOMER"])
        assert client.get("/test-owner").status_code == 403
        assert client.get("/test-customer").status_code == 200
    finally:
        app.router.routes[:] = existing


def test_connections_created_used_and_closed_in_same_worker(client, http_data, monkeypatch):
    actual = database.open_development_database
    records = []

    class Tracked:
        def __init__(self, connection):
            self.connection, self.thread, self.closed = connection, get_ident(), False

        def execute(self, *args, **kwargs):
            assert get_ident() == self.thread and not self.closed
            return self.connection.execute(*args, **kwargs)

        def close(self):
            assert get_ident() == self.thread
            self.connection.close()
            self.closed = True

    def open_tracked(*args, **kwargs):
        tracked = Tracked(actual(*args, **kwargs))
        records.append(tracked)
        return tracked

    monkeypatch.setattr(database, "open_development_database", open_tracked)
    assert login(client, http_data[2]["OWNER"]).status_code == 200
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.get(f"{AUTH}/me"), range(8)))
    assert all(response.status_code == 200 for response in responses)
    assert len(records) == 9 and all(record.closed for record in records)


@pytest.mark.parametrize("material", ["access_code", "session_token", "credential_hash"])
def test_unexpected_error_is_redacted_without_raw_exception_logs(client, http_data, monkeypatch, caplog, material):
    code = http_data[2]["OWNER"]
    if material == "session_token":
        login(client, code)
        secret = client.cookies[SESSION_COOKIE]
    elif material == "credential_hash":
        with closing(database.open_development_database(http_data[0])) as connection:
            secret = repositories.get_user_account_identity(connection, 1, 1)["password_hash"]
    else:
        secret = code

    def fail(*args):
        raise RuntimeError(secret)

    monkeypatch.setattr(auth_dependencies, "authenticate_access_code_with_fingerprint", fail)
    response = login(client, code)
    assert response.status_code == 503
    assert secret not in response.text and secret not in caplog.text
    assert response.headers["cache-control"] == "no-store"


def test_only_approved_routes_and_no_cors(client):
    assert set(app.openapi()["paths"]) == {"/health", f"{AUTH}/dev-session", f"{AUTH}/me", f"{AUTH}/session",
                                          "/api/v1/customer/services", "/api/v1/customer/staff", "/api/v1/customer/booking-searches",
                                          "/api/v1/customer/appointments", "/api/v1/customer/booking-searches/{search_id}/selection"}
    response = client.options(f"{AUTH}/dev-session", headers={"Origin": "http://attacker.test", "Access-Control-Request-Method": "POST"})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("kind", ["owner_tenant", "customer_link", "foreign_customer_link"])
def test_changed_tenant_or_customer_binding_invalidates_session(client, http_data, kind):
    path = http_data[0]
    role = "OWNER" if kind == "owner_tenant" else "CUSTOMER"
    assert login(client, http_data[2][role]).status_code == 200
    execute(path, "INSERT INTO businesses (id, business_name) VALUES (2, 'Other Synthetic Shop')")
    if kind == "owner_tenant":
        execute(path, "UPDATE users SET business_id = 2 WHERE id = 1")
    else:
        execute(path, "INSERT INTO customers (id, business_id, name, customer_type) VALUES (2, ?, 'Other Synthetic Customer', 'REGISTERED')", (2 if kind == "foreign_customer_link" else 1,))
        with closing(database.open_development_database(path)) as connection:
            if kind == "foreign_customer_link":
                connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("UPDATE customer_accounts SET customer_id = 2 WHERE id = 1")
            connection.commit()
    assert client.get(f"{AUTH}/me").status_code == 401


def test_other_identity_deactivation_does_not_revoke_owner_session(client, http_data):
    login(client, http_data[2]["OWNER"])
    execute(http_data[0], "UPDATE customer_accounts SET is_active = 0 WHERE id = 1")
    assert client.get(f"{AUTH}/me").status_code == 200


def test_session_capacity_failure_preserves_existing_login(client, http_data):
    app.state.auth_sessions = InMemorySessionStore(max_sessions=1)
    first = login(client, http_data[2]["OWNER"])
    assert first.status_code == 200
    another = TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50001))
    try:
        response = login(another, http_data[2]["CUSTOMER"])
        assert response.status_code == 503
        assert response.json() == {"error": {"code": "SESSION_CAPACITY_REACHED"}}
        assert client.get(f"{AUTH}/me").status_code == 200
    finally:
        another.close()


def test_duplicate_security_headers_and_query_identity_are_rejected(client, http_data):
    assert client.get("/health", headers=[("Host", "127.0.0.1:8000"), ("Host", "evil.test")]).status_code == 400
    assert client.post(f"{AUTH}/dev-session", json={"access_code": http_data[2]["OWNER"]}, headers=[("Origin", DEVELOPMENT_ORIGIN), ("Origin", DEVELOPMENT_ORIGIN), ("X-CSRF-Protection", "1")]).status_code == 403
    login(client, http_data[2]["OWNER"])
    assert client.get(f"{AUTH}/me?business_id=2&role=OWNER").status_code == 422


def test_global_limits_remain_atomic_across_http_calls(client, monkeypatch):
    app.state.auth_limiter = AuthenticationRateLimiter(lambda: 0.0)
    monkeypatch.setattr(auth_dependencies, "authenticate_access_code_with_fingerprint", lambda *args: (_ for _ in ()).throw(authentication.AuthenticationError()))
    barrier = Barrier(25)

    def attempt(index):
        barrier.wait(timeout=5)
        return login(client, f"{index:016d}.{'s' * 43}").status_code

    with ThreadPoolExecutor(max_workers=25) as pool:
        statuses = list(pool.map(attempt, range(25)))
    assert statuses.count(401) == 20 and statuses.count(429) == 5


def test_restart_discards_in_memory_sessions(http_data):
    with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)) as first:
        assert login(first, http_data[2]["OWNER"]).status_code == 200
        token = first.cookies[SESSION_COOKIE]
    assert not hasattr(app.state, "auth_sessions")
    with TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000)) as second:
        assert second.get(f"{AUTH}/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"}).status_code == 401


def test_malformed_known_selector_secret_cannot_bypass_selector_limit(client, http_data, monkeypatch):
    code = http_data[2]["OWNER"]
    selector = code.split(".")[0]
    monkeypatch.setattr(auth_dependencies, "authenticate_access_code_with_fingerprint", lambda *args: pytest.fail("must not verify"))
    for _ in range(5):
        assert login(client, f"{selector}.short").status_code == 422
    assert login(client, code).status_code == 429


@pytest.mark.parametrize("method,path", [
    ("POST", f"{AUTH}/dev-session/"),
    ("DELETE", f"{AUTH}/session/"),
    ("GET", f"{AUTH}/me/"),
    ("POST", f"{AUTH}/unknown/"),
])
def test_noncanonical_auth_paths_never_redirect_or_reflect_queries(client, http_data, method, path):
    assert login(client, http_data[2]["OWNER"]).status_code == 200
    token = client.cookies[SESSION_COOKIE]
    marker = "AUDIT_ONLY_SENSITIVE_VALUE"
    response = client.request(
        method, path, params={"access_code": marker, "session_token": marker},
        headers={"Origin": DEVELOPMENT_ORIGIN, "Content-Type": "application/json"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "HTTP_ERROR"}}
    assert "location" not in response.headers
    assert marker not in response.text and marker not in str(response.headers)
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert client.cookies[SESSION_COOKIE] == token
    assert client.get(f"{AUTH}/me").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("POST", f"{AUTH}/dev-session"),
    ("GET", f"{AUTH}/me"),
    ("DELETE", f"{AUTH}/session"),
])
def test_all_auth_endpoints_reject_unexpected_queries_without_changing_session(client, http_data, method, path):
    first = login(client, http_data[2]["OWNER"])
    assert first.status_code == 200
    token = client.cookies[SESSION_COOKIE]
    attempts = len(app.state.auth_limiter._global)
    response = client.request(
        method, path, params={"unexpected": "AUDIT_ONLY_VALUE"},
        headers={**LOGIN_HEADERS, "X-CSRF-Token": first.json()["csrf_token"]},
        **({"json": {"access_code": http_data[2]["OWNER"]}} if method == "POST" else {}),
    )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}
    assert "AUDIT_ONLY_VALUE" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert client.cookies[SESSION_COOKIE] == token
    assert client.get(f"{AUTH}/me").status_code == 200
    if method == "POST":
        assert len(app.state.auth_limiter._global) == attempts + 1


@pytest.mark.parametrize("role", ["OWNER", "CUSTOMER"])
@pytest.mark.parametrize("change", ["identity", "fingerprint"])
def test_shared_auth_accepts_endpoint_queries_and_still_revalidates(client, http_data, role, change):
    existing = list(app.router.routes)

    @app.get("/test-authenticated-query")
    def query_probe(day: str, context=Depends(auth_dependencies.require_authenticated_session)):
        return {"day": day, "subject": context.principal.subject, "business_id": context.principal.business_id}

    try:
        url = "/test-authenticated-query?day=2026-10-20"
        assert client.get(url).status_code == 401
        first = login(client, http_data[2][role])
        assert first.status_code == 200
        response = client.get(url)
        assert response.status_code == 200
        assert response.json() == {
            "day": "2026-10-20", "subject": first.json()["principal"]["subject"], "business_id": 1,
        }
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        table = "users" if role == "OWNER" else "customer_accounts"
        if change == "identity":
            execute(http_data[0], f"UPDATE {table} SET is_active = 0 WHERE id = 1")
        else:
            execute(http_data[0], f"UPDATE {table} SET password_hash = ? WHERE id = 1",
                    (authentication.hash_access_secret("n" * 43),))
        denied = client.get(url)
        assert denied.status_code == 401
        assert denied.json() == {"error": {"code": "UNAUTHENTICATED"}}
        assert "Max-Age=0" in denied.headers["set-cookie"]
        assert not app.state.auth_sessions._sessions
    finally:
        app.router.routes[:] = existing


@pytest.mark.parametrize("method,path,allowed", [
    ("GET", f"{AUTH}/dev-session", "POST"),
    ("POST", f"{AUTH}/me", "GET"),
    ("GET", f"{AUTH}/session", "DELETE"),
])
def test_method_not_allowed_preserves_framework_allow_header(client, method, path, allowed):
    response = client.request(method, path, headers=LOGIN_HEADERS, follow_redirects=False)
    assert response.status_code == 405
    assert response.headers["allow"] == allowed
    assert response.json() == {"error": {"code": "HTTP_ERROR"}}
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers


def test_http_exception_preserves_headers_but_redacts_details_and_forces_no_store(client, caplog):
    existing = list(app.router.routes)
    marker = "AUDIT_ONLY_SENSITIVE_EXCEPTION_DETAIL"

    @app.get("/test-http-exception")
    def exception_probe():
        raise HTTPException(
            status_code=405, detail=marker,
            headers={"Allow": "POST", "Cache-Control": "public, max-age=3600"},
        )

    try:
        response = client.get("/test-http-exception")
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"error": {"code": "HTTP_ERROR"}}
        assert marker not in response.text and marker not in str(response.headers)
        assert marker not in caplog.text
    finally:
        app.router.routes[:] = existing

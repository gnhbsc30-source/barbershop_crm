from types import MappingProxyType

import pytest
from starlette.testclient import TestClient

from app.core.local_security import DEVELOPMENT_ORIGIN
from app.core.tenant_slugs import local_slug_mapping
from app.main import app
from app.services.authentication import IdentityReference, hash_access_secret
from test_auth_api import execute, http_data, login


@pytest.fixture
def two_businesses(http_data, monkeypatch):
    path, _, codes = http_data
    execute(path, "INSERT INTO businesses (id,business_name,phone) VALUES (2,'Other','1234567')")
    monkeypatch.setenv("BARBERSHOP_DEV_SLUGS", '{"first":1,"other":2}')
    return path, codes


def browser():
    return TestClient(app, base_url=DEVELOPMENT_ORIGIN, client=("127.0.0.1", 50000))


def test_public_projection_and_tenant_boundaries(two_businesses):
    path, codes = two_businesses
    with browser() as client:
        response = client.get("/api/v1/public/businesses/other")
        assert response.json() == {"slug": "other", "display_name": "Other"}
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/public/businesses/Other").status_code == 404
        assert client.get("/api/v1/auth/tenant-context/first").status_code == 401
        for role, endpoint in [
            ("CUSTOMER", "/api/v1/customer/services"),
            ("OWNER", "/api/v1/owner/booking-options"),
        ]:
            assert login(client, codes[role]).status_code == 200
            assert client.get("/api/v1/auth/tenant-context/first").json() == {"slug": "first"}
            assert client.get("/api/v1/auth/tenant-context/other").status_code == 404
            assert client.get(endpoint).status_code == 200
            assert client.get(endpoint, headers={"X-Barbershop-Slug": "first"}).status_code == 200
            assert client.get(endpoint, headers={"X-Barbershop-Slug": "other"}).status_code == 404
            assert client.get(endpoint, headers=[("X-Barbershop-Slug", "first")] * 2).status_code == 422
        execute(path, "UPDATE businesses SET is_active = 0 WHERE id = 2")
        assert client.get("/api/v1/public/businesses/other").status_code == 404


def test_second_business_customer_and_mismatched_write(two_businesses, monkeypatch):
    from app.services.customer_booking import CustomerBooking

    path, _ = two_businesses
    selector, secret = "b" * 16, "s" * 43
    with browser() as client:
        execute(path, "INSERT INTO customers (id,business_id,name,customer_type) VALUES (20,2,'Other','REGISTERED')")
        execute(path, "INSERT INTO customer_accounts (id,business_id,customer_id,username,password_hash) VALUES (2,2,20,?,?)",
                (selector, hash_access_secret(secret)))
        app.state.auth_identities = MappingProxyType({
            **app.state.auth_identities, selector: IdentityReference("customer_account", 2, 2),
        })
        session = login(client, selector + "." + secret).json()
        assert client.get("/api/v1/auth/tenant-context/other").status_code == 200
        assert client.get("/api/v1/auth/tenant-context/first").status_code == 404
        assert client.get("/api/v1/customer/staff?service_id=1", headers={"X-Barbershop-Slug": "other"}).status_code == 404
        monkeypatch.setattr(CustomerBooking, "book", lambda *args: pytest.fail("tenant mismatch reached booking"))
        response = client.post("/api/v1/customer/appointments", json={
            "service_ids": [1], "start_datetime": "2027-01-04T10:00:00",
            "staff_selection": {"type": "SPECIFIC", "staff_id": 1},
            "confirmation": {"items": [{"service_id": 1, "duration_minutes": 30, "price": "10.00"}],
                             "total_duration_minutes": 30, "total_price": "10.00", "currency": "ILS"},
        }, headers={"Origin": DEVELOPMENT_ORIGIN, "X-CSRF-Token": session["csrf_token"], "X-Barbershop-Slug": "first"})
        assert response.status_code == 404


@pytest.mark.parametrize("mapping", ['{"x":1,"x":2}', '{"x":1,"y":1}', '{"X":1}', '{"x":999}'])
def test_invalid_slug_mapping(http_data, monkeypatch, mapping):
    from contextlib import closing
    from app.db.database import open_development_database

    monkeypatch.setenv("BARBERSHOP_DEV_SLUGS", mapping)
    with closing(open_development_database(http_data[0])) as connection:
        with pytest.raises(ValueError, match="INVALID_DEVELOPMENT_SLUG_CONFIGURATION"):
            local_slug_mapping(connection)

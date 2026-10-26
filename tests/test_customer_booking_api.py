"""Customer HTTP integration uses the isolated provisioned auth test fixture."""

import pytest

from test_auth_api import client, execute, http_data, login


ROOT = "/api/v1/customer"


def test_catalog_is_customer_only_and_safe(client, http_data):
    assert client.get(ROOT + "/services").status_code == 401
    assert login(client, http_data[2]["OWNER"]).status_code == 200
    assert client.get(ROOT + "/services").status_code == 403
    assert login(client, http_data[2]["CUSTOMER"]).status_code == 200
    response = client.get(ROOT + "/services")
    assert response.status_code == 200
    assert response.json()["services"][0]["default_price"] == "80.00"
    assert response.headers["cache-control"] == "no-store"
    staff = client.get(ROOT + "/staff?service_id=2&service_id=1")
    assert staff.status_code == 200
    assert staff.json()["service_ids"] == [2, 1]
    assert len(staff.json()["staff"]) == 2
    assert staff.json()["staff"][0]["total_price"] == "120.00"
    assert [item["service_id"] for item in staff.json()["staff"][0]["items"]] == [2, 1]
    assert not any(field in staff.text for field in ("phone", "email", "user_id", "password"))


@pytest.mark.parametrize("query,status", [("", 422), ("?service_id=true", 422), ("?service_id=1&service_id=1", 422),
                                              ("?service_id=999", 404), ("?service_id=1&business_id=2", 422)])
def test_staff_selection_validation(client, http_data, query, status):
    login(client, http_data[2]["CUSTOMER"])
    assert client.get(ROOT + "/staff" + query).status_code == status


def test_catalog_scope_overrides_and_compatibility(client, http_data):
    login(client, http_data[2]["CUSTOMER"])
    path = http_data[0]
    execute(path, "INSERT INTO businesses (id,business_name) VALUES (2,'Foreign')")
    execute(path, "INSERT INTO services (id,business_id,name,default_duration_minutes,default_price) VALUES (99,2,'Foreign',30,1)")
    execute(path, "UPDATE staff_services SET price=90,duration_minutes=60 WHERE staff_id=1 AND service_id=1")
    execute(path, "UPDATE staff_services SET is_active=0 WHERE staff_id=2 AND service_id=2")
    result = client.get(ROOT + "/staff?service_id=1&service_id=2").json()["staff"]
    assert len(result) == 1
    assert result[0]["total_price"] == "130.00" and result[0]["total_duration_minutes"] == 80
    assert client.get(ROOT + "/staff?service_id=99").status_code == 404
    assert "Foreign" not in client.get(ROOT + "/services").text


def test_invalid_catalog_precision_is_configuration_error(client, http_data):
    login(client, http_data[2]["CUSTOMER"])
    execute(http_data[0], "UPDATE services SET default_price=1.005 WHERE id=1")
    response = client.get(ROOT + "/services")
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "BOOKING_CONFIGURATION_INVALID"}}

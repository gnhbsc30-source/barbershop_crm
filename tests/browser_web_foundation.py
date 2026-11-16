"""Optional Chromium integration check.

Build the frontend, then run this file explicitly with pytest in an environment
with Playwright and Chromium installed. Route requests at the exact dev origin
through the FastAPI test client so an existing launcher need not be stopped.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from app.core import booking_clock
from app.core.auth_sessions import InMemorySessionStore
from app.core.local_security import DEVELOPMENT_ORIGIN
from app.main import app
from app.services import appointments, availability
from test_tenant_context import browser, http_data, two_businesses


def test_chromium_web_foundation(two_businesses, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    _, codes = two_businesses
    start = datetime(2027, 1, 3, 22, tzinfo=timezone.utc)
    auth_now = [start]

    class SchedulingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return start.astimezone(tz) if tz else cls(2027, 1, 4, 0)

    monkeypatch.setattr(appointments, "datetime", SchedulingClock)
    monkeypatch.setattr(availability, "datetime", SchedulingClock)
    monkeypatch.setattr(booking_clock, "scheduling_clock_ready", lambda zone: zone == "Asia/Jerusalem")

    with browser() as client, sync_playwright() as playwright:
        app.state.auth_sessions = InMemorySessionStore(lambda: auth_now[0])
        chromium = playwright.chromium.launch()
        try:
            page = chromium.new_page(viewport={"width": 360, "height": 800}, timezone_id="America/New_York")
            page.clock.install(time=start)
            requested_slots = []

            def relay(route):
                request = route.request
                url = urlsplit(request.url)
                if url.netloc != "127.0.0.1:8000":
                    route.abort()
                    return
                path = url.path + (f"?{url.query}" if url.query else "")
                headers = request.all_headers()
                if path.startswith("/api/v1/customer/available-slots"):
                    requested_slots.append(path)
                    assert headers.get("x-barbershop-slug") == "first"
                response = client.request(request.method, path, headers=headers, content=request.post_data_buffer,
                                          follow_redirects=False)
                # Browser cookie handling is exercised by the real Set-Cookie.
                # Prevent the shared ASGI client from authenticating on its own.
                client.cookies.clear()
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

            page.route("**/*", relay)

            def visit(path):
                return page.goto(DEVELOPMENT_ORIGIN + path)

            def sign_in(role):
                page.get_by_label("קוד גישה").fill(codes[role])
                page.get_by_role("button", name="כניסה", exact=True).click()

            assert visit("/b/Invalid").status == 404
            visit("/b/unknown")
            expect(page.get_by_role("alert")).to_have_text("המספרה לא נמצאה.")
            visit("/b/first")
            expect(page.locator("html")).to_have_attribute("dir", "rtl")
            expect(page.locator("html")).to_have_attribute("lang", "he")
            expect(page.get_by_role("heading", level=1)).to_have_text("Synthetic Development Barbershop")
            sign_in("CUSTOMER")
            expect(page.get_by_role("link", name="קביעת תור")).to_be_visible()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert codes["CUSTOMER"] not in page.url
            session = next(cookie for cookie in page.context.cookies() if cookie["name"] == "barbershop_dev_session")
            assert session["httpOnly"] and session["sameSite"] == "Strict"
            visit("/b/other/book")
            expect(page.get_by_role("alert")).to_contain_text("החיבור אינו מתאים")
            visit("/b/first/owner/calendar")
            expect(page.get_by_role("alert")).to_contain_text("אין הרשאה")
            visit("/b/first/book")
            page.get_by_role("checkbox", name="Haircut").check()
            page.get_by_role("button", name="לבחירת צוות ויום").click()
            expect(page.get_by_label("תאריך")).to_have_value("2027-01-04")
            page.get_by_label("איש צוות").select_option("1")
            first_slot = page.get_by_role("radio", name="09:00", exact=True)
            expect(first_slot).to_be_visible()
            first_slot.focus()
            page.keyboard.press("Space")
            expect(first_slot).to_be_checked()
            page.get_by_label("תאריך").fill("2027-01-05")
            expect(first_slot).not_to_be_checked()
            page.get_by_label("איש צוות").select_option("2")
            expect(first_slot).to_be_visible()
            assert any("date=2027-01-05" in path and "staff_id=2" in path for path in requested_slots)
            page.get_by_role("checkbox", name="Beard trim").check()
            expect(first_slot).to_have_count(0)
            page.get_by_label("איש צוות").select_option("1")
            expect(first_slot).to_be_visible()
            assert "service_id=1&service_id=2" in requested_slots[-1]
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            page.set_viewport_size({"width": 1280, "height": 900})
            expect(first_slot).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            page.reload()
            expect(page.get_by_role("checkbox", name="Haircut")).to_be_visible()
            expect(first_slot).to_have_count(0)
            page.get_by_role("button", name="יציאה").click()
            expect(page.get_by_label("קוד גישה")).to_be_visible()
            assert not any(cookie["name"] == "barbershop_dev_session" for cookie in page.context.cookies())
            expect(page.get_by_role("checkbox")).to_have_count(0)
            sign_in("OWNER")
            expect(page.get_by_role("alert")).to_contain_text("אין הרשאה")
            visit("/b/first/owner/calendar")
            expect(page.get_by_role("heading", name="יומן תורים")).to_be_visible()
            auth_now[0] += timedelta(hours=1, seconds=1)
            page.clock.fast_forward(3_601_000)
            expect(page.get_by_label("קוד גישה")).to_be_visible()
            expect(page.get_by_role("heading", name="יומן תורים")).to_have_count(0)
            page.reload()
            expect(page.get_by_label("קוד גישה")).to_be_visible()
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/api/missing").status_code == 404
        finally:
            chromium.close()


def test_cross_tab_revalidation_and_long_branding(two_businesses):
    from types import MappingProxyType

    from playwright.sync_api import expect, sync_playwright

    from app.services.authentication import IdentityReference, hash_access_secret
    from test_auth_api import execute

    path, codes = two_businesses
    foreign_selector, foreign_secret = "z" * 16, "v" * 43
    long_hebrew = "א" * 120
    mixed_name = "אבג" * 15 + "English" * 9 + "מספרה"
    execute(path, "UPDATE businesses SET display_name = ? WHERE id = 2", (long_hebrew,))
    # Change the first business only after startup so fixture integrity is still checked.
    with browser() as client, sync_playwright() as playwright:
        execute(path, "UPDATE businesses SET display_name = ? WHERE id = 1", (mixed_name,))
        execute(path, "INSERT INTO customers (id,business_id,name,customer_type) VALUES (20,2,'Synthetic B','REGISTERED')")
        execute(path, """INSERT INTO customer_accounts (id,business_id,customer_id,username,password_hash)
                         VALUES (2,2,20,?,?)""", (foreign_selector, hash_access_secret(foreign_secret)))
        app.state.auth_identities = MappingProxyType({
            **app.state.auth_identities, foreign_selector: IdentityReference("customer_account", 2, 2),
        })
        large_id = 9007199254740993
        execute(path, """INSERT INTO services (id,business_id,name,default_duration_minutes,default_price)
                         VALUES (?,1,'Large synthetic service',30,10)""", (large_id,))
        execute(path, "INSERT INTO staff_services (business_id,staff_id,service_id) VALUES (1,1,?)", (large_id,))
        chromium = playwright.chromium.launch()
        try:
            context = chromium.new_context(viewport={"width": 320, "height": 800})
            paths = []
            failed_logout = False
            hold_catalog = False
            held_catalogs = []

            def relay(route):
                request = route.request
                url = urlsplit(request.url)
                if url.netloc != "127.0.0.1:8000":
                    route.abort()
                    return
                endpoint = url.path + (f"?{url.query}" if url.query else "")
                paths.append(endpoint)
                if endpoint == "/api/v1/auth/session" and failed_logout:
                    route.fulfill(status=503, json={"error": {"code": "TEST_UNAVAILABLE"}})
                    return
                response = client.request(request.method, endpoint, headers=request.all_headers(),
                                          content=request.post_data_buffer, follow_redirects=False)
                client.cookies.clear()
                if hold_catalog and endpoint == "/api/v1/customer/services":
                    held_catalogs.append((route, response))
                    return
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

            context.route("**/*", relay)
            first, second = context.new_page(), context.new_page()

            def visit(page, endpoint):
                return page.goto(DEVELOPMENT_ORIGIN + endpoint)

            def sign_in(page, code):
                page.get_by_label("קוד גישה").fill(code)
                page.get_by_role("button", name="כניסה", exact=True).click()

            def logout(page):
                page.get_by_role("button", name="יציאה").click()
                expect(page.get_by_label("קוד גישה")).to_be_visible()

            visit(first, "/b/first/book")
            sign_in(first, codes["CUSTOMER"])
            service = first.get_by_role("checkbox", name="Large synthetic service")
            expect(service).to_be_visible()
            service.check()
            first.get_by_role("button", name="לבחירת צוות ויום").click()
            expect(first.get_by_label("איש צוות")).to_be_visible()
            assert f"/api/v1/customer/staff?service_id={large_id}" in paths
            visit(second, "/b/first")
            expect(second.get_by_role("button", name="יציאה")).to_be_visible()
            logout(second)
            expect(first.get_by_label("קוד גישה")).to_be_visible()
            expect(service).to_have_count(0)

            sign_in(first, codes["CUSTOMER"])
            expect(service).to_be_visible()
            # Replace the shared cookie using the same login route, while the
            # first tab has a catalog response pending under the old identity.
            hold_catalog = True
            with first.expect_request(lambda request: request.url.endswith("/customer/services")):
                first.reload(wait_until="domcontentloaded")
            def replace_in_second(code):
                status = second.evaluate("""async code => {
                  const response = await fetch('/api/v1/auth/dev-session', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRF-Protection': '1'},
                    body: JSON.stringify({access_code: code})
                  });
                  return response.status;
                }""", code)
                assert status == 200
                first.bring_to_front()
                first.evaluate("window.dispatchEvent(new Event('focus'))")

            replace_in_second(codes["OWNER"])
            expect(first.get_by_role("alert")).to_contain_text("אין הרשאה")
            assert held_catalogs
            from playwright.sync_api import Error
            for route, response in held_catalogs:
                try:
                    route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
                except Error as error:
                    if "interception" not in str(error).lower() and "invalid" not in str(error).lower():
                        raise
            expect(service).to_have_count(0)
            hold_catalog = False

            replace_in_second(foreign_selector + "." + foreign_secret)
            expect(first.get_by_role("alert")).to_contain_text("החיבור אינו מתאים")
            expect(service).to_have_count(0)
            visit(second, "/b/other/book")
            expect(second.get_by_role("heading", level=1)).to_have_text(long_hebrew)
            expect(second.locator("h1 bdi")).to_have_text(long_hebrew)
            for zoom in (1, 2):
                second.evaluate("zoom => { document.documentElement.style.zoom = zoom; }", zoom)
                assert second.evaluate("document.documentElement.scrollWidth <= innerWidth")
                button = second.get_by_role("button", name="יציאה")
                button.focus()
                expect(button).to_be_focused()
            failed_logout = True
            second.keyboard.press("Enter")
            expect(second.get_by_role("alert")).to_contain_text("היציאה נכשלה")
            expect(second.get_by_role("status")).to_contain_text("האזור האישי מוסתר")
            expect(second.get_by_role("button", name="יציאה")).to_be_enabled()
            failed_logout = False
            logout(second)
            expect(first.get_by_label("קוד גישה")).to_be_visible()

            visit(first, "/b/first")
            expect(first.locator("h1 bdi")).to_have_text(mixed_name)
            sign_in(first, codes["OWNER"])
            expect(first.get_by_role("link", name="יומן")).to_be_visible()
            for zoom in (1, 2):
                first.evaluate("zoom => { document.documentElement.style.zoom = zoom; }", zoom)
                assert first.evaluate("document.documentElement.scrollWidth <= innerWidth")
                button = first.get_by_role("button", name="יציאה")
                button.focus()
                expect(button).to_be_focused()
        finally:
            chromium.close()


def test_chromium_through_supported_launcher(two_businesses):
    import os
    import socket
    import subprocess
    import sys
    from time import monotonic, sleep
    from urllib.error import URLError
    from urllib.request import urlopen
    from zoneinfo import ZoneInfo

    import pytest
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8000))
        except OSError:
            pytest.skip("Port 8000 is occupied or loopback binding is unavailable")
    path, codes = two_businesses
    env = {**os.environ, "DATABASE_PATH": str(path), "ENVIRONMENT": "development",
           "TZ": "Asia/Jerusalem", "WEB_CONCURRENCY": "1", "BARBERSHOP_DEV_SLUGS": '{"first":1,"other":2}'}
    with subprocess.Popen([sys.executable, "-m", "app.dev_server"], env=env) as server:
        try:
            deadline = monotonic() + 10
            while monotonic() < deadline and server.poll() is None:
                try:
                    with urlopen(DEVELOPMENT_ORIGIN + "/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except URLError:
                    sleep(0.1)
            else:
                pytest.fail("Supported development launcher did not start")
            with sync_playwright() as playwright:
                chromium = playwright.chromium.launch()
                try:
                    page = chromium.new_page()
                    page.goto(DEVELOPMENT_ORIGIN + "/b/first/book")
                    page.get_by_label("קוד גישה").fill(codes["CUSTOMER"])
                    page.get_by_role("button", name="כניסה", exact=True).click()
                    expect(page.get_by_role("checkbox", name="Haircut")).to_be_visible()
                    page.reload()
                    expect(page.get_by_role("checkbox", name="Haircut")).to_be_visible()
                    page.get_by_role("checkbox", name="Haircut").check()
                    page.get_by_role("button", name="לבחירת צוות ויום").click()
                    day = datetime.now(ZoneInfo("Asia/Jerusalem")).date() + timedelta(days=1)
                    while day.weekday() in (4, 5):
                        day += timedelta(days=1)
                    page.get_by_label("תאריך").fill(day.isoformat())
                    page.get_by_label("איש צוות").select_option("1")
                    expect(page.get_by_role("radio", name="09:00", exact=True)).to_be_visible()
                    page.goto(DEVELOPMENT_ORIGIN + "/b/other/book")
                    expect(page.get_by_role("alert")).to_contain_text("החיבור אינו מתאים")
                    page.get_by_role("button", name="יציאה").click()
                    expect(page.get_by_label("קוד גישה")).to_be_visible()
                finally:
                    chromium.close()
        finally:
            server.terminate()
            server.wait(timeout=5)

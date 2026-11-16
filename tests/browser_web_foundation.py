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

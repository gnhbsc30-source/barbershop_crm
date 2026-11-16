# Local Web foundation

From the repository root, run `npm ci --prefix frontend` and
`npm --prefix frontend run build`. Use the existing synthetic development data
and start `python -m app.dev_server` with `TZ=Asia/Jerusalem` and a server-side
`BARBERSHOP_DEV_SLUGS` JSON mapping. For a synthetic business with ID 1, set
`BARBERSHOP_DEV_SLUGS='{"demo":1}'` and open
[the local demo](http://127.0.0.1:8000/b/demo). An unset mapping leaves the
existing API usable without public business URLs. Access-code login is for the
local development fixture only.

Run `npm --prefix frontend run check`, `npm --prefix frontend test`, and the
Python regression suite with `python -m pytest -q`. The optional Chromium
check requires a local Playwright and Chromium installation and a built
frontend. Run it explicitly with
`python -m pytest -q tests/browser_web_foundation.py`. The deterministic checks route the canonical development origin to FastAPI's
test transport and isolated synthetic data. The launcher smoke test uses the
real loopback port and skips when port 8000 is unavailable.

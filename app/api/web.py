"""Serve the local build only at the approved Web routes."""

from pathlib import Path
import re

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.core.tenant_slugs import SLUG


WEB_BUILD = Path(__file__).resolve().parents[2] / "frontend" / "dist"

WEB_ROUTE = re.compile(
    r"(?:login|book(?:/availability|/review)?|appointments(?:/[1-9][0-9]{0,18})?"
    r"|owner/(?:calendar|appointments/[1-9][0-9]{0,18}|book(?:/customer)?))"
)


def install_web(app):
    assets = WEB_BUILD / "web-assets"

    async def asset(request):
        if not assets.is_dir():
            raise HTTPException(404)
        return await StaticFiles(directory=assets).get_response(
            request.path_params["asset_path"], request.scope,
        )

    async def shell(request):
        slug = request.path_params["slug"]
        page = request.path_params.get("page")
        if not SLUG.fullmatch(slug) or (page is not None and not WEB_ROUTE.fullmatch(page)):
            raise HTTPException(404)
        if not (WEB_BUILD / "index.html").is_file():
            raise HTTPException(503)
        return FileResponse(WEB_BUILD / "index.html", media_type="text/html")

    app.add_route("/web-assets/{asset_path:path}", asset, methods=["GET", "HEAD"])
    app.add_route("/b/{slug}", shell, methods=["GET", "HEAD"])
    app.add_route("/b/{slug}/{page:path}", shell, methods=["GET", "HEAD"])

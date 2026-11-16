"""Public identity and authenticated consistency checks."""

from contextlib import closing

from fastapi import APIRouter, Depends, Request

from app.api.auth_dependencies import AuthHTTPError, require_authenticated_session
from app.core.tenant_slugs import SLUG, public_business, public_display_name
from app.db.database import open_development_database


router = APIRouter(prefix="/api/v1")


def _find(request, connection, slug):
    business = public_business(connection, request.app.state.tenant_slugs, slug)
    if business is None:
        raise AuthHTTPError(404, "RESOURCE_NOT_FOUND")
    return business


def _match(request, connection, principal, slug):
    if _find(request, connection, slug)["id"] != principal.business_id:
        raise AuthHTTPError(404, "RESOURCE_NOT_FOUND")


def check_tenant_header(request, connection, principal):
    values = request.headers.getlist("x-barbershop-slug")
    if not values:
        return
    if len(values) != 1 or not SLUG.fullmatch(values[0]):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    _match(request, connection, principal, values[0])


def _no_query(request: Request):
    if request.query_params:
        raise AuthHTTPError(422, "INVALID_REQUEST")


@router.get("/public/businesses/{slug}", dependencies=[Depends(_no_query)])
def business_identity(slug: str, request: Request):
    with closing(open_development_database(request.app.state.auth_database_path)) as connection:
        business = _find(request, connection, slug)
        return {"slug": slug, "display_name": public_display_name(business)}


@router.get("/auth/tenant-context/{slug}", dependencies=[Depends(_no_query)])
def tenant_context(slug: str, request: Request, context=Depends(require_authenticated_session)):
    with closing(open_development_database(request.app.state.auth_database_path)) as connection:
        _match(request, connection, context.principal, slug)
        return {"slug": slug}

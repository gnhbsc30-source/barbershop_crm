"""Authenticated customer routes. No SQL or scheduling rules."""

from fastapi import APIRouter, Depends, Request

from app.api.auth_dependencies import AuthHTTPError, require_customer
from app.api.booking_dependencies import execute_booking, positive_query, query_keys


router = APIRouter(prefix="/api/v1/customer")


@router.get("/services")
def services(request: Request, context=Depends(require_customer)):
    query_keys(request)
    return execute_booking(request, context, lambda service: service.services())


@router.get("/staff")
def staff(request: Request, context=Depends(require_customer)):
    query_keys(request, allowed=("service_id",), repeated=("service_id",))
    ids = [positive_query(value) for value in request.query_params.getlist("service_id")]
    if not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    return execute_booking(request, context, lambda service: service.staff(ids))

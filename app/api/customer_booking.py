"""Authenticated customer routes. No SQL or scheduling rules."""

from fastapi import APIRouter, Depends, Request

from app.api.auth_dependencies import AuthHTTPError, require_customer
from app.api.booking_dependencies import execute_booking, positive_query, query_keys, require_customer_post, read_customer_body, search_owner
from app.api.customer_booking_models import SearchRequest, BookingRequest, SelectionRequest
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


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


@router.post("/booking-searches")
async def search(request: Request, context=Depends(require_customer_post)):
    data = await read_customer_body(request, SearchRequest)
    return await run_in_threadpool(execute_booking, request, context,
        lambda service: service.search(data, search_owner(request, context), request.app.state.booking_contexts, request.app.state.booking_searches), True)


def created(value):
    return JSONResponse(value, status_code=201, headers={"Location": f"/api/v1/customer/appointments/{value['appointment_id']}"})


@router.post("/appointments", status_code=201)
async def book(request: Request, context=Depends(require_customer_post)):
    data = await read_customer_body(request, BookingRequest)
    value = await run_in_threadpool(execute_booking, request, context, lambda service: service.book(data), True)
    return created(value)


@router.post("/booking-searches/{search_id}/selection", status_code=201)
async def select(search_id: str, request: Request, context=Depends(require_customer_post)):
    if not 1 <= len(search_id) <= 128:
        raise AuthHTTPError(422, "INVALID_REQUEST")
    data = await read_customer_body(request, SelectionRequest)
    value = await run_in_threadpool(execute_booking, request, context,
        lambda service: service.select(search_id, data, search_owner(request, context), request.app.state.booking_contexts, request.app.state.booking_searches), True)
    return created(value)


@router.get("/appointments")
def history(request: Request, context=Depends(require_customer)):
    query_keys(request, allowed=("limit", "before_id", "status"))
    limit = positive_query(request.query_params.get("limit", "20"))
    before = request.query_params.get("before_id")
    before = positive_query(before) if before is not None else None
    status = request.query_params.get("status")
    if limit > 100 or (status is not None and status not in ("BOOKED", "COMPLETED", "CANCELLED", "NO_SHOW")):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    return execute_booking(request, context, lambda service: service.history(limit=limit, before_id=before, status=status))


@router.get("/appointments/{appointment_id}")
def detail(appointment_id: str, request: Request, context=Depends(require_customer)):
    query_keys(request)
    identifier = positive_query(appointment_id)
    return execute_booking(request, context, lambda service: service.history(appointment_id=identifier))

"""Authenticated customer routes. No SQL or scheduling rules."""

from datetime import date
import re

from fastapi import APIRouter, Depends, Request

from app.api.auth_dependencies import AuthHTTPError, SESSION_COOKIE, require_customer
from app.api.booking_dependencies import execute_booking, positive_query, query_keys, require_customer_post, read_customer_body, search_owner
from app.api.customer_booking_models import (
    SearchRequest, BookingRequest, SelectionRequest, CatalogResponse, StaffResponse,
    SearchResponse, AppointmentView, HistoryResponse, request_contract,
)
from app.core.slot_admission import SlotsBusy
from app.services.available_slots import available_slots
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


router = APIRouter(prefix="/api/v1/customer")


@router.get("/services", response_model=CatalogResponse)
def services(request: Request, context=Depends(require_customer)):
    query_keys(request)
    return execute_booking(request, context, lambda service: service.services(), response_model=CatalogResponse)


@router.get("/staff", response_model=StaffResponse)
def staff(request: Request, context=Depends(require_customer)):
    query_keys(request, allowed=("service_id",), repeated=("service_id",))
    ids = [positive_query(value) for value in request.query_params.getlist("service_id")]
    if not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    return execute_booking(request, context, lambda service: service.staff(ids), response_model=StaffResponse)


@router.get("/available-slots")
def daily_slots(request: Request, context=Depends(require_customer)):
    query_keys(request, allowed=("date", "staff_id", "service_id"), repeated=("service_id",))
    query = request.query_params
    value = query.get("date", "")
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError()
        day = date.fromisoformat(value)
    except ValueError:
        raise AuthHTTPError(422, "INVALID_REQUEST") from None
    values = query.getlist("service_id")
    staff = query.get("staff_id", "")
    if not 1 <= len(values) <= 20 or any(value.startswith("0") for value in [staff, *values]):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    ids = [positive_query(value) for value in values]
    if len(set(ids)) != len(ids):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    staff_id = positive_query(staff)

    def list_slots(customer):
        binding = request.app.state.auth_sessions.binding_for(request.cookies.get(SESSION_COOKIE))
        try:
            with request.app.state.slot_admission.admit(binding):
                return available_slots(customer, day, staff_id, ids)
        except SlotsBusy:
            raise AuthHTTPError(429, "AVAILABLE_SLOTS_BUSY", headers={"Retry-After": "1"}) from None

    return execute_booking(request, context, list_slots, scheduling=True)


@router.post("/booking-searches", response_model=SearchResponse, openapi_extra=request_contract(SearchRequest))
async def search(request: Request, context=Depends(require_customer_post)):
    data = await read_customer_body(request, SearchRequest)
    return await run_in_threadpool(execute_booking, request, context,
        lambda service: service.search(data, search_owner(request, context), request.app.state.booking_contexts, request.app.state.booking_searches), True, SearchResponse)


def created(value):
    value = AppointmentView.model_validate(value).model_dump(mode="json")
    return JSONResponse(value, status_code=201, headers={"Location": f"/api/v1/customer/appointments/{value['appointment_id']}"})


@router.post("/appointments", status_code=201, response_model=AppointmentView, openapi_extra=request_contract(BookingRequest))
async def book(request: Request, context=Depends(require_customer_post)):
    data = await read_customer_body(request, BookingRequest)
    return await run_in_threadpool(execute_booking, request, context, lambda service: service.book(data, created), True)


@router.post("/booking-searches/{search_id}/selection", status_code=201, response_model=AppointmentView, openapi_extra=request_contract(SelectionRequest))
async def select(search_id: str, request: Request, context=Depends(require_customer_post)):
    if not 1 <= len(search_id) <= 128:
        raise AuthHTTPError(422, "INVALID_REQUEST")
    data = await read_customer_body(request, SelectionRequest)
    return await run_in_threadpool(execute_booking, request, context,
        lambda service: service.select(search_id, data, search_owner(request, context), request.app.state.booking_contexts, request.app.state.booking_searches, created), True)


@router.get("/appointments", response_model=HistoryResponse)
def history(request: Request, context=Depends(require_customer)):
    query_keys(request, allowed=("limit", "before_id", "status"))
    limit = positive_query(request.query_params.get("limit", "20"))
    before = request.query_params.get("before_id")
    before = positive_query(before) if before is not None else None
    status = request.query_params.get("status")
    if limit > 100 or (status is not None and status not in ("BOOKED", "COMPLETED", "CANCELLED", "NO_SHOW")):
        raise AuthHTTPError(422, "INVALID_REQUEST")
    return execute_booking(request, context, lambda service: service.history(limit=limit, before_id=before, status=status), response_model=HistoryResponse)


@router.get("/appointments/{appointment_id}", response_model=AppointmentView)
def detail(appointment_id: str, request: Request, context=Depends(require_customer)):
    query_keys(request)
    identifier = positive_query(appointment_id)
    return execute_booking(request, context, lambda service: service.history(appointment_id=identifier), response_model=AppointmentView)

"""Owner API response and input contracts."""

from app.api.customer_booking_models import PositiveID, QuoteView


class CalendarAppointment(QuoteView):
    appointment_id: PositiveID
    customer_id: PositiveID
    customer_name: str
    status: str


class AppointmentDetail(CalendarAppointment):
    notes: str | None
    customer_phone: str | None

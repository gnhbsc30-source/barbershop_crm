# Barbershop CRM — Current Project State

> Purpose: This is the project's current-state checkpoint. It is the first file to consult when resuming work after a context switch.
> Rule: This file describes the project; it does not authorize automatic code changes.

## 1. Current Phase

**Phase:** Clean Architecture Foundation → Appointment Domain

**Current milestone:** Appointment Service vertical slice completed and verified.

**Next planned milestone:** Add booking-window/minimum-notice policy only after the relevant settings and product contract are defined.

**STOP RULE:** Do not start the next milestone until the current milestone is explicitly considered complete and the user agrees to continue.

## 2. Current Verified State

The clean project is `barbershop_crm_clean`.

Current relevant structure:
- `app/core/`
- `app/db/`
- `app/exceptions/`
- `app/services/`
- `app/api/`
- `app/ai/`
- `frontend/admin/`
- `frontend/customer/`
- `knowledge/`
- `scripts/`
- `docs/`
- `tests/`

Latest known test result:
- **23 Availability Engine tests passed**
- **67 tests passed, 0 failed — full regression suite**
- No regression detected in the previously green tests.
- Tests cover Customer Domain, Appointment Repository, Scheduling Repository, and Availability Engine.

## 3. What Has Been Completed

### Database foundation
- SQLite database.
- Canonical schema is `app/db/schema.sql`.
- Multi-tenant root entity is `businesses`.
- Foreign keys are enabled.
- Tenant isolation is represented in the schema with `business_id` and composite foreign keys where appropriate.
- Current architecture intentionally uses one canonical schema file; migrations are not introduced until they are actually justified.

### Database connection layer
- `app/db/database.py` owns SQLite connection creation and schema initialization.
- Repositories receive an existing `sqlite3.Connection`.
- Repositories do not create connections.
- Repositories do not commit or rollback.
- The caller/service owns the transaction boundary.

### Customer Domain
- Customer repository exists.
- Customer service exists.
- Customer-specific exceptions exist.
- Repository tests and service tests are green.

### Appointment Repository
- Appointment creation and appointment-item creation are performed as one repository operation.
- `create_appointment()` accepts appointment data plus a list of appointment items.
- There is intentionally no separate public appointment-item creation function.
- Repository does not commit/rollback.
- `get_staff()` provides tenant-scoped staff lookup.
- `get_active_staff_for_service()` returns active staff configured for a service.
- `get_services_by_ids()` returns active services belonging to a business.
- `get_staff_service()` returns staff-specific service configuration, including NULL overrides when no override is configured.
- Dedicated repository tests verify correct retrieval, tenant isolation, active/inactive behavior, empty service lists, staff-specific overrides, and preservation of NULL overrides.

### Scheduling Repository
- Scheduling repository provides the persistence operations required by deterministic availability evaluation.
- Current functions:
  - `get_business_working_hours()`
  - `get_staff_working_hours()`
  - `get_schedule_breaks()`
  - `get_schedule_blocks()`
  - `get_staff_appointments()`
  - `get_business_settings()`
- Repository responsibilities are limited to retrieving schedule and booking-state data.
- It does not decide final availability, calculate booking duration/price, create appointments, or own business rules.
- Scheduling Repository tests cover retrieval, isolation, missing schedule rows, ordered breaks, business-wide/staff-specific blocks, overlap behavior, cancelled appointment exclusion, and settings isolation.

### Availability Engine
- `app/services/availability.py` contains the deterministic `AvailabilityEngine`.
- The engine is the shared availability component for future UI and Chat booking flows.
- It does not create appointments.
- It calculates service duration using service defaults and staff-specific overrides.
- It supports multiple services in one appointment.
- It validates staff/service compatibility.
- It evaluates:
  - business working hours
  - staff working hours
  - breaks
  - business-wide schedule blocks
  - staff-specific schedule blocks
  - existing non-cancelled appointments
  - service duration
  - staff-service compatibility
- It uses interval-overlap semantics rather than requiring identical appointment start times.
- It supports `SPECIFIC` staff selection.
- It supports `ANY` staff selection, including deterministic `AUTO` selection and `CHOICE` availability behavior as currently implemented.
- It builds real free windows rather than treating a fixed time grid as a hard availability constraint.
- A cancelled appointment does not block availability.
- The engine rejects invalid, past, or otherwise unsupported requests according to its current contract.
- Booking-window enforcement and richer future slot-search/ranking behavior remain separate concerns to be completed where required by the higher-level Appointment Service/product flow.

### Availability Testing
Dedicated Availability Engine tests cover:
- default service duration
- staff-specific duration override
- fitting inside working hours
- closed business days
- staff not working
- breaks
- staff-specific blocks
- business-wide blocks
- overlapping appointments
- cancelled appointments not blocking
- unsupported services
- multiple-service duration
- `ANY` + `AUTO`
- `ANY` with no available staff
- `ANY` + `CHOICE`
- no eligible staff
- unknown service
- invalid datetime
- past datetime
- empty service list
- invalid staff selection
- free-window behavior after an existing appointment
- absence of a fixed hard time grid

### Appointment Service
- `app/services/appointments.py` is the business orchestration layer for booking.
- It validates customer, staff, service, tenant scope and input date/time.
- It resolves staff-service duration/price overrides, falling back independently to service defaults.
- It calculates `end_datetime`, total duration and total price on the server.
- It checks deterministic availability before the critical section and once more inside it.
- The DB-layer `transaction()` context owns SQLite locking/commit/rollback; the service contains no SQLite command and repositories still do not commit or rollback.
- `ANY + CHOICE` is discovery only and cannot create a booking. `ANY + AUTO` needs the explicit `allow_auto_assign=True` delegation flag.
- Appointment and item snapshots are created together.

Latest verification (2026-09-17):
- **Appointment Service + Appointment Repository tests: 20 passed**
- **Full suite: 54 passed, 20 failed**
- All 20 failures predate this milestone: they use the fixed date `2026-09-14`, which is now in the past and correctly returns `PAST_DATETIME`. Their fixtures should be made date-relative in a dedicated maintenance change.

## 4. Important Architecture Decisions

### Source of truth
Database + deterministic Python business logic are the source of truth.

### AI boundary
The LLM is an interface/NLU layer only.
It may interpret natural language and return structured data.
It must not decide security, availability, pricing, duration, tenant access, or database truth.

### Transaction ownership
Repository = persistence operations.
Service = business rules + transaction boundary.
This prevents hidden commits and keeps multi-step operations atomic.

### Appointment vs Appointment Item
An appointment is the whole booking.
An appointment item is one service inside that booking.

The service layer calculates duration and price from business/staff configuration. User/LLM supplied duration or price is never trusted as authoritative.

### Availability
Availability is a deterministic domain component shared by UI and Chat.
It evaluates real scheduling constraints and does not create appointments.

### Booking interval
A configured booking interval is a preferred start-time/display rule, not a hard legality constraint.
When real appointments create boundaries at non-grid times, legal availability may begin at those actual boundaries. The engine must not create artificial unavailable gaps merely to force starts onto a fixed grid.

### Waitlist
A cancellation should not silently auto-book another customer.
Eligible waitlist customers can receive an offer/notification and must confirm.

### Cancellation
Cancellation changes appointment status to `CANCELLED`; appointments are not deleted.
The business cancellation policy is configurable.

### Price changes
`appointment_items.price` is the appointment's bound/current price.
Business rule: appointments more than one week away may receive the new price and customer notification; appointments within one week retain the old price.

### Tenant isolation
Every tenant-scoped operation must be authorized and scoped by `business_id`.
Client input and AI output are never sufficient authorization.

## 5. Product Direction

Product is a SaaS for barbershops, primarily for independent owners/head barbers with staff.

Core goal:
- Reduce administrative work.
- Save time.
- Make the day's schedule immediately understandable.
- Especially reduce WhatsApp appointment coordination.

Not a marketplace.

MVP direction includes:
- tenant/business
- owner/staff roles
- auth/authz
- CRM
- registered + guest customers
- customer linking/merge foundation
- appointments/calendar
- services, duration, pricing
- working hours/breaks/days off
- booking window up to 3 months
- customer booking
- barber/walk-in booking
- edit/move/cancel
- basic statuses
- waitlist foundation
- history
- dashboard/revenue foundation
- customer AI
- owner read-oriented AI
- AI security/confirmation architecture
- notification architecture

## 6. Working Method

The workflow is:

1. Understand current implementation.
2. Explain relevant technical concepts.
3. Compare implementation to product requirements.
4. Decide: KEEP / REFACTOR / REPLACE / REMOVE / BUILD.
5. Make the smallest appropriate change.
6. Run tests.
7. Verify regressions.
8. Record important architectural decisions.
9. Update the context files when a meaningful checkpoint is reached.
10. Stop and let the user decide whether to continue.

The user is the person who changes the project files unless they explicitly authorize me to create/edit a project artifact.

## 7. Context Recovery Protocol

At the start of a new session:
1. Read `PROJECT_STATE.md`.
2. Read `ARCHITECTURE_DECISIONS.md`.
3. If the current conversation is missing important history, consult `CONVERSATION_CONTEXT.txt`.
4. Compare the checkpoint against the current project/ZIP if one is provided.
5. Do not assume the checkpoint is newer than the actual code.
6. If code and checkpoint disagree, stop and reconcile the discrepancy before implementation.

## 8. Update Triggers

The context files should be reviewed/updated when:
- a milestone is completed
- a test suite changes materially
- an architecture decision changes
- a file/module responsibility changes
- an important product decision is finalized
- a bug reveals a reusable architectural lesson
- a new domain is completed
- the next step changes
- a long conversation reaches a meaningful checkpoint
- context appears at risk of becoming ambiguous

The assistant should proactively tell the user:
**"This is a good checkpoint; we should update the context files before continuing."**

The assistant must not silently modify the user's project.

## 9. Open Decisions

Still unresolved unless explicitly changed later:
- exact dashboard layout
- onboarding UX
- customer authentication method
- guest-to-registered linking/merge flow
- waitlist UX
- manager conflict warning UX
- duration-change behavior when conflicts arise
- "any available barber" selection behavior beyond the currently implemented deterministic availability behavior
- multi-person booking UX
- Google Calendar synchronization
- WhatsApp provider
- notification templates/consent
- payment/invoice architecture
- analytics/KPIs
- AI permission/confirmation matrix
- owner vs staff permission matrix
- first action/landing experience on app open
- richer Best Slot / alternative ranking behavior
- complete booking-window enforcement at the higher-level Appointment Service
- timezone handling strategy for all scheduling flows

## 10. Immediate Next Step

**Appointment Service — Contract Design**

The Appointment Repository, Scheduling Repository, and Availability Engine milestones are complete and verified.

Verified:
- Scheduling Repository: **23 passed**
- Availability Engine: **23 passed**
- Full test suite: **67 passed, 0 failed**
- No regression detected.

Before implementation:
- define the Appointment Service responsibility boundary
- define its input/output contract
- define duration/price resolution and appointment-item creation
- define booking-window enforcement
- define how it calls Availability Engine
- define transaction and final concurrency protection
- define service-level error handling
- design focused tests

**STOP:** Do not implement the Appointment Service until its contract is explicitly reviewed and approved.

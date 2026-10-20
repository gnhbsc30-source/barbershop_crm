# Barbershop CRM — Current Project State

> Purpose: This is the project's current-state checkpoint. It is the first file to consult when resuming work after a context switch.
> Rule: This file describes the project; it does not authorize automatic code changes.

## 1. Current Phase

**Phase:** Clean Architecture Foundation → Appointment Domain

**Current milestone:** Stage 29 — Alternative Discovery and Selection Execution implemented and reviewed on `feature/stage-29-alternative-discovery`. Stage 29 has NOT yet merged into `main`.

**Audit verdict:** READY WITH NON-BLOCKING NOTES for the approved development/MVP scope; this is not a production-readiness claim. The subsequent deterministic datetime validation fix is reviewed and verified.

**Next steps:** documentation review, approved documentation commit/push, PR into `main`, approved merge, then post-merge verification. No later feature milestone has been selected.

**STOP RULE:** Do not start the next milestone until the current milestone is explicitly considered complete and the user agrees to continue.

## 2. Current Verified State

The reviewed project is `barbershop_crm`.

Local project path:
- `/Users/aminadavyamin/Documents/Projects/barbershop_crm`

Physically present directories containing tracked files:
- `app/core/`
- `app/db/`
- `app/exceptions/`
- `app/services/`
- `docs/`
- `tests/`

Tracked application entry point: `app/main.py` (FastAPI startup and `/health`).

Planned structure only; these folders are currently absent and are not tracked:
- `app/api/`, `app/ai/`
- `frontend/admin/`, `frontend/customer/`
- `knowledge/`, `scripts/`

The planned folders do not represent implemented API, UI, AI, or Chat features.

Latest known test result:
- **23 Availability Engine tests passed**
- **167 tests passed, 0 failed — full regression suite**
- No regression detected in the previously green tests.
- Tests cover Customer Domain, repositories, Availability Engine, Appointment Service, candidate generation, alternative models/ranking/discovery, selection contexts, and selection execution/claims.

Git checkpoint:
- Checkpoint date: 2026-10-20
- Branch: `feature/stage-29-alternative-discovery`
- Verified implementation HEAD: `6091baf8caea0aa005bba4e7271b285368da162d`
- `main` remains at `5846451000e8dc2ef75cb9605c63613295ef353e` (Stage 28 baseline plus documentation).
- Feature branch is 9 commits ahead of `main`, 0 behind at this checkpoint.
- Stage 29 is NOT yet merged into `main`.
- GitHub: https://github.com/gnhbsc30-source/barbershop_crm
- Working tree was clean at the implementation checkpoint; this documentation update is pending review and is not committed yet. The HEAD above identifies the verified code, not a future documentation commit.

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
- Booking-window enforcement remains outside the Availability Engine. AppointmentService enforces it before the initial availability check; AlternativeDiscoveryService enforces it before searching. Stage 29 ranking/search orchestration is separate from the engine.

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
- It validates customer, staff, service, tenant scope, active staff status, staff-service compatibility, and input date/time.
- It resolves staff-service duration/price overrides, falling back independently to service defaults.
- It calculates `end_datetime`, total duration and total price on the server.
- It checks deterministic availability before the critical section and once more inside it.
- It enforces tenant-scoped booking policy before the initial availability check:
  - `booking_window_months`
  - `minimum_booking_notice_minutes`
  - calendar-month booking windows with month-end clamping
  - inclusive policy boundaries
  - `PAST_DATETIME`, `MINIMUM_BOOKING_NOTICE_VIOLATION`, and `BOOKING_WINDOW_EXCEEDED` errors
- The DB-layer `transaction()` context owns SQLite locking/commit/rollback; the service contains no SQLite command and repositories still do not commit or rollback.
- `ANY + CHOICE` is discovery only and cannot create a booking. `ANY + AUTO` needs the explicit `allow_auto_assign=True` delegation flag.
- Appointment and item snapshots are created atomically; items preserve service name, duration, and price snapshots.

Historical milestones and current verification:
- **Stage 27 Appointment Service completed and reviewed.**
- Stage 27 reviewed baseline: 75 tests passed at `7bc845c1c44d4d5ef9bd10b6ebd355a84e87a735`.
- **Stage 28 — Booking Window + Minimum Booking Notice completed, reviewed, merged into `main`, and verified.**
- Stage 28 merge: `9c52973266e2d681be1d97201caf016e42de4c8b`; 85 tests passed at that milestone.
- Availability tests use a reusable future-Monday helper rather than brittle hardcoded dates.
- **Availability suite: 23 passed**
- **Stage 29 current full suite: 167 passed, 0 failed**

### Stage 29 — Alternative Discovery and Selection Execution
- `app/services/alternatives.py` owns SPECIFIC-staff discovery separately from AppointmentService. `requested_staff_id=None` is explicitly unsupported.
- Requested staff must exist in the tenant and be active. Valid but service-incompatible requested staff does not block discovery for other staff eligible for all requested services. Invalid/inactive services fail; no eligible staff yields an empty result.
- Original request validation rejects `tzinfo is not None` with `INVALID_DATETIME`, matching AppointmentService. It then applies `PAST_DATETIME`, `MINIMUM_BOOKING_NOTICE_VIOLATION`, and `BOOKING_WINDOW_EXCEEDED`, in that order, with inclusive allowed boundaries.
- Search starts on the requested date, including earlier same-day times still legal under minimum notice, and searches forward through the earlier of requested date + search window and the booking-window boundary.
- Existing AvailabilityEngine eligibility and free-window builders are reused. Candidates include the real legal window start plus later interval-aligned starts only when the entire staff-specific duration fits.
- `get_business_settings()` includes `alternative_search_window_days` (default 7) and `booking_interval_minutes` (default 30). Both have positive-value schema checks. Missing settings rows remain allowed; discovery applies fallbacks 3 months / 0 notice minutes / 7 search days / 30 interval minutes. There is no settings-write API.
- Four priority groups/reason codes: SAME_STAFF_SAME_DAY, OTHER_STAFF_SAME_DAY, SAME_STAFF_OTHER_DAY, OTHER_STAFF_OTHER_DAY. Sorting uses priority, date distance, clock-time distance, alignment, staff ID, and start time. Deduplication uses `(staff_id, start_datetime)` and rejects conflicting business truth.
- `AlternativeSearchResult` retains discovery data; `AlternativeDiscoveryResponse` adds `search_id`. IDs such as `option_001` are assigned after ranking/deduplication. All results are returned (`has_more=False`); an empty result has `search_id=None` and creates no context.
- `app/services/alternative_selection.py` defines the store Protocol and MVP in-memory implementation. Trusted context stores business/service IDs, timestamps, and options containing only option ID, staff ID, and start datetime. It does not trust cached price, duration, end time, names, display fields, or ranking.
- Contexts have opaque UUID search IDs and a 15-minute server-clock TTL. Before `expires_at` is valid; at/after it is expired. Stored data uses frozen dataclasses/tuples to avoid input-list mutation.
- `app/services/alternative_selection_execution.py` accepts search ID, option ID, trusted customer ID, and optional notes. It claims the context and calls AppointmentService.book() with trusted context values and SPECIFIC staff, returning the real booking result.
- AppointmentService recalculates current price/duration/end time, validates customer/staff/services/policies, checks availability, and rechecks inside its existing short DB-layer transaction before atomic appointment/item creation.
- An RLock protects per-search claim/check/update in one store instance. Concurrent selection from the same search fails with `ALTERNATIVE_SELECTION_IN_PROGRESS`. Success expires the entire context; any booking failure releases the claim while preserving an otherwise unexpired context.
- Store errors remain `ALTERNATIVE_SEARCH_EXPIRED` / `ALTERNATIVE_OPTION_NOT_FOUND`. Stale time/availability/staff/service booking failures map to `SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE`; `CUSTOMER_NOT_FOUND` remains unchanged. No fallback option is selected automatically.
- Discovery writes no business data; its only added side effect is ephemeral context creation. No slot reservation/hold exists. Presentation/Chat will own conversational messages; those integrations are not implemented.

### Stage 29 Final Audit
- Reviewed verdict: **READY WITH NON-BLOCKING NOTES**. Audit baseline: 163 passing tests at `ef8fdd4ebf50735c43a4e91e202c1072e9fb3d60`.
- Follow-up fix: aware discovery requests now fail deterministically with `INVALID_DATETIME`; UTC and positive/negative offsets are tested alongside naive requests. Current verification: **167 passed** at `6091baf8caea0aa005bba4e7271b285368da162d`.
- Remaining notes: integer-type settings hardening, lazy expired-context cleanup, duplicated but currently consistent booking-policy checks, and additional regression scenarios for unexpected failures, concurrent independent searches, expiry during booking, and configuration changes between discovery/selection.

### Deferred capabilities and production limitations
- SQLite remains temporary; no production DB migration or persistent migration infrastructure. Schema changes do not upgrade existing databases automatically.
- In-memory selection storage/claims are single-process and require the same store instance. Multi-worker deployment needs shared atomic storage; there is no restart durability or atomic transaction spanning booking and context consumption.
- Business-local timezone/DST hardening remains future work. Rejecting aware input is not timezone conversion/support.
- Settings integer-type enforcement needs hardening: SQLite affinity plus `> 0` checks can still accept fractional/text values.
- Expired-context memory cleanup is lazy. Discovery is unpaginated and has no production resource/capacity controls.
- Authentication/authorization remains an application-boundary responsibility; opaque search IDs do not authorize users.
- ANY Alternative Discovery, cancellation, rescheduling, and API/UI/Chat integration are not implemented. Existing Availability ANY behavior is separate.

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
Planned product rule; no waitlist execution/notification flow is implemented:
A cancellation should not silently auto-book another customer.
Eligible waitlist customers can receive an offer/notification and must confirm.

### Cancellation
Planned product rule; cancellation execution is not implemented:
Cancellation changes appointment status to `CANCELLED`; appointments are not deleted.
The business cancellation policy is configurable.

### Price changes
`appointment_items.price` is the appointment's bound/current price.
Planned business rule (repricing/notification flow is not implemented): appointments more than one week away may receive the new price and customer notification; appointments within one week retain the old price.

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
- presentation/truncation of ranked alternatives and ANY discovery semantics beyond the implemented SPECIFIC contract
- timezone handling strategy for all scheduling flows

## 10. Next Steps Before Merge

Stage 28 is merged. Stage 29 implementation and final audit are reviewed on the feature branch; Stage 29 has NOT yet merged into `main`.

1. Review the three updated context documents for architecture approval.
2. Commit/push the documentation only after explicit approval.
3. Open a Stage 29 PR into `main` when authorized; review its full diff and checks.
4. Merge only after approval.
5. Update local `main`, verify the working tree, confirm Stage 29 is included, and run full regression again. Record the actual merge/post-merge checkpoint.

No PR has been opened or merge performed as part of this documentation task. No subsequent feature milestone is authorized by this file.

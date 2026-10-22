# Barbershop CRM — Current Project State

> Purpose: This is the project's current-state checkpoint. It is the first file to consult when resuming work after a context switch.
> Rule: This file describes the project; it does not authorize automatic code changes.

## 1. Current Phase

**Phase:** Two-sided local development MVP → Authentication and development-data foundation

**Current milestone:** Stage 30B.1 — Local Authentication & Development Foundation implemented, reviewed, merged into `main` through [PR #3](https://github.com/gnhbsc30-source/barbershop_crm/pull/3), and post-merge verified. Regular Merge Commit: `f714c1aea9353b7aca9bbbc052f1df429115b504`.

**Audit checkpoint:** The final Stage 30B.1 audit found an authentication redirect blocker. Approved remediation also corrected reusable query-validation boundaries and protocol-header preservation. All three corrections were implemented/tested before PR #3; the reviewed history is preserved on `main`. Post-merge regression: 405 passed, 0 failed. This is not production readiness.

**Next steps:** Architectural review of this completed post-merge checkpoint. Stage 30B.2 has not started; its implementation requires a separate approved contract.

**STOP RULE:** Do not start the next milestone until the current milestone is explicitly considered complete and the user agrees to continue.

## 2. Current Verified State

The reviewed project is `barbershop_crm`.

Local project path:
- `/Users/aminadavyamin/Documents/Projects/barbershop_crm`

Physically present directories containing tracked files:
- `app/api/`
- `app/core/`
- `app/db/`
- `app/exceptions/`
- `app/services/`
- `docs/`
- `tests/`

Tracked application entry point: `app/main.py` (thin FastAPI composition root, authentication lifespan/router/middleware, and `/health`). Development tools are `app/dev_data.py` and `app/dev_server.py`.

Planned structure only; these folders are currently absent and are not tracked:
- `app/ai/`
- `frontend/admin/`, `frontend/customer/`
- `knowledge/`, `scripts/`

The planned folders do not represent implemented features. `app/api/` now contains only development authentication routes/dependencies; no booking or owner-calendar HTTP API exists.

Latest known test result:
- **23 Availability Engine tests passed**
- **405 tests passed, 0 failed — full post-merge regression suite** on `main` at `f714c1aea9353b7aca9bbbc052f1df429115b504`, verified 2026-10-22.
- **92 focused HTTP/launcher tests passed** during remediation verification.
- No regression detected in the previously green tests.
- Tests cover the existing Customer/scheduling/booking/discovery/selection domains plus identity/credentials, sessions, rate limiting, provisioning/rotation, ASGI authentication/security, and launcher guards. Tests use isolated temporary databases; verification does not provision live credentials or start a real development server.

Git checkpoint:
- Checkpoint date: 2026-10-22
- Branch: `main`
- Verified Stage 30B.1 merge / synchronized local and remote `main` before this documentation commit: `f714c1aea9353b7aca9bbbc052f1df429115b504`. PR #3 merged on 2026-10-20 using a regular two-parent merge, not squash/rebase.
- Historical implementation/remediation HEAD: `24edf454379d142d9816131a83783b4c4e93ff32`; documentation/PR head: `97dc75a1db73838576e46969371a4896e57697e8`; source branch: `feature/stage-30b1-auth-foundation` (retained). All six feature commits are ancestors of `main`; the merge tree exactly matches the reviewed PR head.
- Historical pre-merge `main`: `2936be4013aa2b5c6440442ce49afec8eb3effcb` (Stage 29 final documentation checkpoint).
- Stage 29 historical merge: `d6b4866461bab2ea13dd8a001b3101136110c26a`, PR #2: https://github.com/gnhbsc30-source/barbershop_crm/pull/2; post-merge verification was **167 passed, 0 failed**.
- Historical source branch: `feature/stage-29-alternative-discovery`, retained locally and remotely.
- Historical implementation HEAD: `6091baf8caea0aa005bba4e7271b285368da162d`; reviewed documentation/PR head: `47bdcea7f9fcf90832264ed652d35bb88d682a5f`.
- Historical pre-merge `main`: `5846451000e8dc2ef75cb9605c63613295ef353e` (Stage 28 baseline plus documentation).
- GitHub: https://github.com/gnhbsc30-source/barbershop_crm
- Working tree was clean at post-merge verification. Only the three checkpoint documents are updated by this task; use Git history for the subsequent documentation commit SHA. The merge SHA identifies the verified implementation checkpoint, not the later documentation-only HEAD.

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
- **Stage 29 historical full suite: 167 passed, 0 failed**
- **Stage 29 implemented, reviewed, merged into `main` through PR #2, and verified** at `d6b4866461bab2ea13dd8a001b3101136110c26a`.

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
- Follow-up fix: aware discovery requests now fail deterministically with `INVALID_DATETIME`; UTC and positive/negative offsets are tested alongside naive requests. Historical feature verification: **167 passed** at `6091baf8caea0aa005bba4e7271b285368da162d`.
- Post-merge verification: **167 passed, 0 failed** on `main` at `d6b4866461bab2ea13dd8a001b3101136110c26a`. The regular Merge Commit preserves all approved feature commits; the audit's development/MVP limitations still apply.
- Remaining notes: integer-type settings hardening, lazy expired-context cleanup, duplicated but currently consistent booking-policy checks, and additional regression scenarios for unexpected failures, concurrent independent searches, expiry during booking, and configuration changes between discovery/selection.

### Stage 30B.1 — Local Authentication & Development Foundation (merged and verified)
- `app/services/authentication.py` owns immutable Principal/identity validation and credential functions, not session/rate-limit/HTTP state. OWNER maps to an active OWNER `users` record; CUSTOMER maps through active `customer_accounts` to an active same-tenant customer. Both require an active business. Subjects are namespaced (`user:<id>` / `customer_account:<id>`); CUSTOMER is not a staff role.
- Server-provisioned references, not caller role/business/customer IDs, select trusted identity. Tenant-scoped repository reads remain persistence-only. Protected requests freshly validate account, business, role/customer binding, selector and credential fingerprint; mismatches invalidate the session.
- Access codes have a random public selector and 256-bit random secret. Strict parsing, generic credential failures and unknown-selector dummy verification accompany Argon2id hashing (19 MiB, time cost 2, parallelism 1, 16-byte salt, 32-byte hash). Fingerprints are SHA-256 of the exact stored hash verified at login, preventing silent adoption of replacement credentials.
- Separate `app/core/auth_sessions.py` and `app/core/auth_rate_limit.py` own locked in-memory state. Sessions have one-hour absolute expiry (at/after expiry is invalid), digest-only token keys, independent CSRF tokens and default live capacity 1024. Successful login replaces the presented existing session; failed login preserves it. Logout invalidates only the authenticated session.
- Limits are five admitted attempts per selector/five minutes and twenty globally/minute, including successes. Malformed/unknown selectors count before expensive verification; state is bounded and rejected attempts do not extend windows. Login body buffering is capped at 4096 bytes.
- `app/dev_data.py` explicitly provisions one synthetic business, OWNER, CUSTOMER, two staff, two services, assignments, schedules and settings using the existing schema. Recognized reruns preserve data/appointments and do not regenerate credentials. Unknown/incomplete datasets fail closed without reset.
- Manual terminal-confirmed rotation targets an existing provisioned identity, generates a new secret and conditionally replaces its hash inside a short DB-layer transaction. Other business records are untouched; old sessions fail at subsequent fingerprint revalidation. Interrupted post-commit secret delivery requires another explicit rotation, never fallback credentials.
- Dedicated `data/dev/barbershop.db` and private `fixture-manifest.json` are runtime-only and ignored. Directory/file permissions are checked (0700/0600); the manifest contains references/fixture metadata, no raw codes/hashes. Database and manifest are not a single atomic resource: partial provisioning requires explicit recovery and is never automatically repaired/reset.
- `app/api/auth.py` exposes only `POST /api/v1/auth/dev-session`, `GET /api/v1/auth/me`, and `DELETE /api/v1/auth/session`. Login/me return trusted principal, expiry and CSRF token; the session token is cookie-only. Cookies are host-only, HttpOnly, SameSite=Strict, Path=/, one-hour Max-Age, with the explicit local-HTTP `Secure=False` exception.
- Login requires exact same Origin, `X-CSRF-Protection: 1` and JSON. Logout requires exact same Origin and a session-bound `X-CSRF-Token`. Host is exactly `127.0.0.1:8000`; clients must be loopback over HTTP. Duplicate security headers fail closed. No permissive CORS; responses are no-store and errors redact sensitive detail.
- `app/main.py` remains composition-only. SQLite authentication connections are created, used and closed in the same worker. Startup validates an already provisioned dataset, never seeds it. The supported `python -m app.dev_server` launcher binds 127.0.0.1:8000, one worker, no reload/proxy trust/access logs; public API docs are disabled.
- Audit remediation disables automatic slash redirects, keeps strict query rejection local to the three auth endpoints while shared auth accepts legitimate endpoint queries, and preserves framework headers such as 405 Allow without exposing exception details or relaxing no-store.

Six preserved feature commits (oldest first):
1. `75f3c9d371b2f7cd9c29757212dcfde6f04b45d3` — identity and credential foundation.
2. `507fe74a28ce356c38ca29a8ea985a526267d092` — sessions and rate limiting.
3. `d878fbf031f6c994fc30096bd3a19ca7c1037dce` — safe provisioning and credential rotation.
4. `f5c18ea3e7866eb77702279999cec9eb44ed4e6c` — local HTTP authentication integration; audit baseline 390 passed.
5. `24edf454379d142d9816131a83783b4c4e93ff32` — routing/query/header remediation; full regression 405 passed, focused HTTP/launcher 92 passed, diff check clean.
6. `97dc75a1db73838576e46969371a4896e57697e8` — reviewed three-document checkpoint and PR #3 head.

PR #3 merged at `f714c1aea9353b7aca9bbbc052f1df429115b504`. Post-merge verification on 2026-10-22 confirmed both parents, all six feature ancestors, an unchanged reviewed feature tree, and 405 passing tests. No tracked runtime database, provisioning manifest or credential files were found; the tracked-source secret-pattern scan found no obvious credential literals.

Stage 30B.1 makes no schema or Stage 27–29 scheduling changes. No booking/calendar HTTP API, frontend, search-context ownership execution, cancellation/rescheduling command, or AI/Chat integration is implemented. Stage 30B.2 is not started.

### Deferred capabilities and production limitations
- SQLite remains temporary; no production DB migration or persistent migration infrastructure. Schema changes do not upgrade existing databases automatically.
- In-memory selection storage/claims are single-process and require the same store instance. Multi-worker deployment needs shared atomic storage; there is no restart durability or atomic transaction spanning booking and context consumption.
- Business-local timezone/DST hardening remains future work. Rejecting aware input is not timezone conversion/support.
- Settings integer-type enforcement needs hardening: SQLite affinity plus `> 0` checks can still accept fractional/text values.
- Expired-context memory cleanup is lazy. Discovery is unpaginated and has no production resource/capacity controls.
- Development authentication/role/tenant dependencies now exist on `main`; resource-specific authorization and search-context ownership execution remain application-boundary work. Opaque search IDs do not authorize users.
- Sessions/rate limits and selection contexts are single-process, non-durable state; restart loses state. Production/shared atomic storage, HTTPS/secure-cookie deployment and production identity management remain deferred. The supported local launcher, not arbitrary ASGI invocation, is the deployment contract.
- Stored Argon2 work-factor bounds, slow-body/concurrent-request resource hardening, safe unexpected-error diagnostics, and additional logout/rotation races remain non-blocking hardening notes. No retroactive cancellation of already-authorized in-flight requests is guaranteed.
- ANY Alternative Discovery, cancellation/rescheduling execution, booking/calendar HTTP APIs, frontend and AI/Chat integration are not implemented. Existing Availability ANY behavior is separate; the three development auth routes are implemented.

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
The schema includes cancellation-policy data, but cancellation execution/API, cutoffs, owner override permissions and final status-transition rules are not implemented or newly decided here.

### Price changes
`appointment_items.price` is the appointment's bound/current price.
Historical product direction (repricing/notification flow is not implemented or a finalized Stage 30 contract): appointments more than one week away may receive the new price and customer notification; appointments within one week retain the old price. Final booking/rescheduling repricing policy still requires approval.

### Future MVP owner editing boundary
Approved scope is staff/date/time only. Services, customer identity, manual price and notes changes are excluded from MVP rescheduling. This is a scope decision, not implemented modification behavior; cancellation/rescheduling policies and owner override permissions remain to be agreed.

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
- production customer authentication/onboarding (local development uses explicitly provisioned access codes)
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
- cancellation cutoffs/status transitions, rescheduling policy, owner overrides and final repricing behavior

## 10. Post-Merge Checkpoint and Next Milestone

Stage 30B.1 is implemented, reviewed, merged through PR #3 using regular Merge Commit `f714c1aea9353b7aca9bbbc052f1df429115b504`, and verified on `main` with 405 passing tests. The historical Stage 29 checkpoint and all six Stage 30B.1 feature commits remain in main history.

1. Complete the authorized documentation-only post-merge commit/push to `main`, then stop for architectural review.
2. PR creation, merge approval, merge and post-merge implementation verification are completed, not pending. No branch creation/deletion or further feature work is authorized by this checkpoint.
3. Intended Stage 30B.2 direction: authenticated booking/discovery/selection HTTP boundaries over the existing domain services, including trusted tenant/customer and search-context ownership. The precise endpoint/slice contract still needs review and approval; no Stage 30B.2 implementation is authorized here.

Future customer and owner interfaces must use the same real backend and development DB. Owner calendar/create/cancel/reschedule capabilities remain future work, not part of authentication. No frontend or AI/Chat is implemented. This completed Stage 30B.1 checkpoint does not authorize Stage 30B.2 or production deployment.

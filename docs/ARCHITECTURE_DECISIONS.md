# Barbershop CRM — Architecture Decision Record

This file records decisions that should survive conversation changes.

Current feature checkpoint (2026-10-20): Stage 30B.1 is implemented/reviewed on
`feature/stage-30b1-auth-foundation`, not merged. Implementation/remediation HEAD:
`24edf454379d142d9816131a83783b4c4e93ff32`; full regression **405 passed, 0 failed**;
focused HTTP/launcher verification **92 passed**. The final audit's redirect blocker
and approved query/header corrections are resolved; documentation/PR review is
authorized, merge is not. This remains local development/MVP, not production ready.
Base/local/remote `main` remains `2936be4013aa2b5c6440442ce49afec8eb3effcb`.
Stage 29 was merged via [PR #2](https://github.com/gnhbsc30-source/barbershop_crm/pull/2),
regular Merge Commit `d6b4866461bab2ea13dd8a001b3101136110c26a`, verified with 167 tests.
Its historical audit/implementation facts remain in ADR-027 through ADR-034.
ADR-035 onward records Stage 30 decisions, without changing scheduling contracts.
Stage 30B.2 is not started; the next implementation contract requires approval.

## ADR-001 — Database Is the Source of Truth

**Decision:** Database state and deterministic Python business logic are authoritative.

**Why:** AI output, frontend input, and client-controlled state can be wrong or manipulated.

**Implication:** AI never directly decides whether an operation is allowed.

---

## ADR-002 — LLM Is NLU, Not Business Logic

**Decision:** The LLM is used for natural-language understanding (NLU) and interface behavior.

**NLU = Natural Language Understanding:** converting user language into structured intent/data.

**Why:** Availability, pricing, permissions, tenant isolation, and state transitions must be deterministic.

**Implication:** A model may produce something like:
`{"intent": "book_appointment", "date": "...", "time": "..."}`
but Python validates and executes the request.

---

## ADR-003 — Repository Owns Persistence Only

**Decision:** Repositories perform database operations and do not contain business decisions.

**Repository = data-access layer:** code responsible for reading/writing database records.

**Rules:**
- receives an existing connection
- performs SQL
- does not create connections
- does not commit
- does not rollback
- lets database errors propagate

---

## ADR-004 — Service Owns Business Rules and Transaction Boundary

**Decision:** Services validate business rules and own transactions for multi-step operations.

**Transaction:** a group of database operations that must succeed together or be rolled back together.

Example:
BEGIN
→ create appointment
→ create item 1
→ create item 2
→ COMMIT

If any step fails:
→ ROLLBACK

---

## ADR-005 — Appointment Is a Vertical Slice

**Decision:** Appointment-related behavior is treated as one domain responsibility.

Appointment Domain includes:
- appointment persistence
- appointment items
- booking rules
- availability
- cancellation
- rescheduling
- conflict handling
- waitlist interactions where appropriate

This avoids scattering appointment logic across unrelated modules.

---

## ADR-006 — Appointment Items Are Created With the Appointment

**Decision:** No separate public workflow for creating appointment items.

**Why:** An appointment and its services are one logical booking. Allowing callers to create them separately increases the chance of partial/inconsistent data.

---

## ADR-007 — Duration and Price Are Server-Derived

**Decision:** Duration and price come from tenant service configuration and staff-service overrides.

**Why:** Never trust values supplied by a browser, customer, or LLM.

**Appointment item snapshot:** the appointment stores the relevant service name, duration and price so the appointment retains its historical/current bound values.

---

## ADR-008 — Availability Is Shared by UI and Chat

**Decision:** The same deterministic Availability Engine serves both the graphical UI and conversational AI.

**Why:** Otherwise the UI and AI can disagree about what is actually bookable.

---

## ADR-009 — Availability Is More Than a Boolean

**Decision:** Availability returns structured information rather than only true/false.

The current result can represent:
- requested interval
- available/unavailable state
- calculated end time
- staff selection/result
- rejection reason

Stage 29 adds ranked alternatives through a separate AlternativeDiscoveryService.
These are not responsibilities added to the Availability Engine itself; see ADR-027 onward.

---

## ADR-010 — Preferred Booking Interval Is Not a Hard Constraint

**Decision:** A configured booking interval influences preferred start times/display behavior but does not make all other legal times invalid.

**Why:** A rigid time grid can create artificial gaps and reduce schedule capacity.

**Example:** If a real free window begins at a non-grid time because a previous appointment ends there, that actual boundary can be a legal start time even when it is not aligned to the preferred display interval.

**Implication:** Availability is based on real free windows and actual appointment boundaries, not on a mandatory fixed grid.

---

## ADR-011 — Overlap Means Interval Intersection

**Decision:** Appointment conflicts are determined by interval intersection.

Typical rule:
`existing_start < requested_end AND existing_end > requested_start`

**Why:** Two appointments can overlap even when their start times differ.

---

## ADR-012 — Availability Check Is Not Final Concurrency Protection

**Decision:** A successful availability check does not guarantee the slot remains free.

**Why:** Another request can book the same slot between the check and the insert.

**Implication:** The final database transaction must protect appointment creation from race conditions.

---

## ADR-013 — Cancellation Uses Status, Not Deletion

**Decision:** Cancelled appointments remain in the database with `status = CANCELLED`.

**Why:** History, reporting, customer history and auditability depend on retaining the record.

---

## ADR-014 — Waitlist Does Not Auto-Book

**Decision:** When a cancellation opens a slot, matching waitlist customers may be notified/offered the slot, but the system does not silently book them.

---

## ADR-015 — Multi-Tenant Isolation Is Architectural

**Decision:** Tenant/business scope is not merely a frontend concept.

**Implication:** Database queries, services, authorization, AI tools and future integrations must enforce `business_id` scope.

---

## ADR-016 — One Canonical Schema For Current Stage

**Decision:** Keep `app/db/schema.sql` as the canonical schema for the current development stage.

**Why:** Multiple schema/migration files add complexity before production evolution actually requires them.

**Future:** Introduce migrations when deployment/versioned schema evolution justifies them.

---

## ADR-017 — Keep Architecture Simple

**Decision:** Use the smallest architecture that cleanly separates responsibilities.

**Rule:** Do not create files, abstractions, frameworks, or layers merely because they are considered "professional."

A new abstraction needs a real responsibility and a reason.

---

## ADR-018 — User Controls Project Mutations

**Decision:** The user remains the person who changes project files unless explicit permission is given for an artifact modification.

**Why:** This preserves control, traceability and learning.

---

## ADR-019 — Checkpoint Before Context Becomes Risky

**Decision:** At meaningful milestones or when the conversation becomes large/ambiguous, update the context system before continuing.

**Protocol:**
- identify what changed
- record current state
- record decisions
- record test result
- record next step
- stop if necessary

---

## ADR-020 — No Silent Context Assumptions

**Decision:** If the checkpoint, current files and conversation disagree, do not guess.

**Action:** identify the discrepancy and ask for the missing source of truth or inspect the supplied project state.

---

## ADR-021 — Appointment Repository Contract

**Decision:** The Appointment Repository provides a small set of tenant-scoped database access functions required by the Appointment Domain.

Current repository contract:
- `get_staff(connection, business_id, staff_id)`
  - returns a staff member belonging to the specified business
  - does not perform authorization or business-rule decisions
- `get_active_staff_for_service(connection, business_id, service_id)`
  - returns active staff members who are configured to perform the specified service
  - checks staff and staff-service active status
  - does not evaluate working hours, breaks, blocks, or appointments
- `get_services_by_ids(connection, business_id, service_ids)`
  - returns active services belonging to the specified business
  - does not calculate duration or price
- `get_staff_service(connection, business_id, staff_id, service_id)`
  - returns staff-specific service configuration
  - may contain NULL duration/price overrides
  - does not perform fallback to service defaults

**Why:** The Repository should expose only the database information required by higher layers while keeping business decisions inside the Service/Availability layers.

**Testing:** The repository contract is covered by dedicated tests for:
- correct retrieval
- tenant isolation
- active/inactive records
- staff-service compatibility
- empty service lists
- staff-specific overrides
- preservation of NULL overrides

---

## ADR-022 — Scheduling Repository Contract

**Decision:** The Scheduling Repository provides a small tenant/scoped database-access surface for deterministic availability evaluation.

Current repository contract:
- `get_business_working_hours(connection, business_id, day_of_week)`
  - returns business working hours for the requested day
  - does not decide whether a requested appointment is available
- `get_staff_working_hours(connection, staff_id, day_of_week)`
  - returns staff working hours for the requested day
  - does not evaluate breaks, blocks, or appointments
- `get_schedule_breaks(connection, staff_id, day_of_week)`
  - returns the staff member's breaks for the requested day
  - returns them ordered by start time
- `get_schedule_blocks(connection, business_id, staff_id, start_datetime, end_datetime)`
  - returns overlapping business-wide and staff-specific blocks
  - business-wide blocks are represented by `staff_id IS NULL`
  - uses interval-overlap semantics
- `get_staff_appointments(connection, business_id, staff_id, start_datetime, end_datetime)`
  - returns non-cancelled appointments overlapping the requested interval
  - uses interval-overlap semantics
- `get_business_settings(connection, business_id)`
  - returns business settings required by higher scheduling logic
  - does not interpret booking policy

**Why:** Availability logic needs schedule and booking-state data, but the Repository must remain a persistence layer. Availability decisions belong to the Availability Engine.

**Testing:** The contract is covered by dedicated Scheduling Repository tests for:
- correct retrieval
- business/staff isolation
- working-hours absence
- multiple ordered breaks
- business-wide and staff-specific blocks
- overlap and non-overlap
- cancelled appointment exclusion
- settings isolation
- empty-result behavior

**Historical verification (completed Availability milestone):**
- Scheduling Repository tests: **23 passed**
- Full regression at the completed Availability milestone: **67 passed, 0 failed**

---

## ADR-023 — Availability Engine Is a Deterministic Scheduling Component

**Decision:** The Availability Engine is responsible for deciding whether a requested appointment interval fits the currently known scheduling constraints. It does not create appointments and does not own persistence.

**Responsibilities:**
- resolve eligible staff for the requested staff selection
- verify staff-service compatibility
- calculate service duration using tenant configuration and staff overrides
- evaluate business and staff working hours
- evaluate breaks
- evaluate business-wide and staff-specific blocks
- evaluate existing non-cancelled appointments
- evaluate interval fit using real free windows
- return a structured availability result

**Non-responsibilities:**
- creating appointments
- committing or rolling back transactions
- trusting client/LLM duration or price
- performing authorization
- replacing the Appointment Service
- acting as an AI decision-maker

**Why:** This keeps scheduling truth deterministic and reusable by both UI and Chat while preserving a clean boundary between persistence, availability decisions, and appointment creation.

---

## ADR-024 — Availability Uses Real Free Windows, Not a Mandatory Time Grid

**Decision:** Availability is evaluated against continuous legal free windows created by actual schedules, breaks, blocks, and appointments.

**Why:** A hard start-time grid can waste capacity. If an existing appointment ends at a non-grid boundary, the next legal appointment may begin at that actual boundary when the requested service fits.

**Implication:** A preferred booking interval may influence presentation on an otherwise empty schedule, but it must not cause the Availability Engine to reject a legally fitting start time solely because it is not aligned to the preferred interval.

**Testing:** Dedicated tests verify free-window behavior after existing appointments and verify that availability does not depend on a fixed hard time grid.

---

## ADR-025 — Availability Staff Selection

**Decision:** The Availability Engine supports explicit staff selection and an `ANY` selection mode.

Current behavior:
- `SPECIFIC` evaluates the requested staff member.
- `ANY + AUTO` resolves an available eligible staff member deterministically using the current repository ordering.
- `ANY + CHOICE` can represent availability for eligible staff so the higher-level UI/Chat flow can present choices.
- No load-balancing strategy or Strategy Pattern is introduced at this stage.

**Why:** The current requirement is deterministic, understandable staff resolution. More advanced selection/ranking can be added later only when a real responsibility justifies it.

---

## ADR-026 — Availability Is Tested as an Independent Domain Component

**Decision:** The Availability Engine has dedicated tests in addition to the existing repository/domain tests.

**Current coverage includes:**
- service duration/defaults and staff overrides
- working-hour fit
- closed business days
- staff schedule
- breaks
- business-wide and staff-specific blocks
- overlapping appointments
- cancelled appointments
- staff-service compatibility
- multiple services
- `ANY` staff behavior
- invalid inputs
- real free-window behavior
- absence of a hard fixed time grid

**Historical verification (completed Availability milestone):**
- Availability Engine: **23 tests passed**
- Full regression: **67 tests passed, 0 failed**

---

## ADR-027 — Discovery Is Separate From Booking

**Decision:** AlternativeDiscoveryService in `app/services/alternatives.py` orchestrates proposals; AppointmentService remains the booking authority.

- Discovery is SPECIFIC-staff only. `requested_staff_id=None` fails explicitly; ANY discovery has no approved ranking semantics yet.
- Requested staff identity, tenant, and active status are validated independently of compatibility. A valid but incompatible requested staff member is skipped while other eligible staff are searched; they rank relative to the original requested staff ID.
- All requested services must exist and be active in the tenant. Existing AvailabilityEngine compatibility logic determines staff eligible for all services; no eligible staff means an empty discovery result.
- Public eligibility/free-window wrappers delegate to the existing deterministic Availability Engine logic. No schedule, break, block, or overlap rules are duplicated.
- Per-staff service resolution reuses AppointmentService.resolve_staff_services(); repositories remain persistence-only and do not commit/rollback.
- Discovery writes no business data. Its permitted side effect is creating a temporary selection context through an injected store Protocol.

**Why:** Proposal search and actual appointment creation have distinct responsibilities, while sharing deterministic scheduling truth.

---

## ADR-028 — Legal Discovery Range and Deterministic Candidates

**Decision:** Validate the original request before searching; search legal real free windows on the requested date and later dates only.

- AppointmentService and Discovery reject datetime input with `tzinfo is not None` using `AppointmentBookingError("INVALID_DATETIME")`. This does not implement timezone conversion or DST.
- Policy order: `requested_start < now` -> PAST_DATETIME; `< now + minimum notice` -> MINIMUM_BOOKING_NOTICE_VIOLATION; `> add_calendar_months(now, booking_window_months)` -> BOOKING_WINDOW_EXCEEDED.
- Exact allowed boundaries are inclusive. Month-end clamping reuses AppointmentService.add_calendar_months().
- The last search date is the earlier of requested date + alternative_search_window_days and booking-window end date. Candidate starts also obey the exact booking-window datetime.
- The requested day may contain earlier clock-time candidates if they remain at/after the minimum-notice boundary. Earlier calendar dates are never searched.
- Each free window is clipped to the minimum-notice boundary. Include its actual legal start when the full service fits; then generate later starts aligned from midnight to the preferred interval. Exact end fits are allowed; overruns are excluded. No per-minute enumeration or fixed candidate-count cap is imposed.
- Settings remain in business_settings/get_business_settings(). Defaults and missing-row fallbacks are 3 booking months, 0 minimum-notice minutes, 7 alternative-search days, and 30 interval minutes. Businesses may still lack a settings row; no settings-write flow was added.
- Booking interval is preferred candidate alignment and a ranking tie-breaker, never an availability legality constraint (ADR-010/024).

---

## ADR-029 — Priority, Ranking, and Deduplication

**Decision:** Pure helpers in `app/services/appointments.py` implement the approved SPECIFIC ranking contract without database or clock dependencies.

Priority groups and AlternativeReasonCode values:
1. SAME_STAFF_SAME_DAY
2. OTHER_STAFF_SAME_DAY
3. SAME_STAFF_OTHER_DAY
4. OTHER_STAFF_OTHER_DAY

Ranking key: priority group, absolute calendar-day distance, absolute clock-minute distance, preferred interval alignment, staff ID, start datetime. Date distance is separate from clock-time distance. Price, duration, names, and option IDs do not determine ranking.

Duplicate identity is exactly `(staff_id, start_datetime)`. Identical duplicates retain first occurrence; conflicting staff/name/start/end/items/duration/price business truth raises `CONFLICTING_ALTERNATIVE_DUPLICATE`. Other staff at the same time are preserved.

After final ranking/deduplication, discovery assigns request-scoped IDs (`option_001`, etc.). AlternativeSearchResult contains discovery data and the full list (`total_available=len(alternatives)`, `has_more=False`). AlternativeDiscoveryResponse adds `search_id` without changing that result model. Empty discovery returns `search_id=None` and creates no context.

---

## ADR-030 — Minimal Trusted Selection Context and TTL

**Decision:** Use an injected AlternativeSelectionContextStore Protocol; the initial InMemoryAlternativeSelectionContextStore is development/MVP infrastructure.

- A context retains search_id, business_id, requested_service_ids, created_at, expires_at, and options.
- Each stored option contains only option_id, staff_id, and start_datetime. Discovery price, duration, end time, staff name, display strings, and ranking metadata are not trusted booking data.
- Context creation occurs only after final option IDs are assigned. A UUID provides an opaque search ID; business flow does not parse or depend on its format.
- Option IDs are scoped to their search ID, are not appointment IDs, and do not reserve a slot.
- TTL is 15 minutes from the injectable server clock: before expires_at is valid; at/after expires_at is expired. Claims do not extend TTL.
- Frozen dataclasses, copied option records, and tuple collections protect stored snapshots from ordinary input-list mutation.
- Unknown/missing/expired contexts raise ALTERNATIVE_SEARCH_EXPIRED. A missing option within a valid context raises ALTERNATIVE_OPTION_NOT_FOUND. There is no fallback selection.

---

## ADR-031 — Selection Executes Through AppointmentService

**Decision:** AlternativeSelectionService in `app/services/alternative_selection_execution.py` receives the context store and AppointmentService through dependency injection.

- Inputs are search_id, option_id, trusted customer_id, and optional notes. Customer identity is external trusted application context and is not stored during discovery.
- Trusted business ID, staff ID, service IDs, and start datetime come only from the claimed server context. The service calls AppointmentService.book() with SPECIFIC staff and does not authorize ANY/AUTO assignment.
- AppointmentService freshly validates customer/staff/services, calculates duration/price/end datetime, enforces booking policy, checks availability, and rechecks within its existing DB-layer transaction before atomic appointment/item creation. Discovery display data is never used as final price or duration.
- Return AppointmentBookingResult directly. No API/Chat response abstraction is introduced for execution.
- APPOINTMENT_UNAVAILABLE, PAST_DATETIME, MINIMUM_BOOKING_NOTICE_VIOLATION, BOOKING_WINDOW_EXCEEDED, STAFF_NOT_FOUND, STAFF_INACTIVE, SERVICE_NOT_FOUND, and SERVICE_NOT_SUPPORTED map to AlternativeSelectionError("SELECTED_ALTERNATIVE_NO_LONGER_AVAILABLE").
- CUSTOMER_NOT_FOUND and other unmapped booking errors retain their original meaning. Store/concurrency errors remain distinct and are not mapped to stale availability.
- Do not silently retry with another option or choose another staff member after failure. The caller must explicitly choose a further option.

---

## ADR-032 — Atomic Per-Search Claim and Lifecycle

**Decision:** Selection Execution calls claim_selection() before booking. In-memory context state and a set of claimed search IDs are protected by threading.RLock.

- Existence/TTL, option lookup, claim check, and claim insertion are atomic under the store lock. A second valid-option claim on the same search raises ALTERNATIVE_SELECTION_IN_PROGRESS, even for a different slot/staff member.
- The lock is released before AppointmentService.book(); unrelated searches can proceed independently. The claim remains until completion or expiry.
- Booking success expires the entire context and removes its claim. Booking failure, including unexpected exceptions, releases the claim and preserves an otherwise unexpired context. Exceptions are propagated or mapped explicitly, not swallowed.
- Errors before claim acquisition do not release another request's claim. resolve_selection() remains a read/resolve operation and does not authorize a second execution.
- At/after expires_at, future resolve/claim operations fail and remove context/claim state. Expiry does not cancel an already running booking.
- Per-search claim protection complements AppointmentService's final slot-conflict protection; it is not a general idempotency framework or a reservation.

---

## ADR-033 — Presentation Owns Conversational Messages

**Decision:** Domain services return structured discovery data, reason codes, deterministic errors, and booking results. Future presentation/Chat layers format user-facing explanations and confirmation prompts.

No Stage 29 API routes, UI, Chat, or AI selection execution integration exists. LLM/client input cannot override trusted selection values or deterministic booking rules. Authentication/authorization remains an application-boundary responsibility; UUID/search-ID possession is not a complete authorization design.

---

## ADR-034 — Stage 29 Production-Deferred Limitations

**Decision:** Accept the reviewed implementation for development/MVP with explicit limitations; do not claim production readiness.

- In-memory contexts and RLock claims work only within one process and shared store instance. Multi-worker deployment requires shared atomic storage; no Redis, durable context storage, or distributed claim infrastructure exists.
- No restart durability or atomic commit spans appointment booking and context consumption. Exactly-once execution across crashes is not guaranteed. No reservation/hold exists.
- SQLite is temporary. No production database migration or persistent migration infrastructure exists; canonical-schema edits do not upgrade old databases automatically.
- Business-local timezone/DST hardening and consistent scheduling clocks remain future work. Aware-input rejection is the current deterministic boundary only.
- Positive-value checks and SQLite INTEGER affinity do not fully enforce integer settings; fractional/text rejection needs hardening. No settings-write API exists yet.
- Expired-context cleanup is lazy; unaccessed expired contexts may retain memory. Discovery remains unpaginated without production capacity controls/query optimization.
- At the Stage 29 checkpoint, ANY Alternative Discovery, authentication/authorization integration, API/UI/Chat integration, cancellation, and rescheduling were deferred. Stage 30B.1 now supplies development auth only on its feature branch (ADR-035 onward); booking/search ownership integration, business APIs, UI/Chat and cancellation/rescheduling execution are still deferred.
- Audit follow-ups also include regression coverage for unexpected booking failure cleanup, concurrent independent searches, TTL during active booking, exact candidate datetime boundaries, and changed configuration between discovery and selection. These were non-blocking notes, not completed features/tests.

**Verification history:** Final Audit at `ef8fdd4ebf50735c43a4e91e202c1072e9fb3d60`: 163 passed, READY WITH NON-BLOCKING NOTES. Reviewed aware-input fix at `6091baf8caea0aa005bba4e7271b285368da162d`: 167 passed. Stage 29 was then merged into `main` through [PR #2](https://github.com/gnhbsc30-source/barbershop_crm/pull/2) using regular Merge Commit `d6b4866461bab2ea13dd8a001b3101136110c26a`; post-merge regression: **167 passed, 0 failed**. The later Stage 29 documentation checkpoint is `2936be4013aa2b5c6440442ce49afec8eb3effcb`. Its production limitations remain unless explicitly superseded below.

---

## ADR-035 — Provisioned Development Identities and Credential Truth

**Decision:** Local MVP authentication uses explicitly provisioned synthetic OWNER and CUSTOMER identities with random access codes, not client-selected roles or hardcoded credentials.

- Immutable Principal contains a namespaced subject, business ID and either owner user ID or linked customer ID. OWNER must be an active OWNER user in an active business. CUSTOMER must be an active customer account linked to an active same-business customer; CUSTOMER is not a staff role.
- Server-side IdentityReference selects account kind/business/account ID. Account IDs and customer IDs are distinct; `user:1` and `customer_account:1` are different subjects. Caller IDs, role labels or search IDs never authorize access.
- Authentication validates DB identity truth; a provisioning manifest is a private locator, not a substitute for DB validation. Repository reads are tenant-scoped and persistence-only; no schema change is needed.
- Credentials are `<16-character public selector>.<43-character random secret>` using cryptographic randomness (96-bit selector, 256-bit secret). Strict parsing and generic INVALID_CREDENTIALS errors avoid identity disclosure; unknown/invalid mapped identities receive dummy verification when a well-formed code reaches credential verification.
- Argon2id generation uses 19 MiB, time cost 2, parallelism 1, 16-byte salt and 32-byte hash. Actual verification validates payloads; metadata inspection alone is not payload validation or work-factor hardening.
- `app/services/authentication.py` owns these stateless identity/credential functions. It contains no HTTP, session lifecycle or rate-limit state.

**Status:** Implemented on the Stage 30B.1 feature branch only; not production authentication/onboarding.

---

## ADR-036 — Absolute Sessions, Fresh Identity Binding and Bounded Attempts

**Decision:** Keep independent, locked application-scoped session and rate-limit state in `app/core/auth_sessions.py` and `app/core/auth_rate_limit.py`.

- Session tokens and session-bound CSRF tokens are independent cryptographically random values. Store only SHA-256 token keys; frozen context includes Principal, fingerprint, CSRF token and timestamps. Default live-session capacity is 1024.
- Absolute one-hour TTL uses an injectable aware clock: valid before expires_at, invalid at/after it. No sliding renewal. Successful login replaces the presented existing session only after credential verification; failed login leaves it intact. Logout invalidates only the authenticated session.
- A credential fingerprint is SHA-256 of the exact stored credential hash verified by login, not a second potentially changed DB read. It is an internal change detector, not a public credential.
- HTTP authentication freshly revalidates tenant/account/business activity, OWNER role or CUSTOMER link, selector, Principal and fingerprint. Replacement hashes and changed bindings invalidate old sessions on revalidation; sessions cannot adopt new credentials silently. This does not retract already-authorized in-flight work.
- Rate limits admit at most five attempts per selector/five minutes and twenty globally/minute, counting successes too. Unknown/malformed selectors count before costly verification. Denied attempts do not extend windows; expired window entries are pruned at exact boundaries. State is bounded and operations atomic under locks.
- Infrastructure does not query the DB or own authorization decisions. HTTP dependencies compose fresh identity reads with store checks; SQLite connection creation/use/closure stays in one sync worker.

---

## ADR-037 — Explicit Provisioning, Private Manifest and Manual Rotation

**Decision:** Development provisioning and rotation are explicit terminal commands in `app/dev_data.py`, never startup repair/reset or public HTTP endpoints.

- Only the configured dedicated `data/dev/barbershop.db` in development mode is accepted by the CLI. Private owner-controlled directory/files, symlink checks and an exclusive provisioning lock guard creation. Runtime DB/manifest are ignored, never committed.
- Explicit synthetic seed contains one business, OWNER, CUSTOMER, two staff/services, compatibility assignments, schedules and settings using the canonical schema. Known unchanged reruns preserve appointments/data and do not regenerate credentials. Unknown, incomplete or inconsistent data is refused without automatic reset.
- The private manifest contains selectors, trusted account references, schema fingerprint and non-secret fixture metadata; no raw codes, session tokens or password hashes. Identity still requires current DB validation.
- Manual rotation requires `ROTATE <selector>` confirmation for an existing provisioned identity. It generates a fresh high-entropy secret, hashes with Argon2id and conditionally replaces the expected previous hash within a short DB-layer transaction. No appointment, schedule, service or customer record is changed.
- Credential changes reject existing sessions at subsequent fingerprint validation, including sessions in a running application; no shared session-reset registry is needed.
- Secrets are delivered only to the confirmed terminal after commit, not ordinary logs. Interrupted delivery requires another explicit rotation; no old-secret restoration, hardcoded fallback or automatic reset.
- SQLite and manifest writes are not cross-resource atomic. A partially created DB/manifest may remain after interruption; startup/reruns fail closed and require explicit recovery. File fsync is not a database-plus-manifest atomic commit guarantee.

---

## ADR-038 — Same-Origin, Loopback-Only Development HTTP Authentication

**Decision:** Expose only three auth endpoints plus health under an explicitly local HTTP security boundary.

- POST /api/v1/auth/dev-session accepts only a strict access_code JSON body; exact Origin, `X-CSRF-Protection: 1` and JSON Content-Type are required. Retained body bytes are bounded to 4096 before validation; attempt accounting precedes Argon2 work.
- GET /api/v1/auth/me returns freshly validated Principal, absolute expiry and CSRF token. DELETE /api/v1/auth/session requires exact Origin and its session-bound X-CSRF-Token, then invalidates that session and clears its cookie.
- Session token is cookie-only: host-only, HttpOnly, SameSite=Strict, Path=/, Max-Age=3600. `Secure=False` is permitted only for this approved local HTTP development boundary, not production.
- Require exact Host `127.0.0.1:8000`, loopback client address and HTTP scheme; reject unexpected/duplicate Host/Origin and CSRF headers. No permissive CORS or trust in caller-forwarded client IPs.
- Cache-Control: no-store is enforced on responses. Error bodies do not echo validation input or exception detail; raw credential/token/hash material is not sent to ordinary exception logs. Protocol headers such as 405 Allow are preserved while no-store overrides permissive cache headers.

---

## ADR-039 — Explicit Routes and Endpoint-Owned Query Validation

**Decision:** Disable FastAPI application-level automatic trailing-slash redirects. Wrong slash variants/unknown paths fail safely instead of reflecting caller query values into Location headers; no custom redirect rewriting is introduced.

- All three auth endpoints reject unexpected query parameters with INVALID_REQUEST. Login retains rate-limit accounting before that query rejection.
- Reusable authentication/role dependencies validate identity/authorization, not arbitrary endpoint query schemas. Future business GET endpoints may define legitimate query parameters without bypassing fresh identity/fingerprint checks.
- Test-only probe routes establish this reuse; no business API is introduced by the tests.

**Audit remediation:** `24edf454379d142d9816131a83783b4c4e93ff32` resolves the reproduced redirect blocker and approved query/header findings. Verification: 92 focused HTTP/launcher tests and 405 full regression tests passed; diff check clean. Guards, cookie lifecycle and Stage 27–29 behavior remain intact.

---

## ADR-040 — Thin Composition and Single-Worker Development Limits

**Decision:** `app/main.py` only composes FastAPI lifespan, middleware, handlers/router and health. Identity verification, SQL, sessions, rate limits and domain rules stay in their responsible modules; no factories/framework layers are added.

- App lifespan validates pre-provisioned data and installs one session store and limiter per app instance; it never creates credentials or seeds a DB.
- Supported launcher is `python -m app.dev_server`: 127.0.0.1:8000, one worker, no reload, proxy trust or access logs. Startup checks development mode, WEB_CONCURRENCY and private recognized data. Arbitrary external ASGI launch settings are not certified by these checks.
- In-memory sessions/rate limits and selection contexts are process-local/non-durable; multi-worker deployment requires shared atomic storage. Local HTTP and private OS files assume a trusted development machine. HTTPS, production identity lifecycle and distributed infrastructure remain future work.
- SQLite is temporary, with no migrations or production DB infrastructure. Existing Stage 29 timezone/DST, settings integer enforcement and lazy selection-context cleanup limitations remain.
- Non-blocking hardening remains: stored Argon2 parameter bounds, safe diagnostics for unexpected errors, slow-body/concurrent-request resource limits, and additional logout/rotation race coverage. Login buffering is bounded, not a complete network DoS defense.
- No frontend, booking/calendar HTTP APIs, search-ownership execution, cancellation/rescheduling commands, ANY Alternative Discovery or AI/Chat integration is provided by this foundation. It is not production readiness.

---

## ADR-041 — Future MVP Owner Editing Is Staff/Date/Time Only

**Decision:** The approved future owner-editing/rescheduling boundary allows changing staff, date and time only. Services, customer identity, manual prices and notes are excluded from MVP rescheduling.

**Status:** Scope decision only, not implemented by Stage 30B.1. Both future customer/owner interfaces must share the real backend/development DB. Cancellation cutoffs, rescheduling/status-transition policy, owner overrides and repricing remain undecided; this ADR does not approve those policies or infer a cancellation/rescheduling command implementation.

**Next:** Stage 30B.2 is intended to address the authenticated business API boundary in separate reviewable slices; exact contracts need approval. Do not start it automatically or merge Stage 30B.1 without explicit approval.

---

## Architecture Principle — Complexity-Driven Structure

The project does not introduce classes, files, abstractions, interfaces, or design patterns merely for stylistic or "professional-looking" structure.

A class or file split should be introduced only when it provides meaningful separation of responsibility, state management, complexity reduction, testability, or maintainability.

Architectural structure should evolve with the system rather than being forced prematurely.

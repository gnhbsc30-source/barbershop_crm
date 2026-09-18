# Barbershop CRM — Architecture Decision Record

This file records decisions that should survive conversation changes.

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

Future extensions may add ranked alternatives and richer slot-search results, but those are not considered part of the completed current engine unless explicitly implemented.

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

**Latest verification:**
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

**Latest verification:**
- Availability Engine: **23 tests passed**
- Full regression: **67 tests passed, 0 failed**

---

## Architecture Principle — Complexity-Driven Structure

The project does not introduce classes, files, abstractions, interfaces, or design patterns merely for stylistic or "professional-looking" structure.

A class or file split should be introduced only when it provides meaningful separation of responsibility, state management, complexity reduction, testability, or maintainability.

Architectural structure should evolve with the system rather than being forced prematurely.

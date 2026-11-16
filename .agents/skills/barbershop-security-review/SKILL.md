---
name: barbershop-security-review
description: Review Barbershop CRM security-sensitive changes or threat models for authentication, browser sessions, tenant and slug boundaries, customer privacy, booking transactions, and appointment edits.
license: Apache-2.0
---

# Barbershop Security Review

Set the review scope using [project state](../../../docs/PROJECT_STATE.md) and [architecture decisions](../../../docs/ARCHITECTURE_DECISIONS.md). Trace the relevant request through authentication, authorization, input validation, services, persistence and response preparation. Distinguish implemented controls, proposed behavior and assumptions.

## Project review

- **Identity.** Inspect [authentication dependencies](../../../app/api/auth_dependencies.py), [local request protections](../../../app/core/local_security.py), and relevant tests. Confirm fresh OWNER/CUSTOMER identity and fingerprint revalidation, roles, session-bound CSRF, cookies, Host and Origin checks, logout, session expiry and failed-login throttling. Preserve the local-only deployment assumptions. Avoid exposing credentials, session or CSRF tokens and configuration in logs, URLs, unintended responses or browser storage.
- **Tenant isolation.** A public slug locates a business. Visible brand names, URL paths, client-supplied business IDs, customer IDs and search IDs grant no access. Keep public discovery separate from authenticated booking. Derive tenant, role and ownership on the server through trusted identity and policy. Verify that foreign resources fail closed and that joined rows, search selection and mutations remain scoped.
- **HTTP and privacy.** Check request schemas, duplicate handling where contracted, size and page bounds, sanitized errors, protocol headers, and response projections. Calendar contact fields follow the approved privacy boundary. Treat branding content and browser state as untrusted input at rendering and authorization boundaries.
- **Booking integrity.** Inspect [booking transactions](../../../app/services/appointments.py) and [owner operations](../../../app/services/owner_appointments.py). Verify exact ordered ILS confirmation, transactional policy and availability, guest rollback, and pre-commit response preparation. Check search ownership before claim, guest/account separation, strong If-Match, monotonic revisions, self-exclusion during moves and booked snapshot preservation.
- **Verification.** Use static review and focused tests with synthetic identities and isolated databases. Cover missing or wrong roles, foreign resources, CSRF failures, invalid input, stale revisions and rollback as relevant. Keep exploit development, live probing and destructive activity outside this workflow. Report gaps without changing approved security, business or deployment policy.

## Architecture threat model

Identify assets, actors, entry points and concrete trust boundaries. For each relevant abuse scenario describe realistic actor capabilities, preconditions, affected assets and consequences. Prioritize by impact and likelihood in the actual environment. Separate controls demonstrated by evidence from mitigation proposals and record residual risks.

## Review output

Classify findings as BLOCKER, HIGH, MEDIUM, LOW or ACCEPTED LIMITATION. Include evidence, affected trust boundary, impact, smallest recommended fix and verification approach. Label untested assumptions. Conclude PASS, PASS WITH NOTES or BLOCKED for the reviewed scope.

[Upstream provenance and adaptation](PROVENANCE.md)

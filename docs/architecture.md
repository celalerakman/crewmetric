# Architecture and data invariants

## Modules

- `service.py`: domain validation, exact units, personnel/project management, labor lifecycle, planning and performance calculations.
- `web.py`: role checks, signed sessions, CSRF-protected forms, HTML and CSV responses.
- `cli.py`: initialization, WSGI serving, online backup and integrity verification.
- `seed.py`: fictional construction data covering trades and personnel categories.
- `schema.sql`: foreign keys, constraints, reporting indexes and immutable-record triggers.

## Separation of dimensions

Personnel category describes the person: blue collar, white collar or administrative. Specialty describes the person’s trade. Activity trade describes the work package and drives trade-level actual-hour reporting. Direct/indirect describes whether that activity measures production. These dimensions are deliberately independent: an engineer assisting a direct production package remains white collar, while their hours belong to that direct package.

Crew and contractor snapshots preserve the team context of a daily record. A personnel register row is a stable identity; deactivating it blocks new draft lines and allocations without removing history. The UI supports rate changes and deactivation but not reassignment of an existing person’s category/crew.

## Approval transaction

1. `BEGIN IMMEDIATE` obtains the SQLite write lock before reading approved hours.
2. Require a submitted sheet and an administrator who did not author it.
3. Reject an existing approved sheet for the same activity/date.
4. Sum each person’s already-approved minutes across every project on that date.
5. Reject any person whose new total would exceed 1,440 minutes.
6. Update approval metadata and append audit, then commit once.

Simultaneous approvals serialize at the write lock. A database partial unique index additionally protects the one-approved-sheet-per-activity/day invariant. Submission freezes attendance lines; database triggers reject their modification until an administrator returns the sheet to draft.

Approved sheets cannot be edited or deleted. The only permitted approved-record transition is to `void`, with all original fields retained. The supported void service requires an administrator and reason, and appends audit in the same transaction. Voided sheets stay immutable. File-level administrators remain privileged: a person who can replace the database or modify its triggers can bypass application protections.

## Idempotency

Daily draft creation uses a unique request key. A repeated key returns the existing sheet without creating another one. The key identifies an operation, not a payload comparison; clients must generate a new key for changed input. Repeated approval/submission returns a validation error and cannot double-count time. Allocations upsert a personnel/project/date key, while zero removes that assignment.

## Exact calculations

Durations are integer minutes. Decimal-hour input must convert exactly to minutes; `H:MM` permits individual-minute entries. Quantities have three decimal places and rates have two. Overtime factors use ten-thousandths, allowing an input such as `1.5` without floating-point cost drift. Cost is calculated with `Decimal` and rounded half-up once per line to currency cents.

Package performance uses approved output once per sheet and approved attendance summed across its people. Cost and duration are aggregated from attendance lines separately from output so a join cannot multiply production by crew size. Earned and forecast hours use the original package baseline and are suppressed for indirect work or absent positive production, as appropriate.

## Reporting scope

Period reports filter by the work date, not the audit/posting timestamp. Void operations cause operational totals to be recalculated without that sheet; there is no immutable accounting-period close. The dashboard labels its all-time package performance separately from period actual totals. Reports can include an explicit project. Crew planning is independent of approval and never acts as evidence of attendance.

No automatic legal overtime, payroll deductions, paid leave, monthly salary conversion or attendance clock-overlap detection is performed. The 24-hour aggregate constraint catches impossible daily totals; it cannot prove that two submitted intervals overlap because the application records durations rather than start/finish intervals.

## Security

Sessions are signed, HTTP-only, SameSite=Lax and expire after eight hours. HTTPS deployments enable secure cookies. Successful login rotates session state. Every POST requires a session CSRF token; templates escape text, SQL parameters are bound, downloads neutralize formula-like text and pages use a restrictive same-origin CSP. No external JavaScript, fonts or analytics are loaded.

Non-administrators receive no rate or cost values in the rendered HTML or CSV. All users can inspect operational records within the organization. The service layer separately enforces mutation roles. Account provisioning is administrator-only; account role editing, disabling and password reset are outside the current UI. Generated demo credentials are stored only under ignored runtime paths.

# Implementation Plan — Milestone 2: Student Onboarding (ONB)

Status: IN PROGRESS · Created: 27-07-2026
Source requirements: [02-student-onboarding.md](../../requirements/02-student-onboarding.md),
plus the TTM slice [03-timetable-management.md](../../requirements/03-timetable-management.md) TTM-FR-18/19

## 1. Decisions locked in the SME pass (27-07-2026)

| Open question | Decision |
|---|---|
| Roll-number source | **ERP-issued, imported.** UniCore enforces uniqueness within Program + admission year; collisions are rejected to the error report and resolved in the ERP |
| ERP feed | **CSV upload only for MVP.** The API feed is a later adapter over the *same* validation pipeline |
| Section instances | **Thin TTM slice ships in this milestone** — School academic term + Section-instance endpoints (Timetable Cell), else there is nothing to allot into |
| ERP ID format | **Opaque string** (trimmed, ≤100 chars, non-empty); no format assumptions, tightened per-School later if the ERP team confirms a pattern |

**Campus-scope interpretation:** the requirement says imports are "campus-scoped".
Our org model carries campus as a dimension, not a hierarchy node, so campus scoping
is realised as **org-unit scoping** — an `office-staff` grant at a School covers that
subtree; rows targeting Programs outside it are rejected `scope-conflict`
(ONB §8). System Admin/Super Admin (University scope) cross everything.

## 2. New role

`office-staff` — School-scoped, designated by the School Incharge. Runs imports,
single adds, allotment/re-allotment, withdrawal within their subtree. Added to the
AUTH role registry and seeded in migration 0004.

## 3. Data model (migration 0004)

| Table | Purpose |
|---|---|
| `academic_terms` | Per-School term: code, start/end, exam ranges + special events (JSONB), archival backstop, draft→approved with recorded approver, versioned (TTM-FR-18) |
| `import_batches` | Who/when/file hash/term/counts/status (`processing`/`committed`/`needs-review`) — the >20 % risky-change guardrail parks a batch in `needs-review` |
| `import_row_errors` | Row number, field, reason, raw row — powers the downloadable error report (ONB-FR-03) |
| `student_profiles` | Student-specific fields hanging off `users`: roll number, admission year, DOB, gender, Program org unit |
| `section_memberships` | (student, Section, effective_from, effective_to) — immutable history; **membership as-of-date** reads for TTM/ATT (ONB-FR-10) |

## 4. Phases

### Phase 1 — TTM slice: terms + Section instances
Academic-term CRUD (office staff upload → School Incharge approval, versioned) and
Section-instance creation per Program per term by the Timetable Cell.
**Covers:** TTM-FR-18/19. **Exit:** a term exists and Sections can be created under it.

### Phase 2 — Import pipeline
CSV upload → pre-parse gate (≤50 MB, UTF-8, header matches schema version) → row
validation → partial commit → batch summary + error report. Idempotent upsert on
ERP ID; in-file duplicate detection; roll-number uniqueness; Program/Section
resolution by code within the actor's scope; >20 % org/DOB-change guardrail.
**Covers:** ONB-FR-01/02/03/04/05/07/08/14. **Exit:** 100-row file with deliberate
errors commits the good rows and reports the rest.

### Phase 3 — Lifecycle
Single-student add, Section allotment/re-allotment with effective dates,
Program/campus transfer (System Admin), withdrawal with session revocation, and the
membership-as-of-date read API consumed later by TTM/ATT.
**Covers:** ONB-FR-09/10/11/12/15.

### Phase 4 — Credentials + dashboard
Activation pipeline (credential generation → SMS/email delivery → `ACTIVE`) reusing
the AUTH provider stack; per-student delivery status; batch dashboard.
**Covers:** ONB-FR-06/14.

### Phase 5 — UI
Import screen (upload, batch list, error-report download), roster with membership
and delivery-status badges, term/Section setup screen — in the approved design
system.

## 5. Out of scope for this milestone

ERP API feed (adapter over the same pipeline, later), grievance round-trip to the
ERP (ONB-FR-13 — the AUTH grievance flow already exists; the ERP hand-off waits for
integration details), and everything else in the TTM module beyond FR-18/19.

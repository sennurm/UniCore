# Requirement: Student Onboarding

Module code: ONB · Status: DRAFT — pending approval · Last updated: 2026-07-21

## 1. Summary

Student onboarding in UniCore is **import-only**: students arrive fully formed from the university's existing ERP, either as bulk files (CSV) or via an ERP API feed. UniCore does **not** admit students — it **provisions** them: creates the user account, delivers initial credentials (SMS primary, email fallback), maps each student to campus/School/Department/Program/Section, records the roll number, and kicks off device registration on first login (per 01-authentication-authorization-security.md). All students are 18+ at admission (locked 24-07-2026, 00-overview.md §7) — there is no minor/parental-consent handling. Sections are **per-term instances created by the Timetable Cell during term setup** (TTM-FR-19); allotment targets the current term's instances. The module also covers the ongoing lifecycle: mid-term single-student additions, section allotment and re-allotment, campus/program transfers, withdrawal/dropout deactivation, and data-correction grievances.

## 2. Goals & Non-Goals

**Goals**
- Bulk import of student records from ERP (CSV upload and API feed) with row-level validation and an actionable error report.
- Idempotent imports: re-importing the same batch upserts, never duplicates.
- Account + credential provisioning with initial-credential delivery via SMS (email fallback) and forced password change on first login (AUTH doc).
- Program/Section mapping and roll-number recording at import time.
- Mid-term single-student add, section re-allotment, campus/program transfer, withdrawal deactivation — all audited.
- Data-correction handling via the AUTH grievance flow, with ERP as the master for ERP-owned fields.

**Non-Goals**
- Admissions, merit lists, seat allocation, fee collection or fee status — all remain in the ERP.
- Self-registration of any kind (locked in AUTH doc).
- Faculty/staff onboarding (provisioned by System Admin under AUTH-FR-01; not this module).
- Editing ERP-mastered fields (name, date of birth, ERP ID) inside UniCore — corrections round-trip through the ERP.

## 3. Affected User Groups & Access

| Group | Access granted |
|---|---|
| Admin/office staff | Run imports, single adds, section (re-)allotment, withdrawal — **scoped to their own campus** |
| System Admin (IT cell) | Same operations **cross-campus**; transfer execution; import configuration (ERP API keys, file schema version) |
| HoD | Read-only view of their Department's onboarding status (imported / pending activation / active) |
| Class In-charge | Read-only roster of their Section |
| Students | View own profile; raise correction grievances; no onboarding actions |
| Faculty Members / Executives | No onboarding actions; roster visibility via their module roles |

**Denied:** students cannot self-register, self-select Sections, or edit any imported field. No user outside campus scope sees another campus's import batches.

## 4. Authorization & Business Rules

### Per-action authorization

| Action | Allowed | Enforced at |
|---|---|---|
| Upload/trigger import batch | Admin/office staff (own campus), System Admin (any campus) | API + service layer |
| View import error report | Uploader, Admin/office staff of that campus, System Admin | API |
| Single-student add (mid-term) | Admin/office staff (own campus), System Admin | API + service layer |
| Section allotment / re-allotment | Admin/office staff (own campus, own Program scope), System Admin | API + service layer |
| Campus or Program transfer | System Admin only (crosses org-unit scopes) | API + service layer |
| Withdrawal / dropout deactivation | Admin/office staff (own campus), System Admin; deactivation revokes sessions per AUTH | API + session store |
| Approve data-correction grievance | Admin/office staff for UniCore-owned fields; ERP-mastered fields routed to ERP with tracked status | API |

### Business rules

1. **ERP ID is the identity join key.** Every row must carry it; matching is by ERP ID only — never by name.
2. **Idempotent upsert:** an incoming row whose ERP ID exists updates the existing record (changed fields only, all diffs audited); it never creates a duplicate. Re-running an entire batch is safe.
3. A student is created in state `IMPORTED`; moves to `ACTIVE` once credential delivery is initiated. (No consent gate — all students are 18+ per locked policy; UniCore's own DPDP consent is captured at first login per the AUTH doc.)
4. Roll numbers are **imported from the ERP** (see Open Questions) and unique within a Program + admission year; a collision rejects the row.
5. Section allotment is per Program term; re-allotment closes the old membership with an effective date (history preserved — attendance stays attached to the old Section's Sessions).
6. Transfers create a new org-unit mapping with effective date; the old mapping is closed, never deleted. Roll number handling on transfer follows the ERP's decision carried in the transfer record.
7. Withdrawal sets state `WITHDRAWN`, revokes sessions immediately (AUTH), removes the student from future Sessions, and preserves all history for statutory retention. Re-joining reactivates the same account (AUTH business rule 1).
8. First login triggers device registration (AUTH-FR-06); onboarding only kicks it off, it does not manage devices.

### Audit

Every batch (who, when, file hash, row counts: created/updated/rejected), every single add, re-allotment, transfer, and withdrawal writes to the central audit service with before/after snapshots and reason where applicable.

## 5. Legal & Regulatory Requirements

- **DPDP data minimization:** the import schema carries only fields UniCore needs (identity, contact for OTP/credentials, org mapping, DOB as an identity field). Fee, caste/category, and other ERP fields are not imported.
- **DPDP notice & consent:** consent is captured at first login per the AUTH doc; onboarding must not activate processing beyond provisioning before that (credential delivery itself is covered by the admission-time notice recorded in the ERP — see Assumptions).
- **Minors:** not applicable — all students are 18+ at admission (locked 24-07-2026, 00-overview.md §7). DOB is imported but never age-checked; no consent artifact, minor flag, or `PENDING-CONSENT` state exists.
- **Right to correction:** grievances on UniCore-owned fields (contact number, section-display data) are correctable in UniCore; ERP-mastered fields are forwarded to the ERP and the grievance status tracks the round-trip — the student always receives a response, never silence.
- **Retention:** withdrawn students' academic records are retained per university statute (erasure requests answered with the statutory-exemption response per AUTH doc §5).

## 6. User Stories & Acceptance Criteria

**US-ONB-1** — As an Admin/office staff member, I import the new B.Tech intake CSV so that 600 students get accounts before term start.
- Given a file with 600 rows of which 12 are invalid, when I run the import, then 588 accounts are created/updated, 12 rows land in a downloadable error report with row number + field + reason, and the batch summary is audited.
- Given I re-upload the corrected file, when the import runs, then the 588 already-imported rows update idempotently (0 duplicates) and the 12 fixed rows are created.

**US-ONB-2** — As a newly imported Student, I receive my initial credentials so that I can log in.
- Given my account reached `ACTIVE`, when provisioning completes, then I receive a one-time credential SMS (email fallback on SMS failure) and my first login forces a password change and starts device registration.

**US-ONB-3** — As an Admin/office staff member, I add one mid-term admission so that a late joiner is provisioned without a batch file.
- Given valid single-student data with an ERP ID, when I submit, then the same validation and credential flow run as for bulk rows.

**US-ONB-4** — As an Admin/office staff member, I re-allot a student from Section A to Section B so that the roster matches the Department's decision.
- Given an effective date, when I confirm, then Section B membership starts on that date, Section A history and past attendance are untouched, and both rosters plus the timetable-facing membership update within 5 minutes.

**US-ONB-5** — As a System Admin, I execute a campus transfer so that a student moves campuses with history intact.
- Given ERP transfer confirmation, when I execute, then the old mapping closes, the new campus/Program/Section mapping opens, and the student's login, device registration, and history carry over unchanged.

**US-ONB-6** — *(Removed — locked 24-07-2026: all students are 18+; no parental-consent gate exists. See 00-overview.md §7.)*

## 7. Functional Requirements

- ONB-FR-01: Bulk import via CSV upload and via ERP API feed against a versioned schema; both paths share one validation pipeline.
- ONB-FR-02: Row-level validation: mandatory fields present, ERP ID format, valid campus/School/Department/Program/Section codes, DOB plausibility, roll-number uniqueness, contact format.
- ONB-FR-03: **Partial-commit semantics:** valid rows commit, invalid rows go to a per-batch error report (row number, field, reason, raw row). All-or-nothing is NOT used — see §8 for rationale.
- ONB-FR-04: Idempotent upsert keyed on ERP ID; per-row outcome (created/updated/unchanged/rejected) in the batch summary.
- ONB-FR-05: In-file duplicate detection: two rows with the same ERP ID in one file — first valid row wins, later rows rejected to the error report as in-file duplicates.
- ONB-FR-06: Account provisioning into `IMPORTED` state; activation pipeline: credential generation → delivery (SMS primary, email fallback) → `ACTIVE`.
- ONB-FR-07: **Section-instance dependency:** section allotment (bulk or single) targets only Section instances of the current term created by the Timetable Cell (TTM-FR-19); a row or allotment referencing a Section with no instance for the target term is rejected with `section-not-created`, pointing to the Timetable Cell. Sections are never auto-created from import data.
- ONB-FR-08: Roll-number import and uniqueness enforcement (Program + admission year).
- ONB-FR-09: Mid-term single-student add using the identical validation/activation pipeline.
- ONB-FR-10: Section allotment and re-allotment with effective dates; historical memberships immutable; downstream modules (TTM, ATT) read membership as of a date.
- ONB-FR-11: Campus/Program transfer (System Admin) with mapping close/open, effective date, and full audit.
- ONB-FR-12: Withdrawal/dropout: state change, immediate session revocation, removal from future Sessions, retention of history.
- ONB-FR-13: Data-correction grievance handling: UniCore-owned fields correctable with audit; ERP-mastered fields routed to ERP with tracked status and user-visible outcome.
- ONB-FR-14: Batch dashboard: per-batch counts, error-report download, credential-delivery status (delivered/failed/pending) per student.
- ONB-FR-15: All operations campus-scoped per §4; cross-campus operations restricted to System Admin.

## 8. Edge Cases, Worst Cases & Decisions

| Case | Decision |
|---|---|
| File contains some invalid rows | **Partial commit** (valid rows in, invalid rows to error report). Rationale: at 15,000+ students, all-or-nothing lets one typo block an entire campus go-live; idempotent upsert (ONB-FR-04) makes fix-and-re-import of just the failed rows safe and cheap. |
| Same batch imported twice (double click, retry after timeout) | Idempotent upsert — second run reports all rows `unchanged`; zero duplicates. File hash shown in batch history so staff can see it was a re-run. |
| Two rows in one file share an ERP ID | First valid row processed, subsequent rejected as in-file duplicates (ONB-FR-05). No silent last-write-wins. |
| Row's ERP ID exists but name/DOB wildly differ | Update is applied (ERP is master) but flagged `identity-warning` in the batch report for human review. |
| Invalid Program/Section code | Row rejected to error report. Codes are never auto-created from import data — org structure is configured, not imported. |
| Missing mandatory field (e.g., no mobile AND no email) | Row rejected: with no contact channel, credentials cannot be delivered. Error report says which channel is missing. |
| Credential SMS fails | Automatic email fallback; if both fail, student stays `ACTIVE` but flagged `delivery-failed` in the dashboard; office staff hand out credentials in person via a printed one-time slip (audited). Login still forces password change, so the slip is single-use in effect. |
| Roll-number collision (two students, same Program+year, same roll number) | Second row rejected. Resolution happens in the ERP (it owns roll numbers per the proposed default); re-import after fix. |
| Concurrent imports touching the same campus | Allowed; row-level upsert is transactional per ERP ID. Two batches racing on the same ERP ID: last committed write wins and both batch reports record the final state — no partial-field merges. |
| Re-allotment after attendance has been captured | Past Sessions/attendance stay with the old Section (memberships are dated, ONB-FR-10); only future obligations move. Never retro-rewritten. |
| Transfer while the student has open grievances or pending device change | Transfer proceeds; grievances and device requests follow the student (they attach to the account, not the org mapping). |
| Withdrawal reversed (student returns) | Reactivate the same account (never a new one); prior roll number restored if still unique, else the ERP issues a new one in the next import. |
| Student imported at Campus A appears in Campus B's file | Second file's row rejected with `scope-conflict` unless it is a System Admin-executed transfer; campus staff cannot silently poach records across campuses. |
| Worst case: malformed/oversized file (wrong encoding, 500 MB junk) | Pre-parse gate: size cap 50 MB, UTF-8 required, header row must match schema version — file rejected whole at this gate (this is the only whole-file rejection) with a clear reason. |
| Worst case: ERP sends a corrupted feed that would "update" thousands of records | Batch guardrail: if >20% of rows in a batch would change org mapping or DOB, the batch pauses in `NEEDS-REVIEW` and requires System Admin confirmation before committing. |

## 9. Non-Functional Requirements

- Import throughput: a 20,000-row CSV validates and commits in < 10 minutes; per-row validation feedback available progressively, not only at the end.
- Batch summary + error report available < 1 minute after batch completion.
- Credential delivery initiated < 15 minutes after activation; delivery-status visible on the dashboard within 5 minutes of gateway callback.
- Section/roster changes propagate to TTM/ATT reads < 5 minutes (shared membership-as-of-date API).
- Import runs must not degrade interactive traffic: background queue, throttled to keep API p95 < 500 ms during academic hours.
- All import files encrypted at rest, retained 90 days for dispute resolution, then purged (data minimization); batch summaries and audit records retained 7 years per AUTH.

## 10. Assumptions

- The ERP export includes: ERP ID, name, DOB, gender, mobile, email, campus/School/Department/Program codes, admission year, roll number. Section may be assigned in UniCore if absent from the file.
- All admitted students are 18 or older (university admission policy, locked 24-07-2026); DOB is never age-checked.
- The admission-time ERP notice covers the disclosure of student data to UniCore for provisioning; UniCore's own DPDP consent is captured at first login (AUTH doc).
- ERP is the system of record for identity fields and roll numbers; UniCore is the system of record for account state, Section membership, and device registration.
- The interpretation of "captured timetable update" as attendance corrections (context brief §per-module 3) does not affect this module; onboarding never edits attendance.

## 11. Open Questions

- ~~Roll-number source~~ — **resolved 27-07-2026: ERP-issued roll numbers are imported**; UniCore enforces uniqueness within Program + admission year and rejects collisions to the error report.
- ~~ERP API feed~~ — **resolved 27-07-2026: CSV upload only for MVP.** The API feed lands later as an adapter over the same validation pipeline.
- **ERP ID format** — resolved 27-07-2026 as an opaque non-empty string (≤100 chars); tighten per-School validation only if the ERP team confirms a stable pattern.
- Should Class In-charge receive a notification on re-allotment into/out of their Section? Proposed: yes, in-app notification, post-MVP email digest.

## 12. Flow Diagram

```mermaid
flowchart TD
  A[Staff uploads CSV / API feed arrives] --> B{Pre-parse gate: size, encoding, schema header}
  B -- Fail --> B1[Reject whole file · reason shown · audited]
  B -- Pass --> C[Row-level validation]
  C --> D{Row valid?}
  D -- No --> D1[Row → error report: row no, field, reason]
  D -- Yes --> E{ERP ID exists?}
  E -- Yes --> E1[Idempotent update · diff audited]
  E -- No --> E2[Create in IMPORTED state]
  E1 --> G
  E2 --> F2[Generate credentials]
  F2 --> H{SMS delivered?}
  H -- No --> H1{Email delivered?}
  H1 -- No --> H2[Flag delivery-failed · in-person slip flow]
  H -- Yes --> I[Student ACTIVE]
  H1 -- Yes --> I
  H2 --> I
  I --> J[First login → forced password change + device registration per AUTH]
  D1 --> G[Batch summary: created / updated / unchanged / rejected]
  G --> K{>20% risky changes?}
  K -- Yes --> K1[Batch NEEDS-REVIEW · System Admin confirms]
  K -- No --> L[Batch committed · audited]
```

## 13. Test Cases

| ID | Title / Scenario | Category | Priority | Preconditions | Steps | Expected Result | Covers |
|----|------------------|----------|----------|---------------|-------|-----------------|--------|
| TC-ONB-001 | Clean bulk import creates accounts | Happy | P0 | Valid 100-row CSV, new ERP IDs | Upload, run import | 100 accounts `IMPORTED`→`ACTIVE`, credentials sent, batch audited | ONB-FR-01/06, US-ONB-1 |
| TC-ONB-002 | Partial commit with error report | Happy | P0 | 100 rows, 5 invalid | Run import | 95 committed; error report lists 5 rows with field + reason | ONB-FR-03, US-ONB-1 |
| TC-ONB-003 | Re-import same file is idempotent | Happy | P0 | TC-ONB-001 completed | Upload identical file again | All rows `unchanged`; zero duplicates | ONB-FR-04, §8 |
| TC-ONB-004 | In-file duplicate ERP ID | Negative | P0 | File with same ERP ID twice | Run import | First row processed, second rejected as in-file duplicate | ONB-FR-05 |
| TC-ONB-005 | Invalid Program code rejected | Negative | P0 | Row with unknown Program code | Run import | Row in error report; no org unit auto-created | ONB-FR-02, §8 |
| TC-ONB-006 | Missing both contact channels | Negative | P1 | Row without mobile and email | Run import | Row rejected: no credential-delivery channel | ONB-FR-02, §8 |
| TC-ONB-007 | Allotment to a Section with no current-term instance rejected | Negative | P0 | Section "3B" exists for last term only; new term instance not yet created by Timetable Cell | Attempt allotment of a student to "3B" for the new term | Rejected with `section-not-created` pointing to the Timetable Cell; no membership written | ONB-FR-07, TTM-FR-19 |
| TC-ONB-008 | Allotment targets the per-term Section instance | Happy | P0 | Timetable Cell created new-term instance of "3B" | Allot the same student again | Membership written against the new term's instance; last term's "3B" roster unchanged | ONB-FR-07/10 |
| TC-ONB-009 | SMS fails, email fallback | Boundary | P1 | Student with dead mobile, valid email | Activate | Email credential delivered; dashboard shows fallback used | ONB-FR-06, §8 |
| TC-ONB-010 | Roll-number collision rejected | Boundary | P0 | Existing roll no. R-101 in Program+year | Import row with R-101, different ERP ID | Row rejected; existing record untouched | ONB-FR-08, §8 |
| TC-ONB-011 | Campus staff imports into other campus | Access | P0 | Admin scoped to Campus A | Upload file targeting Campus B | 403 / rows rejected `scope-conflict`; attempt audited | §4, §8 |
| TC-ONB-012 | Concurrent batches, same ERP ID | Concurrency | P1 | Two batches with one shared ERP ID | Run both simultaneously | Row-level transactionality; final state consistent, no field merge; both reports accurate | §8 |
| TC-ONB-013 | Re-allotment preserves attendance | Happy | P0 | Student in Section A with attendance | Re-allot to Section B effective today | Past attendance stays with A; future obligations in B; audit written | ONB-FR-10, US-ONB-4 |
| TC-ONB-014 | Withdrawal revokes access | Access | P0 | Active student with session | Mark withdrawn | Sessions revoked ≤ 60 s; removed from future Sessions; history retained | ONB-FR-12 |
| TC-ONB-015 | Corrupted mega-file rejected at gate | Negative | P1 | 500 MB non-UTF-8 file | Upload | Rejected whole at pre-parse gate with reason; nothing committed | §8 |
| TC-ONB-016 | 20k-row import within 10 min | NFR | P1 | Valid 20,000-row CSV | Run import during academic hours | Completes < 10 min; interactive API p95 stays < 500 ms | §9 |

Coverage: every §6 acceptance criterion, the §4 authorization matrix (TC-011/014), the per-term Section-instance dependency (TC-007/008), and all §8 decisions map to at least one test except the ERP corrupted-feed guardrail and grievance round-trip, which are covered in the integration test phase. Minor-consent tests were removed with the 18+ policy lock (00-overview.md §7).

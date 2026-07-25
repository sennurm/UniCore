# Requirement: Student Promotion

Module code: PRM · Status: DRAFT — pending approval · Last updated: 2026-07-21

## 1. Summary

Promotion moves students from their current semester/year to the next one at term end. UniCore does **not** decide promotions by hardcoded rules — it is a **per-School configurable workflow engine** operating over system-computed eligibility inputs: attendance % (per subject and aggregate, from the ATT module, computed on captured sessions only), results/credits **imported from the external exam system** (UniCore does not conduct exams), and backlog/carry-over (ATKT) counts. Each School configures, per Program, its eligibility criteria, workflow steps, approver chain, and exception paths (attendance condonation, carry-over promotion, detention). A term-end **promotion run** produces a promotion register; approvers work through the configured chain with fully audited override powers; final ratification commits the promotion and feeds onboarding, timetable, and attendance for the new term. Committed promotions are reversible only via a defined, dual-approved rollback flow.

## 2. Goals & Non-Goals

**Goals**
- Compute eligibility inputs automatically per student per term (attendance %, credits, backlog count).
- Let each School configure criteria thresholds, approver chains, and exception paths per Program — semester-based and year-based Programs both supported.
- Generate a promotion register (eligible / eligible-with-exceptions / not-eligible) per Program run.
- Route the register through the School's configured approver chain with audited overrides.
- Commit ratified promotions: advance students to the next semester/year and hand off to section allotment, timetable, and attendance for the new term.
- Support condonation (with document upload), carry-over (ATKT) promotion within configured backlog limits, and detention.
- Provide a controlled, audited rollback flow for committed promotions.

**Non-Goals**
- Conducting or valuing examinations — results/credits arrive from the external exam system.
- Automatic section re-allotment — a separate post-commit step by Admin/office staff (see §4).
- Degree award / graduation certification — final-term students take an exit path out of this module.
- Fee-clearance gating of promotion (out of scope with fees generally; see Open Questions).

## 3. Affected User Groups & Access

| Group | Access granted |
|---|---|
| Students | View own eligibility status, promotion outcome, and any exception applied to them; raise grievances via the AUTH grievance flow |
| Class In-charge | Review stage for their Section (where configured in the chain); annotate cases; initiate condonation requests with documents |
| Faculty Members (non-in-charge) | No access to promotion data (they see attendance/coverage in their own modules only) |
| HoD | Approve/override at their chain step for their Department's Programs; view Department promotion registers |
| School Dean | Configure the School's promotion workflows; ratify promotions; co-approve rollbacks |
| Registrar | Co-approve rollbacks; view all registers (read-only) |
| Admin/office staff | Trigger promotion runs (Program scope); perform post-commit section re-allotment; upload condonation documents on behalf of students |
| Exam Cell | Manage the results-import feed from the external exam system (read/import only; no promotion decisions) |

**Denied:** Students never see other students' eligibility data. No role outside the configured chain can approve, override, or ratify. Parents/guardians have no access (excluded system-wide).

## 4. Authorization & Business Rules

### Per-action authorization

| Action | Allowed | Scope check |
|---|---|---|
| Configure workflow (criteria, chain, exception paths) per Program | School Dean (own School); Super Admin (setup assistance) | School |
| Trigger promotion run for a Program | Admin/office staff, HoD | Program within own scope |
| View promotion register | Chain participants for their step scope; HoD (Department); Dean (School); Registrar (all, read-only) | Org unit |
| Review/annotate cases | Class In-charge (own Section), when that step is configured | Section |
| Approve/reject at a chain step | The role configured for that step only, in order | Step's org unit |
| Override a computed outcome (mandatory reason) | Any chain approver at their step; reason required, audited | Step's org unit |
| Approve condonation | The approver role configured for the condonation path (e.g., HoD or Dean) | Org unit |
| Ratify (final commit) | Final chain step — School Dean by default; School-configurable | School |
| Initiate rollback of a committed promotion | School Dean | School |
| Approve rollback (second approver) | Registrar | University |
| Perform section re-allotment post-commit | Admin/office staff | Program |
| Import results/credits feed | Exam Cell, System Admin (integration) | Campus/University |

All checks use the AUTH scope-aware permission API (see 01-authentication-authorization-security.md); nothing module-local.

### Business rules

1. **Eligibility inputs are computed, never hand-entered:** attendance % from ATT (captured Sessions only — uncaptured Periods do not count in the denominator); results/credits only via the exam-system import; backlog count derived from imported results. Manual edits to inputs are impossible in PRM — corrections happen in the source module (ATT corrections by Class In-charge) or source system (exam re-import).
2. **Default criteria per UGC:** 75% aggregate attendance threshold, applied per subject and aggregate as configured; the threshold, max-backlogs-for-promotion, and credit minimums are all School-configurable per Program and never hardcoded.
3. **Three-way register:** every student in the run lands in exactly one list — eligible, eligible-with-exceptions (condonation candidate or within ATKT limit), or not-eligible.
4. **Chain order is strict:** step N+1 opens only when step N completes for a case. A step approver may approve, reject to the previous step, or override the computed outcome with a mandatory reason. Every action is audited (who, what, when, before/after, reason).
5. **Ratification commits atomically per student:** student term advances (semester+1 or year+1), status becomes `promoted` / `promoted-with-backlogs` / `detained`, and the outcome is published to ONB/TTM/ATT for the new term. Section re-allotment is a **separate** post-commit step by Admin/office staff — students are promoted without a Section until allotment completes.
5a. **Ratification closes the term for the cohort:** when every student of a Section's cohort reaches a final state (promoted/detained/exited/transferred), PRM publishes a **term-closure event** for that Section. AUTH revokes all academic-term-bound grants on it (Class In-charge — AUTH-FR-13) and TTM archives the Section's timetable, ending all subject allocations at class level (TTM-FR-15). The new term starts clean: fresh Class In-charge designation by the HoD and a newly published timetable. Closure is per Section-cohort on each School's own schedule — semester Schools close twice a year, year-based Schools once.
6. **Detained students** repeat the term/year; their record carries the detention decision and reason; they re-enter next term's run for their repeated term.
7. **Condonation** requires: a category (medical/other), an uploaded supporting document, and approval by the configured condonation approver. Condoned attendance never rewrites ATT records — it is a PRM-level exception attached to the promotion decision.
8. **Rollback:** only Dean-initiated + Registrar-approved, only before the new term's attendance capture begins for the affected student, fully audited with reason. Rollback reverts term advancement and re-opens the case at the ratification step. If the rollback re-opens a Section whose term-closure event already fired, the closure is reversed in the same transaction: revoked term-bound grants are restored (AUTH-FR-14) and the Section's archived timetable is un-archived (TTM-FR-15).
9. **Workflow configuration is versioned;** a run binds to the configuration version active at run start and keeps it for the run's lifetime (mid-run config changes affect only future runs).
10. **Attendance freeze (locked 24-07-2026):** triggering a Program's promotion run publishes an **attendance-freeze event** for all of that Program's Sections' current-term Sessions (guaranteed delivery, outbox pattern). ATT enforces it (ATT-FR-11/12). Exemptions: (a) a correction attached to an open dispute/grievance commits for **non-ratified** students and recomputes the case per PRM-FR-12; (b) retro `counts-as-present` leave marking (ATT-FR-17) stays exempt until the student ratifies — after ratification the marking is skipped and surfaces here as post-ratification evidence (rollback/override only); (c) never-opened Periods resolve post-freeze by HoD-acknowledged write-off only. A run abandoned/discarded before any ratification lifts the freeze (audited).

### Audit

Every run trigger, computation snapshot, list assignment, approval, rejection, override, condonation, ratification, and rollback writes to the central append-only audit service (AUTH-FR-08) with before/after state and reason where mandated. Retention 7+ years (academic record).

## 5. Legal & Regulatory Requirements

- **Academic record retention:** promotion decisions, overrides, condonation artifacts, and rollback records are academic records — retained **7+ years minimum** (align with university statute if longer); exempt from DPDP erasure while the retention mandate applies (responses must cite the exemption, per AUTH §5).
- **DPDP correction rights:** students may contest the *inputs* (attendance, imported results) via the grievance flow; corrections flow through the source module/system and trigger recompute (§8). The *decision itself* changes only via the defined override/rollback flows — a grievance never directly edits a promotion outcome.
- **UGC/AICTE:** the 75% attendance default reflects UGC norms; the system must be able to demonstrate, per student, which criteria version and which computed inputs produced the outcome (explainability for regulator/ombudsman queries).
- **Purpose limitation:** eligibility data (attendance %, marks, medical condonation documents) is used only for promotion decisioning and visible only to the roles in §3. Medical documents are sensitive — access restricted to the condonation approver chain and encrypted at rest.
- **Notice:** students are notified of their register status and final outcome; adverse outcomes (not-eligible, detained) state the failing criteria and the grievance route.

## 6. User Stories & Acceptance Criteria

**US-PRM-1** — As a School Dean, I configure my School's promotion workflow per Program so that our academic regulations are enforced as-is.
- Given my Dean role for the School, when I set threshold 70%, max backlogs 4, and chain Class In-charge → HoD → Dean for Program X, then the next run for Program X uses exactly this configuration and the configuration change is audited.
- Given a Program in another School, when I attempt to configure it, then I get 403 and the attempt is audited.

**US-PRM-2** — As Admin/office staff, I trigger the term-end run for a Program so that the promotion register is generated.
- Given results are imported, when I trigger the run, then the attendance freeze fires for the Program (PRM-FR-17) and every enrolled student is computed and placed in exactly one of the three lists with their input values shown.
- Given results are missing for some students, when I trigger the run, then those students are held in a `blocked-awaiting-results` state and the run proceeds for the rest (partial run).

**US-PRM-3** — As an HoD, I work through my approval step so that borderline cases get human judgment.
- Given a student at 73% attendance with an approved medical condonation, when I approve, then the case moves to the next step carrying the exception.
- Given I override a not-eligible student to eligible, when I submit without a reason, then the override is rejected; with a reason, it is applied and audited with before/after state.

**US-PRM-4** — As a School Dean, I ratify the register so that promotions commit.
- Given all prior steps are complete, when I ratify, then students advance to the next semester/year, statuses publish to ONB/TTM/ATT, and students are notified — with Section unassigned until Admin re-allotment.

**US-PRM-5** — As a Student, I see my outcome and can contest inputs so that errors do not cost me a year.
- Given a not-eligible outcome from an attendance error, when my grievance leads to a Class In-charge correction in ATT, then my (non-ratified) case is recomputed and re-listed in the current run.

**US-PRM-6** — As a Registrar, I co-approve a rollback so that a wrong commit is reversible under control.
- Given a Dean-initiated rollback before new-term attendance capture began for the student, when I approve, then the promotion reverts, the case re-opens at ratification, and the full sequence is audited.
- Given the new term's attendance capture has begun for that student, when rollback is attempted, then it is refused with an explanatory error.

## 7. Functional Requirements

- PRM-FR-01: Per-Program workflow configuration by the School Dean: attendance threshold (default 75%, per subject and/or aggregate), max backlogs for promotion, credit minimums, chain steps + approver roles, exception paths (condonation, carry-over, detention). Versioned; runs bind to a version.
- PRM-FR-02: Results/credits import interface from the external exam system, with per-student import status visible (imported / missing / failed) and re-import supported. Managed by Exam Cell.
- PRM-FR-03: Promotion run per Program (semester- or year-based): compute attendance % (ATT, captured Sessions only), credits, and backlog count per student; snapshot inputs; assign each student to eligible / eligible-with-exceptions / not-eligible.
- PRM-FR-04: Partial runs — students with missing results or unresolved attendance grievances are held out (`blocked` / `excluded-flagged`) while the rest proceed; held students join via a later re-run.
- PRM-FR-05: Promotion register UI per run: three lists, per-student input values, criteria version, exception details, and case history.
- PRM-FR-06: Configurable approval chain execution: strict step order, approve / reject-to-previous / override-with-mandatory-reason, all audited.
- PRM-FR-07: Condonation sub-flow: request (Class In-charge or Admin on student's behalf), category, document upload, configured approver decision; approved condonation moves the case without altering ATT data.
- PRM-FR-08: Carry-over (ATKT) promotion: auto-flagged when backlogs ≤ configured max; status `promoted-with-backlogs`; backlog list carried on the student record for future terms.
- PRM-FR-09: Detention: `detained` status with reason; student repeats the term/year and re-enters the next run for it.
- PRM-FR-10: Ratification commit: atomic per student — term advancement, status, notifications, and event publication to ONB/TTM/ATT for the new term.
- PRM-FR-10a: Term-closure event per Section-cohort once all its students reach a final state: triggers AUTH revocation of term-bound grants (Class In-charge) and TTM archival of the Section's timetable/subject allocations; guaranteed delivery (outbox pattern); reversed atomically by an in-window rollback.
- PRM-FR-11: Post-commit section re-allotment as a separate Admin/office-staff step; promoted students are section-less until allotted; TTM/ATT for the new term activate per student only after allotment.
- PRM-FR-12: Recompute on input correction: any non-ratified case is recomputed when its ATT data or imported results change; ratified cases are never silently recomputed (rollback flow only).
- PRM-FR-13: Rollback flow: Dean initiates + Registrar approves; allowed only before the student's new-term attendance capture begins; reverts advancement, re-opens at ratification, fully audited.
- PRM-FR-14: Final-term handling: students completing their last semester/year are routed to a graduation/exit list, not a promotion list; exit list is exported for the university's degree process (out of scope beyond the export).
- PRM-FR-15: Approver-change continuity: when a chain role's holder changes (grant expiry/transfer), the successor inherits all pending approvals at that step with case history intact.
- PRM-FR-16: Student-facing outcome view: own status, failing criteria if adverse, exception applied, and grievance route; nothing about other students.
- PRM-FR-17: **Attendance freeze:** run trigger publishes the freeze event per business rule 10, scoped to the Program's Sections' current-term Sessions; consumed by ATT (correction window), LVE/ATT (retro-marking exemption boundary), and AUTH (edge-case reasoning). Freeze state is queryable per Program/term; lifting (run discard before any ratification) is Dean-approved and audited.

## 8. Edge Cases, Worst Cases & Decisions

| Case | Decision |
|---|---|
| Results arrive late from the exam system for some students | **DECISION:** run is blocked only for the affected students (`blocked-awaiting-results`); a partial run proceeds for the rest; blocked students are computed and merged into the same run on import. |
| Student has a pending grievance on attendance | **DECISION:** excluded from the run and visibly flagged until the grievance resolves; on resolution the student is computed and merged. Never promoted/detained on contested data. |
| Approver leaves / role changes mid-workflow | **DECISION:** successor to the role grant inherits all pending approvals at that step (PRM-FR-15); the handover itself is audited. No case is orphaned. |
| Data correction after the run but before ratification | **DECISION:** possible only via the dispute/grievance exemption of the freeze (business rule 10) or a results re-import; recompute non-ratified cases only (PRM-FR-12); a recompute that changes list assignment resets that case to the first chain step with a visible "recomputed" marker. |
| Routine (non-dispute) correction attempted after the run was triggered | **DECISION:** blocked by the attendance freeze (PRM-FR-17); ATT rejects it pointing to the dispute flow. The freeze is what keeps run snapshots and the register stable while approvers work. |
| Data correction discovered after ratification | **DECISION:** no silent recompute; the only paths are the rollback flow (if in window) or a next-term override — both audited. |
| Graduating final-term students | **DECISION:** exit path, not promotion — routed to the graduation/exit list (PRM-FR-14); never advanced to a non-existent term. |
| Student transfers mid-year (Program/campus) | **DECISION:** transferred-out students are excluded from the source run with status `transferred`; transferred-in students enter the destination Program's run with their imported attendance/results attached; unmappable inputs put them in the exceptions list for manual decision. |
| Backlogs exactly equal the configured max | **DECISION:** ≤ max qualifies for carry-over promotion (inclusive boundary); max+1 is not-eligible. Boundary stated in the configuration UI. |
| Attendance exactly at threshold (e.g., 75.00%) | **DECISION:** ≥ threshold passes (inclusive); computation uses two-decimal precision, round-half-up, and the stored snapshot shows the exact value. |
| Run triggered twice concurrently for one Program | **DECISION:** one active run per Program per term enforced by lock; the second trigger is rejected with a pointer to the active run. |
| A few students stay `blocked-awaiting-results` while the rest of the Section ratifies | **DECISION:** the term-closure event waits — it fires only when **every** student of the cohort reaches a final state, so the Class In-charge and timetable stay active for the stragglers. If blocking drags past the configured term-archival date, the AUTH backstop revokes anyway and the remaining cases are handled via the exception flow with HoD acting where the In-charge role has lapsed. |
| No Sessions captured at all for a subject (denominator zero) | **DECISION:** subject excluded from the attendance computation with a warning on the register (computed on captured sessions only, per ATT); an all-subjects-zero student lands in exceptions for manual decision. |
| Rollback requested after new-term attendance capture began | **DECISION:** refused (hard rule); remediation moves to manual academic-administration process outside UniCore, recorded as an audited note on the case. |
| Worst case: wrong criteria configured, discovered after ratification of a whole Program | **DECISION:** mass rollback uses the same Dean+Registrar flow applied per run (batch), still bounded by the attendance-capture window; students already past the window are handled case-by-case via next-term overrides. Configuration changes require a second Dean confirmation on save to reduce recurrence. |

## 9. Non-Functional Requirements

- Promotion run compute: ≤ 10 minutes for a 5,000-student Program cohort; ≤ 60 minutes for a full-School batch across Programs.
- Register load: < 3 s (p95) for a 1,000-student list with input values.
- Ratification commit: atomic per student; a mid-batch failure leaves every student either fully committed or fully uncommitted — no partial student state; batch resumable.
- Input snapshots: immutable once a run computes; stored with the run for the full 7+-year retention.
- Availability: promotion runs are schedulable off-peak; approval UI meets the 99.5% academic-hours baseline.
- Audit write on every decision action: guaranteed (outbox pattern per AUTH §9); a lost promotion audit record is a sev-2 incident.
- Notifications (outcome to students) delivered within 15 minutes of ratification.

## 10. Assumptions

- The external exam system exposes a machine-readable per-student results/credits feed with a stable student ID matching the ERP identity key (AUTH assumption).
- Attendance denominators use captured Sessions only, as locked in ATT; the university accepts that uncaptured Periods do not penalize students.
- Each School will designate exactly one final-ratification role (Dean by default) and one condonation approver per Program at configuration time.
- "New term's attendance capture begins" is measurable per student as the first captured Session of any subject in the student's new term.
- Section re-allotment procedures (merit-, alphabetical-, or balance-based) are the Admin's manual concern; PRM only enforces that it happens post-commit.

## 11. Open Questions

- Should fee clearance (tracked outside UniCore) gate ratification via an imported flag, or stay fully out of scope? Proposed: fully out of scope for MVP.
- Do any Schools require a Faculty-Division-level ratification step above the Dean (e.g., for professional-body-accredited Programs)? The chain engine supports it; needs confirmation per School.
- Retention beyond 7 years: does university statute mandate permanent retention of promotion registers? Proposed: permanent for the final outcome record, 7 years for working artifacts.

## 12. Flow Diagram

```mermaid
flowchart TD
  A[Admin triggers term-end run for Program] --> B{Results imported for all students?}
  B -- No --> B1[Hold missing students: blocked-awaiting-results]
  B1 --> C
  B -- Yes --> C{Pending attendance grievances?}
  C -- Yes --> C1[Exclude + flag those students until resolved]
  C1 --> D
  C -- No --> D[Compute per student: attendance % · credits · backlogs — snapshot inputs]
  D --> E{Final-term student?}
  E -- Yes --> E1[Graduation/exit list — not promotion]
  E -- No --> F{Meets configured criteria?}
  F -- Yes --> G[Eligible list]
  F -- "Backlogs ≤ max or condonation candidate" --> H[Eligible-with-exceptions list]
  F -- No --> I[Not-eligible list]
  H --> H1{Condonation requested?}
  H1 -- Yes --> H2{Approver accepts document + reason?}
  H2 -- No --> I
  H2 -- Yes --> J
  H1 -- "No (ATKT within max)" --> J[Configured approval chain: step 1 … step N]
  G --> J
  I --> J
  J --> K{Step decision}
  K -- "Reject" --> K1[Return to previous step / annotate]
  K1 --> J
  K -- "Override (mandatory reason, audited)" --> J
  K -- Approve --> L{All steps complete?}
  L -- No --> J
  L -- Yes --> M[Dean ratifies — commit: advance term · set status · notify · publish to ONB/TTM/ATT]
  M --> N[Admin performs section re-allotment (separate step)]
  M --> O{Rollback needed?}
  O -- "Yes, before new-term attendance capture" --> P{Dean initiates + Registrar approves?}
  P -- Yes --> Q[Revert advancement · re-open at ratification · audit]
  P -- No --> R[Rollback refused · audited]
  O -- "Yes, after capture began" --> R
```

## 13. Test Cases

| ID | Title / Scenario | Category | Priority | Preconditions | Steps | Expected Result | Covers |
|----|------------------|----------|----------|---------------|-------|-----------------|--------|
| TC-PRM-001 | Full run, all eligible, ratified | Happy | P0 | Config set; results imported; term closed | 1. Trigger run 2. Approve all steps 3. Ratify | Students advanced, status published, notified; sections unassigned | PRM-FR-03/06/10/11 |
| TC-PRM-002 | ATKT within max promoted with backlogs | Happy | P0 | Student with backlogs = max−1 | Run → chain → ratify | Status `promoted-with-backlogs`; backlog list carried | PRM-FR-08 |
| TC-PRM-003 | Condonation with medical document | Happy | P0 | Student at 71%, threshold 75% | 1. Request condonation + upload 2. Approver accepts 3. Chain → ratify | Promoted with exception recorded; ATT data unchanged | PRM-FR-07 |
| TC-PRM-004 | Backlogs exactly at configured max | Boundary | P0 | Backlogs = max | Run | Placed in eligible-with-exceptions (inclusive) | §8 boundary |
| TC-PRM-005 | Attendance exactly at threshold | Boundary | P0 | Aggregate = 75.00%, threshold 75% | Run | Eligible (inclusive ≥); snapshot shows 75.00 | §8 boundary |
| TC-PRM-006 | Override without reason rejected | Negative | P0 | HoD at their step | Submit override, empty reason | Rejected; with reason it succeeds and audits before/after | PRM-FR-06, US-PRM-3 |
| TC-PRM-007 | Missing results block only affected students | Negative | P0 | 3 of 60 students lack results | Trigger run | 57 computed; 3 in blocked-awaiting-results; later import merges them | PRM-FR-04, §8 |
| TC-PRM-008 | Pending attendance grievance excludes student | Legal | P0 | Open grievance on student's ATT data | Trigger run | Student excluded + flagged; merged after resolution and recompute | PRM-FR-04/12, §5 |
| TC-PRM-009 | Recompute resets non-ratified case | Happy | P1 | Case at step 2; ATT correction lands | Correction saved | Case recomputed, reset to step 1, "recomputed" marker shown | PRM-FR-12, §8 |
| TC-PRM-010 | Ratified case not silently recomputed | Negative | P0 | Case ratified; ATT correction lands | Correction saved | Ratified outcome unchanged; rollback/override paths only | PRM-FR-12/13 |
| TC-PRM-011 | Concurrent double run trigger | Concurrency | P1 | Run active for Program | Second trigger fired simultaneously | Second rejected, points to active run; single run persists | §8 lock |
| TC-PRM-012 | Chain step approval by wrong role | Access | P0 | Chain = In-charge → HoD → Dean; case at HoD step | Dean of another School / Faculty Member attempts approval | 403; attempt audited | §4 matrix |
| TC-PRM-013 | Dean configures another School's workflow | Access | P0 | Dean scoped to School A | Edit School B Program config | 403; attempt audited | §4 matrix, US-PRM-1 |
| TC-PRM-014 | Rollback inside window | Happy | P1 | Ratified student; no new-term Session captured | Dean initiates; Registrar approves | Reverted, re-opened at ratification, fully audited | PRM-FR-13, US-PRM-6 |
| TC-PRM-015 | Rollback after attendance capture began | Negative | P0 | One new-term Session captured for student | Dean initiates rollback | Refused with explanatory error; attempt audited | PRM-FR-13, §8 |
| TC-PRM-016 | Approver role handover mid-workflow | Concurrency | P1 | HoD grant transferred; 12 cases pending at HoD step | Successor opens queue | All 12 pending cases visible with history; handover audited | PRM-FR-15, §8 |
| TC-PRM-017 | Final-term student takes exit path | Boundary | P1 | Student in last semester of Program | Run | Routed to graduation/exit list, not advanced | PRM-FR-14, §8 |
| TC-PRM-018 | Erasure request on promotion record | Legal | P1 | Detained student files erasure grievance | Process request | Response cites academic-record retention exemption; request logged | §5 |
| TC-PRM-019 | 5,000-student run completes in time | NFR | P2 | Cohort of 5,000 with full inputs | Trigger run; measure | Compute ≤ 10 min; register loads < 3 s (p95) | §9 |
| TC-PRM-020 | Mid-batch commit failure leaves no partial student | NFR | P1 | Ratification batch; induced failure at student k | Ratify; inspect states | Students < k committed, ≥ k uncommitted; batch resumable; no half-advanced student | §9, PRM-FR-10 |
| TC-PRM-021 | Term-closure fires only when whole cohort is final | Boundary | P0 | 59 of 60 students ratified; 1 blocked-awaiting-results | 1. Inspect Section state 2. Resolve and ratify the last student | No closure event at step 1 (In-charge + timetable still active); event fires at step 2; AUTH revokes, TTM archives | PRM-FR-10a, §8 |
| TC-PRM-022 | In-window rollback reverses term closure | Happy | P1 | Closure fired; rollback approved before new-term capture | Rollback commits | Grants restored, timetable un-archived, case at ratification — one atomic transition, fully audited | PRM-FR-10a/13 |
| TC-PRM-023 | Run trigger fires the attendance freeze | Happy | P0 | Program with 3 Sections; no freeze active | Trigger the run; Class In-charge attempts a routine ATT correction; then a dispute-driven correction on a non-ratified student | Freeze event published for all 3 Sections; routine correction rejected by ATT; dispute-driven correction commits and the case recomputes with the "recomputed" marker | PRM-FR-17, business rule 10, ATT-FR-11 |

Coverage: every §6 acceptance criterion, the §4 authorization matrix (TC-012/013), all §8 decisions except the mass-rollback worst case (exercised operationally via TC-014's flow in batch mode — add an integration TC during implementation), DPDP/retention obligations (§5 via TC-008/018), the term-closure lifecycle (TC-021/022), the attendance-freeze lifecycle (TC-023 with TC-ATT-022/023), and the §9 compute/atomicity numbers map to at least one test.

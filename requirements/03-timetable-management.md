# Requirement: Timetable Management

Module code: TTM · Status: DRAFT — pending approval · Last updated: 2026-07-21

## 1. Summary

The central **Timetable Cell** (one per campus) builds per-Section timetables for each term — manually in MVP, with the system enforcing **clash detection at save time**: a Faculty Member cannot be in two places, a venue cannot host two sessions (except designated combined classes), and no student can carry two obligations in the same slot (via Section, batch, or elective-group membership). The module supports three non-trivial structures: **lab blocks** (multi-Period contiguous slots where a Section splits into batches, each batch with its own Faculty Member and venue), **elective slots** (students from many Sections converge into elective groups, all running simultaneously), and **combined classes** (multiple Sections, one venue, one Faculty Member). Timetables move through draft → approval → published; the **published** timetable is the source of truth for attendance Sessions (see 04-attendance-capture.md) — only its assigned (or substitute) Faculty Member can open a Session. Mid-term changes are versioned republishes; past Sessions are never rewritten. Constraint-based auto-generation is a documented future non-goal that this design must not preclude.

## 2. Goals & Non-Goals

**Goals**
- Term setup: per-School academic calendars (uploaded by School office staff, School Incharge-approved, versioned — start/end dates, exam-date ranges, special-event dates, term-archival backstop date), campus holiday calendars (System Admin), and per-campus/per-School period-grid definitions (Schools may have different period structures).
- **Section-instance creation:** the Timetable Cell creates each term's Section instances during term setup — Sections are per-term entities (Program × term × label); ONB allotment and draft authoring depend on them existing.
- Manual timetable construction per Section per term by the Timetable Cell, with save-time clash detection (hard blocks, not warnings).
- Lab blocks with student batches; elective slots with elective groups; combined classes.
- Draft vs published states with an HoD → Timetable Cell approval flow before publish.
- Versioned republish for mid-term edits, with change notifications to affected faculty/students; past Sessions unaffected.
- Substitute Faculty Member assignment per Period occurrence — temporary, audited, conferring session-open rights per the ATT doc.
- **Rebalancing suggestions:** when a Faculty Member is on approved leave or absent same-day, ranked substitute candidates per affected Period occurrence, confirmed by the HoD — nothing auto-assigns.
- **Class swaps:** two Faculty Members exchange specific Period occurrences by mutual consent (both-ways clash-checked); HoD notified, not a gate; occurrence-level only.
- Venue-capacity vs enrolled-headcount warnings (soft) alongside hard clash blocks.
- Elective-group enrollment managed by Department admin staff with capacity limits.

**Non-Goals**
- Constraint-based auto-generation (future phase). The data model must keep constraints (faculty availability, venue attributes, batch/group structures) as first-class data so a solver can be added without remodeling — but no solver ships in MVP.
- Decentralized timetable authoring by Departments (MVP is central Timetable Cell only).
- Room booking for non-teaching events (seminars, meetings) — out of scope.
- Student self-selection of electives (Open Question for post-MVP; MVP is admin-assigned).
- Attendance capture itself — see 04-attendance-capture.md; this module only feeds it the published schedule.

## 3. Affected User Groups & Access

| Group | Access granted |
|---|---|
| Timetable Cell | Full authoring: term setup, grids, drafts, publish, republish, substitutes — scoped to their campus |
| HoD | Approve/reject drafts covering their Department's Sections/Faculty Members; request mid-term changes; assign substitutes for their Department |
| Department admin staff | Elective-group creation and student-to-group assignment within their Department, within capacity |
| Faculty Members | Read own published timetable (all versions); notified on changes and substitute assignments |
| Students | Read own published timetable (Section + batch + elective view merged); notified on changes |
| System Admin | Venue master data, campus holiday calendars |
| School office staff (Admin/office staff of a School) | Upload the School's per-term academic calendar (start/end, exam dates, special events) for School Incharge approval |
| School Incharge | Approve the School academic calendar and its amendments (recorded, versioned) |
| Executives (Principal / Faculty Dean / School Incharge) | Read-only dashboards of published timetables in their scope |

**Denied:** faculty and students never see drafts. Nobody outside the Timetable Cell (plus System Admin for master data) can mutate a timetable; HoDs approve and request, they do not edit slots directly.

## 4. Authorization & Business Rules

### Per-action authorization

| Action | Allowed | Enforced at |
|---|---|---|
| Define campus holiday calendar, venue master | System Admin (campus scope) | API + service layer |
| Upload School academic calendar (term dates, exam dates, special events, archival backstop) | School office staff (own School); becomes active only on School Incharge approval | API + service layer |
| Approve School academic calendar / amendment | School Incharge (own School); approval recorded, versions kept | API (workflow) |
| Create Section instances for a term (single or bulk CSV) | Timetable Cell (own campus), per Program per term; deactivate-only once students are allotted or a timetable is published | API + service layer |
| Define period grid per School | Timetable Cell (own campus), after School sign-off recorded | API + service layer |
| Create/edit draft timetable | Timetable Cell (own campus) | API + service layer |
| Approve/reject draft | HoD for their Department's portion; publish requires all covered HoDs approved | API (workflow engine) |
| Publish / versioned republish | Timetable Cell (own campus), only from fully approved draft | API + service layer |
| Assign substitute for a Period occurrence | HoD (own Department) or Timetable Cell (own campus) | API |
| Confirm a rebalancing suggestion | HoD (own Department); Timetable Cell as fallback | API |
| Propose / accept / cancel a class swap | The two Faculty Members assigned to the occurrences being exchanged (own Periods only); HoD(s) auto-notified | API + service layer (both-ways clash check) |
| Create elective groups, set capacity | Department admin staff (own Department) | API |
| Assign/remove student in elective group | Department admin staff (own Department), within capacity | API |
| View published timetable | Any authenticated user within org scope (students/faculty: own; HoD/exec: their units) | API |
| View draft | Timetable Cell + approving HoDs only | API |

### Business rules

1. **Clash detection is a hard save-time block** for: (a) same Faculty Member in two Periods overlapping in time; (b) same venue double-booked, unless both entries belong to one declared combined class; (c) any student with two obligations in one slot, resolved through the union of their Section, batch, and elective-group memberships (membership as of date, per ONB-FR-10).
2. Combined classes are **declared explicitly** (one teaching entry linked to multiple Sections) — never inferred from coincidental identical bookings.
3. A lab block is one contiguous multi-Period entry; its batches must partition the Section (every student in exactly one batch); each batch has its own Faculty Member + venue, all checked for clashes individually.
4. An elective slot pins one time slot across participating Sections; each elective group has its own Faculty Member + venue; a student belongs to at most one group per elective slot; students not enrolled in any group for that slot are flagged before publish (block — see §8).
5. Publish requires: all HoDs whose Departments' Sections or Faculty Members appear have approved; zero hard clashes; zero unassigned-elective students. Capacity warnings do not block (see rule 7).
6. **Republish is versioned:** version N+1 supersedes N from its effective date; Sessions already delivered (or in progress) under N are immutable; change notifications go to every affected Faculty Member and student listing exactly which Periods changed.
7. Venue capacity < enrolled headcount raises a **warning** requiring Timetable Cell explicit acknowledgment (recorded) — not a block, because real-world constraints (renovations, temporary rooms) need overrides; the acknowledgment is audited.
8. Substitute assignment: per Period occurrence (a date) or a date range; the original assignment is retained; the substitute gains session-open rights for exactly those occurrences (ATT doc); substitution never edits the published timetable version.
9. Attendance integration: the ATT module resolves "who may open this Session" strictly from the current published version + active substitutions + committed swaps; drafts have no runtime effect.
9a. **Rebalancing suggestions:** triggered by (a) LVE approval of a Faculty Member's leave (proactive — before the leave starts) and (b) same-day absence (an ATT never-opened flag, or the HoD marking the member absent). For each affected Period occurrence the system proposes ranked candidates: clash-free in that slot (hard filter), then ranked by same-subject familiarity (teaches the same subject this term), same Department, and lowest incremental load. The HoD confirms a candidate — which creates a standard substitution per rule 8 — or rejects all and acts manually (own substitution or Session cancellation per ATT). **Nothing is ever auto-assigned.** Same-day triggers mark the suggestion urgent.
9b. **Class swaps:** Faculty Member A proposes exchanging specific Period occurrences with Faculty Member B (each must be the assigned/substitute/swapped-in teacher of the occurrences offered). Both directions are clash-checked at proposal AND at acceptance (state may have changed between). On B's acceptance the swap **commits immediately** — mutual occurrence-level reassignment, session-open rights follow (ATT), and the HoD(s) of both members are notified (informed, not a gate) with the swap details. Audited end to end. Either party may cancel a committed swap before the first swapped occurrence (counterpart + HoDs notified); after that, changes go through substitution. A swapped-in occurrence cannot be re-swapped — one hop only; the HoD can still substitute over it.
10. **Subject allocation is term-bound.** A Faculty Member's allocation to a subject in a Section exists only inside that term's published timetable and confers no rights beyond the term. When PRM ratification closes a Section's cohort (see 06-student-promotion.md), the Section's timetable for that term is **archived**: no new Sessions can be opened from it, substitutions on it are rejected, and it remains readable as historical record only. The new term's teaching rights exist only once the new term's timetable is published — nothing carries over automatically, even for the same Faculty Member teaching the same subject. A PRM rollback (within its window) un-archives the affected Section's timetable together with the AUTH grant restore.

### Audit

Every publish/republish (version, diff, approver chain), clash-override attempt (there are none — blocks are absolute; the attempt itself is logged), capacity acknowledgment, substitute assignment, and elective-group change writes to the central audit service with actor, timestamp, before/after, and reason where mandated.

## 5. Legal & Regulatory Requirements

- **UGC/AICTE:** published timetables underpin minimum-attendance computation (75%-type norms, School-configurable per the PRM doc); therefore Period → Session traceability must be lossless across republishes — every delivered Session records the timetable version it was scheduled under.
- **DPDP purpose limitation:** timetable data includes personal data (faculty assignments, student group memberships); visibility is org-scoped per §3 — no public timetable exports carrying student lists. Faculty workload views expose only what the viewer's scope permits.
- **DPDP correction rights:** a Faculty Member disputing their assignment (e.g., wrongly rostered) raises it via the HoD/Timetable Cell change flow; the AUTH grievance mechanism covers profile data, not scheduling disputes — this doc's change flow is the remedy and must respond within a defined SLA (§9).
- Localization: all Periods stored and displayed in IST; DD-MM-YYYY; the week structure (working Saturdays etc.) comes from the campus calendar.

## 6. User Stories & Acceptance Criteria

**US-TTM-1** — As a Timetable Cell member, I build Section 3B's draft so that the term schedule exists.
- Given the period grid and term are configured, when I place a Period where the Faculty Member is already teaching elsewhere, then the save is rejected with the exact conflicting entry named.
- Given a clash-free draft, when I submit for approval, then all covered HoDs see it in their approval queue.

**US-TTM-2** — As a Timetable Cell member, I schedule a 3-Period lab block with two batches so that half the Section does Physics lab while the other half does Chemistry lab.
- Given Section 3B is partitioned into batches B1 and B2, when I save the block with distinct Faculty Members and venues per batch, then the system verifies contiguity, the batch partition, and clash checks per batch.
- Given a student is left out of both batches, when I submit for approval, then submission is blocked naming the unassigned students.

**US-TTM-3** — As a Department admin staff member, I assign students to elective groups so that the elective slot is deliverable.
- Given group "German-A" has capacity 40 with 40 assigned, when I add a 41st student, then the assignment is rejected with a capacity message.
- Given a student already in "German-A", when I try adding them to "Music-A" in the same elective slot, then the assignment is rejected (one group per slot).

**US-TTM-4** — As an HoD, I approve the draft portion covering my Department so that publish can proceed.
- Given a pending draft, when I reject with a reason, then the Timetable Cell sees the reason, the draft returns to editing, and prior approvals from other HoDs are preserved unless their portion changes (see §8).

**US-TTM-5** — As a Faculty Member, I see my published timetable and get notified of changes so that I show up at the right venue.
- Given a republish moves my Tuesday Period, when version N+1 publishes, then I receive a notification listing the change, and my calendar shows N+1 from its effective date while past days still reflect N.

**US-TTM-6** — As an HoD, I assign a substitute for Dr. Rao's Thursday Period so that the class runs during his leave.
- Given the substitute is free that slot, when I assign for 2 dates, then the substitute can open those Sessions (ATT doc), the original assignment remains in the published version, and the substitution is audited.
- Given the proposed substitute already teaches in that slot, when I assign, then the assignment is rejected as a clash.

## 7. Functional Requirements

- TTM-FR-01: Term setup: campus holiday/working-day calendar (System Admin, per campus) + **School academic calendar** per term per TTM-FR-18; term dates are per School, not per campus (one campus hosts semester- and year-based Schools simultaneously).
- TTM-FR-02: Period-grid definition per campus/School: named Periods with start/end times; different Schools may run different grids; grid changes mid-term require versioned republish of affected timetables.
- TTM-FR-03: Draft timetable authoring per Section per term: assign course, Faculty Member, venue to each Period.
- TTM-FR-04: Save-time hard clash detection across Faculty Member, venue, and student obligations (Section/batch/elective-group union, membership-as-of-date), spanning Schools and grids by absolute time overlap — not by Period index.
- TTM-FR-05: Lab blocks: contiguous multi-Period entries; batch definitions partitioning a Section; per-batch Faculty Member + venue; per-batch clash checks.
- TTM-FR-06: Elective slots: cross-Section slot pinning; elective groups with own Faculty Member + venue + capacity; one group per student per slot; unassigned-student pre-publish block.
- TTM-FR-07: Combined classes: one teaching entry explicitly linked to multiple Sections; venue clash check exempts only the declared combination; headcount = sum of Sections for capacity warning.
- TTM-FR-08: Approval workflow: draft → per-HoD approval (each HoD approves their Department's portion) → publish eligibility; rejection returns to draft with reason.
- TTM-FR-09: Publish: atomic version creation; visible to faculty/students; becomes the ATT source of truth; publish blocked while hard clashes, missing approvals, or unassigned elective students exist.
- TTM-FR-10: Versioned republish with effective date, computed diff, and notifications to affected users; delivered/in-progress Sessions immutable; each Session records its scheduling version.
- TTM-FR-11: Substitute assignment per occurrence or date range; clash-checked; audited; grants session-open rights per ATT doc; auto-expires at range end.
- TTM-FR-12: Venue-capacity warnings with mandatory recorded acknowledgment to proceed.
- TTM-FR-13: Personal timetable views: student (Section + batch + elective merged), Faculty Member (own load incl. substitutions), HoD/exec (scope dashboards).
- TTM-FR-14: Constraint data (faculty availability notes, venue attributes, group structures) stored as structured first-class data so a future auto-generation solver can consume it (non-goal to build the solver).
- TTM-FR-15: Term-bound archival: on the PRM term-closure event for a Section's cohort, archive that Section's timetable — reject Session opens (ATT) and new substitutions/swaps against it, keep read access for history/audit; un-archive on PRM rollback within the rollback window.
- TTM-FR-16: Rebalancing suggestions per §4 rule 9a: triggered by LVE leave approval and same-day absence; ranked clash-free candidates (subject familiarity, Department, lowest load); HoD confirmation creates a substitution; urgent flag for same-day; suggestion, confirmation, and rejection all audited.
- TTM-FR-17: Class swaps per §4 rule 9b: propose → both-ways clash check → accept (re-check) → immediate commit; occurrence-level only; one-hop (no re-swap); HoD notification; cancellation before first swapped occurrence; session-open rights and ATT/SYL attribution follow the swap.
- TTM-FR-18: **School academic calendar:** per School per term — start/end dates, exam-date ranges, special-event dates, and the term-archival backstop date (consumed by AUTH-FR-13). Uploaded by School office staff, active only on recorded School Incharge approval; amendments create a new version requiring re-approval; all versions retained and audited. Consumers: TTM scheduling warnings (below), TSK exam-duty conflict signal (TSK-FR-17), LVE On-Duty overlap display, AUTH archival backstop. Exam/special-event dates are **soft signals** in MVP: placing a regular Period inside an exam-date range raises a warning requiring recorded Timetable Cell acknowledgment — never a hard block.
- TTM-FR-19: **Section-instance creation:** during term setup the Timetable Cell creates the term's Section instances per Program (label reusable across terms; each instance a distinct org unit for scoping, singleton Class In-charge, and term-closure). Instances with allotted students or a published timetable cannot be deleted — deactivate only. ONB allotment (ONB-FR-07) and draft authoring (TTM-FR-03) reject Sections with no instance for the target term.
- TTM-FR-21: **Bulk Section creation** — Section instances may be created one at a time or from a CSV template (one row per Section: Programme path + label), for the term selected at upload time; the term must already be approved for the owning School, and per-row failures are reported without failing the batch. The template is downloadable in-app per the overview's bulk-upload baseline.
- TTM-FR-20: **Faculty Dean timetable inputs** (locked 25-07-2026, reconciling the access matrix's "upload Time Table" rows): Faculty Dean offices submit structured timetable inputs (course offerings, teaching assignments, constraints) per School/term to the central Timetable Cell — a tracked submission, not direct authoring. The Timetable Cell remains the sole author (locked decision #8, 00-overview.md); submissions are versioned, acknowledged, and audited, and the draft records which submission(s) it drew from.

## 8. Edge Cases, Worst Cases & Decisions

| Case | Decision |
|---|---|
| Two Timetable Cell members edit the same Section draft concurrently | Optimistic locking per Section-draft: second save on a stale version is rejected with a refresh prompt. No silent merge. |
| Clash created indirectly (re-allotted student now double-booked via elective group) | Membership changes (ONB) trigger a re-validation job; conflicts surface in a Timetable Cell "new conflicts" queue. Published timetable stands; Department admin must move the student's group (or Section fix via ONB) within the SLA — the student is never left with two simultaneous obligations silently. |
| Combined class where one Section's HoD approved, the other rejected | Publish blocked until every covered HoD approves. Rejection reason routes to the Timetable Cell; the combined entry is edited or split. |
| HoD approval granted, then that HoD's portion is edited | That HoD's approval auto-invalidates (portion-scoped approval hash); other HoDs' approvals stand. Re-approval required only where content changed. |
| Republish while a Session under version N is in progress | The in-progress Session completes under N; N+1 applies from its effective date, which cannot be earlier than "tomorrow" (next calendar day) — same-day republish is rejected to avoid mid-day ambiguity. Same-day emergencies use substitution or Session cancellation (ATT doc), not republish. |
| Substitute needed but every eligible Faculty Member clashes | System offers no override — a clash block is absolute. HoD either reschedules via republish or cancels the occurrence (ATT doc). Recorded decision: integrity of clash rules outranks convenience. |
| Rebalancing suggestion list is empty (no clash-free candidate) | The HoD is told explicitly ("no eligible substitute") with the option to cancel the occurrence (ATT) or reschedule via republish; the empty result is recorded — silence is never the outcome. |
| Confirmed substitute then goes on leave themselves | Their leave approval re-triggers rebalancing for the occurrences they had picked up — the suggestion flow is re-entrant. |
| Swap accepted but a republish moves one of the swapped occurrences | The swap is auto-voided for the affected occurrences only; both parties and HoD(s) notified; unaffected occurrences of the swap stand. |
| Both parties accept a swap simultaneously with a conflicting change (e.g., substitution landing on the same occurrence) | Acceptance re-runs the clash check transactionally; the first commit wins, the second actor gets a stale-state error naming the conflict. |
| Cross-Department swap (A and B report to different HoDs) | Allowed — both HoDs are notified; each sees the swap in their Department view. |
| Faculty Member leaves service mid-term | Deactivation (AUTH) triggers the orphan check: all their future Periods appear in the Timetable Cell conflicts queue; substitutes cover the gap until a republish reassigns permanently. Past Sessions retain the departed member as historical fact. |
| Venue becomes unavailable mid-term (flooding, renovation) | Venue marked inactive from a date by System Admin; all future Periods there enter the conflicts queue; republish relocates them. Capacity warnings recalculated for the new venues. |
| Period grid change mid-term (School shifts timings) | Allowed only with a new grid version effective a future date + forced republish of every affected Section; absolute-time clash checks (TTM-FR-04) handle the transition week correctly. |
| Elective group under-enrollment (3 students in a group) | No system minimum — running a small group is an academic decision. The pre-publish report lists group sizes so the Department decides; system enforces only the capacity maximum. |
| Student in two Sections' worth of obligations due to cross-Program elective spanning different Schools' grids | Clash check compares absolute time ranges, not Period indexes, so cross-grid overlaps are caught (TTM-FR-04). |
| Worst case: publish attempted with 0 clashes but notification fanout fails | Publish commits (schedule truth first); notifications retry with backoff; undelivered notifications after 3 retries alert the Timetable Cell for manual comms. Publish is never rolled back for notification failure. |
| Worst case: accidental publish of a wrong draft | No unpublish (faculty/students may have seen it). Remedy: immediate republish of the corrected version effective next day + notifications. Same-day damage handled via substitutions/cancellations. Audit trail shows both versions. |
| Faculty Member tries to open a Session from last term's timetable after their Section's cohort was promoted | Rejected — the timetable is archived per business rule 10/TTM-FR-15; the error names the archival cause. The same Faculty Member teaching the same subject next term acts only under the new term's published timetable. |
| Schools promote on different dates (semester Schools ratify while year Schools are mid-term) | Archival is per Section-cohort, driven by each School's own PRM ratification — never a campus-wide cutoff. Mixed-state campuses are the normal case, not an exception. |
| Regular Period scheduled inside a declared exam-date range | Soft warning naming the exam range; Timetable Cell acknowledges to proceed (recorded, audited). Never a hard block — other cohorts legitimately hold classes during a School's exam window. |
| School academic calendar amended mid-term (exam dates move) | New calendar version requires School Incharge re-approval; existing acknowledgments stay attached to the version they were made against; new/edited Periods validate against the newest approved version. |

## 9. Non-Functional Requirements

- Clash detection at save: < 2 s (p95) for a single entry against a full campus term (~500 Sections, ~40 Periods/week each).
- Full-draft validation (pre-publish check of one Section): < 10 s; whole-campus re-validation job after membership changes: < 15 min, run off-peak plus on-demand.
- Publish (version creation + visibility flip): < 30 s; notifications fan out to all affected users < 10 min after publish.
- Timetable read (personal view): < 500 ms (p95); read availability 99.5% during academic hours (ATT depends on it at every Session open).
- Change-request SLA (faculty disputes per §5): Timetable Cell response within 2 working days, tracked in-app.
- Version history retained for the statutory academic-record period (aligned with AUTH 7-year audit retention); every Session permanently linked to its scheduling version.

## 10. Assumptions

- Venue master data (rooms, labs, capacities, campus) is maintainable by System Admin before timetable authoring starts; venues belong to exactly one campus.
- Course/subject catalog (what is taught in which Program term) arrives from ERP or academic setup outside this module; TTM references courses, does not define them.
- Section, batch, and elective-group membership is served by the ONB membership-as-of-date API (ONB-FR-10); TTM owns Section-*instance* creation (TTM-FR-19) but not student membership, except elective-group assignment.
- One Timetable Cell per campus; cross-campus teaching (one Faculty Member at two campuses) is rare but real — clash checks therefore run on the Faculty Member globally, not per campus.
- Substitution semantics for attendance (session-open rights) are specified in 04-attendance-capture.md; TTM only records the assignment.

## 11. Open Questions

- **Student self-selection of electives (post-MVP):** should students pick elective groups themselves with first-come-first-served capacity? Proposed: yes post-MVP, with a selection window and waitlist; MVP stays Department-admin-assigned as locked.
- Should HoD approval be delegable (e.g., to a Department timetable coordinator) via a scoped role grant? Proposed: yes, using the standard AUTH time-bound grant mechanism — no new workflow needed.
- Minimum notice for republish effective dates (next-day per §8, or configurable 48 h per School)? Proposed default: next calendar day, School-configurable later.

## 12. Flow Diagram

```mermaid
flowchart TD
  A[Timetable Cell: term + period grid configured] --> B[Author Section draft: assign course, Faculty Member, venue per Period]
  B --> C{Save: hard clash check — faculty / venue / student obligations}
  C -- Clash --> C1[Save rejected · conflicting entry named · attempt logged]
  C1 --> B
  C -- Clean --> D{Capacity warning?}
  D -- Yes --> D1[Timetable Cell acknowledges · recorded]
  D -- No --> E
  D1 --> E[Draft complete → submit for approval]
  E --> F{Unassigned elective students?}
  F -- Yes --> F1[Submission blocked · students listed → Dept admin assigns groups]
  F1 --> E
  F -- No --> G{All covered HoDs approve?}
  G -- Reject --> G1[Reason to Timetable Cell · back to draft · unchanged portions keep approvals]
  G1 --> B
  G -- Approve --> H[Publish version N · visible to faculty + students · ATT source of truth]
  H --> I{Mid-term change needed?}
  I -- Same-day gap --> I1[Substitute assignment or Session cancellation per ATT · audited]
  I -- Structural change --> J[Edit → re-approve changed portions → republish N+1, effective ≥ next day]
  J --> K[Diff notifications to affected users · past Sessions stay on N]
  I1 --> H
  K --> H
```

## 13. Test Cases

| ID | Title / Scenario | Category | Priority | Preconditions | Steps | Expected Result | Covers |
|----|------------------|----------|----------|---------------|-------|-----------------|--------|
| TC-TTM-001 | Author and publish clean timetable | Happy | P0 | Term + grid configured; HoDs available | Build clash-free draft, get approvals, publish | Version 1 visible to faculty/students; ATT reads it | TTM-FR-03/08/09, US-TTM-1 |
| TC-TTM-002 | Faculty double-booking blocked | Negative | P0 | Dr. X teaches Mon P2 in Sec A | Place Dr. X Mon P2 in Sec B draft | Save rejected naming Sec A entry | TTM-FR-04, US-TTM-1 |
| TC-TTM-003 | Venue clash blocked except combined class | Negative | P0 | Room 101 booked Mon P3 | 1. Book Room 101 Mon P3 for another Section 2. Repeat as declared combined class | Step 1 rejected; step 2 saves | TTM-FR-04/07 |
| TC-TTM-004 | Lab block batch partition enforced | Boundary | P0 | Section of 60; batches of 30+29 | Submit draft for approval | Blocked: 1 unassigned student named | TTM-FR-05, US-TTM-2 |
| TC-TTM-005 | Lab batches clash-checked individually | Negative | P0 | Batch B1 faculty busy that slot | Save lab block | Rejected on B1's faculty clash | TTM-FR-05 |
| TC-TTM-006 | Elective group capacity limit | Boundary | P0 | Group capacity 40, 40 assigned | Assign 41st student | Rejected with capacity message | TTM-FR-06, US-TTM-3 |
| TC-TTM-007 | One elective group per student per slot | Negative | P0 | Student in German-A | Assign same student to Music-A (same slot) | Rejected | TTM-FR-06, US-TTM-3 |
| TC-TTM-008 | Publish blocked: unassigned elective student | Negative | P0 | 1 student in no group for the slot | Submit/publish | Blocked; student listed | TTM-FR-06/09 |
| TC-TTM-009 | Partial HoD rejection returns to draft | Happy | P1 | 2 HoDs; one rejects with reason | Attempt publish | Blocked; reason visible; other approval preserved until portion edited | TTM-FR-08, US-TTM-4, §8 |
| TC-TTM-010 | Republish notifies and preserves past Sessions | Happy | P0 | Version 1 published, Sessions delivered | Republish v2 effective tomorrow moving a Period | Diff notifications sent; past Sessions still on v1; v2 live from effective date | TTM-FR-10, US-TTM-5 |
| TC-TTM-011 | Same-day republish rejected | Boundary | P1 | Version 1 live | Attempt republish effective today | Rejected; guidance points to substitution/cancellation | §8 |
| TC-TTM-012 | Substitute gains session-open rights | Access | P0 | Substitute assigned for 2 dates | Substitute opens Session on those dates; original faculty also tries | Substitute allowed per ATT; assignment audited; auto-expires after range | TTM-FR-11, US-TTM-6 |
| TC-TTM-013 | Substitute with clash rejected | Negative | P0 | Proposed substitute teaches in same slot | Assign substitute | Rejected as clash | TTM-FR-11, US-TTM-6 |
| TC-TTM-014 | Concurrent draft edits | Concurrency | P1 | Two Cell members open same Section draft | Both save changes | Second save rejected as stale; refresh prompted | §8 |
| TC-TTM-015 | Draft invisible to faculty/students | Access | P0 | Draft exists, unpublished | Faculty Member/student request timetable | Only published versions returned; draft 403/absent | §3, TTM-FR-09 |
| TC-TTM-016 | Cross-grid clash caught by absolute time | Boundary | P1 | Two Schools with different grids; elective spans both | Create overlap by wall-clock, different Period indexes | Rejected: absolute-time overlap detected | TTM-FR-04, §8 |
| TC-TTM-017 | Session records scheduling version | Legal | P1 | v1 then v2 published | Inspect Sessions delivered under each | Each Session carries its version; attendance traceability intact | §5, TTM-FR-10 |
| TC-TTM-018 | Clash check performance | NFR | P2 | Full campus term loaded (~500 Sections) | Save one entry | Check completes < 2 s (p95) | §9 |
| TC-TTM-019 | Archived timetable rejects Session open and substitution | Access | P0 | Section cohort ratified in PRM (term-closure event received) | 1. Assigned Faculty Member attempts Session open 2. HoD attempts substitution on the archived timetable | Both rejected naming archival; timetable still readable | TTM-FR-15, §4 rule 10 |
| TC-TTM-020 | Rollback un-archives Section timetable | Happy | P1 | Archived timetable; PRM rollback approved in window | Rollback commits; Faculty Member opens a Session | Timetable active again; Session opens; both transitions audited | TTM-FR-15 |
| TC-TTM-021 | Leave approval triggers ranked rebalancing suggestions | Happy | P0 | Faculty leave approved (LVE) covering 4 Periods; eligible candidates exist | Open HoD suggestion queue | 4 suggestion sets, candidates clash-free, ranked (subject, Department, load); HoD confirms → substitutions created and audited | TTM-FR-16, §4 rule 9a |
| TC-TTM-022 | Same-day absence produces urgent suggestion | Happy | P0 | Period flagged never-opened (ATT) | Inspect HoD queue | Urgent-flagged suggestion for the occurrence; confirmation creates substitution | TTM-FR-16 |
| TC-TTM-023 | Empty candidate list surfaces explicitly | Negative | P1 | All Department faculty clash in the slot | Trigger rebalancing | "No eligible substitute" shown with cancel/reschedule options; empty result recorded | §8 |
| TC-TTM-024 | Class swap: propose, accept, teach, cancel path | Happy | P0 | A and B assigned clash-free swappable occurrences | A proposes; B accepts; B opens A's Session on swap date; A cancels a future occurrence pair | Swap commits on acceptance; HoDs notified; session-open rights follow; cancellation before first occurrence reverts with notifications; all audited | TTM-FR-17, §4 rule 9b |
| TC-TTM-025 | Swap clash re-check at acceptance | Concurrency | P0 | Clash created between proposal and acceptance (substitution landed) | B accepts | Acceptance rejected with stale-state error naming the conflict; no partial swap | TTM-FR-17, §8 |
| TC-TTM-026 | Swapped-in occurrence cannot be re-swapped | Negative | P1 | Committed swap gives B occurrence X | B proposes swapping X onward to C | Rejected: one-hop rule; HoD substitution over X still possible | §4 rule 9b |
| TC-TTM-027 | School calendar upload, approval, amendment versioning | Happy | P0 | School office staff account; School Incharge available | 1. Upload term calendar (dates, exam ranges, events) 2. School Incharge approves 3. Amend exam range; School Incharge re-approves | Calendar active only after approval; amendment creates v2 requiring re-approval; all versions retained and audited | TTM-FR-18 |
| TC-TTM-028 | Period on an exam date raises soft warning | Boundary | P1 | Approved calendar with exam range 10-12-2026→20-12-2026 | Place a regular Period on 15-12-2026 | Warning names the exam range; save proceeds only after recorded acknowledgment; never blocks | TTM-FR-18, §8 |
| TC-TTM-029 | Draft authoring blocked without Section instance | Negative | P0 | New term; no Section instances created yet | Attempt to author a draft for "3B" | Rejected with `section-not-created`; after Timetable Cell creates the instance, authoring proceeds | TTM-FR-19 |
| TC-TTM-030 | Bulk Section import for an approved term | Happy | P1 | Approved term; CSV of Programme paths + labels | Upload for that term | Sections created per row; a row naming an unknown Programme is reported without failing the batch; unapproved term rejects the whole upload | TTM-FR-21 |

Coverage addition: rebalancing triggers/ranking/empty-list (TC-021/022/023), the swap lifecycle incl. concurrency and one-hop (TC-024/025/026) map to TTM-FR-16/17; School calendar lifecycle and exam-date warnings (TC-027/028) map to TTM-FR-18; Section-instance dependency (TC-029, with TC-ONB-007/008) maps to TTM-FR-19.

Coverage: every §6 acceptance criterion, the §4 authorization matrix (TC-012/015 plus scoped-authoring checks folded into TC-001), UGC traceability (TC-017), term-bound archival (TC-019/020), and all §8 decisions map to at least one test except venue-deactivation and faculty-departure conflict-queue flows, which are covered in the integration test phase.

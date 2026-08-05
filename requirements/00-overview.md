# UniCore — System Overview & Cross-Cutting Requirements

Status: DRAFT — pending approval · Last updated: 2026-07-30

## 1. What UniCore is

UniCore is a university management system for a large Indian **multi-campus university** (15,000+ students, ~1,000–2,000 staff). It covers the day-to-day academic operations layer: faculty task management, student onboarding (provisioning), timetabling, QR-based attendance, promotion, syllabus coverage tracking, question paper generation, and an AI-assisted email client for top leadership.

Admissions, fee collection, and examinations conduct/valuation are **out of scope** — students and results arrive from the university's existing ERP/exam systems.

## 2. Organizational model

```
University
└── Faculty Division (7 today: FET, FMC, FHSS, FSC*, FMS, FHS, Agri)
    └── School  (declares curriculum cadence: semester OR yearly) [multiple]
        └── Department                                            [multiple]
            └── Program (inherits the School's cadence; may override) [multiple]
                └── Section (per-term class group, sized by class-size cap)
```

- The university operates **multiple campuses**; campus is an organizational dimension on Schools/Departments/venues/users.
- **Source of truth for roles & access:** the university's role hierarchy and module-access matrix is [`sources/module_access_matrix.xlsx`](sources/module_access_matrix.xlsx) (received 25-07-2026; \*"FSC" carries a "confirm" note in the sheet, and the Agri Division's label needs correction — both flagged in AUTH open questions).
- **Curriculum cadence sits on the School** (locked 30-07-2026). Every School declares `semester` or `yearly` **at creation** — it is mandatory, not inferred — and every Programme beneath it inherits that value. A Programme **may override** it, because real Schools mix cadences: the School of Pharmacy runs B.Pharm and M.Pharm alongside PhD programmes that follow no term ladder at all. Cadence decides how many positions a Programme has (`duration_years × 2` for semester, `duration_years` for yearly), what a term advance means, and how Sections are named. This supersedes the earlier statement that cadence was a Programme attribute; the School is authoritative and the Programme is the exception.
- Each School owns its own promotion workflow.
- All data visibility and administrative authority is scoped by org unit (campus / Faculty Division / School / Department / Program / Section).
- **Exactly one University** exists (locked 28-07-2026): the root is created by bootstrap and a second university row is refused. Campuses remain a dimension on units below it, not a second root.
- **Org-unit lifecycle:** Faculty Divisions, Schools, Departments, and Programs are created/renamed/deactivated (never deleted) by the University Super Admin, fully audited (see AUTH doc). Bulk maintenance uses a **flat course-catalogue import** — one row per Programme carrying its Faculty Division, School and Department as columns, with missing ancestors created automatically. **Department is optional**: most Schools have none, so blank department columns synthesise a default Department mirroring the School, flagged `auto_created` (locked 28-07-2026 against the university structure document). Programmes carry level (UG/PG/PhD/Diploma), duration in academic years, mode (Full-Time/Part-Time), category (Standard / Industry Collaborated / Industry Integrated / Research), industry partner, internship months, and lateral-entry semester. A **Section is a per-term instance** — (Program, term, position, division letter) — created by the **Timetable Cell during term setup** (see TTM doc); ONB section allotment and TTM draft authoring depend on the term's Section instances existing first. Sections are **generated, not typed**: the Timetable Cell triggers generation for a term and the system walks each Programme's live positions, dividing the expected headcount by the School's class-size cap to decide how many parallel Sections that position needs — so B.Tech AI & Data Science semester II with 90 students and a cap of 60 yields "II Semester - A" and "II Semester - B". Labels render from a configurable template and repeat across terms; the Timetable Cell may add further Sections by hand at any time. Term-closure archives a Section instance; a PRM rollback un-archives the old instance without colliding with the new term's Sections.
- **School academic calendar:** before each semester/year, every School uploads its term calendar — start/end dates, exam-date ranges, special-event dates, and the term-archival backstop date. School office staff upload; the School Incharge's approval is recorded before it becomes active; amendments are versioned and re-approved. **Holidays and working days are a separate, two-layer calendar (locked 05-08-2026):** a **university holiday calendar** maintained by System Admin — date ranges with a kind (public / vacation / local), university-wide by default and optionally tagged to specific campuses — layered under a **per-School working-day pattern** set by each School Incharge. The pattern is seven weekday flags plus nth-weekday rules ("Saturdays: 1st and 3rd"), so an Engineering School running Monday–Saturday and a Nursing or Medical School teaching all seven days for clinical postings coexist on one campus — which a campus-scoped calendar could not express. A School may declare itself **working on a university holiday** (the ward does not close for Pongal), and may record dated exceptions including a compensatory day that **follows another weekday's timetable**. One resolver answers "is this date a teaching day for this School" for timetable authoring, attendance sessions, leave balances and task recurrence, so the four never disagree. Exam and special-event dates drive **soft warnings and cross-references only** in MVP (TTM scheduling warnings, TSK exam-duty conflict signal, LVE On-Duty overlap display) — never hard blocks.
- **One calendar, many Schools (locked 30-07-2026):** a university-level actor may apply one set of term dates to **several Schools at once**. The upload **fans out into a separate draft calendar per School** — never one shared record — so each School Incharge still approves their own and may amend the dates before doing so. This is deliberate: a shared record would let one School's approval bind another School's term, and calendar approval is a School-scoped power the School Incharge holds alone. Schools already holding an approved calendar for that term code receive a new draft **version** (the existing amend-and-supersede path), never a silent overwrite.
- **Term parity:** a term is odd or even. A semester-cadence Programme runs only its **odd positions in an odd term** (1, 3, 5, 7) and its even positions in an even term — a 4-year B.Tech therefore needs four Sections' worth of ladder per term, not eight. Yearly-cadence Programmes run every position each term. Parity is declared on the term and drives Section generation.

## 3. Terminology

| Term | Meaning |
|---|---|
| Faculty Division | Academic grouping (never plain "Faculty" — avoids collision with teachers). The university's own documents call these **Faculties**; stakeholders also say **Faculty Streams** — same level, seven of them |
| Faculty Member | A teacher |
| SIF id | Student identifier issued when admission completes — present from day one, so it is the **import join key** |
| Enrollment No | The student's **canonical identifier**, issued after admission; unique university-wide, leads in rosters/exports, one-to-one with the SIF id |
| Section | A class group of students within a program term |
| **Batch** | The **admission cohort** of a Programme — (Programme × joining year), e.g. `BT-CSE-2026`. The unit that moves through the curriculum together and graduates together. Reserved: see the note below |
| **Lab group** | A partition of a Section for lab blocks (B1/B2), each with its own Faculty Member and venue. Formerly called a "lab batch" |
| **Import run** | One execution of a CSV import — its file, row counts and error report. Formerly called an "import batch" |
| Position | A student's place in the curriculum ladder: semester *n* for semester cadence, year *n* for yearly. The authoritative number; the other is derived |
| Class-size cap | Maximum students per Section, used to decide how many parallel Sections a position needs |
| Class In-charge | Faculty Member designated as owner of a Section |
| Period | One timetabled teaching slot |
| Session | One delivered instance of a Period (carries attendance + syllabus log) |
| Timetable Cell | Central campus team that builds timetables (MVP); Faculty Dean offices submit timetable inputs to it |
| Exam Cell | Non-teaching unit controlling exams and final question papers, led by the Controller of Examination |
| School Incharge | Head of a School — holds every School-scoped power (promotion config+ratification, calendar approval, kind taxonomy, tag curation, attendance-impact policy, School Admin designation) |
| Faculty Dean | Head of a Faculty Division (Dean FET, Dean FMC, …); line manager of the Division's School Incharges |
| Dean Academic Affairs | University-level line manager of all Faculty Deans |
| Controller of Examination (CoE) | Exam Cell lead, reports to the Registrar |
| Principal | A **School-level** title (School of Pharmacy, School of Nursing) documented as "equal to school incharge" — a display alias for a School Incharge grant, **not** a campus head |
| Teaching grades | Professor, Associate Professor, Assistant Professor, Tutor, Assistant Teaching Staff — all "Faculty Member" tier (Tutors/ATS cannot author question-bank entries) |

> **"Batch" is a reserved word (locked 30-07-2026).** It means the admission
> cohort and nothing else — in documents, API, database and UI alike. The word
> previously carried three unrelated meanings across these documents: the
> admission cohort, a lab partition of a Section (TTM), and a CSV import run
> (ONB). Three meanings of one word inside one clash-detection engine is a defect
> waiting to be written, so the other two were renamed to **lab group** and
> **import run**. Any surviving use of "batch" for those senses is a stale
> document, not an alternative spelling.

## 4. User groups

Students · teaching staff in five grades (Professor, Associate Professor, Assistant Professor, Tutor, Assistant Teaching Staff) · HoDs · School Incharges · Faculty Deans · Dean Academic Affairs · Admin/office staff · Executives (Chancellor, VC, Registrar, campus Principals/Directors) · Non-teaching support (Exam Cell under the Controller of Examination, Timetable Cell, lab assistants, etc.) · the full non-academic staff tree from the access matrix (Dean Research, Dean Student Welfare & Chief Proctor with wardens/PE, Dean IQAC, Finance Officer chain, Dean Admin & R&A chain, PRO chain — accounts with minimal access: leave applicant, task assignee, tier-2 PA where granted; Dean Research/IQAC/Dean Admin get restricted dashboards that exclude School academic data) · **Chancellor** (top of the reporting chain — minimal-capability account approving VC/Registrar leave). **Pro-Chancellor restored 28-07-2026** (the university's leadership page lists two holders; removed 25-07-2026 when the access matrix omitted it). **Alumni get no accounts in MVP.**

**Reporting chain** (AUTH-configured, used by leave routing and task escalation): Class In-charge → HoD → School Incharge → Faculty Dean → Dean Academic Affairs → VC → **Pro-Chancellor** → Chancellor; Registrar → Pro-Chancellor. **Pro-Chancellor is not a singleton** — the university documents two holders (28-07-2026); either may approve. Principals are School-level heads, not a campus tier, so they route as School Incharges. Non-academic staff resolve to their **unit head** per the AUTH role registry (e.g., Exam Cell → Registrar). Routing **cascades past chain levels that are on approved leave or vacant** (each hop recorded with its cause; the Chancellor is terminal).

**PA (email + tasks) access is two-tier** per the access matrix: tier 1 "Access to AI" (~20 leadership roles) gets the full EML client with draft-only AI; tier 2 (HoDs, all teaching grades, most admin staff) gets email + tasks with **no AI features**; students, security, canteen, hospitality, and alumni get no PA.

**Deliberately excluded:** parents/guardians and external examiners have no system access; alumni have no accounts.

## 5. Modules

| # | Module | Code | Document |
|---|--------|------|----------|
| 1 | Authentication, Authorization & Security (cross-cutting) | AUTH | [01-authentication-authorization-security.md](01-authentication-authorization-security.md) |
| 2 | Student Onboarding (import-only provisioning) | ONB | [02-student-onboarding.md](02-student-onboarding.md) |
| 3 | Timetable Management | TTM | [03-timetable-management.md](03-timetable-management.md) |
| 4 | Attendance Capture (QR) | ATT | [04-attendance-capture.md](04-attendance-capture.md) |
| 5 | Faculty Task Management | TSK | [05-faculty-task-management.md](05-faculty-task-management.md) |
| 6 | Student Promotion | PRM | [06-student-promotion.md](06-student-promotion.md) |
| 7 | Syllabus Coverage Tracking | SYL | [07-syllabus-coverage.md](07-syllabus-coverage.md) |
| 8 | Question Paper Generation | QPG | [08-question-paper-generation.md](08-question-paper-generation.md) |
| 9 | Executive Email Client with AI Agents | EML | [09-executive-email-ai.md](09-executive-email-ai.md) |
| 10 | Leave Management | LVE | [10-leave-management.md](10-leave-management.md) |

## 6. Cross-cutting locked decisions

1. **Identity:** own identity store; username/password + OTP second factor. No SSO in MVP. See AUTH doc.
2. **Authorization:** RBAC + org-unit scoping everywhere. Each module doc carries its own per-action authorization matrix. **Every API call is authenticated (valid session token) and authorized (role + scope) — deny by default, public endpoints are an explicit allowlist; responses are scope-filtered at the query level so no cross-user data can leak** (locked 25-07-2026; enforced structurally, see backend/ARCHITECTURE.md).
3. **Attendance anti-fraud stack (all four):** rotating QR (15–30 s expiry) + one registered device per student + geofence/proximity check + faculty count verification before session close.
4. **Corrections discipline:** captured attendance is modified only by the Class In-charge, with a mandatory reason, fully audited.
5. **Promotion:** a per-School configurable workflow engine over system-computed eligibility inputs; thresholds (e.g., 75% attendance) are School-configurable, not hardcoded.
6. **Question paper confidentiality:** papers are sealed/encrypted until Exam Cell release; contributing faculty never see the assembled final paper.
7. **AI boundaries:** the email AI is draft-only — a human sends every message — and exists only for tier-1 ("Access to AI") roles; tier-2 PA users get email + tasks with no AI processing of their mailboxes. No biometric data anywhere in the system.
8. **Timetable MVP:** built manually by the central Timetable Cell with system-enforced clash detection; constraint-based auto-generation is a later phase and must not be architecturally precluded.
9. **Additional charge & singleton leadership:** the same leadership role can be held at multiple org units by one person (the access matrix itself shows Dean FET holding FMC and Dean FMS holding FHS as additional charge), but each org unit permits only **one active holder** — one HoD per Department, one School Incharge per School, one Faculty Dean per Faculty Division, one Class In-charge per Section — enforced at grant time; holders change only via an atomic supersede (revoke + issue together).
10. **Attendance freeze:** triggering a Program's promotion run (PRM) freezes attendance corrections for all of that Program's Sections' current-term Sessions. Post-freeze, a correction commits only when attached to an **open dispute/grievance** and only for non-ratified students; retro `counts-as-present` leave marking (LVE) stays exempt until the student ratifies; never-opened Periods resolve post-freeze by HoD-acknowledged write-off only. See PRM-FR-17.
11. **Term-scoped academic assignments:** users hold multiple roles (an HoD can also be a Class In-charge — grants union normally), but roles tied to a teaching term are term-bound: the Class In-charge grant and every faculty subject allocation (which lives in the term's published timetable) are valid only for the current semester/year per the School. **Promotion ratification publishes a term-closure event per Section-cohort that revokes these grants and archives the timetable's subject allocations**; each new term starts with fresh HoD designation and a fresh published timetable. An in-window promotion rollback reverses the closure.

## 7. Legal & regulatory baseline (India)

- **DPDP Act 2023** — notice and consent for personal data processing; purpose limitation and data minimization; right to correction and erasure via a grievance mechanism; breach notification duties. Geolocation used for attendance requires explicit notice/consent, is used only for the attendance purpose, and only pass/fail proximity results are retained — not location traces.
- **No minors (locked 24-07-2026):** all students are 18+ at admission per university policy; the system carries no parental-consent machinery and no DOB age guard. DOB is imported but never age-checked. Residual risk accepted and recorded: if admission policy ever changes, minor-consent handling must be re-introduced before processing a minor's data.
- **UGC/AICTE norms** — minimum-attendance norms (commonly 75%) inform promotion/exam eligibility; thresholds are configurable per School.
- **Localization** — IST timezone, DD-MM-YYYY dates, English UI (MVP).

## 8. Non-functional baseline (inherited by every module)

| Concern | Requirement |
|---|---|
| Scale | 15,000+ students, ~1,000–2,000 staff, multiple campuses |
| Attendance burst | ≥50 QR-scan validations/second sustained; scan-to-confirmation < 2 s (p95) |
| Availability | 99.5% during academic hours (08:00–18:00 IST) |
| Encryption | TLS 1.2+ in transit; encryption at rest for all personal data; question papers additionally app-layer encrypted until release |
| Audit | Every privileged/corrective action writes an immutable record: who, what, when, before/after, reason |
| Bulk data upload | Every bulk-upload surface ships a **downloadable CSV template** generated from the same column definition its validator uses, so templates can never drift from the schema. Each carries a comment block (mandatory vs optional fields, formats, safety of re-upload), the header row, and several worked sample rows. Uploads share one discipline: pre-parse gate (size/encoding/header), row-level validation, **partial commit** (valid rows land, invalid rows return an actionable error report with row number, field, reason and raw row), and idempotent re-upload. Templates are listed and downloaded from an authenticated endpoint — no upload surface is public |
| Notifications | Shared baseline for all modules: **in-app always**; **email** per event class (School/University-configurable); **SMS reserved for OTP and credential delivery only** (DLT cost). Delivery is at-least-once with retry/backoff; failures after retries surface on an ops dashboard; user-level muting never applies to security or legal notices. Modules keep their own delivery SLAs and reference this baseline |

## 9. Approval gate

All module documents are **DRAFT — pending approval**. Implementation begins only after these requirements are reviewed and explicitly approved. Each document ends with a test-case table that will seed the test suite during implementation.

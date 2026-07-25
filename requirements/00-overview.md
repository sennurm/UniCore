# UniCore — System Overview & Cross-Cutting Requirements

Status: DRAFT — pending approval · Last updated: 2026-07-21

## 1. What UniCore is

UniCore is a university management system for a large Indian **multi-campus university** (15,000+ students, ~1,000–2,000 staff). It covers the day-to-day academic operations layer: faculty task management, student onboarding (provisioning), timetabling, QR-based attendance, promotion, syllabus coverage tracking, question paper generation, and an AI-assisted email client for top leadership.

Admissions, fee collection, and examinations conduct/valuation are **out of scope** — students and results arrive from the university's existing ERP/exam systems.

## 2. Organizational model

```
University
└── Faculty Division (e.g., Faculty of Engineering)   [multiple]
    └── School                                        [multiple]
        └── Department                                [multiple]
            └── Program (semester-based OR year-based) [multiple]
                └── Section (class group of students)
```

- The university operates **multiple campuses**; campus is an organizational dimension on Schools/Departments/venues/users.
- **Each School decides** whether its programs run on semesters or years, and each School owns its own promotion workflow.
- All data visibility and administrative authority is scoped by org unit (campus / Faculty Division / School / Department / Program / Section).
- **Org-unit lifecycle:** Faculty Divisions, Schools, Departments, and Programs are created/renamed/deactivated (never deleted) by the University Super Admin, fully audited (see AUTH doc). A **Section is a per-term instance** — (Program, term, label), with labels like "3B" reusable across terms — created by the **Timetable Cell during term setup** (see TTM doc); ONB section allotment and TTM draft authoring depend on the term's Section instances existing first. Term-closure archives a Section instance; a PRM rollback un-archives the old instance without colliding with the new term's Sections.
- **School academic calendar:** before each semester/year, every School uploads its term calendar — start/end dates, exam-date ranges, special-event dates, and the term-archival backstop date. School office staff upload; the Dean's approval is recorded before it becomes active; amendments are versioned and re-approved. Campus holidays/working days remain a separate System Admin-maintained campus calendar. Exam and special-event dates drive **soft warnings and cross-references only** in MVP (TTM scheduling warnings, TSK exam-duty conflict signal, LVE On-Duty overlap display) — never hard blocks.

## 3. Terminology

| Term | Meaning |
|---|---|
| Faculty Division | Academic grouping (never plain "Faculty" — avoids collision with teachers) |
| Faculty Member | A teacher |
| Section | A class group of students within a program term |
| Class In-charge | Faculty Member designated as owner of a Section |
| Period | One timetabled teaching slot |
| Session | One delivered instance of a Period (carries attendance + syllabus log) |
| Timetable Cell | Central campus team that builds timetables (MVP) |
| Exam Cell | Non-teaching unit controlling exams and final question papers |

## 4. User groups

Students · Faculty Members · HoDs · Admin/office staff · Executives (VC, Pro-VC, Registrar, campus Principals/Directors, Deans) · Non-teaching support (Exam Cell, Timetable Cell, lab assistants, etc.) · **Pro-Chancellor and Chancellor** (top of the reporting chain — minimal-capability accounts, introduced for leave approvals of VC/Registrar).

**Reporting chain** (AUTH-configured, used by leave routing and task escalation): Class In-charge → HoD → Dean → VC → Pro-Chancellor → Chancellor; Registrar → Pro-Chancellor; **Principal/Director → VC**. Non-academic staff resolve to their **unit head** per the AUTH role registry (e.g., Exam Cell → Registrar). Routing **cascades past chain levels that are on approved leave or vacant** (each hop recorded with its cause; the Chancellor is terminal).

**Deliberately excluded:** parents/guardians and external examiners have no system access.

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
2. **Authorization:** RBAC + org-unit scoping everywhere. Each module doc carries its own per-action authorization matrix.
3. **Attendance anti-fraud stack (all four):** rotating QR (15–30 s expiry) + one registered device per student + geofence/proximity check + faculty count verification before session close.
4. **Corrections discipline:** captured attendance is modified only by the Class In-charge, with a mandatory reason, fully audited.
5. **Promotion:** a per-School configurable workflow engine over system-computed eligibility inputs; thresholds (e.g., 75% attendance) are School-configurable, not hardcoded.
6. **Question paper confidentiality:** papers are sealed/encrypted until Exam Cell release; contributing faculty never see the assembled final paper.
7. **AI boundaries:** the executive email AI is draft-only — a human sends every message. No biometric data anywhere in the system.
8. **Timetable MVP:** built manually by the central Timetable Cell with system-enforced clash detection; constraint-based auto-generation is a later phase and must not be architecturally precluded.
9. **Additional charge & singleton leadership:** the same leadership role can be held at multiple org units by one person (a Dean managing a second School, an HoD temporarily heading another Department), but each org unit permits only **one active holder** — one HoD per Department, one Dean per School, one Class In-charge per Section — enforced at grant time; holders change only via an atomic supersede (revoke + issue together).
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
| Notifications | Shared baseline for all modules: **in-app always**; **email** per event class (School/University-configurable); **SMS reserved for OTP and credential delivery only** (DLT cost). Delivery is at-least-once with retry/backoff; failures after retries surface on an ops dashboard; user-level muting never applies to security or legal notices. Modules keep their own delivery SLAs and reference this baseline |

## 9. Approval gate

All module documents are **DRAFT — pending approval**. Implementation begins only after these requirements are reviewed and explicitly approved. Each document ends with a test-case table that will seed the test suite during implementation.

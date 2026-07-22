# Tasq

**Tasq** is a university management system for a large, multi-campus Indian university (15,000+ students). It runs the day-to-day academic operations layer — the work that happens between admissions and examinations: who teaches what and when, who attended, who gets promoted, what was covered, and who approved it.

## Why "Tasq"?

The name comes from **Task** — the unit everything in a university's daily operation reduces to: a class to conduct, an attendance session to close, a leave to approve, a paper to set, a report to submit. The **q** replaces the *k* with a nod to the **queue** — every task in Tasq flows through someone's queue: an HoD's approval queue, a faculty member's duty list, an exam cell's release pipeline. Tasks, queued and accounted for — **Tasq**.

## What it does

| Module | Purpose |
|---|---|
| Authentication & Authorization | Own identity store (password + OTP), role-based access with org-unit scoping, term-bound roles, audit trail |
| Student Onboarding | Import-only provisioning from the university ERP — accounts, sections, roll numbers |
| Timetable Management | Central timetable cell authoring with hard clash detection; labs, electives, combined classes; substitutions, rebalancing suggestions, and faculty class swaps |
| Attendance Capture | QR-based marking with a four-layer anti-fraud stack; corrections only by the Class In-charge, with reason |
| Faculty Task Management | Hierarchical duty assignment with recurrence, reminders, and escalation up to the VC office |
| Student Promotion | Per-School configurable promotion workflows over computed eligibility (attendance %, results, backlogs) |
| Syllabus Coverage | Per-period topic logging against an approved plan, with lag alerts to HoDs |
| Question Paper Generation | Moderated question bank + blueprint assembly (AI-assisted), sealed until exam-cell release |
| Executive Email Client | AI triage/summarize/draft for top leadership — draft-only, a human always sends |
| Leave Management | Hierarchy-routed leave approvals with auto-cascade past absent approvers and delegation windows |

## Organization model

```
University → Faculty Division → School → Department → Program → Section
```

Multiple campuses; each School chooses semester- or year-based programs and owns its own promotion workflow. Roles support additional charge (one person, multiple units) under a strict one-active-holder-per-unit rule.

## Status

📋 **Requirements phase.** Detailed, decision-locked requirement documents live in [`requirements/`](requirements/) — start with the [system overview](requirements/00-overview.md). Implementation begins after requirements sign-off.

## Compliance context

Built for India: DPDP Act 2023 (consent, purpose limitation, minor protection, no biometric data), UGC/AICTE academic norms (configurable attendance thresholds), IST/DD-MM-YYYY conventions, India-region data residency.

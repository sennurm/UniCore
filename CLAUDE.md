# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State

Tasq is in the **requirements phase** — no source code or build tooling exists yet. Detailed module requirements live in `requirements/` (start with `requirements/00-overview.md`). Implementation begins only after the user approves those documents. Before implementing any feature, read the corresponding requirements doc; before making changes that would result in code, run the SME requirement clarification pass first.

## Application Context (maintained by the SME clarifier)

### Field / Domain
University management system for a large Indian multi-campus university. Modules: authentication/authorization (own identity store, password + OTP), student onboarding (import-only from existing ERP), central timetabling (labs, electives, combined classes), QR-based attendance with anti-fraud controls, per-School configurable student promotion, syllabus coverage tracking, question paper generation (bank + blueprint, AI-assisted, sealed until Exam Cell release), and a draft-only AI email client for top leadership. Admissions, fees, and exam conduct/valuation are out of scope (external ERP/exam systems).

### Users & Roles
Students; Faculty Members (teachers); HoDs; Admin/office staff; Executives (VC, Pro-VC, Registrar, campus Principals/Directors, Deans — the only email-client users, ~10–30); Non-teaching support (Exam Cell, Timetable Cell, lab assistants); Pro-Chancellor and Chancellor (top-of-hierarchy singleton roles, minimal accounts for approving VC/Registrar leave). **Reporting chain** (AUTH-configured, drives leave routing and task escalation): Class In-charge → HoD → Dean → VC → Pro-Chancellor → Chancellor; Registrar → Pro-Chancellor. Leave module (10-leave-management.md): single-approver routing with auto-cascade past approvers on leave and standing upward delegation; student leave types have School-configurable attendance impact (counts-as-present / absent / condonation-evidence); staff have simple per-type balances (accrual/LOP stays in external HR). Faculty leave/absence triggers **rebalancing**: ranked clash-free substitute suggestions to the HoD (never auto-assigned); faculty can also **swap** specific class occurrences by mutual consent (HoD notified, not a gate; occurrence-level only, one hop, no permanent swaps). **No parent/guardian or external-examiner access.** Authorization is RBAC with org-unit scoping over the hierarchy University → Faculty Division → School → Department → Program → Section. Terminology: "Faculty Division" = academic grouping; "Faculty Member" = teacher; "Class In-charge" = section owner (sole authority to correct captured attendance, with mandatory reason). Users hold multiple roles (an HoD may also be a Class In-charge), and leadership roles support **additional charge** — one person holding the same role at multiple org units (Dean of a second School, HoD temporarily heading another Department) — under a strict **singleton rule**: at most one active HoD per Department / Dean per School / Class In-charge per Section, changed only via atomic supersede (revoke + issue together). **Term-scoped assignments:** Class In-charge grants and faculty subject allocations (carried by the term's published timetable) are valid only for the current semester/year per the School; promotion ratification publishes a per-Section term-closure event that revokes these grants and archives the timetable; every new term requires fresh designation — nothing rolls over.

### Regions
India, multiple campuses, 15,000+ students. All personal data resides in India-region infrastructure. IST timezone, DD-MM-YYYY dates, English UI (MVP). Programs are semester-based or year-based — each School decides, and each School owns its own promotion workflow.

### Legal & Regulatory Constraints
- **DPDP Act 2023** — notice + versioned consent; separate explicit consent for attendance geolocation (only pass/fail proximity retained, never location traces); verifiable parental consent required for minors (some UG entrants are 17); correction/erasure grievance flow (academic records carry statutory retention exemptions); breach notification duties.
- **UGC/AICTE norms** — minimum-attendance norms (commonly 75%) gate promotion/exam eligibility; thresholds must be School-configurable, never hardcoded.
- **No biometric data anywhere** — QR attendance was chosen partly to avoid it.
- India SMS/DLT regulations apply to OTP delivery.

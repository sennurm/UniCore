# Port Map — Smart-Attendance-Final → UniCore ATT

Status: ANALYSIS COMPLETE, port deferred to the ATT milestone · Created: 27-07-2026
Reference: `github.com/adminTUAI/Smart-Attendance-Final` (Flask + SQLite, `app.py` ~889 lines)
Target requirements: [04-attendance-capture.md](../../requirements/04-attendance-capture.md)

Decisions taken with the stakeholder (27-07-2026):
1. **Face recognition is dropped** — it is the reference app's core, and biometric
   data is forbidden system-wide (CLAUDE.md legal constraints; overview §7; ATT §5).
   Its anti-proxy *intent* is met by our locked four-layer stack instead.
2. **Build order stays ONB → TTM → ATT.** ATT Sessions derive from TTM published
   Periods and ONB rosters; this document keeps the reusable logic until then.

## 1. What the reference app does

| Area | Implementation |
|---|---|
| Session/QR | `generate_qr()` — UUID4 session, single static QR, `issued_at`/`expires_at` (+60 s), deactivates the author's previous session, payload `{session_id, issued_at, room_id, class_lat/lng}` |
| Verification | `verify_liveness()` + `verify_face_opencv()` — OpenCV Haarcascade face count, DeepFace/Facenet match vs `static/student_faces/<username>.jpg`, `compute_ear()` blink liveness, short-lived in-memory liveness token (120 s, single-use) |
| Scoring | `compute_trust_score()` — QR freshness 50 pts (linear decay 60→80 s), dummy geofence 10 pts (always awarded), face 40 pts; verdict `verified ≥85` / `partial >61` / `proxy_detected` / `rejected`; flags list |
| Marking | `mark_attendance()` — validates liveness token, loads QR session, one record per **day**, writes status + trust score + flags |
| Correction | `manual_override()` + `override_logs` (student, reason, updated_by, new status) |
| Live view | `/api/live-feed` — recent scans with status/score/method for the staff dashboard |
| Auth | Flask session cookie, `login_required` / `staff_required`, SHA-256 unsalted passwords |

## 2. Carries over (with adaptation)

| Reference logic | Lands as | UniCore requirement |
|---|---|---|
| QR session lifecycle: server-issued id, issue/expiry timestamps, single active session per author | `Session` open → scanning → close, opened only against a **published-timetable Period** by the assigned Faculty Member / substitute / swap counterpart | ATT-FR-01, §4 rule 1 |
| Server-side token freshness check (client time never trusted) | Same principle, hardened: **rotating token every 15–30 s**, ≥128-bit entropy, validated against server time; no 20 s grace window | ATT-FR-02, §8 token-replay |
| Weighted score → verdict → flags **structure** | **Scan-risk score** rebuilt on non-biometric signals (§3 below) — ranks `pending-verification` items for faculty review; never auto-rejects | Proposed enhancement, see §5 |
| Duplicate guard (`Already marked today`) | Idempotent scan on (Session, student) uniqueness — **per Session, not per day** | ATT-FR-06 |
| `manual_override` + `override_logs` (reason, actor, new status) | Class In-charge correction flow: mandatory reason, before/after audit, freeze-aware | ATT-FR-11, PRM-FR-17 |
| `/api/live-feed` polling for the staff dashboard | Faculty live view: running scanned + pending counts during scanning | ATT-FR-16 |
| Faculty-facing dashboard shell (`templates/dashboard.html`) | Superseded by our design system (`UniCore.dc.html` Faculty · Live QR session screen) | Design project f1c531d5 |

## 3. Replacement for the face component (non-biometric)

The reference score is 50 QR + 10 geofence + 40 face. Our replacement keeps the
*shape* (weighted signals → verdict → flags) with our locked four-layer stack:

| Signal | Weight | Source |
|---|---|---|
| Token freshness (age vs rotation interval) | high | ATT-FR-02 rotating token |
| Registered-device match | **gate** — mismatch is rejection, not a deduction | ATT-FR-03, AUTH-FR-06 |
| Roster membership | **gate** — off-roster is rejection | ATT-FR-03/10 |
| Proximity pass/fail (GPS geofence or campus Wi-Fi) | medium | ATT-FR-04 — boolean only, no coordinates stored |
| Timing anomaly (scan burst from one device, repeated stale tokens) | low | AUTH-FR-12 telemetry |

Verdict mapping: gates failed → **rejected**; all signals clean → **present**;
anything ambiguous (proximity fail/unavailable, consent refused, stale-but-close
token) → **pending-verification** for faculty resolution at count verification —
never a silent absence. This preserves the reference app's "graded confidence"
idea while honouring ATT §5 (pass/fail proximity only, zero location bytes at rest).

## 4. Explicitly rejected

| Reference behaviour | Why rejected |
|---|---|
| DeepFace/Facenet face matching, stored face photos, EAR blink liveness | **Biometric data — forbidden system-wide** (DPDP; CLAUDE.md; ATT §5, §2 non-goals) |
| `target_uid = data.get('student_id') or session['user_id']` in `mark_attendance` | **Attendance forgery / cross-user write** — a client can mark another student present. Violates the project security rule (subject resolves from AuthContext, never a client-supplied id) |
| Unsalted SHA-256 passwords | Argon2id per AUTH §9 (already implemented) |
| Per-day attendance record | Attendance is per **Session** (one delivered Period) |
| Dummy geofence awarding 10 pts unconditionally | Real pass/fail proximity check, consent-gated (ATT-FR-04) |
| `qr_near_expiry` 60→80 s decay window | Stale token = rejection; the rotation interval is the whole window |
| Staff-vs-student boolean roles | RBAC with org-unit scoping; Class In-charge is the sole correction authority |
| In-memory liveness token dict | Not needed once face verification is gone; session state lives in Redis |
| `/api/sync-offline` bulk offline record upload | ATT-FR-05: no offline queueing — scans require live server validation; the endpoint also trusts client-supplied status and score |
| Client-side geofence coordinates in the QR payload | Venue geofences are server-side master data; coordinates never round-trip through the client |

## 5. The admin page's "student onboarding" — assessed for ONB

Reviewed at the stakeholder's suggestion (27-07-2026). What the admin/staff
surface actually provides:

| Reference feature | Verdict for our ONB milestone |
|---|---|
| `/api/upload-face`, `/api/face-status`, `/api/delete-face` + the student "Register Face" panel — **this is what onboarding means in that app** | **Not portable** — face-photo capture is the biometric flow we dropped (§4). Nothing here survives |
| `seed_data()` — 9 hardcoded demo accounts with plaintext-derived passwords | Not portable. Our accounts arrive from the ERP import; credentials are Argon2id with forced first change (already built) |
| `/api/students` — flat list of `id, username, full_name` for the whole instance | Shape is too thin: no org unit, no Section membership, no ERP id, no state, no campus scoping. Our roster reads are scope-filtered and membership-as-of-date (ONB-FR-10) |
| `/api/download-report` — CSV via `StringIO` + `Response` with `Content-disposition` | **Reusable pattern** (not code) for ONB's downloadable **error report** (ONB-FR-03) and batch summary export. Ours adds row number, field, reason, raw row |
| Roster list with a per-student status badge (`has_face` ✅/⚠️) | **Reusable UI pattern** for the ONB batch dashboard's per-student **credential-delivery status** (delivered / failed / pending — ONB-FR-14) |
| `/api/sync-offline` — bulk-inserts client-supplied offline records | **Rejected**: ATT-FR-05 locks out offline scan queueing (queued scans defeat server-side token freshness and are forgeable). It also trusts client-supplied status/score outright |

**What ONB needs that the reference app has none of:** CSV upload + ERP API feed
through one validation pipeline, row-level validation with an actionable error
report, partial-commit semantics, idempotent upsert keyed on ERP ID, in-file
duplicate detection, the >20 % corrupted-feed guardrail, Section allotment and
re-allotment with effective dates, campus/Program transfers, withdrawal with
session revocation, and 20 k rows in under 10 minutes.

**Conclusion:** ONB gets no material head start from this reference. Build it from
[02-student-onboarding.md](../../requirements/02-student-onboarding.md) as planned;
borrow only the CSV-download and status-badge patterns noted above.

## 6. Open item for the ATT milestone's SME pass

The **scan-risk score** (§3) is an *addition* to the current ATT requirement, which
specifies a binary accept / pending-verification / reject outcome. It is not yet
approved. Take it through the SME clarification pass at the start of the ATT
milestone: decide whether faculty see a ranked pending list (score + flags) or the
current unranked list, and whether the score is persisted (audit value) or computed
transiently (data minimization). Do not build it before that decision.

## 7. Sequencing reminder

ATT cannot start until:
- **ONB** provides student accounts + Section membership as-of-date (ONB-FR-10);
- **TTM** provides published timetables, Period→Faculty assignment, substitutions,
  swaps, and combined-class/batch/elective roster resolution (ATT §10 assumptions).

Until then this document is the port's memory. The reference repo was cloned for
analysis only; no code from it has entered the repository.

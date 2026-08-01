# UniCore Backend Architecture

Modular monolith. One FastAPI deployable, one PostgreSQL database, one package
per functionality under `src/unicore/modules/` — each module is a **vertical
slice** owning its API, business logic, data access, and tables.

## Layout

```
src/unicore/
├── core/            # cross-cutting infrastructure ONLY
│   ├── config.py    #   settings (UNICORE_* env)
│   ├── logging.py   #   project logging standard (JSON, trace ids, timed())
│   ├── db.py        #   engine, sessions, Declarative Base
│   ├── middleware.py#   access log + error envelope
│   └── health.py    #   liveness probe
└── modules/
    ├── auth/        # authentication: login, OTP, sessions, lockout, device registration, consent
    ├── user/        # user accounts & identity lifecycle (provision, deactivate, contact)
    ├── org/         # org units: Faculty Division→…→Program (ltree) + per-term Section instances
    ├── rbac/        # roles, grants, singleton+supersede, permission library, reporting chain
    ├── audit/       # transactional outbox, append-only audit store, scoped read API
    ├── onboarding/  # ONB (milestone 2)
    ├── timetable/   # TTM
    ├── attendance/  # ATT
    ├── tasks/       # TSK
    ├── promotion/   # PRM
    ├── syllabus/    # SYL
    ├── questionpaper/ # QPG
    ├── mail/        # EML (two-tier PA client)
    └── leave/       # LVE
```

## Files inside every module

| File | Role | May import |
|---|---|---|
| `router.py` | FastAPI endpoints; translate HTTP ⇄ schemas; **zero business logic** | own `schemas`, own `service`, `core` |
| `schemas.py` | Pydantic request/response models | `core` |
| `service.py` | All business rules for the module | own `dao`, own `schemas`, **other modules' `service`**, `core` |
| `dao.py` | Every SQLAlchemy query touching this module's tables | own `models`, `core.db` |
| `models.py` | ORM tables this module owns | `core.db` |

Larger modules may split any layer into a package (`service/`, `dao/`) keeping
the same names and rules.

## The four boundary rules

1. **Layering is one-way:** `router → service → dao → models`. A router never
   imports a DAO or a model; a DAO never imports a service.
2. **Cross-module traffic goes through services (or events):** module A may
   call `modules.b.service`, never `modules.b.dao` / `modules.b.models`.
   Anything asynchronous or cross-milestone (term-closure, attendance-freeze,
   leave-approved) goes through the audit/outbox event mechanism instead.
3. **`core/` is dependency-free of modules:** `core` never imports from
   `modules/`; every module may import `core`.
4. **Every table has exactly one owning module** (its `models.py`); other
   modules read that data via the owner's service. Alembic autogenerates from
   `core.db.Base.metadata`, which aggregates all module models.

Rules 1–3 are enforced by `tests/test_architecture.py` — CI fails on a
violating import, so the boundaries cannot rot silently.

## API security rule (project-level, locked 25-07-2026)

**Every API call must present a valid session token and pass a role+scope
permission check; responses must never contain data outside the caller's
scope.** Concretely:

1. **Deny by default:** `core/security.py` installs an authentication gate as
   ASGI middleware, so every route — present and future — rejects requests
   without a valid bearer token (401). Public endpoints are an explicit
   allowlist (`/health`; docs endpoints in dev only). The gate fails closed
   when no token verifier is registered.
2. **Role check on every endpoint:** each non-public path operation declares
   `rbac.service.require_permission("<action>")` as a dependency. Routers under
   `core/` cannot import `modules/` (rule 3 above), so they declare
   `core.security.requires("<action>")`, which delegates to the checker rbac
   registers at startup — the same engine, evaluating role AND org-unit scope
   (AUTH-FR-04/05), with core's independence intact.
3. **No cross-user data leakage:** DAOs take the caller's scope as an explicit
   parameter and filter in the query — never fetch-then-filter in Python;
   response schemas expose only fields the endpoint's audience may see; "own
   data" endpoints (`/me/...`) resolve the subject from the AuthContext, never
   from a client-supplied id. An endpoint that *does* take an id (a Section, a
   batch) must authorise that id against the caller's scope before reading —
   holding the action permission is not authority over every object.
4. **Tests prove it:** `tests/test_security.py` sweeps every path/method pair in
   the OpenAPI schema — parameterised paths and non-GET verbs included — and
   fails if one responds without a token. Enumerate from `app.openapi()`, not
   `app.routes`: FastAPI keeps included routers as opaque `_IncludedRouter`
   entries, so walking `app.routes` sees only the docs endpoints and guards
   nothing. A second test parses each router's AST and fails on any endpoint
   that declares no permission, against a short allowlist of public and
   own-data operations. Every module must also ship access-denial and
   scope-filtering test cases (the TC-*-Access rows).

## Other conventions

- **Permission checks:** every router uses the `rbac` permission dependency
  (AUTH-FR-05); no module rolls its own checks.
- **Audited actions:** services write audit/outbox rows in the same DB
  transaction as the business change (see plan §2 decision 5).
- **Logging:** per the project logging standard (`core.logging`) — structured
  JSON, `trace_id`/`span_id`, `duration_ms` on DB/API/AI calls via `timed()`.
- **Tests:** mirror the module tree — `tests/<module>/test_*.py`; requirement
  test-case IDs (TC-AUTH-001…) are referenced in test docstrings.

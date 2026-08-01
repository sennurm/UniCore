import os
import subprocess
import sys
import uuid as uuid_mod
from pathlib import Path

# Must be set before any unicore import caches Settings. Env vars take
# precedence over backend/.env, so local dev overrides (e.g. OTP disabled)
# can never leak into the test suite.
os.environ.setdefault(
    "UNICORE_DATABASE_URL", "postgresql+asyncpg://unicore:unicore@localhost:5432/unicore_test"
)
os.environ["UNICORE_OTP_LOGIN_ENABLED"] = "true"

from collections.abc import AsyncIterator, Callable  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from unicore.core import security  # noqa: E402
from unicore.core.db import get_engine, get_sessionmaker  # noqa: E402
from unicore.core.security import AuthContext, register_token_verifier  # noqa: E402
from unicore.main import create_app  # noqa: E402
from unicore.modules.auth.providers import email_provider, sms_provider  # noqa: E402
from unicore.modules.org.models import DEFAULT_UNIVERSITY_SETTINGS  # noqa: E402
from unicore.modules.rbac import service as rbac_service  # noqa: E402
from unicore.modules.rbac.models import Grant  # noqa: E402
from unicore.modules.user import dao as user_dao  # noqa: E402
from unicore.modules.user.models import User  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
ADMIN_DSN = "postgresql://unicore:unicore@localhost:5432/unicore"
TEST_DB = "unicore_test"

# Roles the test verifier may self-seed with a university-wide (NULL-unit) grant.
UNIVERSITY_SCOPE_ROLES = {"super-admin", "system-admin"}

# token -> (username, roles-to-seed); a single dispatcher verifier serves all
# clients in a test so several differently-authenticated clients can coexist.
_token_map: dict[str, tuple[str, tuple[str, ...]]] = {}


async def _dispatch_verifier(token: str) -> AuthContext:
    entry = _token_map.get(token)
    if entry is None:
        # Fall through to real Redis sessions so login-flow tokens keep working
        # while fake per-test actors are active.
        from unicore.modules.auth import service as auth_service

        return await auth_service.verify_session_token(token)
    username, roles = entry
    async with get_sessionmaker()() as session:
        user = await user_dao.get_by_username(session, username)
        if user is None:
            user = User(username=username, full_name=username, kind="staff", status="active")
            session.add(user)
            await session.flush()
        for role in roles:
            if role not in UNIVERSITY_SCOPE_ROLES:
                continue  # scoped roles must be granted through the API
            existing = [
                g
                for g in await rbac_service.list_user_grants(session, user.id)
                if g.role_code == role and g.status == "active"
            ]
            if not existing:
                session.add(Grant(user_id=user.id, role_code=role, granted_by="test-verifier"))
        await session.commit()
        rbac_service.invalidate_user(user.id)
        return AuthContext(user_id=str(user.id), session_id="test-session", role_names=roles)


@pytest.fixture(autouse=True)
def _reset_auth_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_token_verifier", None)
    _token_map.clear()
    rbac_service._grant_cache.clear()
    sms_provider.outbox.clear()
    email_provider.outbox.clear()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- database-backed test infrastructure ------------------------------------


@pytest.fixture(scope="session")
def database() -> None:
    """Create a fresh unicore_test database and migrate it to head, once per run."""
    import psycopg

    try:
        conn = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("PostgreSQL unavailable — run: docker compose up -d postgres")
    conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    conn.execute(f"CREATE DATABASE {TEST_DB}")
    conn.close()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
        env={
            **os.environ,
            "UNICORE_DATABASE_URL": f"postgresql+asyncpg://unicore:unicore@localhost:5432/{TEST_DB}",
        },
    )


@pytest.fixture
async def db(database: None) -> AsyncIterator[None]:
    """Fresh engine per test (event-loop safety) with truncated tables."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE org_units, users, audit_events, grants, otp_challenges, "
                "devices, device_change_requests, consent_records, grievances, "
                "domain_events, academic_terms, import_runs, import_row_errors, "
                "student_profiles, section_memberships, batches CASCADE"
            )
        )
        # University settings are configuration, not per-test data: restore the
        # shipped defaults so a test that retunes one cannot leak into the next.
        for key, value in DEFAULT_UNIVERSITY_SETTINGS.items():
            await conn.execute(
                text(
                    "INSERT INTO university_settings (key, value) VALUES (:k, :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"k": key, "v": value},
            )

    from unicore.core.redis import get_redis

    get_redis.cache_clear()
    redis_client = get_redis()
    await redis_client.flushdb()
    yield
    await redis_client.aclose()
    get_redis.cache_clear()
    await engine.dispose()


@pytest.fixture
def make_client(db: None) -> Callable[..., httpx.AsyncClient]:
    """Client factory authenticated as `user_id`; university-scope roles are
    self-seeded as grants, scoped roles must be granted via the API first."""

    def _make(*roles: str, user_id: str = "test-actor") -> httpx.AsyncClient:
        token = uuid_mod.uuid4().hex
        _token_map[token] = (user_id, roles)
        app = create_app()  # registers the real verifier...
        register_token_verifier(_dispatch_verifier)  # ...the dispatcher wraps it
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )

    return _make


@pytest.fixture
async def audit_rows(db: None) -> Callable[..., object]:
    """Query helper: audit events for an action, newest first."""

    async def _rows(action: str) -> list[dict[str, object]]:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                text(
                    "SELECT actor, action, object_type, object_id, before, after "
                    "FROM audit_events WHERE action = :action ORDER BY occurred_at DESC"
                ),
                {"action": action},
            )
            return [dict(row._mapping) for row in result]

    return _rows


# A full org tree with an approved term and Sections. Lives here rather than in
# tests/onboarding so org and timetable tests can use it too.
@pytest.fixture
async def campus(make_client: Callable[..., httpx.AsyncClient]) -> dict[str, str]:
    """University → FET → SOCE (School) → CSE (Dept) → BT-CSE (Program) → Sections 3A/3B,
    with an approved 2026-S1 term. Returns the ids by name."""
    async with make_client("super-admin") as admin:

        async def unit(**body: object) -> dict:
            response = await admin.post("/org/units", json=body)
            assert response.status_code == 201, response.text
            return response.json()

        uni = await unit(type="university", name="U", code="UNI")
        fd = await unit(type="faculty_division", name="FET", code="FET", parent_id=uni["id"])
        school = await unit(
            cadence="semester", type="school", name="SOCE", code="SOCE", parent_id=fd["id"]
        )
        dept = await unit(type="department", name="CSE", code="CSE", parent_id=school["id"])
        program = await unit(type="program", name="BTech CSE", code="BT-CSE", parent_id=dept["id"])
        other_dept = await unit(type="department", name="MEC", code="MEC", parent_id=school["id"])
        other_program = await unit(
            type="program", name="BTech MECH", code="BT-MECH", parent_id=other_dept["id"]
        )

        term = await admin.post(
            "/timetable/terms",
            json={
                "school_id": school["id"],
                "term_code": "2026-S1",
                "start_date": "2026-07-01",
                "end_date": "2026-11-30",
            },
        )
        assert term.status_code == 201, term.text
        term_id = term.json()["id"]

    async with make_client("super-admin", user_id="school.incharge") as si:
        approved = await si.post(f"/timetable/terms/{term_id}/approve")
        assert approved.status_code == 200, approved.text

    async with make_client("super-admin") as admin:
        sections = {}
        for label in ("3A", "3B"):
            created = await admin.post(
                "/timetable/sections",
                json={"program_id": program["id"], "label": label, "term_code": "2026-S1"},
            )
            assert created.status_code == 201, created.text
            sections[label] = created.json()["id"]

        # A Section in a sibling Department — the target of scope-isolation checks.
        other = await admin.post(
            "/timetable/sections",
            json={"program_id": other_program["id"], "label": "1A", "term_code": "2026-S1"},
        )
        assert other.status_code == 201, other.text

    return {
        "university": uni["id"],
        "school": school["id"],
        "department": dept["id"],
        "program": program["id"],
        "other_department": other_dept["id"],
        "other_program": other_program["id"],
        "other_section": other.json()["id"],
        "term_id": term_id,
        "section_3a": sections["3A"],
        "section_3b": sections["3B"],
    }

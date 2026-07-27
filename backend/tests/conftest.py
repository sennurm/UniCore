import os
import subprocess
import sys
from pathlib import Path

# Must be set before any unicore import caches Settings.
os.environ.setdefault(
    "UNICORE_DATABASE_URL", "postgresql+asyncpg://unicore:unicore@localhost:5432/unicore_test"
)

from collections.abc import AsyncIterator, Callable  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from unicore.core import security  # noqa: E402
from unicore.core.db import get_engine, get_sessionmaker  # noqa: E402
from unicore.core.security import AuthContext, register_token_verifier  # noqa: E402
from unicore.main import create_app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
ADMIN_DSN = "postgresql://unicore:unicore@localhost:5432/unicore"
TEST_DB = "unicore_test"


@pytest.fixture(autouse=True)
def _reset_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_token_verifier", None)


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
        await conn.execute(text("TRUNCATE org_units, users, audit_events CASCADE"))
    yield
    await engine.dispose()


@pytest.fixture
def make_client(db: None) -> Callable[..., httpx.AsyncClient]:
    """Client factory authenticated with the given roles via a fake token verifier."""

    def _make(*roles: str, user_id: str = "test-actor") -> httpx.AsyncClient:
        async def verifier(token: str) -> AuthContext:
            return AuthContext(user_id=user_id, session_id="test-session", role_names=roles)

        register_token_verifier(verifier)
        app = create_app()
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
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

# ============================================================
# PaySentinelIQ — Pytest Configuration
# Async test fixtures, database setup, mock factories
# ============================================================

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── SQLite JSONB compatibility ────────────────────────────────────────
# Enable JSONB-type columns (PostgreSQL-only) to work in SQLite test DBs
# by mapping the JSONB compiler method to the existing JSON compiler.
if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON

from app.main import create_app
from app.shared.base_model import Base
from app.shared.settings import get_settings


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create a test database engine using an in-memory SQLite database."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    # ── gen_random_uuid() shim ──────────────────────────────────────
    # Base models use server_default=gen_random_uuid() (PostgreSQL).
    # SQLite has no such function — register a UUIDv4 generator so
    # inserts without an explicit PK work in tests.
    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_connection, connection_record):
        dbapi_connection.create_function(
            "gen_random_uuid", 0, lambda: str(uuid.uuid4())
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional test database session."""
    session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def session_factory(test_engine):
    """Provide a session factory bound to the shared test engine.

    Used by messaging handlers/workers, which create their own sessions
    internally (mirrors production behavior).
    """
    return async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client for the FastAPI app."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def mock_tenant_id() -> str:
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def mock_user_id() -> str:
    return "00000000-0000-0000-0000-000000000002"

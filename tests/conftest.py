"""
Shared pytest fixtures + helpers for the SARMAAN / ERITAS MDA QA suite.

Design notes
------------
* asyncio_mode = auto (see pytest.ini) — async test functions and the async
  fixtures below run without an explicit marker.

* The `client` fixture talks to the FastAPI app **in-process** via
  httpx.ASGITransport by default, so file/route/contract tests need no running
  server. Set TEST_BASE_URL (e.g. http://localhost:8090) to instead hit a live
  container.

* Tests that genuinely need a database or seeded users depend on `require_db`
  / `auth_headers`; those fixtures `pytest.skip(...)` when the live stack is not
  reachable instead of hard-failing. This mirrors the report's "Known
  Limitations" (Domains 3, 4, 9, 11 require the live stack).

* All `import app.*` happens lazily inside fixtures/helpers so test collection
  works even when the application's heavy dependencies are absent.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

try:
    import pytest_asyncio
except ImportError:  # pragma: no cover - pytest-asyncio is a hard test dep
    pytest_asyncio = None


# ── Filesystem layout ────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"
STATIC_DIR = REPO_ROOT / "static"
TERRAFORM_DIR = REPO_ROOT / "terraform"
DEPLOY_DIR = REPO_ROOT / "deploy"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def read_text(relpath: str | Path) -> str:
    """Read a repo-relative file as UTF-8 text (skips the test if absent)."""
    p = (REPO_ROOT / relpath) if not Path(relpath).is_absolute() else Path(relpath)
    if not p.exists():
        pytest.skip(f"required file missing: {relpath}")
    return p.read_text(encoding="utf-8")


def terraform_text() -> str:
    """Concatenate every terraform/*.tf file into one searchable string."""
    if not TERRAFORM_DIR.exists():
        pytest.skip("terraform/ directory missing")
    return "\n".join(
        f.read_text(encoding="utf-8") for f in sorted(TERRAFORM_DIR.glob("*.tf"))
    )


# ── Liveness probes ──────────────────────────────────────────────────────────
def _host_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def db_reachable() -> bool:
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://geouser:geopass@localhost:5432/geospatial_tracker",
    )
    parsed = urlparse(url)
    return _host_port_open(parsed.hostname or "localhost", parsed.port or 5432)


def redis_reachable() -> bool:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    parsed = urlparse(url)
    return _host_port_open(parsed.hostname or "localhost", parsed.port or 6379)


# ── App + HTTP client ────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def app():
    """The FastAPI application object (skips if it cannot be imported)."""
    try:
        from app.main import app as fastapi_app
    except Exception as exc:  # noqa: BLE001 - any import error => skip
        pytest.skip(f"could not import app.main:app ({exc})")
    return fastapi_app


if pytest_asyncio is not None:

    @pytest_asyncio.fixture
    async def client(app):
        """httpx.AsyncClient bound to the app (in-process) or TEST_BASE_URL."""
        import httpx

        base_url = os.getenv("TEST_BASE_URL")
        if base_url:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
                yield c
        else:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver", timeout=10.0
            ) as c:
                yield c

    async def _login(client, username: str, password: str) -> str | None:
        resp = await client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("access_token")

    @pytest_asyncio.fixture
    async def admin_token(client):
        token = await _login(client, "admin", "admin123")
        if not token:
            pytest.skip("admin login unavailable (needs live seeded DB)")
        return token

    @pytest_asyncio.fixture
    async def auth_headers(admin_token):
        return {"Authorization": f"Bearer {admin_token}"}

    @pytest_asyncio.fixture
    async def superadmin_token(client):
        token = await _login(
            client,
            os.getenv("SUPERADMIN_USERNAME", "superadmin"),
            os.getenv("SUPERADMIN_PASSWORD", "superadmin123"),
        )
        if not token:
            pytest.skip("superadmin login unavailable (needs live seeded DB)")
        return token

    @pytest_asyncio.fixture
    async def superadmin_headers(superadmin_token):
        return {"Authorization": f"Bearer {superadmin_token}"}


@pytest.fixture
def require_db():
    """Skip the test unless a PostgreSQL server is reachable."""
    if not db_reachable():
        pytest.skip("live PostgreSQL not reachable (set DATABASE_URL)")


@pytest.fixture
def require_redis():
    """Skip the test unless a Redis server is reachable."""
    if not redis_reachable():
        pytest.skip("live Redis not reachable (set REDIS_URL)")

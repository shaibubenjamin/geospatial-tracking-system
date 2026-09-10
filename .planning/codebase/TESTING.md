# Testing Patterns

**Analysis Date:** 2026-09-08

## Test Framework

**Runner:**
- Framework: **pytest** 8.3.3
- Async support: **pytest-asyncio** 0.24.0 with `asyncio_mode = auto` (no `@pytest.mark.asyncio` decorator needed)
- Coverage: **pytest-cov** 5.0.0
- Config: `pytest.ini` in repo root

**Assertion Library:**
- Standard Python `assert` statements
- FastAPI HTTPException for error validation
- pytest.raises() context manager for exception testing

**Run Commands:**
```bash
# Run all tests
pytest

# Run tests matching a pattern
pytest tests/test_02_api_backend.py
pytest tests/test_02_api_backend.py::TestProjectsEndpoints

# Watch mode (requires pytest-watch, not in deps)
pytest-watch

# Coverage report
pytest --cov=app tests/

# Run with logging output
pytest -s tests/test_04_auth_permissions.py

# Run only livedb tests (those marked @pytest.mark.livedb)
pytest -m livedb
```

**Configuration (`pytest.ini`):**
```ini
[pytest]
asyncio_mode = auto              # Async tests run without decorator
testpaths = tests                # Test directory
python_files = test_*.py         # Test file pattern
python_classes = Test*           # Test class pattern
python_functions = test_*        # Test function pattern
addopts = -ra                    # Show all test outcomes
filterwarnings =
    ignore::DeprecationWarning
    ignore::PendingDeprecationWarning
markers =
    livedb: test requires a reachable PostgreSQL + full Docker stack
    redis: test requires a reachable Redis
```

## Test File Organization

**Location:**
- All tests in `tests/` directory (sibling to `app/`)
- Named by domain: `test_01_frontend_foundation.py`, `test_02_api_backend.py`, etc.
- 14 domain files totaling ~3,300 lines of test code

**Naming:**
- Test files: `test_<NN>_<domain>_<description>.py` (e.g., `test_02_api_backend.py`, `test_04_auth_permissions.py`)
- Test classes: `Test<Feature>` (e.g., `TestHealth`, `TestProjectsEndpoints`, `TestPasswordHashing`)
- Test functions: `test_<behavior_description>` (e.g., `test_health_returns_200`, `test_verify_returns_false_for_wrong_password`)

**Structure:**
```
tests/
├── conftest.py                        # Shared fixtures, helpers, config
├── test_01_frontend_foundation.py     # Static assets, HTML, CSP headers
├── test_02_api_backend.py             # FastAPI routes, CRUD, contract
├── test_03_database_storage.py        # SQLAlchemy models, CRUD, constraints
├── test_04_auth_permissions.py        # JWT, password hashing, RBAC
├── test_08_security_rls.py            # SQL injection, response safety, CORS
├── test_09_rate_limiting.py           # Rate limit headers, slowapi
└── requirements-test.txt              # Test-only deps (pytest, pytest-asyncio, pytest-cov, httpx, anyio)
```

## Test Structure

**Suite Organization:**
```python
class TestPasswordHashing:
    """Exercises password hashing (bcrypt) — isolated, no DB needed."""

    def test_hash_has_bcrypt_prefix(self):
        """Verify bcrypt format; no external dependencies."""
        from app.routes.auth import hash_password

        hashed = hash_password("hunter2")
        assert hashed.startswith("$2b$"), f"expected bcrypt $2b$ prefix, got {hashed[:4]!r}"

    def test_verify_returns_true_for_correct_password(self):
        """Password verification matches original."""
        from app.routes.auth import hash_password, verify_password

        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed) is True
```

**Patterns:**

**1. Setup/Teardown:**
- No per-test setup/teardown; tests are independent and stateless
- DB tests use fixtures that skip when Postgres unavailable: `async def test_create_read_delete(self, require_db)`
- Cleanup done in try/finally within tests, not in teardown:
  ```python
  async def test_duplicate_slug_raises_integrity_error(self, require_db):
      slug = f"qa-uniq-{_uuid.uuid4().hex[:12]}"
      created_id = None
      try:
          async with AsyncSessionLocal() as session:
              session.add(GeoProject(name="First", slug=slug))
              await session.commit()
              created_id = (
                  await session.execute(
                      select(GeoProject.id).where(GeoProject.slug == slug)
                  )
              ).scalar_one()
      finally:
          # cleanup the first row regardless of outcome
          if created_id is not None:
              async with AsyncSessionLocal() as session:
                  obj = await session.get(GeoProject, created_id)
                  if obj is not None:
                      await session.delete(obj)
                      await session.commit()
  ```

**2. Assertion Pattern:**
- Always include assertion message with context (actual value, expected value, reason):
  ```python
  assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
  assert hashed.startswith("$2b$"), f"expected bcrypt $2b$ prefix, got {hashed[:4]!r}"
  assert isinstance(body, list), f"expected a JSON list, got {type(body)}"
  ```
- Compare explicitly: `assert x is True`, not `assert x` for booleans
- Use context manager for exception testing: `with pytest.raises(HTTPException) as exc:`

**3. Test Comments:**
- Docstrings explain the test's purpose and why it matters
- Inline comments clarify non-obvious setup or edge cases:
  ```python
  async def test_list_projects_with_auth_works(self, client, require_db, auth_headers):
      # require_db SKIPs fast on a closed Postgres port; auth_headers SKIPs if
      # the DB is up but the seeded admin login is unavailable.
      resp = await client.get("/api/projects", headers=auth_headers)
  ```

## Mocking

**Framework:** No mocking library (unittest.mock not used); tests use real fixtures instead

**Patterns:**
- **In-process HTTP testing via fixture:** `client` fixture uses `httpx.ASGITransport` to bind directly to FastAPI app
  ```python
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
  ```
  - Default: in-process (no real server), fast, synchronous with app code
  - Optional: real server via `TEST_BASE_URL` env var for integration testing

- **Real database for DB tests:** Tests that need data use `require_db` fixture which SKIPs if Postgres unreachable
  ```python
  async def test_create_read_delete(self, require_db):
      # require_db auto-skips this test if Postgres not reachable
      async with AsyncSessionLocal() as session:
          proj = GeoProject(name="QA CRUD Project", slug=slug)
          session.add(proj)
          await session.commit()
  ```

- **Skip instead of fail when external dependencies absent:**
  - `require_db` fixture: `pytest.skip("live PostgreSQL not reachable (set DATABASE_URL)")`
  - `auth_headers` fixture: `pytest.skip("admin login unavailable (needs live seeded DB)")`
  - `require_redis` fixture: `pytest.skip("live Redis not reachable (set REDIS_URL)")`
  - Allows test collection without running dependencies; tests report as SKIPPED (not FAILED)

- **No dependency injection / monkeypatching:** Tests work with real config, real app instance
  - Exception: `TEST_BASE_URL` env var switches between in-process and live server

**What to Mock:**
- Generally: Don't. Use real fixtures and skip if external resource unavailable
- If needed: `pytest.importorskip()` to skip tests requiring optional packages

**What NOT to Mock:**
- Database queries: use real DB (with `require_db` skip)
- HTTP calls to own app: use `client` fixture (in-process by default)
- Authentication: use real login via `auth_headers` fixture
- Config values: read from environment, not mocked

## Fixtures and Factories

**Test Data:**
```python
# No factories; tests create minimal objects inline
async def test_duplicate_slug_raises_integrity_error(self, require_db):
    slug = f"qa-uniq-{_uuid.uuid4().hex[:12]}"
    async with AsyncSessionLocal() as session:
        session.add(GeoProject(name="First", slug=slug))
        await session.commit()
```

**Location (`tests/conftest.py`):**
- `REPO_ROOT`, `APP_DIR`, `STATIC_DIR`: Filesystem paths
- `read_text(relpath)`: Load repo-relative file (skips if missing)
- `terraform_text()`: Concatenate all terraform/*.tf files
- `db_reachable()`, `redis_reachable()`: Liveness probes
- `app`: Fixture returning FastAPI app instance
- `client`: Async httpx.AsyncClient bound to app (or TEST_BASE_URL if set)
- `admin_token`, `auth_headers`, `superadmin_token`, `superadmin_headers`: Auth tokens from login
- `require_db`, `require_redis`: Skip tests if external resource unavailable

**Fixture Usage Pattern:**
```python
async def test_list_projects_with_auth_works(self, client, require_db, auth_headers):
    # Declare fixtures as parameters; pytest provides them
    resp = await client.get("/api/projects", headers=auth_headers)
    assert resp.status_code == 200
```

## Coverage

**Requirements:** No hard target enforced; coverage report generated by pytest-cov

**View Coverage:**
```bash
pytest --cov=app tests/
pytest --cov=app --cov-report=html tests/  # Generates htmlcov/index.html
```

**Coverage Notes:**
- Domain tests cover: contract (routing, status codes), security (auth, SQL injection), data (CRUD, constraints), infrastructure (config, deployment)
- Some modules not directly tested (e.g., heavy async sync work); tested via integration
- Import guards allow test collection without dependencies: `pytest.skip()` not raised until fixture accessed

## Test Types

**Unit Tests:**
- Scope: Pure functions, no I/O (password hashing, JWT encoding, utility helpers)
- Approach: Call function, assert result
- Example: `test_hash_has_bcrypt_prefix`, `test_verify_returns_true_for_correct_password`, `test_token_has_three_segments`
- No database, no network, no file I/O

**Integration Tests:**
- Scope: API routes + database + auth (end-to-end handler behavior)
- Approach: Call endpoint via `client`, check response contract and side effects
- Example: `test_list_projects_with_auth_works`, `test_create_project_without_token_is_401`, `test_duplicate_slug_raises_integrity_error`
- Live database required (skip if unavailable)

**Domain Tests (Quasi-E2E):**
- Scope: All 14 domains (frontend, API, database, auth, security, rate-limiting, caching, error tracking, availability, APK versioning)
- Approach: Each `test_NN_*.py` file tests a complete system concern end-to-end
- Example: test_02 exercises routes + contracts + error responses; test_08 exercises SQL injection, JWT tampering, CORS
- Live dependencies required (Postgres, Redis for marked tests)

**E2E Tests:**
- Framework: Not used in repo
- Rationale: Integration tests cover HTTP + database; Android app tests live in `android/` (separate CI)

## Common Patterns

**Async Testing:**
```python
async def test_health_returns_200(self, client):
    """Async test function; no @pytest.mark.asyncio needed (asyncio_mode=auto)."""
    resp = await client.get("/api/health")  # await required for async function calls
    assert resp.status_code == 200
```

**Error Testing:**
```python
def test_expired_token_raises_401(self):
    """Test that invalid tokens raise HTTPException with proper status."""
    from datetime import timedelta
    from fastapi import HTTPException
    from app.routes.auth import create_access_token, decode_token

    token = create_access_token({"sub": "carol"}, expires_delta=timedelta(minutes=-5))
    with pytest.raises(HTTPException) as exc:
        decode_token(token)
    assert exc.value.status_code == 401
```

**Conditional Skipping (External Dependency):**
```python
async def test_list_projects_returns_200(self, client, require_db):
    # Test is skipped (not failed) if Postgres unreachable
    resp = await client.get("/api/projects")
    assert resp.status_code == 200

async def test_get_missing_project_with_auth_is_404(self, client, require_db, auth_headers):
    # Requires BOTH database AND authenticated session
    resp = await client.get("/api/projects/999999", headers=auth_headers)
    assert resp.status_code == 404
```

**JSON Response Validation:**
```python
async def test_health_status_ok(self, client):
    resp = await client.get("/api/health")
    body = resp.json()  # Parse JSON response
    assert body.get("status") == "ok", f"status should be 'ok', got {body!r}"
    assert "service" in body, f"health body should expose 'service', got {body!r}"
```

**SQL Injection Testing (Parametrized Queries):**
```python
async def test_classic_or_true_payload(self, client, require_db):
    """Verify login endpoint treats injection payloads as plain data."""
    resp = await client.post(
        "/api/auth/login", json={"username": "' OR '1'='1", "password": "x"}
    )
    assert resp.status_code in (401, 422), f"unexpected status {resp.status_code}"
    assert resp.status_code != 500, "injection payload caused a 500"
```

**Module Import Without Heavy Dependencies:**
```python
def test_engine_url_uses_asyncpg(self):
    """Import happens inside test; collection succeeds even without Postgres."""
    import app.database as db
    url = db.engine.url
    assert url.get_backend_name() == "postgresql"
```

---

*Testing analysis: 2026-09-08*

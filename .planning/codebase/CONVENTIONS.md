# Coding Conventions

**Analysis Date:** 2026-09-08

## Naming Patterns

**Files:**
- `snake_case.py` for all Python modules (e.g., `auth.py`, `spatial_engine.py`, `commcare_sync.py`)
- Test files: `test_<domain>_<name>.py` numbered by domain (e.g., `test_01_frontend_foundation.py`, `test_02_api_backend.py`)
- Route files organized by feature: `auth.py`, `projects.py`, `mda.py`, `analytics.py`
- Service files by responsibility: `aggregation_engine.py`, `commcare_sync.py`, `spatial_engine.py`

**Functions:**
- `snake_case` for all function and method names
- Examples: `hash_password()`, `verify_password()`, `get_current_user()`, `get_lga_metrics()`, `decode_token()`
- Private functions prefixed with `_` when internal to module: `_host_port_open()`, `_int_env()`, `_login()`, `_resolve_boundary_pid()`
- Async functions use `async def` with `await` for all awaitable calls

**Classes:**
- `PascalCase` for all class names
- SQLAlchemy models: `User`, `GeoProject`, `LGA`, `Ward`, `Settlement`, `UploadBatch`
- Pydantic schemas: `TokenResponse`, `UserCreate`, `UserOut`, `ProjectCreate`, `ProjectUpdate`, `LoginRequest`
- No special prefixes; inheritance is clear from base class

**Variables:**
- `snake_case` for all variable and attribute names
- Constants in `UPPER_CASE`: `DATABASE_URL`, `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `MIN_VERSION_CODE`
- Type hints consistently applied: `user: User`, `project_id: int`, `allowed_lgas: Optional[set]`
- Prefix private class attributes with `_` when needed: `_root_logger`, `_log_handler`

**Dictionaries and Complex Types:**
- Use type hints for Dict, List, Optional: `Dict[str, Any]`, `List[Dict[str, Any]]`, `Optional[set]`
- SQL parameter dicts follow pattern: `params: Dict[str, Any] = {"key": value, ...}`

## Code Style

**Formatting:**
- Python 3.11 target
- PEP 8 compliant; enforced via Ruff linter
- 4-space indentation (never tabs)
- Line length: soft limit ~100 chars; Ruff E501 ignored (no hard enforcement)
- No trailing whitespace (W291, W293 ignored by Ruff but cleaned)

**Linting:**
- Tool: **Ruff** (covers flake8 + black + isort)
- Configuration: `.github/workflows/ci.yml` defines rules
- Informational checks: E, F, W (except E501, E402, F401, F841) — non-blocking
- Fatal checks: F821 (undefined name), F823 (use-before-def), F811 (redefinition), E9 (syntax errors) — blocks CI
- Unused imports (F401) and unused locals (F841) tolerated until cleanup pass
- Exit code enforced only for fatal checks; informational uses `--exit-zero`

**Docstrings:**
- Module-level docstrings at file top: triple-quoted, describe purpose and key behavior
- Function/method docstrings for all public functions and complex logic:
  ```python
  def get_lga_metrics(
      project_id: int,
      db: AsyncSession,
  ) -> List[Dict[str, Any]]:
      """Roll up settlement analytics to LGA level for the given round.
      
      Definitions (per the May 2026 campaign-team agreement):
      * A settlement is **visited** when ≥ 1 GPS point falls inside its polygon
      * Per-settlement **completeness** stays as `visited_grids / total_grids`
      * LGA-level **completeness** is the *average* of per-settlement values
      """
  ```
- Pydantic schema docstrings explain field semantics:
  ```python
  class UserCreate(BaseModel):
      # CSV of state names this account may access (e.g. "Sokoto" or "Sokoto,Kano").
      # Null/empty = no implicit access (superadmins always see all states).
      allowed_states: Optional[str] = None
  ```

## Import Organization

**Order:**
1. **Standard library imports:** `import os`, `import json`, `from typing import ...`, `from datetime import ...`
2. **Third-party imports:** `from fastapi import ...`, `from sqlalchemy import ...`, `import bcrypt`, `import jwt`
3. **Local app imports:** `from app.config import ...`, `from app.models import ...`, `from app.database import ...`
4. Blank line between each group

**Path Aliases:**
- No path aliases configured; use absolute imports from `app` root
- Example: `from app.routes.auth import get_current_user`
- Circular imports avoided by lazy import in fixtures/functions: `from app.services.project_scope import in_scope_lgas_for  # local import — avoids circular`

**Lazy Imports:**
- Heavy/slow dependencies imported inside functions/fixtures to allow test collection without them:
  ```python
  @pytest.fixture(scope="session")
  def app():
      try:
          from app.main import app as fastapi_app
      except Exception as exc:
          pytest.skip(f"could not import app.main:app ({exc})")
      return fastapi_app
  ```

## Error Handling

**HTTP Errors:**
- Use `HTTPException` from FastAPI for all API errors
- Always include status code and detail message:
  ```python
  raise HTTPException(status_code=401, detail="Invalid username or password")
  raise HTTPException(status_code=403, detail="Admin access required")
  raise HTTPException(status_code=409, detail=f'Username "{username}" is already taken.')
  ```
- Status codes follow REST conventions: 401 (unauthorized), 403 (forbidden), 404 (not found), 409 (conflict), 422 (validation error), 500 (server error)
- Never expose raw tracebacks or internal details in responses
- Validate input pre-check before database operations to return clear 409 (conflict) instead of raw 500 from DB constraint

**Database Errors:**
- Catch `sqlalchemy.exc.IntegrityError` for constraint violations and convert to HTTPException:
  ```python
  try:
      await db.commit()
  except IntegrityError:
      await db.rollback()
      raise HTTPException(status_code=409, detail="That username or email is already in use.")
  ```
- Always rollback session after error: `await db.rollback()`
- Use `.scalar_one_or_none()` to handle missing rows without exceptions

**Async Exception Handling:**
- Use try/except blocks around async operations:
  ```python
  try:
      await db.commit()
  except Exception:
      await db.rollback()
      raise
  finally:
      await db.close()
  ```
- Re-raise after cleanup unless error is expected/handled
- All database operations are async with `await`

## Logging

**Framework:** Python standard `logging` module

**Setup:**
- Structured JSON logging in `app.main._JsonFormatter`
- Each log line is one JSON object: `{"ts": "...", "level": "INFO", "logger": "...", "msg": "...", "request_id": "...", "exc": "..."}`
- Root logger configured at module level in `app/main.py` before imports
- Application logger: `logger = logging.getLogger(__name__)` in each module

**Patterns:**
- Log startup/shutdown events at INFO level: `logger.info("Starting up — creating database tables...")`
- Log unexpected conditions at WARNING level: `logger.warning("SYNC_ENCRYPTION_KEY is not set — CommCare sync will fail...")`
- Skip tracebacks in normal operation; surface specific context:
  ```python
  logger.warning("GPS poor-accuracy backfill skipped: %s", e)  # Not logger.exception()
  ```
- Never log secrets; reference by name only: `logger.info("Using SECRET_KEY of length %d", len(SECRET_KEY))`

**Log Levels:**
- DEBUG: Verbose operation details (when needed)
- INFO: Important events (startup, sync progress, data backfill counts)
- WARNING: Recoverable errors, missing optional config, skipped operations
- ERROR: Unrecoverable errors (surfaces to Sentry)
- CRITICAL: System cannot continue (boot failures)

## Comments

**When to Comment:**
- Section dividers for major code blocks: `# ── Liveness probes ──────────────────────────────────────────────────────────`
- Complex SQL queries and logic that isn't obvious from code:
  ```python
  # SPATIAL scope, not name-based: keeps ~780 border-mislabelled settlements
  # (labelled with a planned LGA name but polygon inside a hollow LGA) from
  # painting inside supposedly-empty LGAs on the map.
  ```
- Explain WHY, not WHAT: "Cast completeness to numeric for rounding" is better than "Convert to numeric"
- Clarify non-obvious parameter passing or data transformations
- Note temporary workarounds or performance constraints

**Avoid Comments:**
- Don't restate what obvious code does: `x = x + 1  # increment x`
- Don't comment out dead code; delete it
- Don't add comments for every line

**Inline Comments:**
- Use sparingly; prefer clear variable/function names
- Separate from code with 2+ spaces: `result = process(data)  # cache for later use`

## Function Design

**Size:**
- Keep functions focused on a single responsibility
- Service functions typically 30-100 lines; break into helpers if longer
- Route handlers typically 15-40 lines; move logic to services
- Example service function: `get_lga_metrics()` ~60 lines (aggregation query)

**Parameters:**
- Use type hints for all parameters: `async def get_ward_metrics(project_id: int, db: AsyncSession, lgacode: Optional[str] = None)`
- Keep parameters ≤5; use dataclass or schema for >5 related params
- Dependency injection for database: `db: AsyncSession = Depends(get_db)` in route handlers
- Use keyword-only after `*` when multiple optional params: `def func(required, *, optional1=None, optional2=None)`

**Return Values:**
- Use type hints for return types: `-> List[Dict[str, Any]]`, `-> TokenResponse`, `-> None`
- For HTTP handlers, return Pydantic models which FastAPI serializes to JSON
- For database queries, return `Dict[str, Any]` for unstructured results, or SQLAlchemy models for ORM results
- Use `Optional[T]` for nullable returns, not `Union[T, None]`

**Async Functions:**
- All database operations are async; use `async def` and `await`
- Database fixture functions are async: `async def client(app):`
- Test functions are async when they use async fixtures: `async def test_health_returns_200(self, client):`
- Always `await` I/O: `await db.commit()`, `await client.get("/api/health")`

## Module Design

**Exports:**
- No explicit `__all__` lists; publicly import what should be exposed
- Module structure: routes import from services; services are lower-level helpers
- Example hierarchy: `routes/auth.py` → `services/` (lower level)

**Barrel Files:**
- No barrel-file pattern; each module imports what it needs explicitly
- `app/__init__.py` is empty (just marks package)
- Route modules include themselves in `app/main.py`: `from app.routes import auth, projects, boundaries, ...`

**Organization by Domain:**
- Routes organized by feature: `auth`, `projects`, `boundaries`, `mda`, `sync`, `analytics`
- Services organized by responsibility: `commcare_sync`, `spatial_engine`, `aggregation_engine`, `geo_cache`
- Models (`app/models.py`), schemas (`app/schemas.py`), config (`app/config.py`) centralized
- Tests mirror domain structure with numbered domains: test_01 (frontend), test_02 (API), test_03 (database), etc.

---

*Convention analysis: 2026-09-08*

"""
main.py — Geospatial Coverage & Data Quality Monitoring System
FastAPI application entry point.
"""
import json
import logging
import os
import time
from contextlib import asynccontextmanager


# ── Structured JSON logging ─────────────────────────────────────────────────
# Configure the root logger to emit one JSON object per line so CloudWatch
# (and Sentry, Loki, Datadog, …) can index by level/module/request_id.
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Optional fields if present
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_JsonFormatter())
_root_logger = logging.getLogger()
_root_logger.handlers = [_log_handler]
_root_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select, text

from app.database import create_all_tables, AsyncSessionLocal
from app.routes.auth import hash_password
from app.models import User, GeoProject
from app.config import SUPERADMIN_USERNAME, SUPERADMIN_PASSWORD, SUPERADMIN_EMAIL
from app.routes import auth, projects, boundaries, ingestion, analytics, qc, sync
from app.routes import mda as mda_route

# Root logger already configured above as JSON; basicConfig is a no-op now.
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, seed default admin user and Sokoto project."""
    logger.info("Starting up — creating database tables...")
    await create_all_tables()

    # Idempotent column additions for MDA tables (new flags added post-launch)
    async with AsyncSessionLocal() as db:
        for stmt in [
            "ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS flag_duplicate_gps BOOLEAN DEFAULT FALSE",
            "ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS flag_gps_outside_ward BOOLEAN DEFAULT FALSE",
            "ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS flag_gps_outside_state BOOLEAN DEFAULT FALSE",
            "ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS check_treatment_date DATE",
            "ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS hq_user TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN DEFAULT FALSE",
            # Phase 4a — state + round on geo_projects, project_id on MDA tables
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS state_name TEXT",
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS round_number INTEGER",
            "ALTER TABLE mda_households ADD COLUMN IF NOT EXISTS project_id INTEGER",
            "ALTER TABLE mda_individuals ADD COLUMN IF NOT EXISTS project_id INTEGER",
            "ALTER TABLE mda_baseline ADD COLUMN IF NOT EXISTS project_id INTEGER",
            "ALTER TABLE mlos_settlements ADD COLUMN IF NOT EXISTS project_id INTEGER",
            "CREATE INDEX IF NOT EXISTS idx_mda_households_project_id ON mda_households (project_id)",
            "CREATE INDEX IF NOT EXISTS idx_mda_individuals_project_id ON mda_individuals (project_id)",
            "CREATE INDEX IF NOT EXISTS idx_mda_baseline_project_id ON mda_baseline (project_id)",
            "CREATE INDEX IF NOT EXISTS idx_mlos_settlements_project_id ON mlos_settlements (project_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_geo_projects_state_round ON geo_projects (state_name, round_number)",
            # R5+: age/sex target breakdown on mda_baseline
            "ALTER TABLE mda_baseline ADD COLUMN IF NOT EXISTS target_1_11_f INTEGER",
            "ALTER TABLE mda_baseline ADD COLUMN IF NOT EXISTS target_1_11_m INTEGER",
            "ALTER TABLE mda_baseline ADD COLUMN IF NOT EXISTS target_12_59_f INTEGER",
            "ALTER TABLE mda_baseline ADD COLUMN IF NOT EXISTS target_12_59_m INTEGER",
            # Index supporting the per-household individual deletion during sync
            "CREATE INDEX IF NOT EXISTS idx_mda_individuals_hh_formid_proj ON mda_individuals (project_id, hh_formid)",
            # Sync progress + history
            "ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS last_progress_step INTEGER",
            "ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS last_progress_total INTEGER",
            # Onprem mirror progress (added later than the original table)
            "ALTER TABLE onprem_mirror_state ADD COLUMN IF NOT EXISTS last_progress_step INTEGER",
            "ALTER TABLE onprem_mirror_state ADD COLUMN IF NOT EXISTS last_progress_total INTEGER",
            "ALTER TABLE onprem_mirror_state ADD COLUMN IF NOT EXISTS last_progress_label TEXT",
            """CREATE TABLE IF NOT EXISTS sync_history (
                id SERIAL PRIMARY KEY,
                project_id INTEGER REFERENCES geo_projects(id),
                started_at TIMESTAMPTZ DEFAULT NOW(),
                ended_at TIMESTAMPTZ,
                status TEXT DEFAULT 'running',
                rows_fetched INTEGER DEFAULT 0,
                error_message TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_sync_history_project_started ON sync_history (project_id, started_at DESC)",
            # Project's official campaign start (overrides earliest received_on)
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS campaign_start_date DATE",
            # Project's official campaign end (for "Day X of N" displays)
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS campaign_end_date DATE",
            # Auto-sync scheduler (sync_worker checks every minute and enqueues
            # when (now - last_synced_at) >= interval). Opt-in per project.
            "ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS auto_sync_enabled BOOLEAN DEFAULT FALSE",
            "ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS auto_sync_interval_minutes INTEGER DEFAULT 60",
            # Cooperative stop signal. Set TRUE by /api/sync/stop; the
            # per-set loop in run_sync polls it between sets and exits cleanly
            # if true. Cleared at sync start.
            "ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN DEFAULT FALSE",
            # Count of NEW (post-watermark-filter) rows actually written this
            # run — vs sync_history.rows_fetched which is the raw CommCare
            # row count. The UI shows rows_new on the history table because
            # that's the number the operator cares about.
            "ALTER TABLE sync_history ADD COLUMN IF NOT EXISTS rows_new INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(text(stmt))
            except Exception:
                pass
        await db.commit()

    # One-time backfill: GPS-accuracy threshold (set to 20 m for R5).
    # Scoped to the ACTIVE project only — R4 historical data stays at its
    # original threshold so existing reports aren't retroactively rewritten.
    # This runs every startup but is a no-op once the flag matches; the
    # WHERE clause ensures we only update rows whose flag is stale.
    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("""
                UPDATE mda_households h
                SET flag_gps_poor_accuracy = (h.gps_accuracy > 20)
                WHERE h.project_id IN (
                        SELECT id FROM geo_projects WHERE is_active = TRUE
                      )
                  AND h.gps_accuracy IS NOT NULL
                  AND h.flag_gps_poor_accuracy IS DISTINCT FROM (h.gps_accuracy > 20)
            """))
            await db.commit()
            if res.rowcount:
                logger.info("GPS poor-accuracy backfill: %d row(s) updated on active project", res.rowcount)
        except Exception as e:
            logger.warning("GPS poor-accuracy backfill skipped: %s", e)

    # One-time backfill: flag_fast_form threshold restored to <5 min.
    # Active project only; R4 history stays at its original threshold.
    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("""
                UPDATE mda_households h
                SET flag_fast_form = (h.form_duration_min < 5)
                WHERE h.project_id IN (
                        SELECT id FROM geo_projects WHERE is_active = TRUE
                      )
                  AND h.form_duration_min IS NOT NULL
                  AND h.flag_fast_form IS DISTINCT FROM (h.form_duration_min < 5)
            """))
            await db.commit()
            if res.rowcount:
                logger.info("Fast-form (<2 min) backfill: %d row(s) updated on active project", res.rowcount)
        except Exception as e:
            logger.warning("Fast-form backfill skipped: %s", e)

    async with AsyncSessionLocal() as db:
        # Seed default superadmin from env (only if no user with this username exists)
        result = await db.execute(select(User).where(User.username == SUPERADMIN_USERNAME))
        if not result.scalar_one_or_none():
            superadmin = User(
                username=SUPERADMIN_USERNAME,
                email=SUPERADMIN_EMAIL,
                hashed_password=hash_password(SUPERADMIN_PASSWORD),
                is_admin=True,
                is_superadmin=True,
            )
            db.add(superadmin)
            logger.info(f"Created superadmin user ({SUPERADMIN_USERNAME})")

        # Seed default admin user
        result = await db.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            admin = User(
                username="admin",
                email="admin@geospatial.local",
                hashed_password=hash_password("admin123"),
                is_admin=True,
            )
            db.add(admin)
            logger.info("Created default admin user (admin/admin123)")

        # Note: previously seeded "viewer" and "analyst" demo users here.
        # Removed 2026-05-22 because the analyst/viewer presentation tier
        # was retired (PR #14) and the seed would silently recreate the
        # accounts every container restart after a superadmin deleted them.
        # If anyone needs analyst/viewer back as a tier, the right move is
        # to add a real role column; don't reintroduce silent reseeding.

        # Seed / migrate Sokoto Round 4 project
        result = await db.execute(select(GeoProject).where(GeoProject.slug == "sokoto"))
        sokoto_r4 = result.scalar_one_or_none()
        if not sokoto_r4:
            sokoto_r4 = GeoProject(
                name="Sokoto Round 4",
                slug="sokoto",
                description="Sokoto State — Round 4 (historical)",
                state_name="Sokoto",
                round_number=4,
                is_active=False,
            )
            db.add(sokoto_r4)
            logger.info("Created Sokoto R4 project")
        elif sokoto_r4.state_name is None or sokoto_r4.round_number is None:
            # First-time Phase 4a migration: tag the existing "Sokoto" project as R4
            sokoto_r4.state_name = "Sokoto"
            sokoto_r4.round_number = 4
            sokoto_r4.name = "Sokoto Round 4"
            sokoto_r4.description = "Sokoto State — Round 4 (historical)"
            logger.info("Migrated existing Sokoto project to Sokoto R4")

        await db.commit()
        await db.refresh(sokoto_r4)

        # Backfill any unscoped MDA rows to Sokoto R4 (one-time, idempotent)
        for tbl in ("mda_households", "mda_individuals", "mda_baseline", "mlos_settlements"):
            try:
                res = await db.execute(
                    text(f"UPDATE {tbl} SET project_id = :pid WHERE project_id IS NULL"),
                    {"pid": sokoto_r4.id},
                )
                if res.rowcount:
                    logger.info(f"Backfilled {res.rowcount} rows in {tbl} → Sokoto R4 (id={sokoto_r4.id})")
            except Exception as e:
                logger.warning(f"Backfill skipped for {tbl}: {e}")
        await db.commit()

        # Seed Sokoto Round 5 as the active project (R4 deactivates if it was active)
        result = await db.execute(
            select(GeoProject).where(
                GeoProject.state_name == "Sokoto",
                GeoProject.round_number == 5,
            )
        )
        sokoto_r5 = result.scalar_one_or_none()
        if not sokoto_r5:
            sokoto_r5 = GeoProject(
                name="Sokoto Round 5",
                slug="sokoto-r5",
                description="Sokoto State — Round 5 (live)",
                state_name="Sokoto",
                round_number=5,
                is_active=True,
            )
            db.add(sokoto_r5)
            # Only one project may be active at a time
            sokoto_r4.is_active = False
            logger.info("Seeded Sokoto R5 project (active)")

        await db.commit()

    # Recover from any sync that was 'running' when the app last shut down.
    # Without this, a sync_config marked 'running' at the moment of crash /
    # restart would stay 'running' forever, blocking new sync attempts.
    async with AsyncSessionLocal() as db:
        try:
            res1 = await db.execute(text("""
                UPDATE sync_config SET
                  last_status = 'error',
                  last_error = 'Sync was interrupted by app restart',
                  last_progress_step = NULL,
                  last_progress_total = NULL
                WHERE last_status = 'running'
            """))
            res2 = await db.execute(text("""
                UPDATE sync_history SET
                  status = 'error',
                  ended_at = NOW(),
                  error_message = 'Sync was interrupted by app restart'
                WHERE status = 'running'
            """))
            # Same idea for the on-prem mirror — clear stale 'running'
            # locks so the button isn't permanently disabled after a crash.
            # The table may not exist yet on older deployments; ignore that.
            try:
                res3 = await db.execute(text("""
                    UPDATE onprem_mirror_state SET
                      last_status = 'error',
                      last_error = 'Mirror was interrupted by app restart',
                      last_progress_step = NULL,
                      last_progress_total = NULL,
                      last_progress_label = NULL
                    WHERE last_status = 'running'
                """))
            except Exception:
                res3 = None
            await db.commit()
            mirror_n = res3.rowcount if res3 is not None else 0
            if res1.rowcount or res2.rowcount or mirror_n:
                logger.info(
                    "Recovered %d sync_config + %d sync_history + %d mirror rows stuck at 'running'",
                    res1.rowcount, res2.rowcount, mirror_n,
                )
        except Exception as e:
            logger.warning("Sync-recovery cleanup skipped: %s", e)

    logger.info("Startup complete.")
    yield
    logger.info("Shutdown complete.")


_IS_PROD = os.getenv("ENVIRONMENT", "development") == "production"

# Optional error tracking. No-op if SENTRY_DSN is not set.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk  # type: ignore
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            environment=os.getenv("ENVIRONMENT", "development"),
        )
        logger.info("Sentry error tracking enabled.")
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry_sdk not installed. Skipping.")

app = FastAPI(
    title="ERITAS MDA — Geospatial Coverage & Data Quality Monitoring System",
    description="Production-grade geospatial field data monitoring with PostGIS",
    version="1.0.0",
    lifespan=lifespan,
    # Disable interactive API schema in production so the route map isn't public.
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
)

# Explicit CORS allowlist. "*" with credentials is silently rejected by browsers
# anyway, so default to first-party origins and let ops widen via env.
_DEFAULT_ORIGINS = ",".join([
    "https://eha-mda-dashboard.ehealthnigeria.org",
    "https://mda-dashboard-alb-1851779239.us-east-1.elb.amazonaws.com",
])
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# Tag every request with a correlation ID so logs can be threaded.
@app.middleware("http")
async def add_request_id(request, call_next):
    import uuid
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# Baseline security headers. Cheap to add and closes the most obvious gaps.
# CSP is intentionally lenient (allows inline + CDN) because the static
# dashboard relies on inline <script> blocks + CDN-hosted MapLibre/Chart.js;
# tightening to nonce-based requires a refactor of the static templates.
_CSP_DEFAULT = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:; "
    "connect-src 'self' https:; "
    "frame-ancestors 'none';"
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", _CSP_DEFAULT)
    if _IS_PROD:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# ── Authentication gate on protected APIs ───────────────────────────────────
# Historical: every /api/mda/* endpoint accepted anonymous callers, so the
# whole dataset was scrape-able. The QA audit flagged this as a P0.
#
# Fix: a single middleware enforces a valid Bearer token on writes, admin
# routes, and PII-adjacent reads (raw GPS points, individual movement
# tracks, etc.). The public dashboard is preserved by an explicit allowlist
# of aggregate GET endpoints that show campaign-level coverage and KPIs
# without any individual records or PII.
import re

from fastapi.responses import JSONResponse

# Any path matched by _PROTECTED_PREFIXES is gated unless also matched by
# the public allowlist below.
_PROTECTED_PREFIXES = (
    "/api/mda/",
    "/api/projects",
    "/api/qc/",
    "/api/analytics/",
    "/api/auth/",     # auth admin endpoints (users CRUD, password reset)
    "/api/sync/",     # sync config + history
    "/api/ingestion/",
    "/api/boundaries/",
)

# Anonymous GETs allowed on these paths — aggregate / public-by-design.
# Anything not listed here that matches a protected prefix needs a Bearer
# token. Non-GET methods are always gated.
_PUBLIC_GET_PATHS: set[str] = {
    # Always-public utility endpoints
    "/api/health",
    "/api/mda/landing-stats",
    # Campaign-level summaries
    "/api/mda/overview",
    "/api/mda/campaign-dates",
    "/api/mda/rounds/summary",
    "/api/mda/rounds/lga-compare",
    "/api/mda/system/counts",
    "/api/mda/trends/daily",
    "/api/mda/trends/daily-by-round",
    # Coverage aggregates (per LGA / ward / age)
    "/api/mda/coverage/lga",
    "/api/mda/coverage/ward",
    "/api/mda/coverage/lga-by-age",
    "/api/mda/coverage/refusals-analysis",
    # QC summaries (aggregate counters only — raw GPS records remain gated)
    "/api/mda/qc/summary",
    "/api/mda/qc/refusals-by-lga",
    "/api/mda/qc/duration-by-lga",
    "/api/mda/qc/teams-summary",
    # Geo summaries (aggregate; heatmap GeoJSON and movement tracks stay gated)
    "/api/mda/geo/coverage-summary",
    "/api/mda/geo/completeness",
    "/api/mda/geo/settlement-breakdown",
    "/api/mda/geo/mop-up-shortlist",
    # Team + ward + individual aggregates
    "/api/mda/teams/performance",
    "/api/mda/teams/by-lga",
    "/api/mda/teams/footprint",
    "/api/mda/submissions/ward",
    "/api/mda/individuals/age-summary",
    "/api/mda/wards",
    # Project metadata (list view)
    "/api/projects",
    # Anonymous login + token issuance
    "/api/auth/login",
}

# Path patterns (with path params) that are publicly readable.
_PUBLIC_GET_PATTERNS: list[re.Pattern[str]] = [
    # Read a single project
    re.compile(r"^/api/projects/\d+$"),
    # Boundary GeoJSON layers — pure geography, no PII
    re.compile(r"^/api/projects/\d+/boundaries/(lga|ward|settlement|grid)/geojson$"),
]


def _is_public(method: str, path: str) -> bool:
    if method == "OPTIONS":
        return True
    if method != "GET":
        return False
    if path in _PUBLIC_GET_PATHS:
        return True
    return any(p.match(path) for p in _PUBLIC_GET_PATTERNS)


@app.middleware("http")
async def require_auth_on_protected_apis(request, call_next):
    path = request.url.path
    if _is_public(request.method, path):
        return await call_next(request)
    if not any(path.startswith(p) for p in _PROTECTED_PREFIXES):
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        from app.routes.auth import decode_token
        decode_token(auth.split(" ", 1)[1])
    except Exception:
        return JSONResponse(
            {"detail": "Invalid or expired token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


# ── Rate limiting ───────────────────────────────────────────────────────────
# Defensive ceiling per client IP. The default is generous (120/min) so
# normal dashboard usage is unaffected; specific heavy endpoints can override
# with their own decorator.
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
    from slowapi.errors import RateLimitExceeded  # type: ignore
    from slowapi.util import get_remote_address  # type: ignore

    limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    logger.info("Rate limiting enabled (default 120/minute per IP).")
except ImportError:
    logger.warning("slowapi not installed — rate limiting disabled.")

# Register routes
app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(boundaries.router, prefix="/api")
app.include_router(ingestion.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(qc.router, prefix="/api")
app.include_router(mda_route.router, prefix="/api")
app.include_router(sync.router, prefix="/api")

# Serve static files
import os
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """Landing page = welcome page with two clear entry points.

    Shows the SARMAAN programme overview and live campaign stats, plus:
      • "View Dashboard" → /dashboard (public, view-only)
      • "Admin Portal"   → /login (sign in)

    If the visitor is already authenticated, the page reskins itself
    to show "Open Dashboard" + (for admins) "Admin Panel" instead.
    """
    index = os.path.join(static_dir, "home.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "Geospatial Coverage API", "docs": "/docs"}


@app.get("/home")
async def home_page():
    """Same welcome page; this is where /login redirects after sign-in."""
    return FileResponse(os.path.join(static_dir, "home.html"))


@app.get("/login")
async def login_page():
    """Admin Portal sign-in."""
    return FileResponse(os.path.join(static_dir, "login.html"))


@app.get("/dashboard")
async def dashboard_page():
    """Dashboard. PUBLIC_MODE auto-detected from absence of auth token in browser."""
    return FileResponse(os.path.join(static_dir, "mda.html"))


@app.get("/mda")
async def mda_dashboard():
    """Alias for /dashboard, kept for backwards compatibility."""
    return FileResponse(os.path.join(static_dir, "mda.html"))


@app.get("/mda-admin")
async def mda_admin_page():
    return FileResponse(os.path.join(static_dir, "mda-admin.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "geospatial-tracker"}

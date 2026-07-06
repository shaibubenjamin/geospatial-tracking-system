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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select, text

from app.database import create_all_tables, AsyncSessionLocal
from app.routes.auth import hash_password
from app.models import User, GeoProject
from app.config import SUPERADMIN_USERNAME, SUPERADMIN_PASSWORD, SUPERADMIN_EMAIL
from app.config import (
    MIN_VERSION_CODE, LATEST_VERSION_CODE, LATEST_VERSION_NAME, UPDATE_URL,
    APP_API_PREFIX, APK_DIR, APK_FILENAME,
)
from app.routes import auth, projects, boundaries, ingestion, analytics, qc, sync, sources, reports
from app.routes import mda as mda_route
from app.routes import app_api

# Root logger already configured above as JSON; basicConfig is a no-op now.
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, seed default admin user and Sokoto project."""
    logger.info("Starting up — creating database tables...")
    # Surface a missing sync key at boot (in health/logs) instead of only when a
    # user hits "Run Sync". CommCare credentials can't be encrypted/decrypted
    # without it, so sync silently fails until the env is refreshed.
    if not (os.getenv("SYNC_ENCRYPTION_KEY") or "").strip():
        logger.warning(
            "SYNC_ENCRYPTION_KEY is not set — CommCare sync will fail until the "
            "environment is refreshed (run refresh-env.sh / redeploy)."
        )
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
            # State-based access control: which state(s) a user may see (CSV).
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_states TEXT",
            # LGA-level access (Phase 2): which LGA(s) a user is restricted to (CSV).
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_lgas TEXT",
            # Per-project public-dashboard opt-in (used by Phase 1b).
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE",
            # Dashboard switcher multi-select. Nullable so the one-time backfill
            # below only touches never-set rows (removed rows become FALSE, not NULL).
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS show_on_dashboard BOOLEAN",
            "ALTER TABLE geo_projects ADD COLUMN IF NOT EXISTS campaign_paused BOOLEAN DEFAULT FALSE",
        ]:
            try:
                await db.execute(text(stmt))
            except Exception:
                pass
        await db.commit()

        # One-time backfill: seed show_on_dashboard from the current default
        # round (is_active) so exactly the round that's live now stays shown.
        # Only touches never-set (NULL) rows, so later Show/Remove toggles are
        # never clobbered on restart.
        try:
            await db.execute(text(
                "UPDATE geo_projects SET show_on_dashboard = COALESCE(is_active, FALSE) "
                "WHERE show_on_dashboard IS NULL"
            ))
            await db.commit()
        except Exception:
            pass

    # Access-control backfill: scope existing non-superadmin accounts to the
    # currently-loaded state(s) so they keep working but DON'T silently gain
    # access to new states (e.g. Kano) once those are loaded. Superadmins are
    # left unrestricted (they see all). Only touches accounts with no scope yet,
    # so it never overwrites a state list an admin has set.
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(text("""
                UPDATE users
                SET allowed_states = COALESCE((
                    SELECT string_agg(DISTINCT state_name, ',')
                    FROM geo_projects WHERE state_name IS NOT NULL
                ), 'Sokoto')
                WHERE is_superadmin = FALSE
                  AND (allowed_states IS NULL OR allowed_states = '')
            """))
            await db.commit()
        except Exception:
            pass

    # Public-dashboard backfill: preserve the existing public view by marking
    # ONE project public — but ONLY if no project is public yet (first run).
    # Prefer the active project; if none is active (e.g. the campaign was marked
    # ended), fall back to the newest round so the public dashboard is never left
    # empty. After that, admins control is_public per project (this never
    # clobbers a deliberate toggle thanks to the NOT EXISTS guard).
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(text("""
                UPDATE geo_projects SET is_public = TRUE
                WHERE id = (
                    SELECT id FROM geo_projects
                    ORDER BY is_active DESC, round_number DESC NULLS LAST, id DESC
                    LIMIT 1
                )
                AND NOT EXISTS (SELECT 1 FROM geo_projects WHERE is_public = TRUE)
            """))
            await db.commit()
        except Exception:
            pass

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

    # One-time backfill: flag_fast_form threshold currently set to <3 min.
    # History of the threshold: 5 → 2 → 3 (operator-tuned over time as the
    # team observed the actual visit-time distribution; 3 keeps a defensible
    # signal of rushed entry without false-flagging quick honest visits).
    # Active project only; historical rounds keep their original threshold.
    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("""
                UPDATE mda_households h
                SET flag_fast_form = (h.form_duration_min < 3)
                WHERE h.project_id IN (
                        SELECT id FROM geo_projects WHERE is_active = TRUE
                      )
                  AND h.form_duration_min IS NOT NULL
                  AND h.flag_fast_form IS DISTINCT FROM (h.form_duration_min < 3)
            """))
            await db.commit()
            if res.rowcount:
                logger.info("Fast-form (<3 min) backfill: %d row(s) updated on active project", res.rowcount)
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

# Compress large responses. The boundary GeoJSON layers are highly compressible
# text (the settlement layer is ~22 MB raw); gzip cuts the wire payload ~10-20x.
# Only kicks in when the client sends Accept-Encoding: gzip and the body exceeds
# the threshold, so small JSON/HTML responses are unaffected.
app.add_middleware(GZipMiddleware, minimum_size=1024)


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
    # blob: is required by MapLibre GL JS, which builds its GeoJSON vector
    # layers in a blob: web worker. Without blob: in script-src AND a
    # worker-src/child-src that allows blob:, the worker is blocked and every
    # GeoJSON layer (LGA visitation choropleth, ward/settlement/grid coverage)
    # silently fails to render — only the raster basemap shows. This applies to
    # the dashboard map (/mda) and every page that embeds MapLibre.
    "script-src 'self' 'unsafe-inline' blob: https://unpkg.com https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net "
    "https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:; "
    "connect-src 'self' https:; "
    "worker-src 'self' blob:; "
    "child-src 'self' blob:; "
    "frame-ancestors 'none';"
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", _CSP_DEFAULT)
    # The /app-preview browser mirror embeds /app/map in a same-origin iframe,
    # so allow same-origin framing for those two paths (the global default is
    # DENY / frame-ancestors 'none'). The blob: web-worker allowance MapLibre
    # /Leaflet need already lives in the global _CSP_DEFAULT.
    if request.url.path in ("/app/map", "/app/dashboard", "/mda", "/app-preview"):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = _CSP_DEFAULT.replace(
            "frame-ancestors 'none'", "frame-ancestors 'self'"
        )
    # HTML pages (login, home, dashboard, admin) are session-sensitive and must
    # never be cached by browsers or intermediaries — otherwise a logged-out
    # user could see a previous user's authenticated render from the bfcache.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")
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
    "/api/app/",      # Android companion app surface (always requires a token)
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
    "/api/mda/qc/duration-histogram",
    "/api/mda/qc/teams-summary",
    "/api/mda/qc/stacked-points",
    "/api/mda/qc/team-stacked-trend",
    "/api/mda/qc/outside-lga-points",
    # Geo summaries (aggregate; heatmap GeoJSON and movement tracks stay gated)
    "/api/mda/geo/coverage-summary",
    "/api/mda/geo/completeness",
    "/api/mda/geo/settlement-breakdown",
    "/api/mda/geo/mop-up-shortlist",
    # NOTE: the detailed coverage geojson (lgas/wards/settlements-coverage) is
    # deliberately NOT public — the app reaches it via the gated /api/app/geo/*
    # endpoints with a token, so the geographic data isn't exposed publicly.
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


# Paths that are public for any HTTP method (e.g. POST login).
_PUBLIC_ANY_METHOD: set[str] = {
    "/api/auth/login",
    "/api/health",
    # User-submitted concerns / issue reports. Public visitors must be able to
    # POST a report without logging in. The GET (admin triage list) is still
    # protected — it's enforced at the route level via require_admin, and the
    # path-based gate only governs whether a token is required at all.
    "/api/reports",
}


def _is_public(method: str, path: str) -> bool:
    if method == "OPTIONS":
        return True
    if path in _PUBLIC_ANY_METHOD:
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


# ── Android app version gate (force-update) ─────────────────────────────────
# The companion app sends `X-App-Version-Code: <n>` on every request. This
# middleware is defined AFTER the auth gate so it is the OUTERMOST http
# middleware (Starlette runs the last-registered first) — an outdated client
# gets a 426 before the auth gate can return a 401, so the user is told to
# update rather than to log in.
#
# The floor is _effective_min() = the latest published APK's versionCode, so
# every release force-updates all older installs (MIN_VERSION_CODE=0 still
# disables the gate; see _effective_min).
# Rules (only enforced when the floor > 0):
#   • header present and version < floor             → 426 on ANY path
#     (a stale install is locked out everywhere it calls, not just app paths)
#   • header absent on an /api/app/* path            → 426 (only the app
#     should ever call these; a missing header means a tampered/old client)
#   • header absent on a non-app path (web browser)  → pass through untouched
# GET /version is always exempt so the client launch-check can run.
def _effective_min() -> int:
    """Force-update floor. POLICY: every published release force-updates all
    older installs, so the floor TRACKS the latest published APK's versionCode
    (parsed from APK_DIR by _apk_status). Guards:
      • MIN_VERSION_CODE <= 0 hard-disables the gate entirely (ops kill-switch).
      • Never drops below the static MIN_VERSION_CODE floor.
      • Falls back to MIN_VERSION_CODE when no APK is published yet, so a
        missing/unreadable APK_DIR can't lock everyone out.
    """
    if MIN_VERSION_CODE <= 0:
        return 0
    published = _apk_status()["version_code"] or 0
    return max(published, MIN_VERSION_CODE)


def _version_payload(detail: str) -> dict:
    s = _apk_status()
    return {
        "detail": detail,
        # min = the force-update floor. POLICY: every published release forces
        # all older installs to update, so this TRACKS the latest published APK
        # (see _effective_min). MIN_VERSION_CODE is the static fallback floor,
        # and MIN_VERSION_CODE=0 still hard-disables the gate.
        "min": _effective_min(),
        # latest auto-tracks the published APK → drives the optional-update prompt.
        "latest": s["version_code"] or LATEST_VERSION_CODE,
        "latest_name": s["version_name"] or LATEST_VERSION_NAME,
        "update_url": UPDATE_URL,
    }


@app.middleware("http")
async def enforce_app_version(request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path == "/version":
        return await call_next(request)
    min_code = _effective_min()
    if min_code and min_code > 0:
        raw = request.headers.get("X-App-Version-Code")
        is_app_path = path.startswith(APP_API_PREFIX)
        if raw is not None:
            try:
                version_code = int(raw)
            except ValueError:
                version_code = -1
            if version_code < min_code:
                return JSONResponse(
                    _version_payload("Upgrade required"), status_code=426
                )
        elif is_app_path:
            return JSONResponse(
                _version_payload("Upgrade required (missing version header)"),
                status_code=426,
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
app.include_router(sources.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(app_api.router, prefix="/api")

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


@app.get("/app/map")
async def app_map_page():
    """Standalone Leaflet coverage map the Android app loads in a WebView (and
    the /app-preview mirror iframes). Zoom-driven: LGA → ward → settlement →
    GPS points. Reads ?project_id and the token from the URL fragment."""
    return FileResponse(os.path.join(static_dir, "app-map.html"))


@app.get("/app/dashboard")
async def app_dashboard_page():
    """Mobile clone of the web platform's campaign dashboard — Overview,
    Coverage, Quality, Teams, Trends sections via a top section switcher. The
    Android app loads it in a WebView and /app-preview iframes it. Pulls the
    same /api/app/* + /api/mda/* data as the web, so web data updates reflect."""
    return FileResponse(os.path.join(static_dir, "app-dashboard.html"))


@app.get("/app-preview")
async def app_preview_page():
    """Browser mirror of the Android app — logs in and renders the same
    /api/app/* data (and iframes the real /app/map), so app UI/data changes can
    be previewed without building an APK. Not the APK itself."""
    return FileResponse(os.path.join(static_dir, "app-preview.html"))


@app.get("/mda-admin")
async def mda_admin_page():
    return FileResponse(os.path.join(static_dir, "mda-admin.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "geospatial-tracker"}


# ── Android companion app: public version check + static APK host ───────────
# Both are deliberately unauthenticated and un-gated: the launch-time version
# check must work for any client (including one about to be force-updated),
# and the APK download is a dumb public file host.
@app.get("/version")
async def app_version():
    """Drives the client launch check (force-update wall / optional banner).

    Both numbers track the actual APK published on the server (parsed from the
    eritas-<name>-<code>.apk files). ``latest`` drives the optional prompt;
    ``min`` is the force-update floor that TRACKS the latest published build
    (see _effective_min), so every release force-updates all older installs
    without manually bumping env. No APK published yet → falls back to the
    static MIN_VERSION_CODE floor.
    """
    s = _apk_status()
    return {
        "min": _effective_min(),
        "latest": s["version_code"] or LATEST_VERSION_CODE,
        "latest_name": s["version_name"] or LATEST_VERSION_NAME,
        "update_url": UPDATE_URL,
    }


def _serve_apk(filename: str, download_as: str | None = None):
    """Serve an APK from APK_DIR as an attachment, guarding path traversal.

    ``download_as`` overrides the saved filename so the user gets a
    version-stamped name (e.g. eritas-0.1-111.apk) even though the served file
    on disk is the stable eritas-latest.apk.
    """
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".apk"):
        return JSONResponse({"detail": "Not an APK"}, status_code=404)
    apk_path = os.path.join(APK_DIR, safe_name)
    if not os.path.isfile(apk_path):
        return JSONResponse(
            {"detail": "APK not available yet"}, status_code=404
        )
    return FileResponse(
        apk_path,
        media_type="application/vnd.android.package-archive",
        filename=os.path.basename(download_as) if download_as else safe_name,
    )


_APK_STATUS_CACHE: dict = {"val": None, "ts": 0.0}


def _apk_status() -> dict:
    """Inspect APK_DIR: is the latest APK present, its size, and best-known
    version (parsed from any eritas-<name>-<code>.apk siblings).

    Cached ~30s because /version + the landing page call this; the result feeds
    the optional-update prompt (latest), not the force floor."""
    now = time.time()
    if _APK_STATUS_CACHE["val"] is not None and now - _APK_STATUS_CACHE["ts"] < 30:
        return _APK_STATUS_CACHE["val"]
    latest_path = os.path.join(APK_DIR, APK_FILENAME)
    available = os.path.isfile(latest_path)
    size_mb = round(os.path.getsize(latest_path) / (1024 * 1024), 1) if available else 0.0
    version_name = LATEST_VERSION_NAME or "0.1"
    version_code = LATEST_VERSION_CODE or 0
    download_name = "eritas.apk"
    try:
        best = -1
        for f in os.listdir(APK_DIR):
            m = re.match(r"^eritas-(.+)-(\d+)\.apk$", f)
            if m and int(m.group(2)) > best:
                best = int(m.group(2))
                version_name = m.group(1)
                version_code = best
                download_name = f
    except OSError:
        pass
    result = {
        "available": available,
        "size_mb": size_mb,
        "version_name": version_name,
        "version_code": version_code,
        "download_name": download_name,
    }
    _APK_STATUS_CACHE["val"] = result
    _APK_STATUS_CACHE["ts"] = now
    return result


def _apk_landing_html(request: Request) -> str:
    """Public download landing page for the Android app (served at /apk)."""
    from urllib.parse import quote

    s = _apk_status()
    base = str(request.base_url).rstrip("/")
    page_url = f"{base}/apk"
    qr = (
        "https://api.qrserver.com/v1/create-qr-code/?size=440x440&margin=0&data="
        + quote(page_url, safe="")
    )
    ver = f"{s['version_name']}"
    size_txt = f" · {s['size_mb']} MB" if s["available"] else ""

    if s["available"]:
        action = f"""
      <div class="qr-row">
        <img src="{qr}" alt="Scan to open this page on your phone" />
        <div class="copy">
          <h2>Scan with your phone</h2>
          <p>Or, if you're already on your phone, tap Download below.</p>
        </div>
      </div>
      <div class="btn-row">
        <a class="btn" href="/download" download="{s['download_name']}">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          Download v{ver} (build {s['version_code']})
        </a>
        <div class="meta">
          <strong>Version {ver} · build {s['version_code']}</strong>{size_txt}<br/>
          Saves as <code>{s['download_name']}</code> · Android 7.0+
        </div>
      </div>
      <h2>How to install</h2>
      <ol class="steps">
        <li>Tap <strong>Download v{ver}</strong> above. Your browser may warn you because this isn't from the Play Store — accept the download.</li>
        <li>Open the downloaded file. If Android blocks it, go to <strong>Settings → Apps → Special access → Install unknown apps</strong>, find your browser, and allow it just this once.</li>
        <li>Tap <strong>Install</strong>, then open <strong>ERITAS Coverage</strong>. Sign in with your dashboard username and password — you'll need internet for the first sign-in.</li>
        <li>Pick your state &amp; round, then use the map and <strong>My Area</strong> to see what's left to cover.</li>
      </ol>
      <div class="warning">
        <strong>One-time prompt:</strong> Android warns that this app is from outside the Play Store. That's expected — eHealth Nigeria signs and publishes it directly. The signature stays the same on every update.
      </div>"""
    else:
        action = """
      <div class="warning">
        <strong>Not available yet.</strong> The latest build hasn't been published to this server.
        Check back shortly, or contact your supervisor.
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ERITAS MDA Coverage · Download</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    html,body{{margin:0;padding:0;min-height:100%;font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:linear-gradient(135deg,#0A5C37 0%,#16A34A 100%);color:#fff;line-height:1.5}}
    .wrap{{max-width:760px;margin:0 auto;padding:32px 20px 64px}}
    .header{{text-align:center;padding:16px 0 8px}}
    .header h1{{margin:0;font-size:28px;font-weight:800;letter-spacing:-0.01em}}
    .header .sub{{margin-top:6px;font-size:14px;color:#BBF7D0}}
    .card{{background:#fff;color:#0F172A;border-radius:20px;padding:32px 28px;margin-top:24px;box-shadow:0 10px 40px rgba(0,0,0,.18)}}
    .qr-row{{display:grid;grid-template-columns:auto 1fr;gap:24px;align-items:center;padding:8px 0 16px}}
    @media(max-width:540px){{
      .qr-row{{grid-template-columns:1fr;text-align:center;justify-items:center}}
      .qr-row img{{width:min(280px,72vw);height:auto;aspect-ratio:1}}
      .btn{{width:100%;max-width:360px;padding:18px 24px;font-size:19px}}
    }}
    .qr-row img{{width:240px;height:240px;background:#fff;padding:8px;border-radius:12px;border:1px solid #E2E8F0}}
    .qr-row .copy h2{{margin:0 0 6px;font-size:18px;font-weight:700}}
    .qr-row .copy p{{margin:0;font-size:14px;color:#475569}}
    .btn{{display:inline-flex;align-items:center;justify-content:center;gap:12px;background:#16A34A;color:#fff;text-decoration:none;padding:20px 48px;border-radius:14px;font-weight:800;font-size:20px;min-width:260px;transition:background .15s;box-shadow:0 6px 18px rgba(22,163,74,.35)}}
    .btn svg{{width:24px;height:24px}}
    .btn:hover{{background:#0A5C37}}
    .btn-row{{text-align:center;margin:28px 0 8px}}
    .meta{{font-size:12px;color:#64748B;text-align:center;margin-top:12px}}
    .meta code{{font-family:ui-monospace,'SF Mono',Menlo,monospace}}
    .steps{{padding:0;margin:16px 0 0;counter-reset:step;list-style:none}}
    .steps li{{padding:12px 0 12px 44px;position:relative;font-size:14px;color:#0F172A;border-top:1px solid #F1F5F9}}
    .steps li:first-child{{border-top:0;padding-top:4px}}
    .steps li:before{{counter-increment:step;content:counter(step);position:absolute;left:0;top:12px;width:28px;height:28px;line-height:28px;text-align:center;background:#16A34A;color:#fff;border-radius:50%;font-weight:800;font-size:13px}}
    .steps li:first-child:before{{top:4px}}
    .warning{{background:#FEF3C7;color:#92400E;border-left:4px solid #F59E0B;padding:12px 16px;border-radius:8px;font-size:13px;margin:16px 0}}
    .footer{{text-align:center;margin-top:32px;font-size:12px;color:#BBF7D0}}
    h2{{margin:24px 0 12px;font-size:18px;font-weight:700;color:#0F172A}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <h1>ERITAS MDA Coverage</h1>
      <div class="sub">Android monitoring app · MDA geospatial coverage</div>
    </div>
    <div class="card">{action}
      <h2>Need help?</h2>
      <p style="font-size:14px;color:#475569;margin:4px 0 0;">Contact your campaign supervisor or the ERITAS team.</p>
    </div>
    <div class="footer">ERITAS · eHealth Nigeria</div>
  </div>
</body>
</html>"""


@app.get("/apk", response_class=HTMLResponse)
async def apk_landing(request: Request):
    """Public, styled download landing page for the Android app."""
    return HTMLResponse(_apk_landing_html(request))


@app.get("/download")
async def download_apk():
    """The actual signed APK file (linked from the /apk landing page).

    Saved with the version-stamped name so the user can tell which build they
    downloaded (e.g. eritas-0.1-111.apk)."""
    return _serve_apk(APK_FILENAME, download_as=_apk_status()["download_name"])


@app.get("/apk/{filename}")
async def download_apk_versioned(filename: str):
    """Fetch a specific versioned APK (e.g. /apk/eritas-0.1-106.apk)."""
    return _serve_apk(filename)

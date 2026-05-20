"""
main.py — Geospatial Coverage & Data Quality Monitoring System
FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager

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

logging.basicConfig(level=logging.INFO)
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

        # Seed viewer user
        result = await db.execute(select(User).where(User.username == "viewer"))
        if not result.scalar_one_or_none():
            viewer = User(
                username="viewer",
                email="viewer@geospatial.local",
                hashed_password=hash_password("viewer123"),
                is_admin=False,
            )
            db.add(viewer)
            logger.info("Created viewer user (viewer/viewer123)")

        # Seed analyst user
        result = await db.execute(select(User).where(User.username == "analyst"))
        if not result.scalar_one_or_none():
            analyst = User(
                username="analyst",
                email="analyst@geospatial.local",
                hashed_password=hash_password("analyst123"),
                is_admin=False,
            )
            db.add(analyst)
            logger.info("Created analyst user (analyst/analyst123)")

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


app = FastAPI(
    title="Geospatial Coverage & Data Quality Monitoring System",
    description="Production-grade geospatial field data monitoring with PostGIS",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

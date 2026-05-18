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
from app.routes import auth, projects, boundaries, ingestion, analytics, qc
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
        ]:
            try:
                await db.execute(text(stmt))
            except Exception:
                pass
        await db.commit()

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

        # Seed Sokoto project
        result = await db.execute(select(GeoProject).where(GeoProject.slug == "sokoto"))
        if not result.scalar_one_or_none():
            project = GeoProject(
                name="Sokoto",
                slug="sokoto",
                description="Sokoto State geospatial coverage monitoring",
                is_active=True,
            )
            db.add(project)
            logger.info("Created default Sokoto project")

        await db.commit()

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

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
from app.routes import auth, projects, boundaries, ingestion, analytics, qc
from app.routes import mda as mda_route

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, seed default admin user and Sokoto project."""
    logger.info("Starting up — creating database tables...")
    await create_all_tables()

    async with AsyncSessionLocal() as db:
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
    index = os.path.join(static_dir, "login.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "Geospatial Coverage API", "docs": "/docs"}


@app.get("/dashboard")
async def dashboard():
    return FileResponse(os.path.join(static_dir, "dashboard.html"))


@app.get("/admin")
async def admin():
    return FileResponse(os.path.join(static_dir, "admin.html"))


@app.get("/quality")
async def quality_page():
    return FileResponse(os.path.join(static_dir, "quality.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "geospatial-tracker"}

# ERITAS MDA Monitoring Dashboard
### Sokoto State Mass Drug Administration — Geospatial Coverage & Data Quality Platform

**ERITAS** — **E**vidence through **R**eal-time **I**ntelligence, **T**racking, and **A**ccountability **S**ystems.

Built with FastAPI · PostGIS · MapLibre GL JS · Chart.js

---

## Getting Started (New Team Member)

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | ≥ 4.x | https://www.docker.com/products/docker-desktop |
| Git | any | https://git-scm.com |
| Python | 3.10+ | https://www.python.org (for setup scripts only) |

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/shaibubenjamin/geospatial-tracking-system.git
cd geospatial-tracking-system
```

---

### Step 2 — Configure environment

```bash
# Copy the example env file (values are correct for local Docker)
cp .env.example .env
```

The `.env.example` already contains all defaults needed for local development — no changes required unless deploying to a server.

---

### Step 3 — Start all services

```bash
docker-compose up -d
```

This starts three containers:

| Container | External Port | Description |
|-----------|-------------|-------------|
| `geo_tracker_api` | **8090** → 8080 | FastAPI application |
| `geo_tracker_db`  | **5433** → 5432 | PostGIS 15-3.4 database |
| `geo_tracker_redis` | **6380** → 6379 | Redis cache |

Wait ~15 seconds for the database to initialize, then open:

```
http://localhost:8090
```

---

### Step 4 — Log in

| Username | Password | Role |
|----------|----------|------|
| `admin`  | `admin123` | Administrator — full access |
| `analyst` | `analyst123` | Analyst — view-only |
| `viewer`  | `viewer123` | Viewer — view-only |

> ⚠️ Change these passwords before deploying to any shared environment.

---

### Step 5 — Load boundaries and compute analytics

The database starts empty. Run the boundary reload script to load the Sokoto shapefiles and compute settlement analytics.

First install the Python dependency:

```bash
pip install psycopg2-binary
```

Then run (PowerShell on Windows):

```powershell
$env:DATABASE_URL_SYNC = "postgresql://geouser:geopass@localhost:5433/geospatial_tracker"
$env:PYTHONIOENCODING = "utf-8"
python scripts/reload_boundaries_and_compute.py
```

Or on Linux/macOS:

```bash
DATABASE_URL_SYNC="postgresql://geouser:geopass@localhost:5433/geospatial_tracker" \
python scripts/reload_boundaries_and_compute.py
```

Expected output (takes ~30 seconds):

```
[1/5] Clearing existing boundaries ...
[2a/5] Loading LGA boundaries — 23 inserted
[2b/5] Loading Ward boundaries — 213 inserted
[2c/5] Loading Settlement boundaries — 9473 inserted
[2d/5] Loading Grid cells — 86511 inserted
[3/5] Populating mda_households.geom ...
[4/5] Computing settlement analytics ...
[5/5] Summary: 23 LGAs · 213 Wards · 9473 Settlements · 86511 Grids
✓ Done.
```

> **Shapefile location**: the script expects shapefiles at:
> `C:\Users\Benjamin.shaibu\Downloads\SOKOTO MDA RESOURCE\SOKOTO MDA RESOURCE\`
> Update the paths at the top of `scripts/reload_boundaries_and_compute.py` if your paths differ.

---

### Step 6 — (Optional) Upload MDA household data

To populate the dashboard with field data, upload the MDA Excel workbook via the dashboard:

1. Open `http://localhost:8090/mda`
2. Click the **Upload** button (cloud icon) in the top bar
3. Select the `SARMAAN Sokoto MLOS.xlsx` workbook
4. Click **Upload MDA Data**

After uploading, run the ward spatial join script to assign ward names:

```powershell
$env:DATABASE_URL_SYNC = "postgresql://geouser:geopass@localhost:5433/geospatial_tracker"
$env:PYTHONIOENCODING = "utf-8"
python scripts/update_ward_spatial.py
```

And pre-compute the `in_grid` flag for GPS points:

```powershell
python scripts/add_in_grid_column.py
```

---

## Dashboard Pages

| URL | Description |
|-----|-------------|
| `http://localhost:8090/` | Login |
| `http://localhost:8090/home` | Welcome landing page |
| `http://localhost:8090/mda` | ERITAS MDA dashboard |
| `http://localhost:8090/mda-admin` | Admin panel (admin only) |

---

## Project Structure

```
geospatial-tracking-system/
├── app/
│   ├── main.py                  # FastAPI entry point, startup, seeding
│   ├── config.py                # Environment config
│   ├── database.py              # Async SQLAlchemy + PostGIS engine
│   ├── models.py                # ORM models
│   ├── schemas.py               # Pydantic schemas
│   └── routes/
│       ├── auth.py              # JWT login, user management
│       ├── mda.py               # MDA-specific API endpoints (overview, QC, coverage)
│       ├── projects.py          # Project CRUD
│       ├── boundaries.py        # GeoJSON boundary endpoints
│       ├── analytics.py         # Settlement/ward/LGA analytics
│       └── ingestion.py         # Excel/CSV upload
│   └── services/
│       ├── spatial_engine.py    # PostGIS queries, ST_Within, grid analytics
│       └── qc_engine.py         # Data quality flag computation
├── static/
│   ├── login.html               # Login page
│   ├── home.html                # Landing page
│   ├── mda.html                 # Main MDA dashboard (MapLibre + Chart.js)
│   └── mda-admin.html           # Admin panel
├── scripts/
│   ├── reload_boundaries_and_compute.py  # Load shapefiles + compute analytics
│   ├── update_ward_spatial.py            # Assign ward_name to households via ST_Within
│   └── add_in_grid_column.py             # Pre-compute in_grid flag
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

---

## Key Technical Details

### Coordinate System
All geometry columns use **EPSG:4326** (WGS84 lat/lon). Shapefiles are reprojected from EPSG:3857 on import.

### Spatial Logic
- **Grid visitation**: `ST_Within(household.geom, grid.geom)` — a grid cell is green when ≥1 household GPS point falls inside it
- **Settlement completeness**: `visited_grids / total_grids × 100` ≥ 70% → settlement marked as visited
- **Ward/LGA completeness**: rolled up from settlement-level analytics

### Database Connection (local)
```
Host:     localhost
Port:     5433
Database: geospatial_tracker
User:     geouser
Password: geopass
```

Connect with any PostgreSQL client (DBeaver, psql, TablePlus) using these credentials.

---

## Common Commands

```bash
# View running containers
docker-compose ps

# View API logs
docker-compose logs -f api

# Restart API (after code changes)
docker-compose restart api

# Connect to database
docker exec -it geo_tracker_db psql -U geouser -d geospatial_tracker

# Stop everything
docker-compose down

# Stop and delete all data (fresh start)
docker-compose down -v
```

---

## GitHub Repository

```
https://github.com/shaibubenjamin/geospatial-tracking-system
```

# Geospatial Coverage & Data Quality Monitoring System

A production-grade geospatial field data coverage and quality monitoring platform built with FastAPI, PostGIS, and MapLibre GL JS.

## Features

- **Multi-project support** — Sokoto, Kano, and any future programs
- **LGA → Ward → Settlement → Grid drill-down** navigation with live visitation bars
- **MapLibre GL JS dashboard** with 5 toggleable layers and satellite/street/terrain basemaps
- **Shapefile import** — LGA, Ward, Settlement, Grid boundaries (ZIP upload via admin panel)
- **CSV GPS data ingestion** — validation preview, deduplication, incremental processing
- **PostGIS spatial analytics** — grid intersection (20m buffer), completeness %, visitation %
- **QC engine** — out-of-bound detection, time violations (outside 07:00–19:00), stacked point clustering
- **Coverage timeline** with forecast toggle (Chart.js)
- **JWT authentication** with admin/user roles
- **Docker Compose** — PostGIS 15.4, FastAPI, Redis

## Quick Start

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start all services
docker-compose up -d

# 3. Open browser
open http://localhost:8080
# Login: admin / admin123
```

## Project Structure

```
/app
  main.py                          # FastAPI app + startup (table creation, seed data)
  config.py                        # Environment config
  database.py                      # Async SQLAlchemy + PostGIS engine
  models.py                        # All ORM models with GeoAlchemy2 geometry columns
  schemas.py                       # Pydantic request/response schemas
  /routes
    auth.py                        # Login, JWT, user management
    projects.py                    # CRUD for geo_projects
    boundaries.py                  # Shapefile upload + GeoJSON endpoints
    ingestion.py                   # CSV upload, validation, background processing
    analytics.py                   # Coverage/completeness metrics, timeline, compute trigger
    qc.py                          # QC flag summary and listing
  /services
    spatial_engine.py              # PostGIS GeoJSON queries, spatial joins, analytics compute
    aggregation_engine.py          # Settlement → Ward → LGA metric rollups
    qc_engine.py                   # Out-of-bound, time violation, stacked point checks
    boundary_importer.py           # pyshp + pyproj shapefile → PostGIS import
/static
  login.html                       # JWT login page
  dashboard.html                   # MapLibre GL JS main dashboard
  admin.html                       # Admin panel (projects, boundaries, data, compute)
  /css/styles.css                  # Dark green theme
  /js/dashboard.js                 # Map logic, drill-down nav, charts
  /js/admin.js                     # Admin panel logic
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | Login → JWT token |
| GET | `/api/projects` | List all projects |
| POST | `/api/projects` | Create project (admin) |
| POST | `/api/projects/{id}/boundaries/lga` | Upload LGA shapefile ZIP |
| POST | `/api/projects/{id}/boundaries/ward` | Upload Ward shapefile ZIP |
| POST | `/api/projects/{id}/boundaries/settlement` | Upload Settlement shapefile ZIP |
| POST | `/api/projects/{id}/boundaries/grid` | Upload Grid shapefile ZIP |
| GET | `/api/projects/{id}/boundaries/lga/geojson` | LGA GeoJSON with analytics |
| GET | `/api/projects/{id}/boundaries/ward/geojson` | Ward GeoJSON (filter by lgacode) |
| GET | `/api/projects/{id}/boundaries/settlement/geojson` | Settlement GeoJSON (filter by lgacode/wardcode) |
| GET | `/api/projects/{id}/boundaries/grid/geojson` | Grid cells GeoJSON (by unique_cod) |
| POST | `/api/projects/{id}/ingest/validate` | Validate CSV without inserting |
| POST | `/api/projects/{id}/ingest/upload` | Upload CSV → background processing |
| GET | `/api/projects/{id}/analytics/summary` | Project-level summary stats |
| GET | `/api/projects/{id}/analytics/lgas` | LGA metrics |
| GET | `/api/projects/{id}/analytics/wards` | Ward metrics (filter by lgacode) |
| GET | `/api/projects/{id}/analytics/settlements` | Settlement metrics |
| GET | `/api/projects/{id}/analytics/timeline` | Coverage over time |
| GET | `/api/projects/{id}/analytics/points/geojson` | GPS points GeoJSON |
| POST | `/api/projects/{id}/analytics/compute` | Trigger full/incremental analytics |
| GET | `/api/projects/{id}/qc/summary` | QC flag counts by type |
| GET | `/api/projects/{id}/qc/flags` | Paginated QC flag list |

## Shapefile Field Requirements

All shapefiles are in **EPSG:3857** (Web Mercator) — reprojected to EPSG:4326 on import.

| Layer | Required Fields |
|-------|----------------|
| LGA | `lgacode_` |
| Ward | `lgacode_`, `Wardcode` |
| Settlement | `lgacode_`, `Wardcode`, `unique_cod` |
| Grid | `lgacode_`, `Wardcode`, `unique_cod` |

## QC Checks

- **Out of Bound** — Point claims LGA X but doesn't spatially fall there
- **Time Violation** — Collection timestamp outside 07:00–19:00 UTC
- **Stacked Points** — Clusters of >5 points within 5m radius (DBSCAN)

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| `api` | 8080 | FastAPI application |
| `db` | 5432 | PostGIS 15-3.4 database |
| `redis` | 6379 | Redis (optional caching) |

## Environment Variables

```env
DATABASE_URL=postgresql+asyncpg://geouser:geopass@db:5432/geospatial_tracker
DATABASE_URL_SYNC=postgresql://geouser:geopass@db:5432/geospatial_tracker
SECRET_KEY=change-this-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=480
ALGORITHM=HS256
ENVIRONMENT=production
```

## User Accounts

The following user accounts are seeded automatically on first startup:

| Username | Password | Role | Access |
|----------|----------|------|--------|
| `admin` | `admin123` | Administrator | Full admin access — data uploads, user management, admin panel |
| `analyst` | `analyst123` | Analyst | View-only access to MDA dashboard and analytics |
| `viewer` | `viewer123` | Viewer | View-only access to MDA dashboard and analytics |

> **Security note:** Change all default passwords immediately in production environments.

### Pages

| URL | Description | Access |
|-----|-------------|--------|
| `/` | Login page | Public |
| `/home` | Welcome / landing page | All authenticated users |
| `/mda` | SARMAAN MDA dashboard | All authenticated users |
| `/mda-admin` | MDA Admin panel | Admin only |
| `/admin` | General geo admin panel | Admin only |

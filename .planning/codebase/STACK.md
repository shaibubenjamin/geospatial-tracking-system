# Technology Stack

**Analysis Date:** 2026-09-08

## Languages

**Primary:**
- Python 3.11 - Backend API (FastAPI) in `app/`
- JavaScript/HTML/CSS - Web dashboard in `static/*.html`
- Kotlin - Android companion app in `android/app/src/`

**Secondary:**
- SQL - PostGIS database queries, schema in database models
- HCL (Terraform) - Infrastructure as Code in `terraform/`

## Runtime

**Environment:**
- Python 3.11-slim (Docker base image)
- Docker containers orchestrated via Docker Compose

**Package Manager:**
- pip - Python packages
- Lockfile: `requirements.txt` (pinned versions)
- Android: Gradle (Kotlin/Java dependencies)

## Frameworks

**Core:**
- FastAPI 0.111.0 - REST API framework in `app/main.py`
- Uvicorn 0.29.0 - ASGI server (running on port 8080)

**Database:**
- SQLAlchemy 2.0.30 with async support (`app/database.py`)
- asyncpg 0.29.0 - async PostgreSQL driver
- psycopg2-binary 2.9.9 - sync PostgreSQL driver (for sync_worker)
- GeoAlchemy2 0.15.1 - PostGIS geospatial ORM extension

**Mobile:**
- Jetpack Compose (UI framework for Android)
- Retrofit 2.11.0 - HTTP client
- Moshi 1.15.1 - JSON serialization

**Testing:**
- pytest (configured in `pytest.ini`)
- asyncio for async test support

**Build/Dev:**
- Alembic 1.13.1 - Database migrations
- Docker & Docker Compose - Local dev environment
- GitHub Actions - CI/CD pipelines in `.github/workflows/`

## Key Dependencies

**Critical:**
- fastapi 0.111.0 - REST API framework
- sqlalchemy[asyncio] 2.0.30 - ORM with async/await
- asyncpg 0.29.0 - PostgreSQL async driver (API container uses this)
- geoalchemy2 0.15.1 - PostGIS integration for spatial queries
- redis 5.0.7 - Cache and job queue (Redis 7-alpine in containers)

**Geospatial & Data:**
- shapely 2.0.4 - Geometry operations (boundary processing)
- pyshp 2.3.1 - Shapefile support for boundary imports
- pyproj 3.6.1 - Coordinate system transformations
- pandas 2.2.2 - Data analysis (CSV export, aggregations)
- numpy 1.26.4 - Numerical operations
- openpyxl 3.1.2 - Excel file handling for data uploads

**Networking & Data Sync:**
- httpx 0.27.0 - Async HTTP client for CommCare OData feeds (`app/services/commcare_sync.py`)

**Security & Auth:**
- pyjwt 2.8.1 - JWT token signing/verification (`app/routes/auth.py`)
- bcrypt 4.2.1 - Password hashing
- python-jose[cryptography] 3.3.0 - JWT with cryptography backend
- cryptography - Symmetric encryption for stored credentials (SYNC_ENCRYPTION_KEY)

**Observability & Monitoring:**
- sentry-sdk[fastapi] 2.18.0 - Error tracking (optional, requires SENTRY_DSN env var)

**Rate Limiting:**
- slowapi 0.1.9 - API rate limiting middleware

**Utilities:**
- python-dotenv 1.0.1 - Environment configuration loading
- python-multipart 0.0.9 - Multipart form parsing for file uploads
- aiofiles 23.2.1 - Async file I/O (APK serving, uploads)

## Configuration

**Environment:**
- `.env` file - Project-root configuration (not committed, see `.env.example`)
- Environment variables control: database URL, auth keys, API endpoints, Sentry DSN, SMTP, AWS credentials
- Key env vars: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `SYNC_ENCRYPTION_KEY`, `SENTRY_DSN`, `ENVIRONMENT`

**Build:**
- `Dockerfile` - Single image for both API and sync_worker containers
- `docker-compose.yml` - Local dev stack (api, redis, sync_worker)
- `deploy/docker-compose.prod.yml` - Production stack (pulled from ECR)
- `pytest.ini` - Pytest configuration with async test support

**Database Migrations:**
- Alembic configured (requires Alembic version control)
- Migrations run automatically on startup via `create_all_tables()` in `app/database.py`

## Platform Requirements

**Development:**
- Docker & Docker Compose
- Python 3.11 (only for local non-containerized work)
- PostgreSQL 14+ with PostGIS extension (or use RDS tunnel via `scripts/dev-aws.sh`)
- Redis 7 (containerized)

**Production:**
- AWS account (EC2, RDS, ECR, S3, ALB, Secrets Manager, VPC, IAM)
- Terraform for infrastructure provisioning
- Docker registry (ECR) for image storage
- Persistent storage: EBS volumes (for uploads), RDS PostgreSQL with PostGIS
- Load Balancer (AWS ALB) for traffic distribution

---

*Stack analysis: 2026-09-08*

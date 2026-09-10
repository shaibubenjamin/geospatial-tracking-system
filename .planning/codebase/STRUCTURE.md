# Codebase Structure

**Analysis Date:** 2026-09-08

## Directory Layout

```
geospatial-tracking-system/
├── .claude/                    # Claude workspace config
├── .github/                    # GitHub Actions workflows (CI/CD)
├── .planning/codebase/         # Generated codebase maps (this dir)
├── android/                    # Android app (Kotlin/Compose)
├── apk/                        # Built APK artifacts (deployed to /apk endpoint)
├── app/                        # FastAPI backend (Python)
│   ├── __init__.py
│   ├── main.py                 # Entry point, FastAPI app, middleware, lifespan
│   ├── config.py               # Environment parsing, app constants
│   ├── database.py             # AsyncSessionLocal, engine config, table creation
│   ├── models.py               # ORM table definitions (SQLAlchemy)
│   ├── schemas.py              # Pydantic request/response schemas
│   ├── sync_worker.py          # Standalone sync job consumer (Redis BLPOP loop)
│   ├── routes/                 # API endpoint handlers
│   │   ├── __init__.py
│   │   ├── auth.py             # Login, token issuance, user CRUD, decorators
│   │   ├── projects.py         # Project CRUD, active project mgmt
│   │   ├── boundaries.py       # LGA/ward/settlement GeoJSON, boundary imports
│   │   ├── ingestion.py        # CSV/Excel/Shapefile upload handling
│   │   ├── analytics.py        # Coverage rollups, timeline, points GeoJSON
│   │   ├── qc.py               # Quality check endpoints, flagged records
│   │   ├── mda.py              # Campaign dashboard data, coverage by level
│   │   ├── sync.py             # Sync trigger, status, history, auto-sync config
│   │   ├── sources.py          # Data source registry (CommCare, Kobo, etc.)
│   │   ├── reports.py          # User-submitted concerns/issue reports
│   │   └── app_api.py          # Android app API surface (/api/app/*)
│   └── services/               # Business logic, aggregations, integrations
│       ├── __init__.py
│       ├── aggregation_engine.py        # LGA/ward/settlement metric rollups
│       ├── spatial_engine.py            # PostGIS queries, coverage analysis
│       ├── qc_engine.py                 # Quality checks (duplicate, outside-area, etc.)
│       ├── commcare_sync.py             # CommCare OData pull, watermarking, delta
│       ├── geo_cache.py                 # In-process TTL cache for cold reads
│       ├── job_queue.py                 # Redis BLPOP task queue (sync enqueuing)
│       ├── boundary_importer.py         # Shapefile → PostGIS geometry import
│       ├── uploads.py                   # CSV/Excel parsing, validation
│       ├── crypto.py                    # Encryption/decryption (CommCare creds)
│       ├── health_metrics.py            # Error tracking for watchdog (n8n) probes
│       ├── notifier.py                  # Alerts/notifications (stub)
│       ├── project_scope.py             # State/LGA scope filters
│       └── onprem_mirror.py             # On-premises mirror sync (optional)
├── deploy/                     # Docker Compose & deployment configs
│   ├── docker-compose.prod.yml # Production stack (used by app-deploy.sh)
│   └── app-deploy.sh           # Deploy script (called by GitHub Actions)
├── docs/                       # Internal documentation
├── scripts/                    # Utility scripts (data loading, testing, ops)
├── static/                     # Frontend HTML/JS/CSS (served as static files)
│   ├── home.html               # Landing page (public overview)
│   ├── login.html              # Admin portal sign-in
│   ├── mda.html                # Dashboard (main campaign view)
│   ├── mda-admin.html          # Admin panel (project + user mgmt)
│   ├── app-map.html            # Standalone Leaflet map (Android WebView)
│   ├── app-dashboard.html      # Mobile clone of campaign dashboard
│   ├── app-preview.html        # Browser mirror of Android app
│   └── idle-logout.js          # Session timeout + logout
├── terraform/                  # Infrastructure as code (AWS)
│   ├── main.tf, variables.tf, outputs.tf  # EC2, RDS, ECR, IAM, networking
│   ├── bootstrap/              # Bootstrap stack (VPC, security groups)
│   └── terraform.tfvars        # Resource IDs & config (NOT committed for secrets)
├── tests/                      # Test suite (pytest)
│   ├── test_01_frontend_foundation.py
│   ├── test_02_api_backend.py
│   ├── test_03_database_storage.py
│   ├── ...
│   └── conftest.py             # Pytest fixtures, test database setup
├── android/                    # Android app (Kotlin/Compose)
│   ├── app/src/main/java/...   # Kotlin source (UI, data sync)
│   ├── app/src/main/res/...    # Android resources (layouts, strings)
│   ├── build.gradle.kts        # Gradle build config
│   └── gradle/wrapper/         # Gradle wrapper (reproducible builds)
├── eritas-mda-tests/           # E2E & integration tests (separate from unit tests)
│   ├── backend/, frontend/, e2e/
│   └── scripts/
├── .env.example                # Example environment variables (safe defaults)
├── .env                        # Local environment (NOT committed)
├── .gitignore                  # Git exclusions (env, cache, build artifacts)
├── requirements.txt            # Python dependencies (pip)
├── docker-compose.yml          # Local dev stack (FastAPI, PostGIS, Redis)
├── pytest.ini                  # Pytest configuration
├── Dockerfile                  # Image for production backend
├── CLAUDE.md                   # Project instructions (read first!)
├── README.md                   # Overview, local setup, tech stack
├── ARCHITECTURE.md             # System design (high-level)
├── Lga_Target.xlsx             # LGA population targets (reference data)
└── eritas-network-architecture.excalidraw  # Editable network diagram
```

## Directory Purposes

**`.github/workflows/`:**
- Purpose: GitHub Actions CI/CD pipelines
- Contains: `ci.yml` (lint + test on push), `deploy.yml` (web backend to EC2), `app-build.yml` (APK build/sign)
- Key: OIDC auth to AWS (no SSH keys); branch-triggered (main for web, apk_dev for app)

**`app/`:**
- Purpose: FastAPI backend (Python)
- Contains: All server-side code (routes, services, models, config)
- Key: Async/await throughout; database operations via AsyncSession; external integrations (CommCare, Kobo, Redis) via services

**`app/routes/`:**
- Purpose: HTTP endpoint implementations
- Contains: 10 APIRouter modules, each with GET/POST handlers
- Key: Thin handlers that delegate to services layer; auth decorators on protected routes

**`app/services/`:**
- Purpose: Reusable business logic, unaware of HTTP
- Contains: Stateless functions for sync, aggregation, spatial queries, QC checks
- Key: Can be called from routes OR sync_worker; testable in isolation

**`static/`:**
- Purpose: Frontend HTML/JS dashboards and mobile webapps
- Contains: 7 HTML files + 1 JS utility (idle logout); embedded CSS + JS (no build step)
- Key: Maps embed MapLibre GL JS + Leaflet; charts use Chart.js; no bundler (keep it simple)

**`deploy/`:**
- Purpose: Container orchestration and deployment automation
- Contains: docker-compose.prod.yml (production stack), app-deploy.sh (deployment script)
- Key: Runs on EC2 via GitHub Actions; docker-compose files pushed as base64 over SSM

**`terraform/`:**
- Purpose: Infrastructure as code (AWS)
- Contains: EC2 instance, RDS database, ECR registry, security groups, networking, IAM roles
- Key: OIDC auth for GitHub Actions (no hardcoded AWS keys); bootstrap stack (separate) creates VPC

**`tests/`:**
- Purpose: Unit + integration tests (pytest)
- Contains: ~14 test files covering frontend, API, database, auth, compute, deployment
- Key: Tests run on push/PR to main and dev; conftest.py provides shared fixtures

**`eritas-mda-tests/`:**
- Purpose: E2E and user-acceptance tests (separate from unit tests)
- Contains: Cypress, Selenium, or Playwright tests for user workflows
- Key: More comprehensive but slower; runs on staging/QA environments

**`android/`:**
- Purpose: Native Android companion app
- Contains: Kotlin/Compose source, Android resources, Gradle build config
- Key: Builds to APK; force-update gate on main app checks /version endpoint

**`docs/`:**
- Purpose: Internal documentation
- Contains: Operation guides, data schemas, deployment runbooks
- Key: Separate from README.md (high-level public) and CLAUDE.md (project instructions)

**`.planning/codebase/`:**
- Purpose: Generated codebase maps (this directory)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md, STACK.md, INTEGRATIONS.md
- Key: Consumed by /gsd-plan-phase and /gsd-execute-phase for code generation guidance

## Key File Locations

**Entry Points:**
- `app/main.py` - FastAPI app init, middleware, lifespan, static file serving
- `static/home.html` - Public landing page (no auth required)
- `static/login.html` - Admin portal sign-in
- `static/mda.html` - Main campaign dashboard (requires auth or public project)
- `app/sync_worker.py` - Standalone sync job consumer (runs in separate container)

**Configuration:**
- `.env.example` - Safe environment template (copy to .env for local dev)
- `app/config.py` - Environment parsing, app constants, feature gates
- `requirements.txt` - Python dependencies
- `docker-compose.yml` - Local dev stack
- `pytest.ini` - Test runner config

**Core Logic:**
- `app/models.py` - ORM table definitions (40+ tables: projects, boundaries, submissions, users, sync metadata)
- `app/schemas.py` - Pydantic request/response contracts
- `app/database.py` - AsyncSessionLocal, engine config, pool sizing, extension creation
- `app/services/aggregation_engine.py` - LGA/ward/settlement rollups (most frequently called)
- `app/services/spatial_engine.py` - PostGIS queries, coverage analysis
- `app/services/commcare_sync.py` - CommCare OData pull, watermarking logic

**Testing:**
- `tests/conftest.py` - Pytest fixtures (mock database, test data)
- `tests/test_*.py` - Individual test files (one per concern: frontend, API, database, auth, etc.)

**Infrastructure:**
- `terraform/main.tf` - EC2 instance, RDS database, ECR, security groups
- `terraform/bootstrap/main.tf` - VPC, subnets, routing (pre-run before main)
- `deploy/docker-compose.prod.yml` - Production container orchestration
- `.github/workflows/deploy.yml` - Web backend deployment pipeline

## Naming Conventions

**Files:**
- Backend routes: `app/routes/<domain>.py` (e.g., `mda.py`, `analytics.py`, `qc.py`)
- Services: `app/services/<function>.py` (e.g., `aggregation_engine.py`, `commcare_sync.py`)
- Tests: `tests/test_<number>_<concern>.py` (e.g., `test_02_api_backend.py`, `test_04_auth_permissions.py`)
- Terraform: `terraform/<component>.tf` (e.g., `main.tf`, `variables.tf`, `outputs.tf`)
- Frontend: `static/<page>.html` (e.g., `mda.html`, `app-map.html`)

**Functions:**
- Routes: `async def <resource>_<action>(...)` (e.g., `lga_metrics()`, `coverage_by_lga()`)
- Services: `async def <action>_<object>(...)` or `async def compute_<thing>(...)` (e.g., `get_lga_metrics()`, `compute_settlement_analytics()`)
- Filters/helpers: `def _<name>(...)` (leading underscore; internal, not exported)
- Query builders: `def _sql_<thing>(...)` (prefixed to signal SQL construction)

**Variables & Parameters:**
- Database session: `db` (AsyncSession)
- User object: `_u` or `_user` (leading underscore in route kwargs to signal injected dependency)
- Project ID: `pid` or `project_id` (short form in cache keys, full in function params)
- LGA code: `lgacode`, `lga_name` (match column names for clarity)
- Database connection: `conn` (psycopg2 for sync worker; `db` for async API)

**Database Tables:**
- Boundaries: `lgas`, `wards`, `settlements`, `grids` (lowercase, plural)
- MDA submissions: `mda_households`, `mda_individuals`, `mda_baseline` (mda_ prefix, entity-based)
- Metadata: `geo_projects`, `users`, `sync_config`, `sync_history` (entity names)
- Analytics cache: `settlement_analytics` (pre-aggregated; computed post-sync)

**Types:**
- Pydantic schemas: `PascalCase` (e.g., `LgaMetricsOut`, `ProjectCreate`)
- SQLAlchemy models: `PascalCase` (e.g., `GeoProject`, `MdaHousehold`, `User`)
- Enums: `PascalCase` (e.g., `SyncStatus`, `LgaMetricLevel`)

## Where to Add New Code

**New Feature (e.g., "Export coverage as CSV"):**
- Endpoint: `app/routes/analytics.py` (add @router.get("/coverage/csv"))
- Business logic: `app/services/aggregation_engine.py` (add `async def export_lga_coverage_csv(...)`)
- Test: `tests/test_02_api_backend.py` (add `test_export_coverage_csv(...)`)
- Frontend: `static/mda.html` (add button that calls the endpoint, format response as CSV download)

**New Aggregation / Rollup:**
- Service: `app/services/aggregation_engine.py` (add function following LGA/ward/settlement rollup pattern)
- Route: `app/routes/analytics.py` (add endpoint, call service, return JSON)
- Cache: Add decorator `@cache_json("layer_name")` if read-only and expensive
- Test: `tests/test_02_api_backend.py`

**New Quality Check:**
- Logic: `app/services/qc_engine.py` (add check function, flag logic)
- Route: `app/routes/qc.py` (add endpoint to trigger check or fetch flagged records)
- Model: Ensure `mda_households` has a corresponding `flag_<check_name>` column (add via migration or startup script in main.py)
- Test: `tests/test_02_api_backend.py` or `tests/test_04_auth_permissions.py`

**New External Integration (e.g., Kobo Collect sync):**
- Service: `app/services/` (add `kobo_sync.py` following `commcare_sync.py` pattern)
- Route: `app/routes/sources.py` (or create `app/routes/kobo.py`)
- Worker task: Update `sync_worker.py` to dequeue Kobo jobs (or extend job_queue to handle multiple job types)
- Schema: Add source config to `SyncConfig` model if needed
- Test: `tests/test_02_api_backend.py`

**New Component / Module:**
- Implementation: Create file(s) in the appropriate layer (routes, services, or models)
- Exports: Define __all__ in `__init__.py` if module is used across packages
- Tests: Create corresponding test file in `tests/`
- Documentation: Update this STRUCTURE.md if the module is significant

**Utilities:**
- Shared helpers: `app/services/<name>.py` (e.g., `crypto.py`, `health_metrics.py`)
- Database query builders: Small functions at module level or static methods on models (prefer service functions for testability)
- Frontend utilities: `static/<name>.js` (e.g., `idle-logout.js`; keep JS minimal, prefer HTML attributes where possible)

## Special Directories

**`uploads/`:**
- Purpose: Temporary storage for uploaded files (CSV, Excel, Shapefile)
- Generated: Yes (created by upload endpoints in `routes/ingestion.py`)
- Committed: No (.gitignore excludes)
- Retention: Files moved to database or archived after processing; disk space should be monitored

**`apk/`:**
- Purpose: APK artifacts (built by app-build.yml, served by /apk endpoint)
- Generated: Yes (CI/CD builds APK weekly or on-demand)
- Committed: No (.gitignore excludes)
- Contents: Symlink to latest eritas-latest.apk, plus versioned copies

**`.terraform/`:**
- Purpose: Terraform working directory (backend state, plugins, modules)
- Generated: Yes (`terraform init` creates it)
- Committed: No (.gitignore excludes)
- Backend: AWS S3 (state locked and encrypted; accessed via OIDC in GitHub Actions)

**`.ruff_cache/` & `.pytest_cache/`:**
- Purpose: Build/test caches (ruff linter, pytest)
- Generated: Yes (automatic during development)
- Committed: No (.gitignore excludes)

**`.planning/codebase/`:**
- Purpose: Codebase maps (this directory)
- Generated: Yes (`/gsd-map-codebase` writes here)
- Committed: Yes (checked into git; consumed by planning/execution tools)

---

*Structure analysis: 2026-09-08*

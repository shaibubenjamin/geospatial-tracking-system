<!-- refreshed: 2026-09-08 -->
# Architecture

**Analysis Date:** 2026-09-08

## System Overview

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                    Static Frontend Layer (HTML/JS)                         │
│      home.html · mda.html · login.html · app-map.html · app-dashboard.html │
│                      (MapLibre GL JS · Chart.js)                           │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │ HTTPS (bearer token auth)
                               ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Application Layer                         │
│                          `app/main.py` — uvicorn                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Routes (10 modules in `app/routes/`)                              │   │
│  │  • auth, projects, boundaries, ingestion, analytics, qc, mda, sync │   │
│  │  • sources, reports, app_api                                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Services Layer (12 modules in `app/services/`)                    │   │
│  │  • aggregation_engine, boundary_importer, commcare_sync            │   │
│  │  • spatial_engine, qc_engine, geo_cache (in-process TTL)           │   │
│  │  • job_queue (Redis), health_metrics, onprem_mirror, uploads       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Core Infrastructure                                               │   │
│  │  • `app/config.py` — environment & app constants                   │   │
│  │  • `app/database.py` — async SQLAlchemy engine, session factory    │   │
│  │  • `app/models.py` — ORM table definitions                         │   │
│  │  • `app/schemas.py` — Pydantic request/response schemas            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │ Raw queries & ORM
                               ├──────────────────────────┐
                               ▼                          ▼
        ┌──────────────────────────────────────┐   ┌──────────────────┐
        │  PostgreSQL + PostGIS                │   │  Redis           │
        │  (Managed, encrypted, backed up)     │   │  (Job queue +    │
        │                                      │   │   in-process     │
        │  • geo_projects, users              │   │   cache/TTL)     │
        │  • Boundaries: lgas, wards,         │   │                  │
        │    settlements, grids               │   │  • sync_config   │
        │  • MDA data: households,            │   │  • geo_cache     │
        │    individuals, baseline            │   │  • pending syncs  │
        │  • Analytics: settlement_analytics  │   │                  │
        │  • Sync metadata: sync_config,      │   │                  │
        │    sync_history, onprem_mirror_     │   │                  │
        │    state                            │   │                  │
        └──────────────────────────────────────┘   └──────────────────┘
                               ▲
                               │ (incremental writes)
                               │
                    ┌──────────┴──────────┐
                    │                     │
        ┌───────────────────┐    ┌─────────────────┐
        │  Sync Worker      │    │  Manual Upload  │
        │  `app/sync_worker │    │  endpoints      │
        │  .py` (Redis BLPOP)    │ `ingestion.py`  │
        │                   │    │                 │
        │ • CommCare Sync   │    │ • CSV/Excel     │
        │ • Auto-scheduler  │    │ • Shapefile     │
        │ • Retry logic     │    │ • Boundary      │
        └───────────────────┘    │   imports       │
                                 └─────────────────┘
                                        ▲
                                        │
                    ┌───────────────────┴────────────────────┐
                    │ Field Data Sources                     │
                    ├──────────────────────────────────────────┤
                    │ • CommCare HQ (OData primary)          │
                    │ • Kobo, ODK, DHIS2, SurveyCTO, Google │
                    │ • File uploads (CSV, Excel, Shapefile)│
                    └──────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **FastAPI App** | Request routing, middleware stack, lifecycle mgmt | `app/main.py` |
| **Routes** | HTTP endpoint handlers, request validation, auth checks | `app/routes/*` |
| **Services** | Business logic, aggregations, spatial analysis, sync | `app/services/*` |
| **Database** | Async SQLAlchemy config, session factory, extensions | `app/database.py` |
| **Models** | ORM table definitions (projects, boundaries, MDA data) | `app/models.py` |
| **Schemas** | Pydantic request/response contracts | `app/schemas.py` |
| **Config** | Environment parsing, app constants, feature gates | `app/config.py` |
| **Sync Worker** | Redis queue consumer, CommCare delta sync, auto-scheduling | `app/sync_worker.py` |
| **Static UI** | HTML/JS dashboard and mobile webapp | `static/*.html` |
| **Android App** | Native Kotlin/Compose UI, WebView integration | `android/app/` |

## Pattern Overview

**Overall:** Layered API architecture with async I/O, service-based business logic, and background sync via Redis job queue.

**Key Characteristics:**
- **Async-first:** FastAPI + async SQLAlchemy (AsyncSession, async context managers)
- **Request-scoped auth:** JWT bearer tokens, state/LGA-scoped access control, required on protected endpoints
- **Caching layer:** In-process TTL cache (geo_cache) for expensive aggregates, keyed by project + LGA scope + filter params
- **Cache coherence:** Manual cache flush after syncs (geo_cache.clear()), not automatic invalidation
- **Data-as-layers:** Boundary geometries scoped to "boundary projects" (one per state), MDA submissions scoped to campaign projects (state + round)
- **Single active campaign:** Only one geo_project is active (is_active=TRUE) at a time; others visible but read-only
- **Watermarked incremental sync:** CommCare pulls fetch only new rows (modified_on > watermark), idempotent and re-runnable

## Layers

**Presentation Layer (Frontend):**
- Purpose: User-facing dashboards and data explorers
- Location: `static/*.html` (web) + `android/app/` (mobile)
- Contains: HTML templates, embedded JS (MapLibre GL JS, Chart.js), CSS
- Depends on: `/api/*` endpoints, bearer tokens for auth
- Used by: Web browsers, Android WebView, public viewers

**API Layer (Endpoints):**
- Purpose: HTTP request handling, endpoint routing, request/response validation
- Location: `app/routes/` (10 modules organized by domain: auth, projects, mda, analytics, qc, sync, boundaries, etc.)
- Contains: FastAPI APIRouter instances with @app.get/@app.post/@app.put decorators
- Depends on: Services layer, database session, auth decorators
- Used by: Frontend, mobile app, external integrations

**Service Layer (Business Logic):**
- Purpose: Stateless business logic, data aggregations, external integrations
- Location: `app/services/` (12 modules)
- Contains: Functions for sync, aggregation, spatial analysis, caching, QC checks
- Key services:
  - `aggregation_engine.py`: LGA/ward/settlement metric rollups
  - `spatial_engine.py`: PostGIS queries, coverage analysis, geometry operations
  - `qc_engine.py`: Quality checks (duplicate GPS, outside-area, fast-forms, refusals)
  - `commcare_sync.py`: CommCare OData pull, watermarking, delta handling
  - `geo_cache.py`: In-process TTL cache for cold-read performance
  - `job_queue.py`: Redis BLPOP-based task queue (sync enqueuing)
  - `boundary_importer.py`: Shapefile → PostGIS geometry import
  - `uploads.py`: CSV/Excel upload parsing and validation
- Depends on: Database (AsyncSession), external APIs (CommCare), Redis, config
- Used by: Routes layer

**Data Access Layer (ORM + Database):**
- Purpose: Persistent storage, async transaction management, schema definition
- Location: `app/models.py` (ORM), `app/database.py` (session factory + engine config)
- Contains:
  - Models: GeoProject, LGA, Ward, Settlement, Grid, PointRaw (boundaries); MdaHousehold, MdaIndividual, MdaBaseline (submissions); User, SyncConfig, SyncHistory, UploadBatch
  - AsyncSessionLocal factory (pool_size=10, max_overflow=20)
  - 45-second statement_timeout to prevent query runaway
- Depends on: PostgreSQL, PostGIS extension
- Used by: Services, routes, sync_worker

**Background Worker (Async Tasks):**
- Purpose: Long-running sync jobs decoupled from the API
- Location: `app/sync_worker.py` (standalone container process)
- Contains: Redis BLPOP loop, async job timeout (30 min), graceful shutdown
- Depends on: Redis job queue, CommCare API, AsyncSessionLocal, database
- Used by: Triggered by /api/sync/run (enqueues job), auto-scheduler (enqueues on interval)

## Data Flow

### Primary Request Path (Dashboard Analytics Query)

1. **Client request** (`static/mda.html`) → GET `/api/mda/coverage/lga` with bearer token
2. **Authentication gate** (`main.py:require_auth_on_protected_apis`) validates token (`routes/auth.py:decode_token`)
3. **Cache check** (`mda.py:cache_json` decorator) looks up result in Redis by project_id + LGA scope + query params
4. **Cache miss** → calls handler (`routes/mda.py:coverage_by_lga`)
5. **Handler queries database** via AsyncSession:
   - Resolves "boundary project" for the state (via `spatial_engine._resolve_boundary_pid`)
   - Joins LGA geometries + MDA submission analytics (settlement_analytics → ward → LGA rollup)
   - Filters by user's allowed_states/allowed_lgas (access control)
6. **Response returned** (list of LGAs with coverage metrics)
7. **Cache store** → Redis TTL cache (300s default)
8. **Client renders** → MapLibre GL JS heatmap, Chart.js KPI cards

### Sync Data Pipeline

1. **Trigger:** Admin clicks "Run Sync" → POST `/api/sync/run` (route: `routes/sync.py`)
2. **Enqueue:** Job inserted into Redis queue via `job_queue.enqueue_sync_job(project_id)`
3. **Sync worker polls:** `sync_worker.py` runs BLPOP loop (5s timeout), dequeues job
4. **Run sync:** `commcare_sync.run_sync(project_id)` fetches incremental CommCare data:
   - Loads watermark from `sync_config.last_sync_watermark`
   - Calls CommCare OData API: `?filter=modified_on gt datetime'...'`
   - Decrypts CommCare credentials (via `crypto.py`)
   - Parses household/individual records
5. **Write to database:** Async session inserts/updates MDA tables, updates watermark
6. **Recompute analytics:** `spatial_engine.compute_settlement_analytics(project_id)` aggregates:
   - Per-settlement: total_grids, visited_grids (has ≥1 point), completeness_pct, point_count
   - Writes to `settlement_analytics` table
7. **Clear cache:** `geo_cache.clear()` flushes in-process cache TTL entries
8. **Update sync metadata:** Writes to `sync_history`, `sync_config.last_status`, sets `campaign_status`
9. **Dashboard auto-refreshes** via frontend polling /api/sync/status

### QC Check Pipeline

1. **On data ingestion** or manual `/api/qc/check` endpoint
2. **Run quality checks** (`qc_engine.py`):
   - Flag GPS outside bounds: `ST_DWithin(geom, ward_geom, 0) = FALSE`
   - Flag duplicate GPS: `ROW_NUMBER() OVER (PARTITION BY hh_formid, geom ORDER BY received_on)`
   - Flag fast forms: `form_duration_min < 3`
   - Flag after-hours: `EXTRACT(HOUR FROM received_on) NOT BETWEEN 6 AND 18`
3. **Flag records** in `mda_households`: boolean columns `flag_gps_outside_ward`, `flag_duplicate_gps`, `flag_fast_form`, `flag_after_hours`
4. **Dashboard displays** flagged records with drill-down by LGA/team/flag type

**State Management:**
- Active project (is_active=TRUE) drives which project the dashboard defaults to
- User's allowed_states/allowed_lgas filter every read query (enforced at service layer)
- Campaign status (paused/ended) controlled via `campaign_paused`, `campaign_ended` flags
- Sync state (running/success/error) stored in `sync_config` and `sync_history`

## Key Abstractions

**GeoProject:**
- Purpose: Encapsulates a campaign round (state + round number)
- Examples: "Sokoto Round 4" (historical, inactive), "Sokoto Round 5" (active, live data)
- Pattern: One project is active (is_active=TRUE); others visible for historical view
- Fields: state_name, round_number, campaign_start_date, campaign_end_date, campaign_paused, campaign_ended, is_public, show_on_dashboard

**Boundary Layers (LGA/Ward/Settlement/Grid):**
- Purpose: Geographic hierarchy for coverage rollup and map rendering
- Scoped to: One "boundary project" per state (shared by all rounds in that state)
- Pattern: Geometries stored as PostGIS MULTIPOLYGON; queried via ST_Intersects, ST_DWithin
- Queries: Coverage = count(GPS points inside settlement geom) / (grid_count)

**MDA Submission Data (Household/Individual/Baseline):**
- Purpose: Field team submissions from CommCare
- Scoped to: project_id (round-specific)
- Pattern: Household = team + location + form; Individual = person + age/sex + treatment; Baseline = planned targets per settlement
- Structure: Normalized (1:N households:individuals); watermarked on received_on

**Settlement Analytics Cache:**
- Purpose: Pre-aggregated coverage metrics, keyed per settlement per project
- Pattern: Materialized view (computed post-sync), TTL-cached in Redis for API reads
- Columns: total_grids, visited_grids, completeness_pct, point_count
- Refresh: Recomputed by `spatial_engine.compute_settlement_analytics()` after sync finishes

**Sync Configuration & History:**
- Purpose: Track CommCare pull metadata and audit trail
- Pattern: sync_config = ONE row per project (watermark, last_status, last_error); sync_history = append-only log
- Fields: last_sync_watermark (received_on), auto_sync_enabled, auto_sync_interval_minutes, cancel_requested, rows_fetched, rows_new

## Entry Points

**HTTP Endpoints (FastAPI):**
- Location: `app/main.py:app = FastAPI(...)`
- Triggers: Inbound HTTP requests from browser/mobile/external
- Responsibilities: Request routing, middleware chain (auth, CSP, rate limit, version gate), static file serving

**Sync Trigger:**
- Location: `routes/sync.py:POST /api/sync/run`
- Triggers: Admin clicks "Sync Now" button
- Responsibilities: Enqueue CommCare sync job, return 202 Accepted

**Version Gate (Android APK Force-Update):**
- Location: `main.py:_app_version_gate` middleware (runs AFTER auth)
- Triggers: Any request from an Android app with X-App-Version-Code header
- Responsibilities: Check if version < min_version_code (latest published APK); if yes, return 426 Upgrade Required

**Auto-Sync Scheduler:**
- Location: `sync_worker.py:scheduler_loop()` (runs in the sync worker container)
- Triggers: Runs every 60 seconds, checks auto_sync_enabled + interval
- Responsibilities: Enqueue sync for projects where (now - last_synced_at) >= interval_minutes

**Static File Routes:**
- Location: `main.py` (FileResponse handlers for home.html, mda.html, login.html, app-map.html, app-dashboard.html)
- Triggers: GET `/`, `/login`, `/dashboard`, `/mda`, `/app/map`, `/app/dashboard`, `/app-preview`
- Responsibilities: Serve HTML with session detection (PUBLIC_MODE = absent/expired token)

## Architectural Constraints

- **Threading:** Single-threaded async event loop per uvicorn worker (no thread pool by default; CPU-bound tasks moved to sync_worker or pre-computed)
- **Global state:** `geo_cache` (in-process TTL dict) is process-local; multi-worker deployments must clear cache cooperatively or via Redis
- **Circular imports:** Avoided via local imports in functions (e.g., `from app.services.project_scope import in_scope_lgas_for` inside aggregation_engine.py)
- **Pool saturation:** AsyncSessionLocal pool_size=10, max_overflow=20; long queries are capped at 45s statement_timeout to prevent holding connections
- **Database encoding:** UTF-8; geo settlement names with diacritics (Ë, Ü, etc.) preserved
- **PostGIS SRID:** All geometries in EPSG:4326 (WGS84 lat/lon); no on-the-fly reprojection
- **Cascading deletes:** geo_projects has cascade="all, delete-orphan" on all relationships; deleting a project cascades to boundaries and submissions

## Anti-Patterns

### Treating is_active as "only accessible" project

**What happens:** Code assumes only the active project contains valid data; historical rounds are skipped
**Why it's wrong:** Historical rounds are valid for comparison and trend analysis. The active flag is just the dashboard default.
**Do this instead:** Always query by explicit project_id. Use is_active only to set the dashboard's default project on page load.

### Ignoring user.allowed_states / allowed_lgas scope

**What happens:** A state-scoped analyst's query returns data from all states; LGA-scoped staff see all LGAs in their state
**Why it's wrong:** Access control becomes a no-op, leaking data to users who should be restricted
**Do this instead:** Call `allowed_states_of(user)` and `allowed_lgas_of(user)` on every query, filter in the WHERE clause. See `aggregation_engine.py` for the pattern.

### Caching aggregate queries without flushing on sync

**What happens:** After a sync lands, the dashboard shows old numbers until the TTL expires (5 min)
**Why it's wrong:** Operators expect live updates when they see "Last synced: just now"
**Do this instead:** Call `geo_cache.clear()` after every sync completes (already done in `commcare_sync.py:run_sync()`)

### Using form_duration_min < 5 for fast-form detection

**What happens:** Honest quick visits get flagged as suspicious; QC noise confuses the operator
**Why it's wrong:** The threshold was operator-tuned to 3 min over Sokoto campaigns; different settings may apply elsewhere
**Do this instead:** Make thresholds configurable per project (not yet implemented; currently hardcoded). See CONCERNS.md.

### Joining MDA data across different projects without project_id filter

**What happens:** A household's submitted form matches settlement geometry from a different round; counted twice in coverage
**Why it's wrong:** Each project (round) has its own instance of every settlement; they must be joined on (project_id, geom), not just geom
**Do this instead:** Always include project_id in the join predicate. See `spatial_engine.py:get_coverage_summary()` for the pattern.

## Error Handling

**Strategy:** Async exception handling with structured logging (JSON to CloudWatch/Sentry)

**Patterns:**
- **Route handlers:** Catch IntegrityError (duplicate key, constraint violation) → return 400 with specific field error. Unhandled exceptions → 500 logged to Sentry.
- **Service layer:** Propagate exceptions; let the route layer decide 4xx vs 5xx. For sync, catch + log to sync_history, don't re-raise.
- **Database access:** AsyncSession.rollback() on exception; connections returned to pool for reuse
- **Sync pipeline:** Every step wrapped in try/except, result + error message written to sync_config + sync_history; next job starts regardless
- **API errors:** Return JSONResponse with `{"detail": "..."}` for auth/validation failures; 401 with WWW-Authenticate header for token issues

## Cross-Cutting Concerns

**Logging:** Structured JSON (ts, level, logger, msg, request_id, exc) via `_JsonFormatter` in main.py. CloudWatch/Loki/Datadog parse automatically. Request ID propagated via X-Request-ID header (UUID if absent).

**Validation:** Pydantic schemas enforce request shape + basic types (str, int, date). Business logic validation (e.g., "settlement must exist in project") happens in service layer and returns specific 4xx errors.

**Authentication:** JWT bearer tokens (expire after ACCESS_TOKEN_EXPIRE_MINUTES). Decoded in `auth.py:decode_token()`. State/LGA scope attached to token payload; decoded and enforced on every protected read.

**Rate Limiting:** slowapi middleware (120 req/min per IP by default) applied globally. Disabled if slowapi not installed (logged as warning). Individual endpoints can add @limiter decorators for stricter limits.

**CORS:** Allowlist-based (explicit origins, not "*"). Defaults to first-party domains; expanded via ALLOWED_ORIGINS env var. Credentials allowed; wildcard origins rejected.

**CSP (Content Security Policy):** Lenient to allow inline scripts + CDN (MapLibre, Chart.js). Tightened for specific paths (e.g., /app/map CSP allows blob: for MapLibre workers). nonce-based CSP not yet implemented.

---

*Architecture analysis: 2026-09-08*

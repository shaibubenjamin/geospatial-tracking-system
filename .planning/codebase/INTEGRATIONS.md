# External Integrations

**Analysis Date:** 2026-09-08

## APIs & External Services

**CommCare HQ (Form Data Sync):**
- CommCare OData API - Primary data source for MDA household and individual records
  - SDK/Client: `httpx` async HTTP client
  - Implementation: `app/services/commcare_sync.py` pulls OData feeds for configured projects
  - Auth: Basic auth (username/password) stored encrypted in database
  - Credentials stored in: `sync_config` table (encrypted with `SYNC_ENCRYPTION_KEY`)
  - Env vars: `SYNC_ENCRYPTION_KEY` (symmetric Fernet key for credential storage)

## Data Storage

**Databases:**
- PostgreSQL 14+ with PostGIS extension (production on AWS RDS)
  - Connection: `DATABASE_URL` environment variable
  - Async client: `asyncpg` (API container on port 8080)
  - Sync client: `psycopg2-binary` (sync_worker, `app/sync_worker.py`)
  - ORM: SQLAlchemy 2.0+ with async support
  - Geospatial: GeoAlchemy2 for PostGIS integration
  - Dev/staging: RDS tunnel via `scripts/dev-aws.sh` (bastion EC2 SSH tunnel)
  - Models defined in: `app/models.py`

**File Storage:**
- Local filesystem: `/app/uploads` directory in containers (persisted as Docker volume)
- Production: EBS volume mount at `/app/uploads` on EC2
- S3 bucket: APK distribution (`aws_s3_bucket.apk` in `terraform/apk.tf`)
  - Signed APK uploaded by `app-build.yml` GitHub Actions
  - Served at `/apk` endpoint from the API

**Caching:**
- Redis 7-alpine (in-memory cache + job queue)
  - Connection: `REDIS_URL` environment variable (defaults to `redis://redis:6379/0`)
  - Purpose: CommCare sync job queue (reliable work-queue for long-running syncs)
  - Client libraries: `redis` (sync) and `redis.asyncio` (async)
  - Implemented in: `app/services/job_queue.py`

## Authentication & Identity

**Auth Provider:**
- Custom JWT-based authentication (no external OAuth/OIDC for users)
  - Implementation: `app/routes/auth.py`
  - Token signing: PyJWT with HS256 algorithm
  - Password hashing: bcrypt
  - Token storage: HTTP cookies (via FastAPI)
  - Session management: JWT tokens with configurable expiry (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 480)

**API Authentication (Android App):**
- Header-based version gating: `X-App-Version-Code` header
  - Enforced at: APP_API_PREFIX endpoints (`/api/app/*`)
  - Returns 426 Upgrade Required if version below `MIN_VERSION_CODE`
  - Configured in: `app/config.py`

**AWS Authentication:**
- OIDC (OpenID Connect) for GitHub Actions CI/CD
  - Role: `arn:aws:iam::387526361725:role/mda-dashboard-github-deploy`
  - Trust policy configured in: `terraform/github-oidc.tf`
  - Scope: No long-lived AWS keys in repo; all auth via temporary OIDC tokens

**User Access Control:**
- Role-based: admin, analyst, viewer, field_supervisor (defined as role strings in database)
- LGA-scoped logins: Per-user `allowed_states` and `allowed_lgas` CSV columns
- State-based scoping via `allowed_states` column in users table

## Monitoring & Observability

**Error Tracking:**
- Sentry (optional integration)
  - Enabled when: `SENTRY_DSN` environment variable is set
  - SDK: `sentry-sdk[fastapi]` 2.18.0
  - Configuration: `app/main.py` lines 406-418
  - Trace sampling: `SENTRY_TRACES_SAMPLE_RATE` (default 0.05 = 5%)
  - Graceful degradation: If SENTRY_DSN is missing or sentry-sdk not installed, app runs normally

**Logs:**
- Structured JSON logging to stdout
  - Formatter: `_JsonFormatter` in `app/main.py` emits one JSON object per log line
  - Fields: `ts`, `level`, `logger`, `msg`, `request_id` (if present), `exc` (if exception)
  - Destination: CloudWatch (production) via Docker logging driver
  - Log group: `/mda-dashboard/app` (configured in `deploy/docker-compose.prod.yml`)

**Rate Limiting:**
- SlowAPI middleware (`slowapi` 0.1.9)
  - Applied to API endpoints to prevent abuse
  - Configuration in: `app/main.py`

## CI/CD & Deployment

**Hosting:**
- AWS EC2 (single instance for web + background work)
  - Instance: `aws_instance.app` (tag-resolved in deploy scripts)
  - Region: us-east-1
  - Security: Bastion host for SSH access, SSM Run Command for deployments
  - APK host: Separate S3 bucket + EC2 directory `/var/app-apk`

**Container Registry:**
- AWS ECR (Elastic Container Registry)
  - Repository: `mda-dashboard` in account `387526361725`
  - Tags: Latest (per commit SHA), `latest` tag
  - Pulled by: Production EC2 via IAM role (`aws_iam_role_policy.secrets_read`)

**CI Pipeline:**
- GitHub Actions (`/.github/workflows/`)
  - `ci.yml` - Runs on push/PR to `main` and `dev` (lint + pytest)
  - `deploy.yml` - Runs on push to `main` (build Docker image, push to ECR, deploy to EC2)
  - `app-build.yml` - Runs on push to `apk_dev` (build + sign APK, push to S3/EC2)
  - Authentication: OIDC token exchange (no SSH keys stored)

**Deployment Mechanism:**
- SSM Run Command: GitHub Actions runner invokes AWS Systems Manager to push config + restart containers
- Health checks: `localhost:8080/api/health` (Python urllib in production healthcheck)
- Rollback: Manual via pushing a previous commit or re-running workflow with explicit image_tag

## Environment Configuration

**Required env vars:**
- `DATABASE_URL` - AsyncPg connection string (e.g., `postgresql+asyncpg://user:pass@host:5432/db`)
- `DATABASE_URL_SYNC` - Psycopg2 connection string (sync_worker, used in `app/services/commcare_sync.py`)
- `SECRET_KEY` - ≥32 bytes for JWT signing (required in production, auto-generated dev fallback)
- `SYNC_ENCRYPTION_KEY` - Fernet symmetric key for CommCare credential storage
- `REDIS_URL` - Redis connection string (defaults to `redis://redis:6379/0`)
- `ENVIRONMENT` - "production", "staging", or "development" (affects behavior)

**Optional env vars:**
- `SENTRY_DSN` - Sentry error tracking URL (integration disabled if unset)
- `SENTRY_TRACES_SAMPLE_RATE` - Float 0-1 for trace sampling (default 0.05)
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` - Email notifications
- `REPORT_RECIPIENTS` - Comma-separated email list for concern reports (overrides hardcoded recipients)
- `MIN_VERSION_CODE`, `LATEST_VERSION_CODE`, `LATEST_VERSION_NAME` - Android app versioning
- `APP_API_PREFIX` - Prefix for app-only endpoints (default `/api/app/`)
- `APK_DIR` - Directory where signed APK is served from (default `./apk/`)
- `APK_FILENAME` - Filename of the stable APK release
- `LOG_LEVEL` - Python logging level (default "INFO")
- `ONPREM_BACKUP_DATABASE_URL` - On-prem Postgres for reverse-mirror (dev only, leave blank in production)

**Secrets location:**
- Production: AWS Secrets Manager
  - Retrieved by: `deploy/refresh-env.sh` via IAM role
  - Injected into: `/opt/mda-dashboard/.env` before container start
- Development: `.env` file (local only, not committed)
- Terraform secrets: `terraform/terraform.tfvars` (not committed, referenced by variable names in docs)

## Webhooks & Callbacks

**Incoming:**
- `/api/sync/run` - Manual trigger for CommCare sync (POST from UI)
- `/api/sync/stop` - Cooperative stop signal for in-flight syncs (POST from UI)
- `/api/concerns/report` - Report a data quality concern (POST from field)

**Outgoing:**
- Email notifications via SMTP to `REPORT_RECIPIENTS` when concerns are filed
  - Implementation: `app/services/notifier.py`
  - Fail-soft: Email errors logged but don't block the HTTP response
- APK distribution: `/apk` serves signed APK to field devices
  - Versioning: Enforced by `MIN_VERSION_CODE` on app-only endpoints

## Third-Party SaaS Integrations

**Mapping & Geospatial:**
- MapLibre GL JS 3.6.2 - Vector mapping library (web dashboard)
  - CDN: `https://unpkg.com/maplibre-gl@3.6.2/`
  - Basemaps: OpenStreetMap (or configured alternative)
  - Used in: `static/mda.html`, `static/app-map.html`

**Charting:**
- Chart.js 4.4.1 - Chart rendering (web dashboard)
  - CDN: `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/`
  - Plugin: chartjs-plugin-datalabels 2.2.0 for data labels
  - Used in: `static/mda.html` for coverage trends, team performance

**Fonts & Icons:**
- Google Fonts (Inter family)
  - CDN: `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900`
- Font Awesome 6.5.0 - Icon library
  - CDN: `https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css`

---

*Integration audit: 2026-09-08*

# Codebase Concerns

**Analysis Date:** 2026-09-08

## Tech Debt

### Monolithic MDA Routes File
- Issue: `app/routes/mda.py` contains 4,840 lines of endpoint handlers for all MDA data endpoints (QC, coverage, trends, team performance, etc.). This makes testing, code review, and maintenance difficult.
- Files: `app/routes/mda.py`
- Impact: Changes to one endpoint risk affecting others; testing requires large integration test suites; difficult to understand request flow and dependencies.
- Fix approach: Split into focused modules by feature (e.g., `app/routes/mda_coverage.py`, `app/routes/mda_qc.py`, `app/routes/mda_trends.py`), each with its own cache/helper functions. Keep a thin `mda.py` that imports and registers routers.

### Global In-Flight Cache Dict Without Multi-Process Safety
- Issue: `app/routes/mda.py:33` uses module-level `_INFLIGHT: dict[str, asyncio.Future]` for single-flight request deduplication. This dict is not process-safe and will not prevent cache stampedes if uvicorn runs with `--workers > 1`.
- Files: `app/routes/mda.py` lines 33, 83-91, 103
- Impact: If application is scaled to multiple uvicorn worker processes, concurrent requests across processes will each run the same heavy query (cache stampede), returning to original 9-33s query times under load.
- Fix approach: Migrate to a Redis-backed distributed cache (`redis://` via the existing REDIS_URL env var) for the `_INFLIGHT` lock, or ensure a single-worker architecture constraint is documented and enforced in deployment config.

### Bare `except Exception:` Blocks
- Issue: 20+ bare `except Exception:` clauses without specific exception types across the app (e.g., `app/database.py:40`, `app/sync_worker.py:119,121,222,239`). These mask bugs and make error handling strategy unclear.
- Files: `app/database.py`, `app/sync_worker.py`, `app/routes/sync.py`, `app/main.py`, `app/routes/reports.py`, `app/routes/ingestion.py`, `app/routes/mda.py:4620,4625`
- Impact: Silent failures or unclear error recovery paths; exceptions might be swallowed, leaving the system in an inconsistent state.
- Fix approach: Replace with specific exception types (`except (TimeoutError, IntegrityError, ...) as e:`) and log/re-raise or recover explicitly. Use `# noqa: BLE001` only where catch-all is intentional.

## Performance Bottlenecks

### Settlement Analytics Recompute — Asymmetric Timeouts
- Problem: The API has a 45s statement timeout (`app/database.py:21`) but the sync worker's settlement_analytics rebuild runs under a 300s timeout (`app/services/commcare_sync.py:808`). This asymmetry creates two issues:
  1. If an API query accidentally runs the full recompute logic (instead of reading cached results), it will be killed after 45s, corrupting data.
  2. The 300s recompute is still a bottleneck for large states, though it was optimized from 40+ minutes in 2026-07-04.
- Files: `app/database.py:21`, `app/services/commcare_sync.py:808`, `app/sync_worker.py:93`
- Impact: Long syncs can delay all other operations on the worker; if the recompute is still slow on very large states (>40k settlements), it may hit the 300s timeout and leave incomplete data.
- Fix approach: Monitor production 300s timeout hits in CloudWatch (`/mda-dashboard/ec2-sync` logs); if observed, further optimize the spatial join or split settlement_analytics into background batches. Ensure the API never runs recompute logic (cache misses should not trigger rebuilds).

### Cache Stampede Risk at Scale
- Problem: The `cache_json` decorator in `app/routes/mda.py` was added to solve a cache-stampede issue where concurrent requests on cold cache entry caused 9→33s query times. The single-flight deduplication within one process is working, but the solution is not cluster-aware.
- Files: `app/routes/mda.py:36-106`
- Impact: If the application scales to multiple EC2 instances or multiple uvicorn workers, the cache stampede returns (each worker's process-local dict is independent).
- Fix approach: Monitor P99 response times on aggregate endpoints (coverage, QC, trends, etc.); if queries exceed 5s under concurrent load, migrate to distributed single-flight via Redis GETEX or similar.

## Security Considerations

### Terraform Variables File Exposed on Developer Machine
- Risk: `terraform/terraform.tfvars` (gitignored but present on disk) contains plaintext CommCare credentials and SSH public key, as well as developer IP addresses (CIDR blocks).
- Files: `terraform/terraform.tfvars` (contains: `commcare_username`, `commcare_password`, `ssh_public_key`, `ssh_allowed_cidrs`)
- Current mitigation: File is in `.gitignore` so not committed; accessed only during terraform apply.
- Recommendations:
  1. Rotate CommCare credentials immediately (file comments state the password should have been rotated at platform launch).
  2. Use Terraform Cloud/Enterprise for state management with encrypted at-rest variables instead of local tfvars.
  3. Consider using AWS Secrets Manager for CommCare credentials (already done for some secrets via `app/config.py`, but CommCare seed is still in tfvars).
  4. Document that tfvars must be regenerated per-environment and never shared.

### F-String SQL Templates with Dynamic WHERE Clauses
- Risk: `app/routes/mda.py` constructs SQL WHERE clauses using f-strings (e.g., lines 986, 1071, 1147, 1292, 1409, etc.) with variables like `{lga_b}`, `{DATE_CLAUSE}`, `{state_filter_sql}`. While parameterized values are passed separately via the `params` dict, the column/table fragments themselves are string-interpolated.
- Files: `app/routes/mda.py` (lines 986, 1071, 1147, 1292, 1409, 1460, 1503, 1567, 1578, 1638, etc.)
- Current mitigation: Column names are hardcoded strings (not user input), so SQL injection via WHERE clauses is mitigated. The `_lga_and()` helper properly uses `:lgascope` parameter binding (line 323).
- Recommendations:
  1. Document this pattern clearly (it's not obviously safe to future maintainers).
  2. Audit new WHERE clause construction to ensure no user input is interpolated into column/table names.
  3. Consider moving to SQLAlchemy's Core expression language (`.select()`, `.where()`) instead of f-string text() to eliminate this risk class entirely.

### Credentials in Environment Docs
- Risk: `docs/CREDENTIALS.md` mentions hardcoded EC2 instance ID `i-0f57573ce98580bfc` and specific AWS IAM principals, potentially leaking internal infrastructure details if the docs are shared.
- Files: `docs/CREDENTIALS.md`, `CLAUDE.md` (also mentions the instance ID)
- Current mitigation: File is in `.gitignore` (if configured as such) and is internal documentation.
- Recommendations:
  1. Update `docs/CREDENTIALS.md` to use placeholder instance names (e.g., "mda-dashboard-app") or remove hardcoded IDs, since `deploy.yml` now resolves by tag.
  2. Move sensitive setup docs to a separate internal wiki or 1Password/secrets manager.

## Fragile Areas

### APK WebView Rendering Limitations
- Files: `static/app-map.html`, `android/...` (WebView integration)
- Why fragile: Android WebView on the field devices will not render Leaflet (raster tiles + Canvas2D) or MapLibre GL JS (WebGL) — the canvas paints blank. The current workaround is a pure-DOM LGA→Ward→Settlement drill-down (no visual map).
- Safe modification:
  1. Do NOT re-add Leaflet/MapLibre to the Map tab; the DOM drill-down is the only working approach on this hardware.
  2. Never set `height:100%` or use `position:absolute; inset:0` for full-height layout in the WebView — use natural document flow + `position:sticky` header.
  3. Test any layout changes on actual field devices (emulator may behave differently).
- Test coverage: Manual testing on physical devices required; no automated tests for WebView rendering.

### Branch Drift: `apk_dev` Static Files Out of Sync with `main`
- Issue: The `apk_dev` branch is a full checkout of the monorepo but only `android/**` receives active commits. The static web files (`static/mda.html`, `static/mda-admin.html`, etc.) on `apk_dev` gradually become stale relative to `main` because they're only updated when `apk_dev` is merged from `main` or vice versa.
- Files: All files in `static/` on the `apk_dev` branch
- Impact: When a developer works on the app and the in-app WebView endpoints hit `/mda?app=1`, they hit stale versions if the running server was deployed from `apk_dev` (before a `main` merge that updated static assets). The APK itself always fetches from the deployed server (not from the local in-image copy), so it gets the live version, but the risk is confusion during development.
- Safe modification:
  1. Before starting app work, run `git fetch origin main && git merge origin/main` to bring `apk_dev` current with `main`.
  2. Do NOT commit changes to `static/**` on `apk_dev` unless they are app-specific (e.g., app-mode CSS in `mda.html`). Web-only changes go on `dev`.
  3. Document that `apk_dev` should always track `main` for non-Android files.

### Hardcoded EC2 Instance ID in Documentation (Fixed in Deploy, Stale in Docs)
- Issue: `CLAUDE.md:61` and `docs/CREDENTIALS.md` mention a specific hardcoded instance ID `i-0f57573ce98580bfc`.
- Files: `CLAUDE.md:61`, `docs/CREDENTIALS.md:59,205,278,297`
- Current status: The issue is FIXED in `deploy.yml:48-56` (instance ID is resolved dynamically by tag `mda-dashboard-app`). The hardcoded IDs in docs are now stale.
- Impact: Low (the deploy pipeline is correct). Stale docs could mislead if someone manually runs SSM commands.
- Fix approach: Update `CLAUDE.md:61` and `docs/CREDENTIALS.md` to reflect tag-based resolution. Search for any scripts that may hardcode the instance ID and migrate them to tag-based lookup.

## Test Coverage Gaps

### Limited API Integration Test Coverage
- What's not tested: The 143 async handlers in `app/routes/` are covered by 14 test files with ~3,466 total lines of test code (1:4 app-to-test ratio). Key gaps:
  1. No explicit tests for cache invalidation on sync completion (`geo_cache.clear()` calls).
  2. Limited testing of concurrent requests hitting the same cache key (the `_INFLIGHT` single-flight deduplication is not tested).
  3. No tests for the `cache_json` decorator behavior under cache-hit/miss/exception scenarios.
  4. Minimal coverage of LGA-scoped filtering edge cases (empty LGA list, malformed LGA names, LGA not in allowed set).
  5. No E2E tests for the full sync → settlement_analytics → cache invalidation → API response chain.
- Files: `tests/` (all test files), `app/routes/mda.py` (cache_json, _inflight, _lga_and)
- Risk: Cache misses, multi-user filtering bugs, and concurrent access issues are not caught until production.
- Priority: Medium (the app has been in production; high-priority bugs would have surfaced).
- Remediation approach:
  1. Add `test_cache_json_decorator.py` with fixtures for hit/miss/exception, concurrent requests, and cache invalidation.
  2. Add `test_lga_scoped_filtering.py` with edge cases (empty, blank, malformed, out-of-scope LGAs).
  3. Add sync integration tests that validate cache is cleared after `run_sync()` completes.

### Missing Async Exception Testing
- What's not tested: The pattern of wrapping async operations in `asyncio.wait_for(timeout=...)` (e.g., `app/sync_worker.py:93`) is tested only for success cases. Timeout exceptions and their recovery paths (marking sync as errored) are not explicitly tested.
- Files: `tests/test_07_cicd_version_control.py`, `app/sync_worker.py:88-111`
- Risk: A regression in timeout handling could leave syncs stuck in 'running' state indefinitely.
- Priority: Medium.

### No Load/Stress Tests
- What's not tested: Query performance under concurrent load, connection pool exhaustion, and cache effectiveness under N concurrent viewers.
- Files: No load test suite (e.g., `load_tests/` or a Locust/k6 script).
- Risk: Performance bottlenecks are discovered in production (the 9→33s query slowdown on cold cache was discovered by users, not in tests).
- Priority: Low (no budget for performance infrastructure in initial launch), but should be considered for Phase 2.

## Scaling Limits

### API Connection Pool at Capacity Under Concurrent Load
- Current capacity: `app/database.py:9-10` defines `pool_size=10, max_overflow=20` (max 30 connections from the API to Postgres).
- Limit: With 30 concurrent API requests running slow aggregation queries (30-90s), all 30 connections are exhausted. The 31st request queues until one completes. A cache miss on a popular view (e.g., during a deploy) causes a stampede that fills the pool.
- Scaling path:
  1. Short term: Increase `pool_size` (currently 10 + 20 overflow = 30), but this just delays the problem.
  2. Long term: Implement distributed query result caching (Redis) and migrate heavy aggregations to a read-only replica or dedicated analytics database.
  3. Immediate: Ensure cache TTL (300s) is adequate for the deployment pattern (sync → deploy → cache clear → N concurrent users hitting cold cache).

### Settlement Analytics Recompute Duration at Scale
- Current capacity: Kano R3 (36 LGAs, 28k settlements, ~770k grid cells) recomputes in ~9s after optimization (was 40+ minutes pre-2026-07-04). Very large states with 100k+ settlements may still be slow.
- Limit: If a future state doubles in size, the recompute could approach or exceed the 300s timeout, causing incomplete settlement analytics data and stale settlement maps.
- Scaling path:
  1. Monitor Kano R3 and future large-state recompute durations; log slow queries to CloudWatch.
  2. If durations exceed 60s, consider batching the recompute by LGA (parallelizable via sync_worker job queue).
  3. Move settlement_analytics to a separate materialized view that can be refreshed independently of the main sync.

## Missing Critical Features / Known Limitations

### APK Visual Map Rendering
- Problem: The Android app cannot display a visual map (Leaflet, MapLibre GL JS, or any WebGL-based map engine render blank on the field devices). Users rely on the pure-DOM LGA→Ward→Settlement drill-down for spatial browsing, which is less intuitive than a visual map.
- Blocks: Rich map interactions (pan, zoom, tap-on-map to select settlement), visual confirmation of GPS points on a map background.
- Workaround: The drill-down provides the same coverage data; users navigate by text instead of visually.
- Long-term fix: Investigate WebView rendering issues on the specific device models in use (Galaxy Tab, etc.) — may require a different map engine or native map rendering (Android's Google Maps API).

### Hardcoded Update Gate for APK Versions
- Problem: The `MIN_VERSION_CODE` floor is now tied to the latest published APK (`_effective_min()` in `app/main.py`), so every new APK release force-updates all older installs. While this ensures all users are on current, it doesn't allow for gradual rollouts or emergency downgrades.
- Blocks: A/B testing, gradual rollout, downgrade recovery (e.g., if a buggy version is published, old versions can't be reverted to).
- Workaround: Manual intervention in Secrets Manager to override `MIN_VERSION_CODE` if needed (kill-switch).
- Fix approach: Implement a version strategy table in the database (`app_versions` table) that allows:
  1. Specifying min/latest per app channel (e.g., "beta" vs "stable").
  2. Time-based or percentage-based rollouts (e.g., force-update 10% of users first).
  3. Downgrade windows (allow downgrade-to for N days if a rollback is needed).

### LGA-Scoped Login Credentials Not Rotated
- Problem: LGA-scoped user accounts (e.g., username=`kano`, password=`Kano@2026`) are created via manual database tunnel and not rotated through the UI or an automated process. The password pattern is documented in `CLAUDE.md`, increasing the risk of guessing.
- Blocks: Password rotation without manual DB intervention; per-LGA teams requesting password self-service.
- Fix approach: Add an admin endpoint `/api/users/{user_id}/reset-password` (with audit logging) and allow LGA admins to request resets.

---

*Concerns audit: 2026-09-08*

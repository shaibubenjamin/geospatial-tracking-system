# CLAUDE.md

Guidance for Claude (and humans) working in this repo. Read this first.

**Product:** ERITAS — **E**vidence through **R**eal-time **I**ntelligence, **T**racking, and **A**ccountability **S**ystems (formerly SARMAAN). Sokoto State MDA geospatial coverage & data-quality platform. FastAPI · PostGIS · MapLibre GL JS · Chart.js, plus an Android companion app.

---

## Branches

Two long-lived dev lines, both PR'd into `main`:

| Branch | Owns | Notes |
|--------|------|-------|
| `dev` | **Web platform** — FastAPI backend + `static/*.html` dashboard | Web-platform changes go here |
| `apk_dev` | **Android companion app** — `android/…` + app-only server bits (`/app/map`, `app-map.html`, `/api/app/*`) | App changes go here |
| `main` | Release / deploy target | Pushes here trigger deploys (see below) |

**Workflow rules:**
- Work **directly on `dev` or `apk_dev`** — do **not** create per-feature branches (`feat/...`, `infra/...`). They cause branch sprawl and force a "sync dev with main" dance. (User explicitly called this out 2026-05-21.)
- New change → checkout the branch that owns that surface, commit, push, then `gh pr create --base main --head <dev|apk_dev>`, merge.
- Put a fix on the branch that owns the surface: dashboard map CSP fix → `dev`; WebView / `app-map.html` change → `apk_dev`.
- Don't carry one stream's uncommitted changes onto the other — **stash before switching**.
- Only create a separate branch for genuinely parallel work-streams (e.g. emergency hotfix during a larger refactor) — and call it out to the user first.

---

## Remotes & QA propagation

| Remote | Repo | Role |
|--------|------|------|
| `origin` | `shaibubenjamin/geospatial-tracking-system` | Primary; PR target |
| `eha` | `eHealthAfrica/sarmaan_mda_geospatial_tracking_system` | Upstream where **QA runs** (`eha/main`) |

`origin` and `eha` are **NOT linked as GitHub forks** (different repo names), so a cross-fork PR fails. They share history, so mirror via a direct fast-forward push:

```bash
# verify first
git merge-base --is-ancestor eha/main origin/main
# then mirror
git push eha origin/main:main
```

There is **no `eha/dev`** branch and the user does not want one.

**Full release flow:** commit on `dev` → PR `dev → origin/main` → merge → mirror `origin/main → eha/main` so QA covers the update.

---

## Deploy (branch-triggered GitHub Actions — no SSH, no keys)

AWS auth is **OIDC** (role `arn:aws:iam::387526361725:role/mda-dashboard-github-deploy`). No long-lived AWS keys in the repo. Infra lives in `terraform/`. Three workflows in `.github/workflows/`:

### `ci.yml` — gate
Runs on push/PR to `main` and `dev`. Ruff lint (informational, non-blocking) + pytest.

### `deploy.yml` — web backend
Runs on **push to `main`** (or manual `workflow_dispatch` with an `image_tag`).
1. Build Docker image → push to ECR `387526361725.dkr.ecr.us-east-1.amazonaws.com/mda-dashboard` (tags: 7-char SHA + `latest`).
2. Roll the container on EC2 `i-0f57573ce98580bfc` (us-east-1) via **AWS SSM Run Command**.
3. Ships `deploy/docker-compose.prod.yml` → `/opt/mda-dashboard/docker-compose.yml` (base64 over SSM) so new services aren't silently ignored.
4. Health-checks `localhost:8080/api/health`.
- **Live:** https://eha-mda-dashboard.ehealthnigeria.org

### `app-build.yml` — Android APK
Runs on **push to `apk_dev`** touching `android/**` (or manual dispatch). Deliberately **separate** from the backend deploy — building the app never deploys server code and vice versa.
- Builds + signs the release APK (`versionCode = git rev-list --count HEAD`, `versionName = git describe --tags`).
- Uploads as a workflow artifact; if the `APK_S3_BUCKET` Actions var is set, stages to S3 → pulls onto EC2 via SSM → promotes to `eritas-latest.apk` served at **`/apk`**.

### Deploy mental model
- Merge `dev → main` ⇒ web backend auto-deploys to EC2.
- Push to `apk_dev` (android paths) ⇒ APK auto-builds/publishes.
- They are **independent pipelines on different branches.**

---

## Local development

```bash
cp .env.example .env
docker-compose up -d        # api :8090→8080 · postgis :5433→5432 · redis :6380→6379
```

Open http://localhost:8090. Logins: `admin/admin123`, `analyst/analyst123`, `viewer/viewer123` (change before any shared env). See [README.md](README.md) for boundary loading, data upload, DB credentials, and project structure.

# ERITAS Field Coverage App — Blueprint (v0.1)

> Status: **v0.1 PILOT BUILT.** End-to-end implementation on branch `apk_dev`:
> the server-side OTA system + gated `/api/app` surface, and the full Kotlin/
> Compose app under `android/`. See "Implementation status" below.

## Implementation status (v0.1)

| Area | State | Where |
|---|---|---|
| Version gate (426) + `/version` + `/apk` host | ✅ built, 17 tests passing | `app/main.py`, `app/config.py` |
| Gated app API (`projects`, `overview`, `geo/wards`, `near`) | ✅ built | `app/routes/app_api.py` |
| Env + prod APK volume + serve dir | ✅ built | `.env.example`, `deploy/docker-compose.prod.yml`, `apk/` |
| Android app (login, selector, dashboard, map, My Area, update wall) | ✅ built | `android/` |
| App-build CI (signed APK, git versions, S3→SSM publish) | ✅ built | `.github/workflows/app-build.yml` |
| Build runbook + signing + force-update | ✅ documented | `android/README.md` |

Not yet done (intentional for v0.1): offline caching, a local compile/emulator
run of the Android app (no JDK/Gradle in the authoring env — verified by Android
Studio / CI), and a real signing keystore (team-owned secret).

## Purpose

An Android companion app to the ERITAS MDA monitoring platform. It does **not**
collect survey data — CommCare remains the system of record. The app is a
**field-coverage aid**: it helps a team member, from their current GPS position,
see **which ward/settlement they are in, how covered it is, and where is left to
cover**. Secondary: a read-only dashboard overview and a geospatial coverage map
mirroring the web dashboard.

**Multi-campaign by design.** The app is **not** pinned to one state or round.
It works for **any state + round** the platform holds (e.g. Sokoto R4, Sokoto
R5, and future states/rounds). Every screen is scoped to a user-selected
project; the app defaults to the active project and lets the user switch.

---

## 0. Feasibility verdict

**Feasible, and a clean fit.** The backend already exposes nearly everything a
coverage-aid app needs, as public aggregate endpoints behind the existing gate:

- Boundary GeoJSON per project: `/api/projects/{id}/boundaries/{lga|ward|settlement|grid}/geojson`
- Coverage status: `/api/mda/coverage/{lga,ward}`, `/api/mda/geo/completeness`,
  `/api/mda/geo/settlement-breakdown`, `/api/mda/geo/mop-up-shortlist`, `/api/mda/overview`
- Login: `POST /api/auth/login` (JWT) — reused as-is.

The app is mostly a **read client** over data already served. The only new
backend piece for the core feature is one small "where am I / what's near me"
endpoint. The rest of the backend work is the self-contained OTA system.

### Caveats (feasibility risks, not blockers)

1. **Payload size on mobile** — 9,473 settlement polygons + 86,511 grid cells is
   too heavy to load whole onto a phone. The app scopes geometry to the user's
   current LGA/ward — hence a bbox/near-me endpoint rather than reusing the
   whole-project GeoJSON dumps.
2. **Freshness = last CommCare sync** — "what's left to cover" is only as current
   as the last sync, not real-time. Acceptable for a monitoring aid; the UI must
   show "coverage as of HH:MM".
3. **Offline** — v1 is **online-only with graceful degradation** (decision below).
   Offline boundary caching is a later phase.
4. **Signing keystore** — one-time: the team generates and owns the release
   keystore; CI consumes it as a secret.

---

## Confirmed decisions

| Decision | Choice |
|---|---|
| Live tracking meaning | Device's own GPS → which ward/settlement to cover (point-in-polygon against existing coverage). **No central fleet-tracking / no new ingestion.** |
| Android stack | **Kotlin + Jetpack Compose + MapLibre Native** |
| Delivery sequence | **OTA infrastructure first** (server-testable), then the app |
| Repo layout | **Monorepo** — `android/` folder in this repo (versionCode/tag scheme uses the same git history) |
| Offline scope (v1) | **Online-only, graceful** — cached last-known + clear offline state; offline caching deferred |
| State / round scope | **Any state + round.** App shows a project (state + round) selector, defaults to the active project, threads `project_id` through every call. Backend already supports this via `resolve_pid()`. |
| Branch | `apk_dev` (intentional exception to the usual dev-only workflow, for the app track) |

---

## 1. What's reused vs. new

| Layer | Reused (exists today) | New |
|---|---|---|
| Auth | JWT login, bcrypt, auth-gate middleware | — |
| Project scope | `GET /api/projects` (lists every state+round), `resolve_pid()` — every data endpoint already accepts `?project_id=N`, defaulting to the active project | — (app just adds a selector and passes `project_id`) |
| Coverage data | boundary GeoJSON, coverage/completeness/mop-up endpoints | `GET /api/app/near?lat&lon&project_id` — containing ward+settlement + coverage status, scoped to nearby geometry of the selected project |
| OTA | the auth-gate middleware pattern in `app/main.py` | version-gate middleware, `GET /version`, `/apk` file host, CI app pipeline |
| App | — | the entire Kotlin/Compose app under `android/` |

> **Project scoping is a cross-cutting requirement.** Every data call the app
> makes (`/api/mda/overview`, `/api/mda/coverage/*`, boundary GeoJSON,
> `/api/app/near`, …) carries the selected `project_id`. The selector reads
> `GET /api/projects` and groups by `state_name` → `round_number`.

---

## 2. Android app template (Kotlin + Compose + MapLibre Native)

```
android/
├── app/build.gradle.kts        # versionCode = git commit count, versionName = git tag
├── app/src/main/
│   ├── AndroidManifest.xml      # INTERNET + ACCESS_FINE_LOCATION
│   └── java/org/ehealth/eritas/
│       ├── EritasApp.kt
│       ├── core/
│       │   ├── net/ApiClient.kt          # Retrofit + Bearer + X-App-Version-Code header
│       │   ├── net/VersionInterceptor.kt # injects versionCode on every request
│       │   └── auth/TokenStore.kt        # EncryptedSharedPreferences
│       ├── core/project/ProjectStore.kt   # selected project_id (persisted); list from /api/projects
│       ├── feature/
│       │   ├── update/UpdateGate.kt      # launch check vs GET /version → wall|banner|ok
│       │   ├── login/LoginScreen.kt
│       │   ├── project/ProjectPickerScreen.kt # state + round selector
│       │   ├── dashboard/OverviewScreen.kt   # KPIs from /api/mda/overview?project_id
│       │   ├── map/CoverageMapScreen.kt      # MapLibre + boundary/coverage layers (project-scoped)
│       │   └── locate/MyAreaScreen.kt        # device GPS → /api/app/near?project_id
│       └── ui/ (theme, nav graph)
```

**Project selector** (state + round) lives in the top app bar, available from
every tab. It reads `GET /api/projects`, groups by `state_name` then
`round_number`, defaults to the active project, and persists the choice. The
selected `project_id` is threaded into every data request.

**Three tabs** (bottom nav), gated behind login + the update wall, all scoped to
the selected project:

1. **Dashboard** — overview KPIs from `/api/mda/overview?project_id=…` (mirrors web overview tiles).
2. **Coverage Map** — MapLibre Native: ward/settlement polygons colored by
   coverage %, same logic as the web map, for the selected project.
3. **My Area** — the core aid: reads device GPS, calls the near-me endpoint,
   shows which ward/settlement the user is in, its coverage %, and the nearest
   uncovered settlement to head to — within the selected state/round.

**Launch flow:** `GET /version` → if `versionCode < min` render full-screen
**Update wall** (blocks everything) → else if `< latest` show dismissible
**banner** → else → login → project selector (defaulting to active) → main nav.

---

## 3. OTA system template (4 parts)

### Versioning
- `versionName` = `git describe --tags` (e.g. `v1.2`)
- `versionCode` = `git rev-list --count HEAD` (monotonic; currently 105)
- App sends `X-App-Version-Code: <versionCode>` on every API request.

### 3.1 Static APK distribution
`GET /apk` (and alias `/download`) → `FileResponse` from `APK_DIR`
(default `/var/app-apk/`, mounted read-only into the container). Dumb file host.

### 3.2 CI build & deploy (app artifact only)
`.github/workflows/app-build.yml`, **separate from `deploy.yml`**:
- Trigger: push to `apk_dev` touching `android/**`.
- Build signed APK (Android SDK + keystore secret); derive versions from git.
- Upload to the EC2 download dir. Mechanism: infra is **keyless SSM (no SSH)**
  and SSM params can't carry a multi-MB APK, so: **stage to S3 → SSM pulls it to
  `/var/app-apk/`** (matches existing deploy pattern). Plain `scp` is the
  alternative if an SSH deploy key is added.
- Tag `vX.Y` before/with the branch push so the version name is correct.
- Never deploys server code; the backend deploy never builds the app.

### 3.3 Server-side version gate (force-update)
New middleware, sibling to the auth gate in `app/main.py`:
- Applies to app-facing endpoints under `APP_API_PREFIX` (default `/api/app/`).
- `MIN_VERSION_CODE == 0` → gate disabled.
- Otherwise: header missing on an app endpoint, **or** `versionCode < MIN_VERSION_CODE`
  → **HTTP 426 Upgrade Required**.
- The web dashboard never sends the header and never hits `/api/app/*`, so it is
  unaffected — defense in depth without breaking the public dashboard.

Public contract (no auth, no gate): `GET /version` →
```json
{ "min": 105, "latest": 110, "latest_name": "v1.2", "update_url": "/apk" }
```

### 3.4 Client-side update wall + banner
On launch, call `GET /version` and compare installed `versionCode`:
- `< min` → full-screen blocking "Update required" (cannot be bypassed).
- `min ≤ versionCode < latest` → dismissible optional-update banner.
- `≥ latest` → no prompt.

Both states link to `update_url`. The client wall is UX; the server 426 gate is
the real enforcement — an outdated/tampered client still cannot pull data.

### Config (env, not hardcoded)
`MIN_VERSION_CODE`, `LATEST_VERSION_CODE`, `LATEST_VERSION_NAME`, `UPDATE_URL`,
`APK_DIR`, `APP_API_PREFIX`.

### Ordering rule (runbook)
Always publish the new APK to `/apk` **before** raising `MIN_VERSION_CODE`, or
live installs lock out with no upgrade path. To force-update: publish APK →
raise `MIN_VERSION_CODE` → restart.

---

## 3.5 Web ⇄ App isolation & dual-pipeline deployment

**There is one backend server.** The web dashboard and the Android app both talk
to the same FastAPI instance on EC2. Isolation is therefore *behavioural*, not
separate infrastructure — achieved three ways:

### (a) Code is additive and inert to the web

Every app-supporting backend change is new code that touches zero existing web
routes:

- `/version`, `/apk`, `/download`, `/api/app/*` are brand-new paths.
- The version gate is **off by default** (`MIN_VERSION_CODE=0`).
- Even when on, it only acts on requests that **carry `X-App-Version-Code`**
  (only the app sends it) **or** hit **`/api/app/*`** (only the app calls it).
  The web dashboard sends no such header and never calls those paths.

This is enforced by tests (`tests/test_14_app_ota_version_gate.py`), including
"web browser without header is unaffected" and "gate disabled by default".

### (b) Branch + trigger separation

| Pipeline | File | Triggers on | Deploys |
|---|---|---|---|
| Backend deploy | `.github/workflows/deploy.yml` | push to **main** | server Docker image → EC2 |
| CI checks | `.github/workflows/ci.yml` | push/PR to **main, dev** | nothing (lint/build/QA) |
| **App build** | `.github/workflows/app-build.yml` *(new)* | push to **apk_dev** touching **android/** | signed APK → `/var/app-apk` |

A push to `apk_dev` triggers **neither** `ci.yml` **nor** `deploy.yml` (both are
main/dev-only) — so app work never redeploys or even re-tests the web app.

### (c) Two independent release vehicles

- **App release:** tag `vX.Y` on `apk_dev` → `app-build.yml` builds the signed
  APK → uploads to `/var/app-apk` → live at `/apk`. **Server code untouched.**
- **Backend release:** `apk_dev` → PR → **main** → `deploy.yml` rolls the
  container. **APK artifact untouched.**

> **The one coupling, stated plainly:** the OTA *backend* code (the gate,
> `/version`, the `/apk` host) is server code, so it goes live through the
> **backend** pipeline (merge to main → deploy), not the app pipeline. The APK
> goes live through the **app** pipeline. Two artifacts, two pipelines, one
> shared server. "Building the app never deploys server code, and vice versa"
> holds — the only thing that ships server-side is the inert, default-off gate.

### Force-update sequence (ties the two pipelines)

1. App pipeline: build + publish the new APK to `/apk` (app release).
2. Ops/backend: raise `MIN_VERSION_CODE` in the prod `.env` and restart.
   Order matters — APK first, gate second, or live installs lock out.

---

## 4. Phasing

1. **OTA infra** (server-testable without the app): gate + `/version` + `/apk`
   host + env + CI skeleton.
2. **App shell**: scaffold under `android/`, login, update wall/banner, nav.
3. **Dashboard + Coverage Map** tabs (read existing endpoints).
4. **My Area** feature + the `/api/app/near` endpoint.
5. **Hardening**: payload scoping, keystore/CI finalization. (Offline caching is
   out of scope for v1.)

---

## Open items to settle before/within each phase

- Exact MapLibre style/tiles source for the base map (the web app's source).
- Whether `/api/app/near` returns geometry (for drawing) or just status text.
- Keystore custody + GitHub secret names.
- S3 staging bucket name/region for the APK upload, or decision to use SSH `scp`.

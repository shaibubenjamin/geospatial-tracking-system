# ERITAS Field Coverage — Android app (v0.1 pilot)

Kotlin + Jetpack Compose + MapLibre Native companion app for the ERITAS MDA
platform. It is a **monitoring / field-coverage aid**, not a data-collection
tool (CommCare remains the system of record). See
`docs/apk-app-blueprint.md` for the full design.

## What it does

- **Login** — reuses the platform JWT (`POST /api/auth/login`).
- **Campaign selector** — works for **any state + round**; reads
  `GET /api/app/projects`, defaults to the active project, persists the choice,
  and scopes every screen by `project_id`.
- **Dashboard** — overview KPIs from `GET /api/app/overview`.
- **Coverage map** — ward polygons colored by coverage % (MapLibre Native),
  from `GET /api/app/geo/wards`.
- **My Area** — the core aid: reads device GPS and calls `GET /api/app/near`
  to tell the field user which settlement/ward they're in, its coverage, and
  the nearest settlement still left to cover.
- **OTA update wall/banner** — on launch, calls `GET /version`; force-updates
  below the server minimum, offers an optional update below the latest.

## Build

> No Gradle wrapper jar is committed. Open the `android/` folder in Android
> Studio (which materialises the wrapper on first sync), or run `gradle wrapper`
> once with a local Gradle 8.9+. CI uses an action-provided Gradle and needs no
> wrapper.

```bash
# from android/
gradle :app:assembleRelease     # or ./gradlew once the wrapper exists
```

- `versionCode` = `git rev-list --count HEAD` (monotonic — the value the server
  gate compares against).
- `versionName` = latest git tag (e.g. `0.1`), via `git describe --tags`.
- `BASE_URL` is `https://eha-mda-dashboard.ehealthnigeria.org` for release and
  `http://10.0.2.2:8090` for debug (emulator → host machine).

## Signing (release)

CI signs with a keystore injected from GitHub secrets. Locally, if
`android/keystore.properties` is absent the release build falls back to debug
signing so it still assembles.

Generate a release keystore once and keep it safe (never commit it):

```bash
keytool -genkeypair -v -keystore eritas-release.keystore \
  -alias eritas -keyalg RSA -keysize 2048 -validity 10000
```

Then store these as GitHub Actions secrets for `app-build.yml`:

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `base64 -w0 eritas-release.keystore` |
| `ANDROID_KEYSTORE_PASSWORD` | store password |
| `ANDROID_KEY_ALIAS` | `eritas` |
| `ANDROID_KEY_PASSWORD` | key password |

And one Actions **variable**: `APK_S3_BUCKET` = the S3 bucket used to stage the
APK before it is pulled onto the EC2 host (leave unset to build artifact-only).

## Release & force-update runbook

The app artifact and the backend deploy on **separate pipelines** — building
the app never deploys server code (see `docs/apk-app-blueprint.md` §3.5).

**Cut a release**
1. Tag the version on `apk_dev`: `git tag v0.1 && git push origin v0.1`.
2. Push the `android/` change to `apk_dev`. `app-build.yml` builds the signed
   APK, uploads it to `/var/app-apk/` on the server (→ live at `/apk`), and
   attaches it as a workflow artifact.

**Force an update** (lock out installs below a version)
1. Confirm the new APK is live at `/apk` **first**.
2. Raise `MIN_VERSION_CODE` in the production `.env` to the new minimum
   `versionCode` and restart the API.
3. Older installs now receive **HTTP 426** on `/api/app/*` and show the
   blocking update wall. `LATEST_VERSION_CODE` / `LATEST_VERSION_NAME` drive
   the optional-update banner for installs between min and latest.

> Ordering matters: never raise `MIN_VERSION_CODE` before the new APK is live,
> or existing installs lock out with no upgrade path.

"""
Domain 14 — Android companion app: OTA version gate, /version, and the APK
static host.

These tests exercise the server-side half of the over-the-air update system
(see docs/apk-app-blueprint.md). They run fully in-process against the FastAPI
app and need no database — the version gate, /version contract, and APK file
host are all DB-independent.

The gate is configured via app.config module-level constants
(MIN_VERSION_CODE, APP_API_PREFIX, …). Because main.py captures those at
import time, the gate-behaviour tests patch BOTH app.config and app.main so the
running middleware sees the override.
"""
import importlib

import pytest


# ── /version — public launch-check contract ──────────────────────────────────
class TestVersionEndpoint:
    """GET /version — unauthenticated, un-gated, drives the client wall."""

    async def test_version_returns_200(self, client):
        resp = await client.get("/version")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"

    async def test_version_has_required_keys(self, client):
        body = (await client.get("/version")).json()
        for key in ("min", "latest", "latest_name", "update_url"):
            assert key in body, f"/version must expose {key!r}, got {body!r}"

    async def test_version_is_public_no_auth(self, client):
        # No Authorization header, no X-App-Version-Code — must still work so a
        # client about to be force-updated can read the gate values.
        resp = await client.get("/version")
        assert resp.status_code == 200

    async def test_version_min_and_latest_are_ints(self, client):
        body = (await client.get("/version")).json()
        assert isinstance(body["min"], int)
        assert isinstance(body["latest"], int)


# ── APK static host ───────────────────────────────────────────────────────────
class TestApkHost:
    """GET /apk, /download, /apk/{filename} — dumb public file host."""

    async def test_apk_missing_returns_404_json(self, client):
        # No APK has been uploaded in the test env → graceful 404, not a 500.
        resp = await client.get("/apk")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    async def test_download_alias_behaves_like_apk(self, client):
        resp = await client.get("/download")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    async def test_apk_path_traversal_is_blocked(self, client):
        # A non-.apk / traversal filename must be rejected (404), never served.
        resp = await client.get("/apk/..%2f..%2fapp%2fconfig.py")
        assert resp.status_code == 404

    async def test_apk_non_apk_extension_rejected(self, client):
        resp = await client.get("/apk/notanapk.txt")
        assert resp.status_code == 404

    async def test_apk_served_when_present(self, client, tmp_path, monkeypatch):
        """When an APK exists in APK_DIR it is served with the Android MIME."""
        import app.main as main

        apk = tmp_path / "served.apk"
        apk.write_bytes(b"PK\x03\x04 fake-apk-bytes")
        monkeypatch.setattr(main, "APK_DIR", str(tmp_path))
        resp = await client.get("/apk/served.apk")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/vnd.android.package-archive"
        assert resp.content == b"PK\x03\x04 fake-apk-bytes"


# ── Version gate (force-update) middleware ────────────────────────────────────
class TestVersionGate:
    """The X-App-Version-Code → HTTP 426 enforcement on /api/app/*."""

    async def test_gate_disabled_by_default(self, client):
        # MIN_VERSION_CODE defaults to 0 → gate is a no-op. A request to an
        # app path with no version header should NOT 426 (it 401s for missing
        # auth instead, proving the version gate let it through).
        resp = await client.get("/api/app/projects")
        assert resp.status_code != 426, "gate must be disabled when MIN=0"
        assert resp.status_code == 401

    async def test_stale_version_is_426(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        resp = await client.get(
            "/api/app/projects", headers={"X-App-Version-Code": "100"}
        )
        assert resp.status_code == 426, f"stale client must 426, got {resp.status_code}"
        body = resp.json()
        assert body["min"] == 105
        assert "update_url" in body

    async def test_current_version_passes_gate(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        # Version is current → gate passes; auth gate then 401s (no token).
        resp = await client.get(
            "/api/app/projects", headers={"X-App-Version-Code": "105"}
        )
        assert resp.status_code != 426
        assert resp.status_code == 401

    async def test_missing_header_on_app_path_is_426(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        resp = await client.get("/api/app/projects")  # no version header
        assert resp.status_code == 426

    async def test_stale_header_locks_out_non_app_paths_too(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        # A stale app calling a public web endpoint is still locked out.
        resp = await client.get(
            "/api/health", headers={"X-App-Version-Code": "1"}
        )
        assert resp.status_code == 426

    async def test_web_browser_without_header_unaffected(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        # No version header (a browser) on a public endpoint → untouched.
        resp = await client.get("/api/health")
        assert resp.status_code == 200

    async def test_version_endpoint_exempt_from_gate(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        # Even a stale client must be able to read /version to learn it's stale.
        resp = await client.get("/version", headers={"X-App-Version-Code": "1"})
        assert resp.status_code == 200

    async def test_non_numeric_header_treated_as_stale(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "MIN_VERSION_CODE", 105)
        resp = await client.get(
            "/api/app/projects", headers={"X-App-Version-Code": "garbage"}
        )
        assert resp.status_code == 426

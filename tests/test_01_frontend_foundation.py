"""
Domain 01 — Frontend Foundation.

Verifies the static HTML pages exist and are clean (no embedded secrets, only
legitimate CDN references), that the FastAPI route map registers the expected
API and page routes (and does NOT register page routes for what are really API
routers), and that the page routes actually serve HTML with the right caching
headers.

Per the test-authoring contract, all heavy `app.*` imports happen lazily inside
the test methods; only stdlib + pytest are imported at module top level.
"""
import re

import pytest

from conftest import STATIC_DIR


# The four first-party static pages this domain owns.
_HTML_FILES = ("login.html", "home.html", "mda.html", "mda-admin.html")


def _read(name: str) -> str:
    """Read a static HTML file as text, skipping the test if it is missing."""
    p = STATIC_DIR / name
    if not p.exists():
        pytest.skip(f"static file missing: {name}")
    return p.read_text(encoding="utf-8", errors="ignore")


def _all_html() -> str:
    """Concatenate every owned static HTML file into one searchable string."""
    return "\n".join(_read(name) for name in _HTML_FILES)


class TestHTMLFilesExist:
    """The four first-party static pages exist, are non-empty, and look like HTML."""

    def test_login_html_exists_nonempty(self):
        p = STATIC_DIR / "login.html"
        assert p.exists(), "static/login.html must exist"
        assert p.stat().st_size > 0, "static/login.html must be non-empty"

    def test_home_html_exists_nonempty(self):
        p = STATIC_DIR / "home.html"
        assert p.exists(), "static/home.html must exist"
        assert p.stat().st_size > 0, "static/home.html must be non-empty"

    def test_mda_html_exists_nonempty(self):
        p = STATIC_DIR / "mda.html"
        assert p.exists(), "static/mda.html must exist"
        assert p.stat().st_size > 0, "static/mda.html must be non-empty"

    def test_mda_admin_html_exists_nonempty(self):
        p = STATIC_DIR / "mda-admin.html"
        assert p.exists(), "static/mda-admin.html must exist"
        assert p.stat().st_size > 0, "static/mda-admin.html must be non-empty"

    def test_all_four_pages_exist(self):
        missing = [name for name in _HTML_FILES if not (STATIC_DIR / name).exists()]
        assert not missing, f"missing static HTML page(s): {missing}"

    def test_each_page_has_html_marker(self):
        for name in _HTML_FILES:
            text = _read(name).lower()
            assert ("<html" in text) or ("<!doctype" in text), (
                f"{name} should contain an <html ...> or <!doctype ...> marker"
            )


class TestHTMLSafety:
    """The static pages carry no hardcoded secrets; external refs are CDN-only."""

    def test_no_aws_access_keys(self):
        for name in _HTML_FILES:
            assert "AKIA" not in _read(name), (
                f"{name} appears to contain an AWS access key id (AKIA...)"
            )

    def test_no_secret_key_literal(self):
        for name in _HTML_FILES:
            assert "SECRET_KEY=" not in _read(name), (
                f"{name} contains a hardcoded SECRET_KEY= literal"
            )

    def test_no_bcrypt_hashes(self):
        for name in _HTML_FILES:
            assert "$2b$" not in _read(name), (
                f"{name} contains what looks like a bcrypt ($2b$) hash"
            )

    def test_no_password_assignment(self):
        # Catch obvious `password = "..."` / `password: "..."` literals.
        pat = re.compile(r"""password\s*[:=]\s*["'][^"']+["']""", re.IGNORECASE)
        for name in _HTML_FILES:
            assert not pat.search(_read(name)), (
                f"{name} contains an obvious hardcoded password assignment"
            )

    def test_no_private_key_blocks(self):
        for name in _HTML_FILES:
            assert "PRIVATE KEY" not in _read(name), (
                f"{name} contains a private key block"
            )

    def test_maplibre_and_chartjs_referenced_via_cdn(self):
        corpus = _all_html()
        assert re.search(r"https://[^\s\"']*maplibre", corpus, re.IGNORECASE), (
            "expected a CDN reference to MapLibre (legitimate external dep)"
        )
        assert re.search(r"https://[^\s\"']*chart", corpus, re.IGNORECASE), (
            "expected a CDN reference to Chart.js (legitimate external dep)"
        )


class TestRouteRegistration:
    """The FastAPI route map registers the expected API + page routes."""

    def _paths(self, app):
        return {getattr(r, "path", None) for r in app.routes}

    def test_health_route_registered(self, app):
        assert "/api/health" in self._paths(app), "/api/health must be registered"

    def test_auth_routes_registered(self, app):
        paths = self._paths(app)
        assert "/api/auth/login" in paths, "/api/auth/login must be registered"
        assert "/api/auth/me" in paths, "/api/auth/me must be registered"

    def test_projects_route_registered(self, app):
        assert "/api/projects" in self._paths(app), "/api/projects must be registered"

    def test_page_routes_registered(self, app):
        paths = self._paths(app)
        for page in ("/login", "/dashboard", "/mda", "/mda-admin", "/home"):
            assert page in paths, f"page route {page} must be registered"

    def test_no_literal_page_route_for_api_routers(self, app):
        # /sync, /qc, /analytics are API routers under /api/... — there must be
        # NO top-level PAGE route with those literal paths.
        paths = self._paths(app)
        for bad in ("/sync", "/qc", "/analytics"):
            assert bad not in paths, (
                f"{bad} should NOT be a page route (it lives under /api/...)"
            )

    def test_sync_and_analytics_exist_under_api(self, app):
        # Confirm those features are mounted under /api/... instead of as pages.
        paths = self._paths(app)
        assert any(p and p.startswith("/api/sync/") for p in paths), (
            "expected sync endpoints under /api/sync/..."
        )
        # Analytics is nested per-project: /api/projects/{id}/analytics/...
        assert any(p and p.startswith("/api/") and "/analytics/" in p for p in paths), (
            "expected analytics endpoints under /api/.../analytics/..."
        )

    def test_qc_exists_under_api(self, app):
        # QC lives under /api/... (both /api/mda/qc/... and per-project /qc/...).
        paths = self._paths(app)
        assert any(p and p.startswith("/api/") and "/qc/" in p for p in paths), (
            "expected qc endpoints under /api/.../qc/..."
        )


class TestPageRoutes:
    """The page routes serve HTML (200 + text/html) with no-store caching."""

    async def test_login_page_serves_html(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200, "/login should return 200"
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/login should serve text/html"
        )

    async def test_root_page_serves_html(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200, "/ should return 200"
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/ should serve text/html"
        )

    async def test_dashboard_page_serves_html(self, client):
        resp = await client.get("/dashboard")
        assert resp.status_code == 200, "/dashboard should return 200"
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/dashboard should serve text/html"
        )

    async def test_mda_page_serves_html(self, client):
        resp = await client.get("/mda")
        assert resp.status_code == 200, "/mda should return 200"
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/mda should serve text/html"
        )

    async def test_mda_admin_page_serves_html(self, client):
        resp = await client.get("/mda-admin")
        assert resp.status_code == 200, "/mda-admin should return 200"
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/mda-admin should serve text/html"
        )

    async def test_html_page_has_no_store_cache_control(self, client):
        resp = await client.get("/login")
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/login should serve text/html"
        )
        cache_control = resp.headers.get("Cache-Control", "")
        assert "no-store" in cache_control, (
            f"HTML page Cache-Control should contain no-store, got: {cache_control!r}"
        )

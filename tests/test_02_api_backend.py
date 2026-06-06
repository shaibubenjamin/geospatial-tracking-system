"""
Domain 02 — APIs & Backend Logic.

Exercises the FastAPI surface of the ERITAS MDA dashboard: the public health
probe, the projects CRUD endpoints (public list / single read, auth-gated
writes), the project-scoped analytics and QC routers, login input validation,
and a handful of response-contract guarantees (JSON errors, no PII leakage,
CORS, no secret exposure).

Behaviors that genuinely need the live seeded stack depend on `auth_headers`
(or `require_db`) so they SKIP rather than fail when the database is absent.
All `app.*` imports happen inside the test methods so collection works even
when application dependencies are missing.
"""
import json

import pytest


class TestHealth:
    """GET /api/health — public liveness probe."""

    async def test_health_returns_200(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"

    async def test_health_status_ok(self, client):
        resp = await client.get("/api/health")
        body = resp.json()
        assert body.get("status") == "ok", f"status should be 'ok', got {body!r}"

    async def test_health_has_service_key(self, client):
        resp = await client.get("/api/health")
        body = resp.json()
        assert "service" in body, f"health body should expose 'service', got {body!r}"

    async def test_health_content_type_is_json(self, client):
        resp = await client.get("/api/health")
        ctype = resp.headers.get("content-type", "")
        assert ctype.startswith("application/json"), f"expected JSON, got {ctype!r}"


class TestProjectsEndpoints:
    """/api/projects — public reads, auth-gated writes."""

    async def test_list_projects_returns_200(self, client, require_db):
        # Public per the allowlist, but the handler reads the DB — depend on
        # require_db so this SKIPs (instead of hanging/failing) without Postgres.
        resp = await client.get("/api/projects")
        assert resp.status_code == 200, (
            f"GET /api/projects is public per allowlist, got {resp.status_code}"
        )

    async def test_list_projects_returns_list(self, client, require_db):
        resp = await client.get("/api/projects")
        body = resp.json()
        assert isinstance(body, list), f"expected a JSON list, got {type(body)}"

    async def test_create_project_without_token_is_401(self, client):
        resp = await client.post("/api/projects", json={"name": "X"})
        assert resp.status_code == 401, (
            f"POST without Bearer token must be gated to 401, got {resp.status_code}"
        )

    async def test_update_project_without_token_is_401(self, client):
        # PUT is a non-GET method on a protected prefix -> auth gate -> 401.
        resp = await client.put("/api/projects/1", json={"name": "X"})
        assert resp.status_code == 401, (
            f"PUT without Bearer token must be gated to 401, got {resp.status_code}"
        )

    async def test_delete_project_without_token_is_401(self, client):
        resp = await client.delete("/api/projects/1")
        assert resp.status_code == 401, (
            f"DELETE without Bearer token must be gated to 401, got {resp.status_code}"
        )

    async def test_list_projects_with_auth_works(self, client, require_db, auth_headers):
        # require_db SKIPs fast on a closed Postgres port; auth_headers SKIPs if
        # the DB is up but the seeded admin login is unavailable.
        resp = await client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200, (
            f"authenticated list should be 200, got {resp.status_code}"
        )
        assert isinstance(resp.json(), list), "authenticated list should be a list"

    async def test_single_project_route_exists(self, client):
        from app.main import app

        paths = {r.path for r in app.routes}
        assert "/api/projects/{project_id}" in paths, (
            "single-project route /api/projects/{project_id} should be registered"
        )

    async def test_get_missing_project_with_auth_is_404(self, client, require_db, auth_headers):
        # require_db SKIPs fast without Postgres; auth_headers SKIPs without seed.
        resp = await client.get("/api/projects/999999", headers=auth_headers)
        assert resp.status_code == 404, (
            f"unknown project id should 404 when authenticated, got {resp.status_code}"
        )


class TestAnalyticsEndpoints:
    """Project-scoped /api/projects/{id}/analytics/* router."""

    @staticmethod
    def _route_paths():
        from app.main import app

        return {r.path for r in app.routes}

    async def test_summary_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/analytics/summary" in self._route_paths()
        ), "analytics summary route should be registered"

    async def test_lgas_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/analytics/lgas" in self._route_paths()
        ), "analytics lgas route should be registered"

    async def test_wards_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/analytics/wards" in self._route_paths()
        ), "analytics wards route should be registered"

    async def test_settlements_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/analytics/settlements" in self._route_paths()
        ), "analytics settlements route should be registered"

    async def test_timeline_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/analytics/timeline" in self._route_paths()
        ), "analytics timeline route should be registered"

    async def test_points_geojson_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/analytics/points/geojson"
            in self._route_paths()
        ), "analytics points/geojson route should be registered"

    async def test_analytics_requires_auth(self, client):
        # Project-scoped analytics is NOT on the public GET allowlist, so an
        # anonymous GET on the protected prefix must be gated to 401.
        resp = await client.get("/api/projects/1/analytics/summary")
        assert resp.status_code == 401, (
            f"analytics endpoint should require auth (401), got {resp.status_code}"
        )


class TestQCEndpoints:
    """Project-scoped /api/projects/{id}/qc/* router."""

    @staticmethod
    def _route_paths():
        from app.main import app

        return {r.path for r in app.routes}

    async def test_qc_summary_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/qc/summary" in self._route_paths()
        ), "qc summary route should be registered"

    async def test_qc_flags_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/qc/flags" in self._route_paths()
        ), "qc flags route should be registered"

    async def test_qc_field_checks_route_registered(self, client):
        assert (
            "/api/projects/{project_id}/qc/field-checks" in self._route_paths()
        ), "qc field-checks route should be registered"

    async def test_qc_requires_auth(self, client):
        resp = await client.get("/api/projects/1/qc/summary")
        assert resp.status_code == 401, (
            f"qc endpoint should require auth (401), got {resp.status_code}"
        )

    async def test_qc_flags_pagination_params_gated_before_db(self, client):
        # The /flags route declares `limit`/`offset` query params. Anonymous
        # access is gated by the auth middleware BEFORE any handler/DB work, so
        # pagination params must yield a clean 401 (not a 500 from query parsing
        # or DB access).
        resp = await client.get("/api/projects/1/qc/flags?limit=1&offset=0")
        assert resp.status_code == 401, (
            f"paginated qc/flags without token should be 401, got {resp.status_code}"
        )


class TestInputValidation:
    """POST /api/auth/login — request-body validation (public endpoint)."""

    # 422 = validation rejected the body. If a syntactically valid body slips
    # past validation it reaches credential checking -> 401. Both are acceptable
    # for the malformed/edge cases; pure-shape errors must be 422.
    _OK = {400, 401, 422}

    async def test_missing_fields_is_422(self, client):
        resp = await client.post("/api/auth/login", json={"username": "admin"})
        assert resp.status_code == 422, (
            f"missing 'password' should be 422, got {resp.status_code}"
        )

    async def test_wrong_types_is_422(self, client):
        resp = await client.post(
            "/api/auth/login", json={"username": 123, "password": [1, 2]}
        )
        assert resp.status_code == 422, (
            f"wrong field types should be 422, got {resp.status_code}"
        )

    async def test_malformed_json_body(self, client):
        resp = await client.post(
            "/api/auth/login",
            content="{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in self._OK, (
            f"malformed JSON should be 400/422, got {resp.status_code}"
        )

    async def test_empty_body_is_422(self, client):
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 422, (
            f"empty body should be 422, got {resp.status_code}"
        )

    async def test_extra_only_payload_is_422(self, client):
        # Required fields absent (only unrelated keys present) -> validation 422.
        resp = await client.post(
            "/api/auth/login", json={"foo": "bar", "baz": 1}
        )
        assert resp.status_code == 422, (
            f"payload without required fields should be 422, got {resp.status_code}"
        )


class TestResponseContracts:
    """Cross-cutting response guarantees: JSON errors, no PII, CORS, no secrets."""

    async def test_unknown_route_returns_json_no_traceback(self, client):
        resp = await client.get("/api/does-not-exist")
        assert resp.status_code == 404, f"expected 404, got {resp.status_code}"
        ctype = resp.headers.get("content-type", "")
        assert ctype.startswith("application/json"), (
            f"404 should be JSON, not HTML, got {ctype!r}"
        )
        text = resp.text
        assert "Traceback (most recent call last)" not in text, (
            "404 body must not leak a Python traceback"
        )

    async def test_project_responses_have_no_password(self, client, require_db, auth_headers):
        # require_db SKIPs fast without Postgres; auth_headers SKIPs without seed.
        resp = await client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        body = resp.text
        assert "hashed_password" not in body, "project list must not leak hashed_password"
        assert "$2b$" not in body, "project list must not leak a bcrypt hash"

    async def test_cors_headers_present_on_allowed_origin(self, client):
        origin = "https://eha-mda-dashboard.ehealthnigeria.org"
        resp = await client.get("/api/health", headers={"Origin": origin})
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        acao = resp.headers.get("access-control-allow-origin")
        assert acao == origin, (
            f"CORS allow-origin should echo the allowlisted origin, got {acao!r}"
        )

    async def test_health_exposes_no_secret_keys(self, client):
        resp = await client.get("/api/health")
        body = resp.json()
        keys = {k.lower() for k in body.keys()}
        for forbidden in ("secret", "secret_key", "password", "token", "database_url"):
            assert forbidden not in keys, (
                f"health response must not expose secret key {forbidden!r}"
            )

    async def test_unknown_route_body_is_parseable_json(self, client):
        resp = await client.get("/api/does-not-exist")
        # Body must parse as JSON (i.e. a structured error, not an HTML page).
        parsed = json.loads(resp.text)
        assert isinstance(parsed, dict), "JSON 404 error should be an object"

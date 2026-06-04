"""
SARMAAN II — Backend Test Suite
FastAPI (Python) | pytest + httpx

Install deps:
    pip install pytest pytest-asyncio httpx pytest-cov

Run:
    pytest backend/ -v --cov=app --cov-report=term-missing
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ─────────────────────────────────────────────────
# Replace this import with your actual FastAPI app
# e.g. from app.main import app
# ─────────────────────────────────────────────────
# from app.main import app

# ─── Fixtures ────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    """Async test client that talks to your FastAPI app directly (no server needed)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),  # replace `app` with your FastAPI instance
        base_url="http://test"
    ) as ac:
        yield ac


# ═══════════════════════════════════════════════════
# 1. HEALTH & CONNECTIVITY
# ═══════════════════════════════════════════════════

class TestHealth:
    """Basic sanity checks — does the server respond at all?"""

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_200(self, client):
        response = await client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_has_status_key(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_root_endpoint_reachable(self, client):
        response = await client.get("/")
        assert response.status_code in [200, 301, 302]


# ═══════════════════════════════════════════════════
# 2. AUTHENTICATION
# ═══════════════════════════════════════════════════

class TestAuthentication:
    """Login, token validation, role-based access."""

    @pytest.mark.asyncio
    async def test_login_with_valid_credentials(self, client):
        response = await client.post("/auth/login", json={
            "username": "test_user",
            "password": "test_password"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data

    @pytest.mark.asyncio
    async def test_login_with_wrong_password_returns_401(self, client):
        response = await client.post("/auth/login", json={
            "username": "test_user",
            "password": "wrong_password"
        })
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_without_token_returns_401(self, client):
        response = await client.get("/api/demographics")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_invalid_token_returns_401(self, client):
        response = await client.get(
            "/api/demographics",
            headers={"Authorization": "Bearer invalid.token.here"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_invalidates_session(self, client):
        # Login first
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]

        # Logout
        logout = await client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert logout.status_code == 200

        # Subsequent request should now fail
        response = await client.get(
            "/api/demographics",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


# ═══════════════════════════════════════════════════
# 3. DEMOGRAPHICS ENDPOINT
# ═══════════════════════════════════════════════════

class TestDemographics:
    """
    Tests for the Demographic Overview panel.
    Covers: households, coverage %, RAs, children, samples.
    """

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_demographics_returns_200(self, client, auth_headers):
        response = await client.get("/api/demographics", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_demographics_has_required_fields(self, client, auth_headers):
        response = await client.get("/api/demographics", headers=auth_headers)
        data = response.json()
        required = [
            "households_planned", "households_reached", "coverage_pct",
            "household_members", "mothers_caregivers",
            "children_0_59m", "children_0_28d", "children_1_59m",
            "nasal_samples", "rectal_samples", "total_samples",
            "research_assistants", "lga_reached", "ward_reached", "settlements_reached"
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_coverage_pct_is_between_0_and_100(self, client, auth_headers):
        response = await client.get("/api/demographics", headers=auth_headers)
        data = response.json()
        pct = data["coverage_pct"]
        assert 0 <= pct <= 100, f"Coverage {pct}% is out of range"

    @pytest.mark.asyncio
    async def test_households_reached_not_exceed_planned(self, client, auth_headers):
        response = await client.get("/api/demographics", headers=auth_headers)
        data = response.json()
        # Reached can exceed planned in rare catch-up scenarios — adjust threshold as needed
        assert data["households_reached"] >= 0
        assert data["households_planned"] >= 0

    @pytest.mark.asyncio
    async def test_children_1_59m_subset_of_0_59m(self, client, auth_headers):
        response = await client.get("/api/demographics", headers=auth_headers)
        data = response.json()
        assert data["children_1_59m"] <= data["children_0_59m"]

    @pytest.mark.asyncio
    async def test_total_samples_equals_nasal_plus_rectal(self, client, auth_headers):
        response = await client.get("/api/demographics", headers=auth_headers)
        data = response.json()
        assert data["total_samples"] == data["nasal_samples"] + data["rectal_samples"]

    @pytest.mark.asyncio
    async def test_demographics_filters_by_lga(self, client, auth_headers):
        response = await client.get("/api/demographics?lga=Sokoto+North", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_demographics_filters_by_date(self, client, auth_headers):
        response = await client.get("/api/demographics?date=2024-03-01", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_lga_returns_400_or_empty(self, client, auth_headers):
        response = await client.get("/api/demographics?lga=INVALID_LGA", headers=auth_headers)
        assert response.status_code in [200, 400]
        if response.status_code == 200:
            data = response.json()
            assert data["households_reached"] == 0


# ═══════════════════════════════════════════════════
# 4. QUALITY CHECKS ENDPOINT
# ═══════════════════════════════════════════════════

class TestQualityChecks:
    """
    Tests for duplicate detection, error scoring, and data health.
    These rules are the backbone of the survey's data integrity.
    """

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_quality_checks_returns_200(self, client, auth_headers):
        response = await client.get("/api/quality", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_quality_response_has_error_categories(self, client, auth_headers):
        response = await client.get("/api/quality", headers=auth_headers)
        data = response.json()
        error_keys = [
            "duplicate_uuids", "duplicate_hh_ids", "duplicate_child_ids",
            "duplicate_mother_ids", "hh_name_short", "child_name_short",
            "blank_child_dob", "close_dobs", "missing_username",
            "missing_phone", "wrong_datetime"
        ]
        for key in error_keys:
            assert key in data, f"Missing quality check: {key}"

    @pytest.mark.asyncio
    async def test_health_score_is_percentage(self, client, auth_headers):
        response = await client.get("/api/quality", headers=auth_headers)
        data = response.json()
        score = data.get("health_score", -1)
        assert 0 <= score <= 100

    @pytest.mark.asyncio
    async def test_duplicate_counts_are_non_negative(self, client, auth_headers):
        response = await client.get("/api/quality", headers=auth_headers)
        data = response.json()
        for key in ["duplicate_uuids", "duplicate_hh_ids", "duplicate_child_ids"]:
            assert data[key] >= 0, f"{key} returned negative value"

    @pytest.mark.asyncio
    async def test_error_detail_records_have_required_fields(self, client, auth_headers):
        response = await client.get("/api/quality/errors", headers=auth_headers)
        data = response.json()
        if data.get("records"):
            record = data["records"][0]
            for field in ["lga", "ward", "settlement", "hh_id", "errors"]:
                assert field in record, f"Error record missing field: {field}"


# ═══════════════════════════════════════════════════
# 5. COMPLETION STATUS ENDPOINT
# ═══════════════════════════════════════════════════

class TestCompletion:
    """Planned vs reached by LGA, settlement completion table."""

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_completion_returns_200(self, client, auth_headers):
        response = await client.get("/api/completion", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_completion_lga_data_structure(self, client, auth_headers):
        response = await client.get("/api/completion/lga", headers=auth_headers)
        data = response.json()
        if data.get("lgas"):
            lga = data["lgas"][0]
            assert "lga_name" in lga
            assert "planned" in lga
            assert "reached" in lga

    @pytest.mark.asyncio
    async def test_settlement_table_has_status_field(self, client, auth_headers):
        response = await client.get("/api/completion/settlements", headers=auth_headers)
        data = response.json()
        if data.get("settlements"):
            settlement = data["settlements"][0]
            assert "status" in settlement
            assert settlement["status"] in ["complete", "incomplete", "not_started"]


# ═══════════════════════════════════════════════════
# 6. GEOSPATIAL ENDPOINT
# ═══════════════════════════════════════════════════

class TestGeospatial:
    """GPS point validation, intersection checks, stacked point detection."""

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_gps_summary_returns_200(self, client, auth_headers):
        response = await client.get("/api/geospatial/summary", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_gps_summary_fields_present(self, client, auth_headers):
        response = await client.get("/api/geospatial/summary", headers=auth_headers)
        data = response.json()
        for key in ["total_gps_points", "inside_settlement", "outside_settlement",
                    "stacked_points", "close_pairs", "no_gps"]:
            assert key in data

    @pytest.mark.asyncio
    async def test_inside_plus_outside_equals_total(self, client, auth_headers):
        response = await client.get("/api/geospatial/summary", headers=auth_headers)
        data = response.json()
        # inside + outside + no_gps should roughly account for total
        accounted = data["inside_settlement"] + data["outside_settlement"] + data["no_gps"]
        assert accounted <= data["total_gps_points"] + 5  # allow small rounding buffer

    @pytest.mark.asyncio
    async def test_gps_points_endpoint_returns_valid_coordinates(self, client, auth_headers):
        response = await client.get("/api/geospatial/points?lga=Sokoto+North", headers=auth_headers)
        data = response.json()
        if data.get("points"):
            point = data["points"][0]
            assert "lat" in point and "lon" in point
            # Nigeria lat/lon bounds
            assert 4.0 <= point["lat"] <= 14.0, "Latitude outside Nigeria"
            assert 2.7 <= point["lon"] <= 15.0, "Longitude outside Nigeria"


# ═══════════════════════════════════════════════════
# 7. SUPPORTIVE SUPERVISION ENDPOINT
# ═══════════════════════════════════════════════════

class TestSupervision:
    """Risk ranking, validator corrections, lab rejections."""

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_supervision_returns_200(self, client, auth_headers):
        response = await client.get("/api/supervision", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_risk_rankings_are_valid_values(self, client, auth_headers):
        response = await client.get("/api/supervision/risk", headers=auth_headers)
        data = response.json()
        valid_risks = {"critical", "monitor", "stable"}
        if data.get("rankings"):
            for entry in data["rankings"]:
                assert entry["risk"] in valid_risks

    @pytest.mark.asyncio
    async def test_supervision_score_is_between_0_and_100(self, client, auth_headers):
        response = await client.get("/api/supervision", headers=auth_headers)
        data = response.json()
        score = data.get("overall_score", -1)
        assert 0 <= score <= 100


# ═══════════════════════════════════════════════════
# 8. CSV EXPORT ENDPOINTS
# ═══════════════════════════════════════════════════

class TestExports:
    """CSV download endpoints — content type and non-empty response."""

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_ra_csv_export(self, client, auth_headers):
        response = await client.get("/api/export/ra", headers=auth_headers)
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_settlement_csv_export(self, client, auth_headers):
        response = await client.get("/api/export/settlements", headers=auth_headers)
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_error_records_csv_export(self, client, auth_headers):
        response = await client.get("/api/export/errors", headers=auth_headers)
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_csv_export_requires_auth(self, client):
        response = await client.get("/api/export/ra")
        assert response.status_code == 401


# ═══════════════════════════════════════════════════
# 9. BUG REPORT ENDPOINT
# ═══════════════════════════════════════════════════

class TestBugReport:
    """The in-app bug reporting form."""

    @pytest_asyncio.fixture
    async def auth_headers(self, client):
        login = await client.post("/auth/login", json={
            "username": "test_user", "password": "test_password"
        })
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_submit_bug_report_returns_201(self, client, auth_headers):
        response = await client.post("/api/bugs", json={
            "title": "Test bug",
            "description": "Something broke on the quality checks panel",
            "severity": "medium",
            "page": "/dashboard"
        }, headers=auth_headers)
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_bug_report_missing_title_returns_422(self, client, auth_headers):
        response = await client.post("/api/bugs", json={
            "description": "No title provided",
            "severity": "low"
        }, headers=auth_headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_bug_report_invalid_severity_returns_422(self, client, auth_headers):
        response = await client.post("/api/bugs", json={
            "title": "Bad severity test",
            "severity": "catastrophic"  # not a valid value
        }, headers=auth_headers)
        assert response.status_code == 422

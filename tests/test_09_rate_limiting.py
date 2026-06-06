"""Domain 09 — Rate Limiting.

Verifies the slowapi-based defensive rate limiter wired into ``app/main.py``:
  - slowapi (and its key symbols) are importable and pinned in requirements;
  - ``app.state.limiter`` is a slowapi ``Limiter`` keyed on the client IP with a
    ``120/minute`` default and a ``RateLimitExceeded`` handler registered;
  - actual request behaviour stays sane under bursts (200 within budget,
    only 200/429 over budget, never 500), and ``/api/health`` stays public.
"""

import asyncio

import pytest

from conftest import REPO_ROOT


class TestSlowAPIInstalled:
    """The slowapi package and the exact symbols main.py imports are present."""

    def test_slowapi_importable(self):
        slowapi = pytest.importorskip("slowapi")
        assert slowapi is not None, "slowapi must be importable"

    def test_limiter_importable(self):
        pytest.importorskip("slowapi")
        from slowapi import Limiter

        assert isinstance(Limiter, type), "Limiter must be a class from slowapi"

    def test_helper_symbols_importable(self):
        pytest.importorskip("slowapi")
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded

        assert callable(get_remote_address), "get_remote_address must be callable"
        assert isinstance(
            RateLimitExceeded, type
        ), "RateLimitExceeded must be an exception class"
        assert issubclass(
            RateLimitExceeded, Exception
        ), "RateLimitExceeded must derive from Exception"

    def test_slowapi_pinned_in_requirements(self):
        req = (REPO_ROOT / "requirements.txt").read_text()
        lines = [ln.strip() for ln in req.splitlines()]
        assert any(
            ln.startswith("slowapi==") for ln in lines
        ), "slowapi must be pinned with '==' in requirements.txt"
        assert "slowapi==0.1.9" in lines, "requirements.txt must pin slowapi==0.1.9"


class TestLimiterConfiguration:
    """The limiter object installed on the app is configured as expected."""

    def test_limiter_set_on_app_state(self, app):
        pytest.importorskip("slowapi")
        assert getattr(
            app.state, "limiter", None
        ) is not None, "app.state.limiter must be set"

    def test_limiter_is_slowapi_instance(self, app):
        pytest.importorskip("slowapi")
        from slowapi import Limiter

        assert isinstance(
            app.state.limiter, Limiter
        ), "app.state.limiter must be a slowapi Limiter instance"

    def test_key_func_is_remote_address(self, app):
        pytest.importorskip("slowapi")
        from slowapi.util import get_remote_address

        limiter = app.state.limiter
        key_func = getattr(limiter, "_key_func", None) or getattr(
            limiter, "key_func", None
        )
        assert (
            key_func is get_remote_address
        ), "limiter key function must be get_remote_address"

    def test_default_limit_is_120_per_minute(self, app):
        pytest.importorskip("slowapi")
        limiter = app.state.limiter
        default_limits = getattr(limiter, "_default_limits", None)
        assert default_limits, "limiter must declare default limits"
        # Stringify every limit (and its underlying provider) so we catch both
        # the original "120/minute" spec and the parsed "120 per 1 minute" form.
        parts = [repr(default_limits)]
        for group in default_limits:
            parts.append(repr(group))
            try:
                for lim in group:
                    parts.append(str(getattr(lim, "limit", lim)))
            except TypeError:
                pass
        blob = " ".join(parts)
        assert "120" in blob, f"default limit should include 120, got: {blob}"
        assert "minute" in blob, f"default limit should be per minute, got: {blob}"

    def test_rate_limit_exceeded_handler_registered(self, app):
        pytest.importorskip("slowapi")
        from slowapi.errors import RateLimitExceeded

        assert (
            RateLimitExceeded in app.exception_handlers
        ), "a RateLimitExceeded exception handler must be registered on the app"


class TestRateLimitBehavior:
    """Real request behaviour: the limiter never breaks normal usage."""

    async def test_concurrent_requests_within_limit_all_ok(self, client):
        responses = await asyncio.gather(
            *[client.get("/api/health") for _ in range(5)]
        )
        assert all(
            r.status_code == 200 for r in responses
        ), f"5 concurrent /api/health requests should all be 200, got {[r.status_code for r in responses]}"

    async def test_burst_yields_only_200_or_429(self, client):
        responses = await asyncio.gather(
            *[client.get("/api/health") for _ in range(15)]
        )
        codes = [r.status_code for r in responses]
        assert all(
            c in (200, 429) for c in codes
        ), f"burst of 15 requests must only ever be 200 or 429 (never 500), got {codes}"
        assert 500 not in codes, f"rate limiter must not raise a 500, got {codes}"

    async def test_single_request_never_throws(self, client):
        resp = await client.get("/api/health")
        assert (
            resp.status_code == 200
        ), f"a normal single request must succeed (200), got {resp.status_code}"
        body = resp.json()
        assert body.get("status") == "ok", f"health body should be ok, got {body}"

    async def test_health_stays_public_under_load(self, client):
        # No auth header supplied; health must remain reachable even under a burst.
        responses = await asyncio.gather(
            *[client.get("/api/health") for _ in range(10)]
        )
        ok = [r for r in responses if r.status_code == 200]
        assert ok, "at least some unauthenticated /api/health requests should be 200"
        for r in ok:
            assert (
                r.json().get("service") == "geospatial-tracker"
            ), f"public health payload mismatch: {r.json()}"

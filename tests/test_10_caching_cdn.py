"""Domain 10 — Caching & CDN.

Covers the Redis-backed job queue stack and HTML cache-control behavior:
  * Redis package installed & pinned; job_queue reads REDIS_URL (redis:// scheme).
  * docker-compose (dev + prod) wires a persistent redis:7-alpine service and
    exposes REDIS_URL to the api / sync_worker containers.
  * The add_security_headers middleware forces no-store on text/html responses
    (session-sensitive pages) but leaves JSON endpoints alone.
  * Live Redis connectivity (skips when Redis is unreachable).
"""
from __future__ import annotations

import re

import pytest

from conftest import read_text


class TestRedisInstalled:
    """The redis client is available, pinned, and used with a redis:// URL."""

    def test_redis_importable(self):
        # The thing under test is package presence — skip if not installed.
        pytest.importorskip("redis")

    def test_redis_pinned_in_requirements(self):
        reqs = read_text("requirements.txt")
        lines = [ln.strip() for ln in reqs.splitlines()]
        redis_lines = [
            ln for ln in lines if re.match(r"^redis\s*==", ln, re.IGNORECASE)
        ]
        assert redis_lines, (
            "requirements.txt must pin redis with '==' (e.g. redis==5.0.7); "
            f"lines: {lines!r}"
        )

    def test_job_queue_reads_redis_url_with_scheme(self):
        src = read_text("app/services/job_queue.py")
        assert "REDIS_URL" in src, "job_queue.py must reference REDIS_URL"
        assert 'os.getenv("REDIS_URL"' in src, (
            "job_queue.py must read REDIS_URL from the environment via os.getenv"
        )
        # The default fed to os.getenv must carry the redis:// scheme.
        m = re.search(r'os\.getenv\(\s*["\']REDIS_URL["\']\s*,\s*["\']([^"\']+)["\']', src)
        assert m, "expected os.getenv(\"REDIS_URL\", \"redis://...\") default in job_queue.py"
        assert m.group(1).startswith("redis://"), (
            f"REDIS_URL default must use the redis:// scheme, got {m.group(1)!r}"
        )


class TestRedisDockerConfig:
    """docker-compose (dev + prod) provisions and wires Redis correctly."""

    def test_dev_compose_has_redis_service_image(self):
        dev = read_text("docker-compose.yml")
        assert "redis:7-alpine" in dev, (
            "dev docker-compose.yml must declare a redis:7-alpine service"
        )

    def test_dev_compose_declares_redisdata_volume(self):
        dev = read_text("docker-compose.yml")
        assert re.search(r"^volumes:", dev, re.MULTILINE), (
            "dev compose must declare a top-level volumes: section"
        )
        # Top-level volume name (indented under volumes:).
        assert re.search(r"^\s+redisdata:\s*$", dev, re.MULTILINE), (
            "dev compose must declare a top-level 'redisdata' volume"
        )

    def test_dev_compose_sets_redis_url_for_api(self):
        dev = read_text("docker-compose.yml")
        # Isolate the api service block so we attribute REDIS_URL correctly.
        api_block = re.search(r"\n  api:\n(.*?)(?=\n  \w+:|\nvolumes:)", dev, re.DOTALL)
        assert api_block, "could not locate the 'api' service block in dev compose"
        assert "REDIS_URL=redis://" in api_block.group(1), (
            "dev compose 'api' service must set REDIS_URL=redis://..."
        )

    def test_dev_compose_sets_redis_url_for_sync_worker(self):
        dev = read_text("docker-compose.yml")
        worker_block = re.search(
            r"\n  sync_worker:\n(.*?)(?=\n  \w+:|\nvolumes:)", dev, re.DOTALL
        )
        assert worker_block, "could not locate the 'sync_worker' service block in dev compose"
        assert "REDIS_URL=redis://" in worker_block.group(1), (
            "dev compose 'sync_worker' service must set REDIS_URL=redis://..."
        )

    def test_dev_compose_redisdata_mounted_at_data(self):
        dev = read_text("docker-compose.yml")
        assert "redisdata:/data" in dev, (
            "redisdata volume must be mounted at /data for Redis persistence"
        )

    def test_prod_compose_has_redis_service(self):
        prod = read_text("deploy/docker-compose.prod.yml")
        assert "redis:7-alpine" in prod, (
            "prod docker-compose.prod.yml must also include a redis service"
        )


class TestCacheControlHeaders:
    """HTML page responses are never cached; JSON endpoints are unaffected."""

    async def test_login_html_is_no_store(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200, f"GET /login -> {resp.status_code}"
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc or "no-cache" in cc, (
            f"/login must carry a no-store/no-cache Cache-Control, got {cc!r}"
        )

    async def test_home_html_is_no_store(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200, f"GET / -> {resp.status_code}"
        ctype = resp.headers.get("content-type", "")
        assert "text/html" in ctype, f"GET / should be HTML, got {ctype!r}"
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc or "no-cache" in cc, (
            f"home page must carry a no-store/no-cache Cache-Control, got {cc!r}"
        )

    async def test_header_set_for_text_html(self, client):
        # Any HTML page route should get the forced cache-control from the
        # add_security_headers middleware keyed on text/html content-type.
        resp = await client.get("/dashboard")
        assert resp.status_code == 200, f"GET /dashboard -> {resp.status_code}"
        assert "text/html" in resp.headers.get("content-type", ""), (
            "/dashboard should be served as text/html"
        )
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc or "no-cache" in cc, (
            f"text/html responses must get a no-store Cache-Control, got {cc!r}"
        )

    async def test_json_endpoint_not_forced_html_caching(self, client):
        # The middleware only forces no-store for text/html. A JSON endpoint
        # should respond normally and as JSON — i.e. the behavior is HTML-specific.
        resp = await client.get("/api/health")
        assert resp.status_code == 200, f"GET /api/health -> {resp.status_code}"
        ctype = resp.headers.get("content-type", "")
        assert "application/json" in ctype, (
            f"/api/health should be JSON, got {ctype!r}"
        )
        assert resp.json().get("status") == "ok", "health endpoint must report status ok"


class TestRedisConnectivity:
    """Live Redis round-trip — skips cleanly when Redis is unreachable."""

    def test_redis_ping(self, require_redis):
        import os

        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, decode_responses=True)
        assert client.ping() is True, "Redis PING should return True"

    def test_redis_set_get_roundtrip(self, require_redis):
        import os

        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, decode_responses=True)
        key = "test:domain10:roundtrip"
        client.set(key, "cdn-value")
        assert client.get(key) == "cdn-value", "set/get must round-trip the value"

    def test_redis_delete_key(self, require_redis):
        import os

        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, decode_responses=True)
        key = "test:domain10:delete"
        client.set(key, "to-be-removed")
        client.delete(key)
        assert client.get(key) is None, "key should be gone after delete"

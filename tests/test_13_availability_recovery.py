"""
Domain 13 — Availability & Recovery.

Verifies the pieces that keep the ERITAS MDA dashboard available and
recoverable: the public health endpoint, RDS backup/snapshot/deletion
protections, container restart policies, async DB connection-pool resilience,
persistent Redis storage, the remote Terraform state backend with locking, and
the CloudWatch monitoring alarms.

Per the test-authoring contract, all `app.*` / sqlalchemy imports are performed
lazily inside the test methods so collection never breaks when app deps are
absent.
"""
import re

import pytest

from conftest import read_text, terraform_text


class TestHealthEndpoint:
    """GET /api/health — public, static, dependency-free liveness probe."""

    async def test_health_returns_200(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"

    async def test_health_status_ok(self, client):
        resp = await client.get("/api/health")
        body = resp.json()
        assert body.get("status") == "ok", f"status not 'ok': {body!r}"

    async def test_health_is_public_no_auth_header(self, client):
        # No Authorization header supplied — must still succeed.
        resp = await client.get("/api/health")
        assert resp.status_code == 200, "health must be reachable without auth"

    async def test_health_returns_json_content_type(self, client):
        resp = await client.get("/api/health")
        ctype = resp.headers.get("content-type", "")
        assert "application/json" in ctype, f"not JSON: {ctype!r}"

    async def test_health_body_has_no_traceback(self, client):
        resp = await client.get("/api/health")
        text = resp.text
        assert "Traceback" not in text, "health body leaked a Python traceback"
        assert "File \"" not in text, "health body leaked a stack frame"

    async def test_health_responds_without_db(self, client):
        # The endpoint is a static dict in app/main.py — it does not touch the
        # DB, so it must answer 200 even when PostgreSQL is unreachable.
        resp = await client.get("/api/health")
        assert resp.status_code == 200, "health must not depend on the database"
        assert resp.json() == {"status": "ok", "service": "geospatial-tracker"}


class TestRDSRecovery:
    """terraform/database.tf — backups, snapshots, encryption, deletion guard."""

    def _db(self) -> str:
        return read_text("terraform/database.tf")

    def test_deletion_protection_enabled(self):
        txt = self._db()
        assert re.search(r"deletion_protection\s*=\s*true", txt), \
            "deletion_protection must be true"

    def test_skip_final_snapshot_false(self):
        txt = self._db()
        assert re.search(r"skip_final_snapshot\s*=\s*false", txt), \
            "skip_final_snapshot must be false (a final snapshot is required)"

    def test_final_snapshot_identifier_set(self):
        txt = self._db()
        assert re.search(r"final_snapshot_identifier\s*=", txt), \
            "final_snapshot_identifier must be configured"

    def test_backup_retention_at_least_7(self):
        txt = self._db()
        m = re.search(r"backup_retention_period\s*=\s*(\d+)", txt)
        assert m, "backup_retention_period not found"
        assert int(m.group(1)) >= 7, \
            f"backup_retention_period must be >= 7, got {m.group(1)}"

    def test_storage_encrypted(self):
        txt = self._db()
        assert re.search(r"storage_encrypted\s*=\s*true", txt), \
            "storage_encrypted must be true"

    def test_max_allocated_storage_is_200(self):
        txt = self._db()
        m = re.search(r"max_allocated_storage\s*=\s*(\d+)", txt)
        assert m, "max_allocated_storage not found"
        assert int(m.group(1)) == 200, \
            f"max_allocated_storage must be 200, got {m.group(1)}"

    def test_engine_is_postgres(self):
        txt = self._db()
        assert re.search(r'engine\s*=\s*"postgres"', txt), \
            "RDS engine must be postgres"


class TestContainerRestart:
    """docker-compose restart policies keep containers alive across crashes."""

    _RESTART = re.compile(r"restart:\s*(unless-stopped|always)")

    def test_dev_api_has_restart_policy(self):
        txt = read_text("docker-compose.yml")
        # The api service block, up to the next top-level service.
        block = re.search(r"\n  api:\n(.*?)(?=\n  \w+:|\nvolumes:)", txt, re.S)
        assert block, "api service block not found in docker-compose.yml"
        assert self._RESTART.search(block.group(1)), \
            "dev api service needs a restart policy"

    def test_dev_sync_worker_has_restart_policy(self):
        txt = read_text("docker-compose.yml")
        block = re.search(r"\n  sync_worker:\n(.*?)(?=\n  \w+:|\nvolumes:)", txt, re.S)
        assert block, "sync_worker service block not found in docker-compose.yml"
        assert self._RESTART.search(block.group(1)), \
            "dev sync_worker service needs a restart policy"

    def test_prod_has_restart_policy(self):
        txt = read_text("deploy/docker-compose.prod.yml")
        assert self._RESTART.search(txt), \
            "prod compose must declare at least one restart policy"

    def test_prod_api_restart_present(self):
        txt = read_text("deploy/docker-compose.prod.yml")
        block = re.search(r"\n  api:\n(.*?)(?=\n  \w+:|\nvolumes:)", txt, re.S)
        assert block, "api service block not found in prod compose"
        assert self._RESTART.search(block.group(1)), \
            "prod api service needs a restart policy"


class TestDatabaseResilience:
    """app/database.py — async engine pool tuned for resilience."""

    def test_pool_pre_ping_enabled(self):
        db = pytest.importorskip("app.database")
        assert db.engine.pool._pre_ping is True, \
            "pool_pre_ping must be True to drop dead connections"

    def test_engine_uses_asyncpg(self):
        db = pytest.importorskip("app.database")
        url = str(db.engine.url)
        assert "asyncpg" in url, f"engine should use asyncpg driver, got {url}"

    def test_async_session_local_exists(self):
        db = pytest.importorskip("app.database")
        assert hasattr(db, "AsyncSessionLocal"), "AsyncSessionLocal must exist"
        assert db.AsyncSessionLocal is not None

    def test_total_pool_capacity_at_least_30(self):
        db = pytest.importorskip("app.database")
        pool = db.engine.pool
        total = pool.size() + pool._max_overflow
        assert total >= 30, \
            f"pool_size + max_overflow must be >= 30, got {total}"

    def test_pool_size_at_least_10(self):
        db = pytest.importorskip("app.database")
        assert db.engine.pool.size() >= 10, \
            f"pool_size must be >= 10, got {db.engine.pool.size()}"


class TestRedisResilience:
    """Redis state survives restarts via a named persistent volume."""

    def test_named_redisdata_volume_declared(self):
        txt = read_text("docker-compose.yml")
        # Top-level volumes: block must declare a named redisdata volume.
        vol_block = re.search(r"\nvolumes:\n(.*)$", txt, re.S)
        assert vol_block, "top-level volumes: block not found"
        assert re.search(r"^\s+redisdata:", vol_block.group(1), re.M), \
            "a NAMED redisdata volume must be declared (not a tmpfs)"
        assert "tmpfs" not in txt, "redis data must not be on tmpfs"

    def test_redisdata_mounted_at_data(self):
        txt = read_text("docker-compose.yml")
        assert re.search(r"redisdata:/data", txt), \
            "redisdata must be mounted at /data for persistence across restarts"

    def test_redis_uses_image_not_build(self):
        txt = read_text("docker-compose.yml")
        block = re.search(r"\n  redis:\n(.*?)(?=\n  \w+:|\nvolumes:)", txt, re.S)
        assert block, "redis service block not found"
        assert re.search(r"image:\s*redis:7-alpine", block.group(1)), \
            "redis must use the redis:7-alpine image"
        assert "build:" not in block.group(1), \
            "redis must not be an ephemeral build"


class TestTerraformStateBackend:
    """Remote S3 state + DynamoDB lock prevent state loss / concurrent applies."""

    def test_s3_backend_block_present(self):
        txt = terraform_text()
        assert re.search(r'backend\s+"s3"', txt), \
            "an S3 state backend prevents local state loss"

    def test_dynamodb_lock_configured(self):
        txt = terraform_text()
        assert re.search(r"dynamodb_table\s*=", txt), \
            "a DynamoDB lock table prevents concurrent applies"

    def test_lock_table_name_referenced(self):
        txt = terraform_text()
        assert "eha-mda-dashboard-tflock" in txt, \
            "the expected DynamoDB lock table name must be referenced"


class TestMonitoringAlarms:
    """observability.tf — CloudWatch alarms covering the failure modes."""

    def _obs(self) -> str:
        return read_text("terraform/observability.tf")

    def test_cpu_alarm(self):
        assert "CPUUtilization" in self._obs(), \
            "a CPUUtilization alarm must be configured"

    def test_db_connections_alarm(self):
        assert "DatabaseConnections" in self._obs(), \
            "a DatabaseConnections alarm must be configured"

    def test_ec2_status_check_alarm(self):
        assert "StatusCheckFailed" in self._obs(), \
            "an EC2 StatusCheckFailed alarm must be configured"

    def test_alb_5xx_alarm(self):
        assert "HTTPCode_Target_5XX_Count" in self._obs(), \
            "an ALB 5xx alarm must be configured"

"""
Domain 05 — Hosting & Deployment.

Pure file-content assertion tests over the container/build/runtime config:
the root Dockerfile, the dev docker-compose.yml, the production
deploy/docker-compose.prod.yml, and the documented .env.example. No live app
or database is required — every assertion reads a real file via the
``read_text`` helper from conftest (or, for the SECRET_KEY guard, imports
app.config in a throwaway subprocess).
"""
from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPO_ROOT, read_text


class TestDockerfile:
    """The container image build (root Dockerfile)."""

    def test_python_311_base_image(self):
        text = read_text("Dockerfile")
        assert "FROM python:3.11" in text, "Dockerfile must build FROM python:3.11"

    def test_workdir_is_app(self):
        text = read_text("Dockerfile")
        assert "WORKDIR /app" in text, "Dockerfile must set WORKDIR /app"

    def test_copies_requirements(self):
        text = read_text("Dockerfile")
        assert "COPY requirements.txt" in text, "Dockerfile must COPY requirements.txt"

    def test_runs_pip_install(self):
        text = read_text("Dockerfile")
        assert "pip install" in text, "Dockerfile must run pip install"
        assert "requirements.txt" in text, "pip install must target requirements.txt"

    def test_exposes_port_8080(self):
        text = read_text("Dockerfile")
        assert "EXPOSE 8080" in text, "Dockerfile must EXPOSE 8080"

    def test_creates_uploads_dir(self):
        text = read_text("Dockerfile")
        assert "mkdir -p /app/uploads" in text, (
            "Dockerfile must create the uploads dir (mkdir -p /app/uploads)"
        )

    def test_pip_uses_no_cache_dir(self):
        text = read_text("Dockerfile")
        assert "--no-cache-dir" in text, (
            "pip install must use --no-cache-dir to keep the image small"
        )


class TestDockerCompose:
    """The local development docker-compose.yml."""

    def test_has_restart_policy(self):
        text = read_text("docker-compose.yml")
        assert "restart:" in text, "dev compose must declare a restart: policy"

    def test_depends_on_redis(self):
        text = read_text("docker-compose.yml")
        assert "depends_on:" in text, "dev compose must declare depends_on"
        assert "redis" in text, "dev compose must depend on redis"

    def test_defines_sync_worker_service(self):
        text = read_text("docker-compose.yml")
        assert "sync_worker:" in text, "dev compose must define a sync_worker service"

    def test_maps_container_port_8080(self):
        text = read_text("docker-compose.yml")
        assert "8080" in text, "dev compose ports must reference container port 8080"

    def test_references_env_file(self):
        text = read_text("docker-compose.yml")
        assert "env_file" in text and ".env" in text, (
            "dev compose must load secrets from the .env env_file"
        )

    def test_declares_redisdata_volume(self):
        text = read_text("docker-compose.yml")
        # Top-level volumes: block declares the named redisdata volume.
        assert "redisdata" in text, "dev compose must declare a redisdata volume"

    def test_declares_uploads_volume(self):
        text = read_text("docker-compose.yml")
        assert "uploads" in text, "dev compose must declare an uploads volume"

    def test_redis_uses_7_alpine_image(self):
        text = read_text("docker-compose.yml")
        assert "redis:7-alpine" in text, "redis service must use redis:7-alpine"

    def test_sets_redis_url(self):
        text = read_text("docker-compose.yml")
        assert "REDIS_URL" in text, "dev compose must set REDIS_URL"


class TestProdDockerCompose:
    """The production deploy/docker-compose.prod.yml."""

    def test_healthcheck_targets_api_health(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "healthcheck:" in text, "prod compose must define a healthcheck"
        assert "/api/health" in text, "prod healthcheck must target /api/health"

    def test_uses_awslogs_driver(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "awslogs" in text, "prod compose must use the awslogs logging driver"

    def test_uvicorn_uses_proxy_headers(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "--proxy-headers" in text, (
            "prod uvicorn command must include --proxy-headers (behind the ALB)"
        )

    def test_image_references_ecr_repo_url(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "${ECR_REPO_URL}" in text, (
            "prod image must reference the ${ECR_REPO_URL} variable"
        )

    def test_has_restart_policy(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "restart:" in text, "prod compose must declare a restart: policy"

    def test_maps_8080_to_8080(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "8080:8080" in text, "prod compose must map 8080:8080 for the ALB"

    def test_includes_redis_service(self):
        text = read_text("deploy/docker-compose.prod.yml")
        assert "redis:" in text, "prod compose must include a redis service"


class TestEnvConfig:
    """The documented environment template (.env.example) and the config guard."""

    def test_documents_database_url(self):
        text = read_text(".env.example")
        assert "DATABASE_URL" in text, ".env.example must document DATABASE_URL"

    def test_documents_secret_key(self):
        text = read_text(".env.example")
        assert "SECRET_KEY" in text, ".env.example must document SECRET_KEY"

    def test_documents_redis_url(self):
        text = read_text(".env.example")
        assert "REDIS_URL" in text, ".env.example must document REDIS_URL"

    def test_documents_sentry_dsn(self):
        text = read_text(".env.example")
        assert "SENTRY_DSN" in text, ".env.example must document SENTRY_DSN"

    def test_documents_superadmin_bootstrap_vars(self):
        text = read_text(".env.example")
        for var in ("SUPERADMIN_USERNAME", "SUPERADMIN_PASSWORD", "SUPERADMIN_EMAIL"):
            assert var in text, f".env.example must document the bootstrap var {var}"

    def test_short_secret_key_raises_runtime_error(self):
        # Importing app.config with a too-short SECRET_KEY must raise RuntimeError
        # (the >= 32 byte guard). Run in a subprocess so the bad key never
        # contaminates this interpreter's already-imported config.
        env = dict(os.environ)
        env["SECRET_KEY"] = "short"  # < 32 bytes
        env["ENVIRONMENT"] = "development"
        proc = subprocess.run(
            [sys.executable, "-c", "import app.config"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0, (
            "importing app.config with a short SECRET_KEY should fail"
        )
        assert "RuntimeError" in proc.stderr, (
            f"expected a RuntimeError from the SECRET_KEY guard; got: {proc.stderr}"
        )

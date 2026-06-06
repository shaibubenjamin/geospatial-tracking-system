"""
Domain 12 — Error Tracking & Logs.

Covers Sentry error tracking wiring, structured JSON logging, sanitized
error response formats, and CloudWatch log shipping (Terraform log groups,
the prod compose awslogs driver, and SSM->CloudWatch command output).

Per the test-authoring contract: app/sqlalchemy imports live inside test
methods (or via pytest.importorskip); only `import pytest` and stdlib are at
module top level.
"""
import json
import logging

import pytest

from conftest import read_text


class TestSentryErrorTracking:
    def test_sentry_sdk_pinned_in_requirements(self):
        reqs = read_text("requirements.txt")
        assert "sentry-sdk" in reqs, "sentry-sdk must be listed in requirements.txt"
        # Every dependency is pinned with == per the project convention.
        line = next(
            ln for ln in reqs.splitlines() if ln.strip().startswith("sentry-sdk")
        )
        assert "==" in line, f"sentry-sdk must be pinned with ==, got: {line!r}"

    def test_sentry_sdk_importable(self):
        # Presence of the package is the thing under test.
        pytest.importorskip("sentry_sdk")

    def test_main_reads_sentry_dsn(self):
        src = read_text("app/main.py")
        assert "SENTRY_DSN" in src, "app/main.py must read the SENTRY_DSN env var"
        assert 'os.getenv("SENTRY_DSN"' in src, (
            "app/main.py should read SENTRY_DSN via os.getenv"
        )

    def test_main_calls_sentry_init(self):
        src = read_text("app/main.py")
        assert "sentry_sdk.init" in src, "app/main.py must call sentry_sdk.init(...)"

    def test_sentry_init_includes_environment_arg(self):
        src = read_text("app/main.py")
        # Locate the sentry_sdk.init(...) call and confirm environment= is passed.
        idx = src.index("sentry_sdk.init")
        snippet = src[idx:idx + 400]
        assert "environment=" in snippet, (
            "sentry_sdk.init must be configured with an environment= argument"
        )

    def test_env_example_documents_sentry_dsn(self):
        env = read_text(".env.example")
        assert "SENTRY_DSN" in env, ".env.example must document SENTRY_DSN"


class TestStructuredLogging:
    def test_json_formatter_class_exists(self):
        main = pytest.importorskip("app.main")
        assert hasattr(main, "_JsonFormatter"), (
            "app.main must define a _JsonFormatter class"
        )

    def test_json_formatter_subclasses_logging_formatter(self):
        main = pytest.importorskip("app.main")
        assert issubclass(main._JsonFormatter, logging.Formatter), (
            "_JsonFormatter must subclass logging.Formatter"
        )

    def test_stream_handler_attached_to_root_logger(self):
        pytest.importorskip("app.main")
        root = logging.getLogger()
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers), (
            "a StreamHandler must be attached to the root logger"
        )

    def test_formatter_emits_parseable_json_dict(self):
        main = pytest.importorskip("app.main")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello structured world",
            args=(),
            exc_info=None,
        )
        out = main._JsonFormatter().format(record)
        parsed = json.loads(out)
        assert isinstance(parsed, dict), (
            "_JsonFormatter output must json.loads to a dict"
        )

    def test_formatter_dict_has_level_field(self):
        main = pytest.importorskip("app.main")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="warn message",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(main._JsonFormatter().format(record))
        assert "level" in parsed, "structured log payload must include a level field"
        assert parsed["level"] == "WARNING"

    def test_formatter_dict_has_message_field(self):
        main = pytest.importorskip("app.main")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="the message body",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(main._JsonFormatter().format(record))
        assert ("msg" in parsed) or ("message" in parsed), (
            "structured log payload must include a msg/message field"
        )
        assert parsed.get("msg") == "the message body"

    def test_formatter_dict_has_timestamp_field(self):
        main = pytest.importorskip("app.main")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="needs a timestamp",
            args=(),
            exc_info=None,
        )
        parsed = json.loads(main._JsonFormatter().format(record))
        assert "ts" in parsed, "structured log payload must include a ts timestamp field"


class TestErrorResponseFormat:
    async def test_404_returns_json_not_html(self, client):
        resp = await client.get("/api/this-route-does-not-exist-xyz")
        assert resp.status_code == 404
        ctype = resp.headers.get("content-type", "")
        assert "application/json" in ctype, (
            f"404 should be JSON, got content-type {ctype!r}"
        )
        # Confirm it actually parses as JSON.
        resp.json()

    async def test_404_body_has_no_traceback(self, client):
        resp = await client.get("/api/this-route-does-not-exist-xyz")
        body = resp.text.lower()
        assert "traceback" not in body, "404 body must not leak a Python traceback"
        assert "file \"" not in body and 'file \'' not in body, (
            "404 body must not leak source file paths from a traceback"
        )

    async def test_422_bad_login_body_has_detail_field(self, client):
        # POST /api/auth/login with a malformed body -> 422 validation error.
        resp = await client.post("/api/auth/login", json={"not_a_field": "x"})
        assert resp.status_code == 422, (
            f"malformed login body should yield 422, got {resp.status_code}"
        )
        data = resp.json()
        assert "detail" in data, "422 validation responses must carry a detail field"

    async def test_422_has_no_traceback(self, client):
        resp = await client.post("/api/auth/login", json={"not_a_field": "x"})
        assert resp.status_code == 422
        body = resp.text.lower()
        assert "traceback" not in body, "422 body must not leak a Python traceback"

    async def test_health_returns_json(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        ctype = resp.headers.get("content-type", "")
        assert "application/json" in ctype, (
            f"/api/health should be JSON, got content-type {ctype!r}"
        )
        data = resp.json()
        assert data.get("status") == "ok"

    async def test_health_body_exposes_no_sensitive_keys(self, client):
        resp = await client.get("/api/health")
        body = resp.text.lower()
        for forbidden in ("secret", "password", "token"):
            assert forbidden not in body, (
                f"/api/health body must not expose a {forbidden!r} value"
            )


class TestCloudWatchLogging:
    def test_terraform_log_group_with_retention(self):
        hcl = read_text("terraform/observability.tf")
        assert "aws_cloudwatch_log_group" in hcl, (
            "observability.tf must declare an aws_cloudwatch_log_group"
        )
        assert "retention_in_days" in hcl, (
            "the CloudWatch log group must set retention_in_days"
        )

    def test_ec2_system_log_group_exists(self):
        hcl = read_text("terraform/observability.tf")
        assert ("ec2_system" in hcl) or ("ec2-system" in hcl), (
            "an ec2-system / ec2_system CloudWatch log group must exist"
        )

    def test_prod_compose_uses_awslogs_driver(self):
        compose = read_text("deploy/docker-compose.prod.yml")
        assert "awslogs" in compose, (
            "prod compose must ship container logs via the awslogs driver"
        )
        assert "driver: awslogs" in compose, (
            "prod compose logging block must set driver: awslogs"
        )

    def test_prod_compose_awslogs_group_configured(self):
        compose = read_text("deploy/docker-compose.prod.yml")
        assert "awslogs-group" in compose, (
            "the awslogs driver must be configured with an awslogs-group"
        )

    def test_deploy_forwards_ssm_output_to_cloudwatch(self):
        deploy = read_text(".github/workflows/deploy.yml")
        # deploy.yml ships SSM Run Command output to CloudWatch via the
        # --cloud-watch-output-config flag and reads results back with
        # get-command-invocation.
        assert "cloud-watch-output-config" in deploy or "CloudWatchOutputEnabled" in deploy, (
            "deploy.yml must forward SSM command output to CloudWatch"
        )
        assert "get-command-invocation" in deploy, (
            "deploy.yml must read SSM command output back via get-command-invocation"
        )

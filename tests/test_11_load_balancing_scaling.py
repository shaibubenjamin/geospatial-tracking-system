"""Domain 11 — Load Balancing & Scaling.

Asserts the infrastructure and app-config posture that lets the platform
absorb load and scale: the public ALB fanning out across two AZs with a
health-checked target group, the SQLAlchemy async connection pool sizing,
EC2 instance sizing, the isolated CommCare sync worker, and RDS storage
autoscaling / Multi-AZ wiring.

Most assertions read the real Terraform HCL / docker-compose text. A couple
inspect the live `app.database` engine config in-process. Per the contract,
all `app.*` / sqlalchemy imports happen lazily inside the test methods.
"""

import re

from conftest import read_text


class TestALBLoadBalancing:
    """The application load balancer is public, multi-AZ, and health-checked."""

    def test_load_balancer_type_application(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert 'load_balancer_type = "application"' in hcl, \
            "ALB must be an application load balancer"

    def test_alb_is_internet_facing(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert "internal           = false" in hcl or "internal = false" in hcl, \
            "ALB must be internet-facing (internal = false)"

    def test_alb_spans_two_az_subnets(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert "aws_subnet.public_a" in hcl and "aws_subnet.public_b" in hcl, \
            "ALB subnets must span both public_a and public_b (two AZs)"

    def test_health_check_path_is_api_health(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert 'path                = "/api/health"' in hcl or 'path = "/api/health"' in hcl, \
            "Target group health check path must be /api/health"

    def test_health_check_expects_200(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert re.search(r'matcher\s*=\s*"200"', hcl), \
            "Health check matcher must expect HTTP 200"

    def test_idle_timeout_is_600(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert re.search(r'idle_timeout\s*=\s*600', hcl), \
            "ALB idle_timeout must be 600s (long bundle uploads)"

    def test_tls_ssl_policy_is_set(self):
        hcl = read_text("terraform/load-balancer.tf")
        assert re.search(r'ssl_policy\s*=\s*"ELBSecurityPolicy', hcl), \
            "HTTPS listener must set a TLS ssl_policy"


class TestConnectionPool:
    """The async SQLAlchemy engine is sized for concurrent load."""

    def test_pool_size_at_least_10(self):
        from app.database import engine  # lazy import: app.database under test
        assert engine.pool.size() >= 10, "pool_size must be >= 10"

    def test_max_overflow_at_least_20(self):
        from app.database import engine
        # SQLAlchemy QueuePool exposes the configured overflow ceiling as _max_overflow.
        assert engine.pool._max_overflow >= 20, "max_overflow must be >= 20"

    def test_total_capacity_at_least_30(self):
        from app.database import engine
        total = engine.pool.size() + engine.pool._max_overflow
        assert total >= 30, "size + max_overflow must give >= 30 total connections"

    def test_pool_pre_ping_enabled(self):
        from app.database import engine
        assert engine.pool._pre_ping is True, "pool_pre_ping must be True"

    def test_engine_uses_asyncpg(self):
        from app.database import engine
        assert "asyncpg" in engine.url.drivername, \
            "engine must use the postgresql+asyncpg driver"


class TestEC2Sizing:
    """The EC2 host is sized and parameterised for the combined stack."""

    def test_instance_type_default_is_t3_large(self):
        hcl = read_text("terraform/variables.tf")
        m = re.search(
            r'variable\s+"ec2_instance_type"\s*\{.*?default\s*=\s*"([^"]+)"',
            hcl, re.DOTALL)
        assert m and m.group(1) == "t3.large", \
            "ec2_instance_type default must be t3.large"

    def test_root_volume_size_is_30(self):
        hcl = read_text("terraform/compute.tf")
        assert re.search(r'volume_size\s*=\s*30', hcl), \
            "root EBS volume_size must be 30 GB"

    def test_instance_type_is_parameterised(self):
        hcl = read_text("terraform/compute.tf")
        assert re.search(r'instance_type\s*=\s*var\.ec2_instance_type', hcl), \
            "instance_type must be wired to var.ec2_instance_type"

    def test_root_volume_type_is_gp3(self):
        hcl = read_text("terraform/compute.tf")
        assert re.search(r'volume_type\s*=\s*"gp3"', hcl), \
            "root EBS volume_type must be gp3"


class TestSyncWorkerIsolation:
    """The CommCare sync worker runs as its own container, isolated from the API."""

    def test_sync_worker_service_defined(self):
        compose = read_text("docker-compose.yml")
        assert re.search(r'^\s*sync_worker\s*:', compose, re.MULTILINE), \
            "docker-compose must define a separate sync_worker service"

    def test_sync_worker_has_restart_policy(self):
        compose = read_text("docker-compose.yml")
        # Isolate the sync_worker block and confirm it has its own restart:.
        block = compose.split("sync_worker:", 1)[1]
        assert re.search(r'^\s*restart\s*:', block, re.MULTILINE), \
            "sync_worker service must declare its own restart policy"

    def test_sync_worker_runs_different_command(self):
        compose = read_text("docker-compose.yml")
        block = compose.split("sync_worker:", 1)[1]
        assert "python -m app.sync_worker" in block, \
            "sync_worker must run `python -m app.sync_worker`"
        # And explicitly NOT uvicorn, so syncs don't block API redeploys.
        cmd_line = next(
            (ln for ln in block.splitlines() if "command:" in ln), "")
        assert "uvicorn" not in cmd_line, \
            "sync_worker command must not be uvicorn"


class TestRDSScaling:
    """RDS uses gp3 with storage autoscaling and variable-driven Multi-AZ."""

    def test_storage_type_is_gp3(self):
        hcl = read_text("terraform/database.tf")
        assert re.search(r'storage_type\s*=\s*"gp3"', hcl), \
            "RDS storage_type must be gp3"

    def test_max_allocated_storage_is_200(self):
        hcl = read_text("terraform/database.tf")
        assert re.search(r'max_allocated_storage\s*=\s*200', hcl), \
            "RDS max_allocated_storage (autoscaling ceiling) must be 200"

    def test_multi_az_wired_to_variable(self):
        hcl = read_text("terraform/database.tf")
        assert re.search(r'multi_az\s*=\s*var\.rds_multi_az', hcl), \
            "multi_az must be wired to var.rds_multi_az"

    def test_rds_multi_az_variable_exists(self):
        hcl = read_text("terraform/variables.tf")
        assert re.search(r'variable\s+"rds_multi_az"\s*\{', hcl), \
            "rds_multi_az variable must be declared"

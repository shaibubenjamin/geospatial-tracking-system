"""Domain 06 — Cloud & Compute.

Pure file-content (HCL text) assertions over the real terraform/*.tf sources.
No app, no DB. Everything here reads the actual checked-in Terraform via the
conftest helpers (`read_text` / `terraform_text`) and asserts only facts that
are TRUE of the committed infrastructure code.
"""

import re

import pytest

from conftest import read_text, terraform_text


# Strip HCL comments so we never match a value that only appears in a comment.
def _strip_comments(text: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"#.*", "", no_block)


class TestEC2Config:
    def test_instance_type_references_variable(self):
        tf = read_text("terraform/compute.tf")
        assert "instance_type" in tf and "var.ec2_instance_type" in tf, (
            "EC2 instance_type should reference var.ec2_instance_type"
        )
        assert re.search(
            r"instance_type\s*=\s*var\.ec2_instance_type", tf
        ), "instance_type must be wired to var.ec2_instance_type"

    def test_root_volume_is_gp3(self):
        tf = read_text("terraform/compute.tf")
        assert re.search(
            r'volume_type\s*=\s*"gp3"', tf
        ), "Root block device should use gp3"

    def test_root_volume_size_30(self):
        tf = read_text("terraform/compute.tf")
        assert re.search(
            r"volume_size\s*=\s*30\b", tf
        ), "Root volume_size should be 30"

    def test_root_volume_encrypted(self):
        tf = read_text("terraform/compute.tf")
        assert re.search(
            r"encrypted\s*=\s*true", tf
        ), "Root block device must be encrypted"

    def test_imdsv2_http_tokens_required(self):
        tf = read_text("terraform/compute.tf")
        assert re.search(
            r'http_tokens\s*=\s*"required"', tf
        ), "IMDSv2 requires http_tokens = \"required\""

    def test_metadata_http_endpoint_enabled(self):
        tf = read_text("terraform/compute.tf")
        assert re.search(
            r'http_endpoint\s*=\s*"enabled"', tf
        ), "metadata_options http_endpoint should be enabled"


class TestRDSConfig:
    def test_engine_is_postgres(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r'engine\s*=\s*"postgres"', tf
        ), "RDS engine should be postgres"

    def test_engine_version_uses_variable(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r"engine_version\s*=\s*var\.rds_engine_version", tf
        ), "engine_version should reference var.rds_engine_version"
        vt = read_text("terraform/variables.tf")
        assert re.search(
            r'default\s*=\s*"16\.\d+"', vt
        ), "rds_engine_version default should be a 16.x version"

    def test_storage_encrypted(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r"storage_encrypted\s*=\s*true", tf
        ), "RDS storage must be encrypted at rest"

    def test_deletion_protection(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r"deletion_protection\s*=\s*true", tf
        ), "RDS deletion_protection must be true"

    def test_backup_retention_at_least_7(self):
        tf = read_text("terraform/database.tf")
        m = re.search(r"backup_retention_period\s*=\s*(\d+)", tf)
        assert m, "backup_retention_period must be set"
        assert int(m.group(1)) >= 7, "backup_retention_period should be >= 7"

    def test_not_publicly_accessible(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r"publicly_accessible\s*=\s*false", tf
        ), "RDS must not be publicly accessible"

    def test_storage_type_gp3(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r'storage_type\s*=\s*"gp3"', tf
        ), "RDS storage_type should be gp3"

    def test_max_allocated_storage_200(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r"max_allocated_storage\s*=\s*200\b", tf
        ), "RDS max_allocated_storage should be 200"

    def test_performance_insights_enabled(self):
        tf = read_text("terraform/database.tf")
        assert re.search(
            r"performance_insights_enabled\s*=\s*true", tf
        ), "RDS performance_insights_enabled should be true"


class TestALBConfig:
    def test_load_balancer_type_application(self):
        tf = read_text("terraform/load-balancer.tf")
        assert re.search(
            r'load_balancer_type\s*=\s*"application"', tf
        ), "ALB load_balancer_type should be application"

    def test_alb_internet_facing(self):
        tf = read_text("terraform/load-balancer.tf")
        assert re.search(
            r"internal\s*=\s*false", tf
        ), "ALB should be internet-facing (internal = false)"

    def test_health_check_path(self):
        tf = read_text("terraform/load-balancer.tf")
        assert re.search(
            r'path\s*=\s*"/api/health"', tf
        ), "Target group health check path should be /api/health"

    def test_idle_timeout_600(self):
        tf = read_text("terraform/load-balancer.tf")
        assert re.search(
            r"idle_timeout\s*=\s*600\b", tf
        ), "ALB idle_timeout should be 600"

    def test_tls13_ssl_policy(self):
        tf = read_text("terraform/load-balancer.tf")
        assert "ELBSecurityPolicy-TLS13" in tf, (
            "HTTPS listener should use a TLS1.3 ssl_policy"
        )

    def test_http_to_https_redirect(self):
        tf = read_text("terraform/load-balancer.tf")
        assert re.search(
            r'status_code\s*=\s*"HTTP_301"', tf
        ), "HTTP listener should permanently redirect (HTTP_301) to HTTPS"

    def test_target_group_port_8080(self):
        tf = read_text("terraform/load-balancer.tf")
        assert re.search(
            r"port\s*=\s*8080\b", tf
        ), "Target group should forward to port 8080"


class TestECRConfig:
    def test_scan_on_push(self):
        tf = read_text("terraform/ecr.tf")
        assert re.search(
            r"scan_on_push\s*=\s*true", tf
        ), "ECR repository should scan images on push"

    def test_lifecycle_policy_exists(self):
        tf = read_text("terraform/ecr.tf")
        assert 'resource "aws_ecr_lifecycle_policy"' in tf, (
            "An aws_ecr_lifecycle_policy resource should exist"
        )

    def test_repository_exists(self):
        tf = read_text("terraform/ecr.tf")
        assert 'resource "aws_ecr_repository"' in tf, (
            "An aws_ecr_repository resource should exist"
        )

    def test_repository_name_or_mutability_present(self):
        tf = read_text("terraform/ecr.tf")
        assert re.search(
            r"image_tag_mutability\s*=", tf
        ), "ECR repo should declare image_tag_mutability"


class TestIAMAndOIDC:
    def test_github_oidc_provider(self):
        tf = read_text("terraform/github-oidc.tf")
        assert "aws_iam_openid_connect_provider" in tf, (
            "GitHub OIDC provider should be referenced"
        )
        assert "token.actions.githubusercontent.com" in tf, (
            "OIDC provider URL should be GitHub's token endpoint"
        )

    def test_assume_role_has_federated_principal(self):
        tf = read_text("terraform/github-oidc.tf")
        assert re.search(
            r'type\s*=\s*"Federated"', tf
        ), "Assume-role policy should use a Federated principal"

    def test_sub_condition(self):
        tf = read_text("terraform/github-oidc.tf")
        assert "token.actions.githubusercontent.com:sub" in tf, (
            "Trust policy should constrain on the :sub claim"
        )

    def test_aud_condition(self):
        tf = read_text("terraform/github-oidc.tf")
        assert "token.actions.githubusercontent.com:aud" in tf, (
            "Trust policy should constrain on the :aud claim"
        )

    def test_no_static_aws_keys(self):
        text = _strip_comments(terraform_text())
        assert not re.search(
            r"\baws_access_key\b", text
        ), "No static aws_access_key should appear in terraform"
        assert not re.search(
            r"\bsecret_key\b", text
        ), "No static secret_key should appear in terraform"


class TestCloudWatchConfig:
    def test_log_group_has_retention(self):
        tf = read_text("terraform/observability.tf")
        assert 'resource "aws_cloudwatch_log_group"' in tf, (
            "A CloudWatch log group should exist"
        )
        assert re.search(
            r"retention_in_days\s*=\s*\d+", tf
        ), "Log groups should set retention_in_days"

    def test_app_log_group(self):
        tf = read_text("terraform/observability.tf")
        assert re.search(
            r'resource\s+"aws_cloudwatch_log_group"\s+"app"', tf
        ), "An app log group should exist"

    def test_ec2_system_log_group(self):
        tf = read_text("terraform/observability.tf")
        assert re.search(
            r'resource\s+"aws_cloudwatch_log_group"\s+"ec2_system"', tf
        ), "An ec2_system log group should exist"

    def test_at_least_one_metric_alarm(self):
        tf = read_text("terraform/observability.tf")
        assert 'resource "aws_cloudwatch_metric_alarm"' in tf, (
            "At least one aws_cloudwatch_metric_alarm should exist"
        )

    def test_alarm_metric_names_present(self):
        tf = read_text("terraform/observability.tf")
        for metric in (
            "CPUUtilization",
            "DatabaseConnections",
            "HTTPCode_Target_5XX_Count",
        ):
            assert metric in tf, f"Alarm metric {metric} should be configured"


class TestTerraformBackend:
    def test_s3_backend_block(self):
        tf = read_text("terraform/main.tf")
        assert re.search(
            r'backend\s+"s3"', tf
        ), "Terraform should use an S3 backend"

    def test_dynamodb_lock_table(self):
        tf = read_text("terraform/main.tf")
        assert "eha-mda-dashboard-tflock" in tf, (
            "Backend should use the DynamoDB lock table eha-mda-dashboard-tflock"
        )
        assert "dynamodb_table" in tf, "Backend should declare a dynamodb_table"

    def test_state_bucket_reference(self):
        tf = read_text("terraform/main.tf")
        assert re.search(
            r"bucket\s*=\s*\"[^\"]*tfstate[^\"]*\"", tf
        ), "Backend should reference a state bucket"

    def test_region_configured(self):
        tf = read_text("terraform/main.tf")
        assert re.search(
            r'region\s*=\s*"us-east-1"', tf
        ), "Backend should configure a region"


class TestVPCConfig:
    def test_vpc_resource(self):
        tf = read_text("terraform/network.tf")
        assert 'resource "aws_vpc"' in tf, "An aws_vpc resource should exist"

    def test_public_subnets_two_azs(self):
        tf = read_text("terraform/network.tf")
        assert re.search(
            r'resource\s+"aws_subnet"\s+"public_a"', tf
        ), "public_a subnet should exist"
        assert re.search(
            r'resource\s+"aws_subnet"\s+"public_b"', tf
        ), "public_b subnet should exist"

    def test_private_subnets(self):
        tf = read_text("terraform/network.tf")
        assert re.search(
            r'resource\s+"aws_subnet"\s+"private_', tf
        ), "At least one private subnet should exist"

    def test_internet_gateway(self):
        tf = read_text("terraform/network.tf")
        assert 'resource "aws_internet_gateway"' in tf, (
            "An internet gateway should exist"
        )

    def test_alb_spans_two_public_subnets(self):
        tf = read_text("terraform/load-balancer.tf")
        assert "aws_subnet.public_a.id" in tf and "aws_subnet.public_b.id" in tf, (
            "ALB should span both public subnets (public_a + public_b)"
        )

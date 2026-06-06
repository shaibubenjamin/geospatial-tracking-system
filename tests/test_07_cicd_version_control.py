"""
Domain 07 — CI/CD & Version Control.

Pure file-content assertions over the GitHub Actions workflows
(.github/workflows/ci.yml, .github/workflows/deploy.yml), .gitignore, and
requirements.txt. No app import, no database — everything reads the real repo
files via the conftest `read_text` helper.
"""
import re

from conftest import read_text


class TestCIWorkflow:
    """.github/workflows/ci.yml — lint, build, terraform, QA smoke."""

    def test_triggers_on_pull_request_and_push_main_dev(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "pull_request:" in ci, "CI must trigger on pull_request"
        assert "push:" in ci, "CI must trigger on push"
        # push branches include both main and dev
        m = re.search(r"push:\s*\n\s*branches:\s*\[([^\]]+)\]", ci)
        assert m, "expected `push: branches: [..]` block"
        branches = m.group(1)
        assert "main" in branches and "dev" in branches, (
            f"push must target main and dev, got: {branches}"
        )

    def test_concurrency_cancels_in_progress(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "concurrency:" in ci, "CI must declare a concurrency group"
        assert "cancel-in-progress: true" in ci, (
            "CI concurrency should cancel in-progress runs"
        )

    def test_runs_ruff(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "ruff" in ci, "CI must run ruff"

    def test_has_fatal_ruff_bug_rule_check(self):
        ci = read_text(".github/workflows/ci.yml")
        for rule in ("F821", "F823", "F811", "E9"):
            assert rule in ci, f"CI must have a fatal ruff check for {rule}"

    def test_builds_docker_image_without_push(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "build-push-action" in ci or "docker" in ci.lower(), (
            "CI must build a Docker image"
        )
        assert "push: false" in ci, "CI Docker build must not push (push: false)"

    def test_runs_terraform_fmt_check(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "terraform" in ci and "fmt" in ci, "CI must run terraform fmt"
        assert "-check" in ci, "terraform fmt must run with -check"

    def test_runs_terraform_validate(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "terraform" in ci and "validate" in ci, (
            "CI must run terraform validate"
        )

    def test_runs_qa_smoke_script(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "eritas-mda-tests/scripts/qa-prod.sh" in ci, (
            "CI must run the QA prod smoke script"
        )

    def test_qa_job_is_conditional_or_nonblocking(self):
        ci = read_text(".github/workflows/ci.yml")
        assert "continue-on-error" in ci or "if:" in ci, (
            "QA job must be non-blocking (continue-on-error) or conditioned (if:)"
        )

    def test_workflow_name_is_ci(self):
        ci = read_text(".github/workflows/ci.yml")
        assert re.search(r"^name:\s*CI\s*$", ci, flags=re.MULTILINE), (
            "workflow name must be 'CI'"
        )


class TestDeployWorkflow:
    """.github/workflows/deploy.yml — OIDC, SSM-based, no static keys/SSH."""

    def test_has_oidc_id_token_write(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "id-token: write" in dep, (
            "deploy must request OIDC id-token: write permission"
        )

    def test_has_role_to_assume(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "role-to-assume" in dep, (
            "deploy must assume an IAM role (role-to-assume)"
        )

    def test_no_static_aws_keys(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "aws-access-key" not in dep, "deploy must not use a static AWS access key"
        assert "aws-secret-access-key" not in dep, (
            "deploy must not use a static AWS secret access key"
        )

    def test_uses_ssm_send_command(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "aws ssm send-command" in dep, (
            "deploy must roll the container via `aws ssm send-command`"
        )

    def test_no_direct_ssh_deploy(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "ssh " not in dep, (
            "deploy must not shell in via SSH (SSM Run Command only)"
        )

    def test_ships_prod_compose_file(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "docker-compose.prod.yml" in dep, (
            "deploy must ship deploy/docker-compose.prod.yml"
        )

    def test_polls_command_status(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "get-command-invocation" in dep, (
            "deploy must poll SSM command status via get-command-invocation"
        )

    def test_checks_health_after_deploy(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "/api/health" in dep, "deploy must verify /api/health after rollout"

    def test_deploy_concurrency_does_not_cancel(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "concurrency:" in dep, "deploy must declare a concurrency group"
        assert "cancel-in-progress: false" in dep, (
            "deploy concurrency must queue (cancel-in-progress: false), not cancel"
        )

    def test_targets_ec2_instance(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert (
            "EC2_INSTANCE_ID" in dep
            or "--instance-ids" in dep
            or re.search(r"i-[0-9a-f]{8,}", dep)
        ), "deploy must target an EC2 instance (id or tag)"

    def test_references_ecr(self):
        dep = read_text(".github/workflows/deploy.yml")
        assert "ecr" in dep.lower(), "deploy must reference ECR (push image)"


class TestVersionControl:
    """.gitignore secrets hygiene + requirements.txt pinning."""

    def test_gitignore_excludes_env(self):
        gi = read_text(".gitignore")
        assert re.search(r"^\.env\s*$", gi, flags=re.MULTILINE), (
            ".gitignore must exclude .env"
        )

    def test_gitignore_excludes_tfvars(self):
        gi = read_text(".gitignore")
        assert "*.tfvars" in gi, ".gitignore must exclude *.tfvars"

    def test_gitignore_excludes_uploads(self):
        gi = read_text(".gitignore")
        assert "uploads/" in gi, ".gitignore must exclude uploads/"

    def test_gitignore_excludes_terraform_state(self):
        gi = read_text(".gitignore")
        assert "*.tfstate" in gi, ".gitignore must exclude terraform state (*.tfstate)"

    def test_requirements_pin_every_dependency(self):
        req = read_text("requirements.txt")
        lines = [
            ln.strip()
            for ln in req.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert lines, "requirements.txt has no dependency lines"
        for ln in lines:
            assert "==" in ln, f"dependency not pinned with '==': {ln!r}"

    def test_requirements_no_unpinned_specifiers(self):
        req = read_text("requirements.txt")
        lines = [
            ln.strip()
            for ln in req.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        for ln in lines:
            assert ">=" not in ln, f"dependency uses unpinned '>=': {ln!r}"
            assert "~=" not in ln, f"dependency uses unpinned '~=': {ln!r}"

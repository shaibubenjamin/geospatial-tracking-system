###############################################################################
# Secrets Manager — all sensitive runtime values live here, not in .env files.
#
# The EC2 instance's IAM role grants secretsmanager:GetSecretValue. The app's
# entrypoint script fetches each secret and exports it before docker-compose
# starts, so the container sees them as ordinary env vars.
###############################################################################

# Fernet key for app/services/crypto.py. Generated once; rotate by adding a
# new version and updating the app to try both.
resource "random_password" "fernet_key_raw" {
  length  = 32
  special = false
}

# Fernet expects a urlsafe-base64 32-byte key — generated outside Terraform
# would normally be ideal, but we can do it in-line:
locals {
  # base64-encoded 32 bytes works as a Fernet key
  fernet_key = base64encode(substr(random_password.fernet_key_raw.result, 0, 32))
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = true
}

# ── RDS app credentials ─────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "db_url_app" {
  name                    = "${var.project_name}/database-url-app"
  description             = "asyncpg URL for the app's read/write role (app_prod)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_url_app" {
  secret_id = aws_secretsmanager_secret.db_url_app.id
  # The app role's password is set manually via psql (see database.tf) — this
  # placeholder is updated by the operator once the role is created.
  secret_string = jsonencode({
    DATABASE_URL      = "postgresql+asyncpg://app_prod:CHANGE_ME@${aws_db_instance.main.address}:5432/${aws_db_instance.main.db_name}"
    DATABASE_URL_SYNC = "postgresql://app_prod:CHANGE_ME@${aws_db_instance.main.address}:5432/${aws_db_instance.main.db_name}"
  })

  lifecycle {
    ignore_changes = [secret_string] # operator updates this after role creation
  }
}

# RDS master password — for break-glass / role creation
resource "aws_secretsmanager_secret" "db_master_password" {
  name                    = "${var.project_name}/rds-master-password"
  description             = "RDS master (postgres_admin) password — break-glass only"
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "db_master_password" {
  secret_id     = aws_secretsmanager_secret.db_master_password.id
  secret_string = random_password.db_master.result
}

# ── Application secrets ─────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "app_secrets" {
  name                    = "${var.project_name}/app-secrets"
  description             = "JWT signing key + Fernet sync-encryption key for the FastAPI app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app_secrets" {
  secret_id = aws_secretsmanager_secret.app_secrets.id
  secret_string = jsonencode({
    SECRET_KEY          = random_password.jwt_secret.result
    SYNC_ENCRYPTION_KEY = local.fernet_key
    SUPERADMIN_USERNAME = "superadmin"
    SUPERADMIN_PASSWORD = "CHANGE_ON_FIRST_LOGIN"
  })

  lifecycle {
    ignore_changes = [secret_string] # rotate via console / aws cli, not via terraform
  }
}

# ── CommCare HQ credentials (optional initial seed; rotated via admin panel) ──

resource "aws_secretsmanager_secret" "commcare" {
  name                    = "${var.project_name}/commcare"
  description             = "CommCare HQ credentials. Stored separately so they can be rotated without touching app secrets."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "commcare" {
  secret_id = aws_secretsmanager_secret.commcare.id
  secret_string = jsonencode({
    COMMCARE_USERNAME = var.commcare_username
    COMMCARE_PASSWORD = var.commcare_password
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

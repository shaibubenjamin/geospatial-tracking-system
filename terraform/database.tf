###############################################################################
# RDS — PostgreSQL 16 with PostGIS
#
# PostGIS is installed by issuing `CREATE EXTENSION postgis;` against the
# database AFTER the instance is up. The parameter group below enables the
# necessary settings (rds.force_ssl, shared_preload_libraries).
#
# The DB password is generated random + stored in Secrets Manager — see
# secrets.tf. The app reads it at container start via the IAM role attached
# to the EC2.
###############################################################################

# Random master password — stored in Secrets Manager, never in plain state.
# Note: Terraform state contains this in plaintext (S3 + at-rest encryption
# protects it). The app never reads from state — only from Secrets Manager.
resource "random_password" "db_master" {
  length  = 32
  special = true
  # Avoid characters that interfere with libpq URI parsing
  override_special = "_-+="
}

resource "aws_db_subnet_group" "main" {
  name        = "${var.project_name}-rds"
  description = "Private subnets for RDS"
  subnet_ids  = [aws_subnet.private_a.id, aws_subnet.private_b.id]

  tags = { Name = "${var.project_name}-rds-subnets" }
}

resource "aws_db_parameter_group" "postgis" {
  name        = "${var.project_name}-postgis-pg16"
  family      = "postgres16"
  description = "Force SSL + preload extensions for PostGIS / pg_stat_statements"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project_name}-db"
  engine         = "postgres"
  engine_version = var.rds_engine_version
  instance_class = var.rds_instance_class

  allocated_storage     = var.rds_allocated_storage_gb
  max_allocated_storage = 200
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "geospatial_tracking_system"
  username = "postgres_admin"
  password = random_password.db_master.result
  port     = 5432

  multi_az               = var.rds_multi_az
  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.postgis.name

  # Backup & maintenance — daily backups retained 14 days
  backup_retention_period = 14
  backup_window           = "02:00-03:00" # UTC = 03:00-04:00 WAT
  maintenance_window      = "Sun:04:00-Sun:05:00"
  copy_tags_to_snapshot   = true

  performance_insights_enabled          = true
  performance_insights_retention_period = 7 # free tier

  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-db-final-snapshot"

  apply_immediately = false

  tags = { Name = "${var.project_name}-db" }
}

# ── Two Postgres roles for dev-vs-prod isolation ─────────────────────────────
#
# The Terraform AWS provider can't run SQL — these are documented here as the
# operator's first task after the RDS comes up. Run via the bastion:
#
#   psql "host=<rds-endpoint> user=postgres_admin dbname=geospatial_tracking_system"
#
#   CREATE EXTENSION IF NOT EXISTS postgis;
#   CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
#
#   -- The app role: full read/write
#   CREATE ROLE app_prod LOGIN PASSWORD '<from-secrets-manager>';
#   GRANT CONNECT ON DATABASE geospatial_tracking_system TO app_prod;
#   GRANT USAGE, CREATE ON SCHEMA public TO app_prod;
#   GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO app_prod;
#   GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO app_prod;
#   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app_prod;
#   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO app_prod;
#
#   -- The dev tunnel role: SELECT only
#   CREATE ROLE app_dev LOGIN PASSWORD '<separately-managed>';
#   GRANT CONNECT ON DATABASE geospatial_tracking_system TO app_dev;
#   GRANT USAGE ON SCHEMA public TO app_dev;
#   GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_dev;
#   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_dev;

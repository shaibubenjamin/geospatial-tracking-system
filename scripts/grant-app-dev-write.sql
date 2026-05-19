-- ---------------------------------------------------------------------------
-- grant-app-dev-write.sql
--
-- Upgrades the existing app_dev role on RDS from SELECT-only to full
-- read/write — same grants as app_prod. Run once after `app_dev` has already
-- been created with the original (SELECT-only) grants.
--
-- Usage (from a workstation that can reach RDS via the bastion tunnel):
--
--   ssh -L 5432:$(terraform -chdir=terraform output -raw rds_endpoint):5432 \
--       ec2-user@$(terraform -chdir=terraform output -raw bastion_public_ip)
--
--   # in another terminal:
--   RDS_MASTER=$(aws secretsmanager get-secret-value \
--       --secret-id mda-dashboard/rds-master-password \
--       --query SecretString --output text)
--   psql -h localhost -p 5432 -U postgres_admin \
--        -d geospatial_tracking_system \
--        -f scripts/grant-app-dev-write.sql
--
-- Idempotent: re-running is safe.
-- ---------------------------------------------------------------------------

GRANT CREATE ON SCHEMA public TO app_dev;

GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO app_dev;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO app_dev;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES    TO app_dev;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON SEQUENCES TO app_dev;

-- Sanity: list app_dev's table grants after running, should include
-- SELECT, INSERT, UPDATE, DELETE, etc.
-- \dp

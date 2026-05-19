# Terraform — AWS infrastructure for the SARMAAN MDA dashboard

Single-environment production deployment in **us-east-1**, account **`387526361725`**.

## Architecture at a glance

```
                                 internet
                                    │
                                    ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  ALB (public subnets, HTTPS via ACM)                           │
   └────────────────┬───────────────────────────┬───────────────────┘
                    │ 8080                      │ 22 (SSH)
                    ▼                           ▼
   ┌────────────────────────────┐    ┌──────────────────────────────┐
   │  EC2 t3.medium (private)   │    │  Bastion t3.nano (public)    │
   │  docker-compose: api+redis │    │  ssh / psql tunnel jumphost  │
   │  pulls image from ECR      │    └───────────────┬──────────────┘
   │  reads secrets from SM     │                    │
   └─────────────┬──────────────┘                    │
                 │ 5432                              │ 5432 (dev only)
                 ▼                                   ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  RDS Postgres 16 + PostGIS (db.t3.large, private subnets)      │
   └────────────────────────────────────────────────────────────────┘
```

## One-time bootstrap

The remote state bucket has to exist before anything else.

```bash
cd terraform/bootstrap
terraform init
terraform apply
```

Creates:
- S3 bucket `eha-mda-dashboard-tfstate` (versioned + encrypted)
- DynamoDB table `eha-mda-dashboard-tflock`

You only do this once per account.

## Standing the platform up

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Fill in ssh_public_key, ssh_allowed_cidrs, commcare_password

terraform init    # picks up the S3 backend
terraform plan
terraform apply   # ~12 minutes (RDS dominates)
```

After apply prints `terraform output domain`, the FastAPI image still has to be built and pushed before the ALB has anything to talk to.

## First deploy (no CI yet)

```bash
# Build & push the image
$(aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $(terraform output -raw ecr_repository_url | cut -d/ -f1))
docker build -t mda-dashboard:v1 ../
docker tag mda-dashboard:v1 $(terraform output -raw ecr_repository_url):v1
docker tag mda-dashboard:v1 $(terraform output -raw ecr_repository_url):latest
docker push $(terraform output -raw ecr_repository_url):v1
docker push $(terraform output -raw ecr_repository_url):latest

# SSH into the app server (through the bastion) and start the stack
BASTION=$(terraform output -raw bastion_public_ip)
APP=$(terraform output -raw app_private_ip)
ssh -J ec2-user@$BASTION ec2-user@$APP
# on the EC2:
./ecr-login.sh
# place docker-compose.yml on /opt/mda-dashboard, then:
docker compose up -d
```

## Database roles + PostGIS

After RDS comes up, run these once via the bastion. The password for `app_prod`
must match the placeholder in the `db_url_app` Secrets Manager entry.

```bash
ssh -L 5432:$(terraform output -raw rds_endpoint):5432 ec2-user@$BASTION
# in another terminal, with libpq installed locally:
RDS_MASTER=$(aws secretsmanager get-secret-value --secret-id mda-dashboard/rds-master-password --query SecretString --output text)
psql "host=localhost user=postgres_admin dbname=geospatial_tracking_system password=$RDS_MASTER"
```

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- App role
CREATE ROLE app_prod LOGIN PASSWORD '<paste-from-secrets-manager-once-updated>';
GRANT CONNECT ON DATABASE geospatial_tracking_system TO app_prod;
GRANT USAGE, CREATE ON SCHEMA public TO app_prod;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app_prod;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO app_prod;

-- Dev tunnel role. Same read/write grants as app_prod — separate credential
-- only so dev/prod sessions are distinguishable in audit logs. Data done
-- from the dev laptop lands in the same RDS that prod reads from.
CREATE ROLE app_dev LOGIN PASSWORD '<choose-something-strong>';
GRANT CONNECT ON DATABASE geospatial_tracking_system TO app_dev;
GRANT USAGE, CREATE ON SCHEMA public TO app_dev;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO app_dev;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO app_dev;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app_dev;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO app_dev;
```

If `app_dev` already exists with the older SELECT-only grants, upgrade in
place with the SQL in [`scripts/grant-app-dev-write.sql`](../scripts/grant-app-dev-write.sql).

Then update the `mda-dashboard/database-url-app` Secrets Manager entry with the
real `app_prod` password (Terraform won't overwrite — see `lifecycle.ignore_changes`).

## Migrating data from the existing on-prem DB

```bash
# From the local workstation (must be on VPN to reach the on-prem DB)
pg_dump "postgresql://server_admin:***@10.11.52.96:5434/geospatial_tracking_system" \
  --no-owner --no-acl --format=custom \
  > /tmp/sarmaan.pgdump

# Restore through the bastion tunnel
pg_restore -h localhost -p 5432 -U postgres_admin -d geospatial_tracking_system \
  --no-owner --no-acl --clean --if-exists /tmp/sarmaan.pgdump
```

## Cost estimate (us-east-1, on-demand)

| Resource | Spec | Monthly |
|---|---|---|
| EC2 app | t3.medium (1-year reserved) | ~$20 |
| EC2 bastion | t3.nano | ~$4 |
| RDS | db.t3.large, single-AZ, 50 GB gp3 | ~$130 |
| ALB | Application load balancer | ~$20 |
| NAT Gateway | Single AZ | ~$32 |
| Data transfer | est. 10 GB out | ~$1 |
| Route 53 | Hosted zone (existing) + queries | ~$1 |
| ACM | Cert | $0 |
| Secrets Manager | 4 secrets | ~$2 |
| S3 + DynamoDB | TF state | ~$1 |
| **Total** | | **~$211/mo** |

Multi-AZ RDS adds ~$130 — flip on once traffic justifies it via `rds_multi_az = true`.

## Tearing down

```bash
terraform destroy
```

RDS is protected by `deletion_protection = true` — disable that in the console
or set the variable to false first, otherwise destroy fails.

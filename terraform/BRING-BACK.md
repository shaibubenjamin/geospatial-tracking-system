# Bringing the platform back up

This stack was **fully decommissioned on 2026-08-04** (Kano R3 campaign ended;
all datasets migrated off-platform) to stop AWS spend. Everything below rebuilds
it from scratch in account `387526361725` / `us-east-1`.

Terraform reproduces the **operation**, not the identity — instance IDs, private
IPs, the RDS endpoint, the ALB DNS name and every Secrets Manager ARN **will be
different**. That's expected and fine: every internal reference uses Terraform
attributes (e.g. the dev-tunnel policy points at `aws_instance.bastion.arn`, not
a hardcoded ID), so they self-heal on apply.

The Terraform in this directory is complete as of teardown — a drift audit
folded every out-of-band resource back into code before destroying: the APK S3
bucket (`apk.tf`), the dev-tunnel IAM policy (`dev-tunnel.tf`), the
`app-dev-password` + `onprem-database-url` secret containers (`secrets.tf`), and
the org billing tags (`default_tags` in `main.tf`).

---

## 1. Prerequisites

- AWS credentials for `387526361725` with admin (or equivalent) permissions.
- Terraform ≥ 1.5, Docker, `psql` (libpq), the AWS CLI, `gh`.
- `terraform/terraform.tfvars` with the real values (gitignored — recreate it):
  `ssh_public_key`, `ssh_allowed_cidrs` (your current IP/32), `commcare_username`,
  `commcare_password`.
- The **GitHub OIDC provider** (`token.actions.githubusercontent.com`) must exist
  in the account. `github-oidc.tf` reads it via a `data` source, NOT a `resource`
  — it's shared, account-wide infra and was never owned by this stack. If it was
  removed, uncomment the fallback `resource` block in `github-oidc.tf` for one
  apply, then re-comment.
- The Route 53 hosted zone `ehealthnigeria.org` must exist (also a `data`
  source, managed elsewhere).

## 2. Bootstrap the state backend (once)

```bash
cd terraform/bootstrap
terraform init
terraform apply        # creates S3 bucket eha-mda-dashboard-tfstate + DynamoDB lock table
```

## 3. Stand up the infrastructure

```bash
cd terraform
terraform init         # picks up the S3 backend
terraform plan
terraform apply        # ~12 min (RDS dominates)
```

This creates: VPC + subnets + IGW, ALB + ACM cert (DNS-validated) + Route 53 A
record, EC2 app (t3.large) + bastion (t3.nano), RDS (db.t4g.medium, PostGIS),
ECR, all Secrets Manager **containers**, CloudWatch logs + alarms + SNS topic,
the GitHub deploy role, the **APK bucket**, and the **dev-tunnel policy**.

## 4. Manual post-apply bootstrap (what Terraform can't do)

### 4a. First image → ECR
CI builds + pushes on merge to `main`. To seed immediately: build locally and
push to `$(terraform output -raw ecr_repository_url)` (see this dir's README).

### 4b. Database roles + PostGIS
Tunnel in through the bastion and run the SQL in this directory's README
(`CREATE EXTENSION postgis;`, `CREATE ROLE app_prod …`, `CREATE ROLE app_dev …`).

### 4c. Populate secret VALUES
Terraform creates the secret *containers* but never the sensitive values (the
value-bearing secrets use `ignore_changes`; `app-dev-password` and
`onprem-database-url` have no version at all). Set each with the CLI:

```bash
# real app_prod password (must match the app_prod role from 4b)
aws secretsmanager put-secret-value --secret-id mda-dashboard/database-url-app \
  --secret-string '{"DATABASE_URL":"postgresql+asyncpg://app_prod:<pw>@<rds-endpoint>:5432/geospatial_tracking_system","DATABASE_URL_SYNC":"postgresql://app_prod:<pw>@<rds-endpoint>:5432/geospatial_tracking_system"}'

# app_dev tunnel password (must match the app_dev role from 4b)
aws secretsmanager put-secret-value --secret-id mda-dashboard/app-dev-password --secret-string '<pw>'

# on-prem mirror URL (VPN-reachable) — only if the reverse mirror is used
aws secretsmanager put-secret-value --secret-id mda-dashboard/onprem-database-url --secret-string 'postgresql://…'

# app-secrets: rotate SUPERADMIN_PASSWORD off the CHANGE_ON_FIRST_LOGIN placeholder
```

### 4d. Restore data
Restore the migrated dataset into RDS through the bastion tunnel
(`pg_restore … --no-owner --no-acl`). See this dir's README "Migrating data".

### 4e. Alarm email subscription
```bash
aws sns subscribe --topic-arn $(terraform output -raw ... alarms topic) \
  --protocol email --notification-endpoint you@ehealthnigeria.org
```
(Confirm via the email link.)

### 4f. Per-dev tunnel users
For each developer who needs RDS tunnel access, create an IAM user and attach
the codified policy — this is per-person, so it's not in Terraform:
```bash
aws iam create-user --user-name <name>-dev-tunnel
aws iam attach-user-policy --user-name <name>-dev-tunnel \
  --policy-arn $(terraform output -raw dev_tunnel_policy_arn)
aws iam create-access-key --user-name <name>-dev-tunnel   # hand off securely
```
(At teardown the sole user was `ben-dev-tunnel`.)

### 4g. GitHub Actions
- `APK_S3_BUCKET` repo variable = `terraform output -raw apk_bucket`
  (`eha-mda-dashboard-apk`) — already set at teardown; re-set if the repo config
  was cleared.
- `deploy.yml` / `app-build.yml` assume `terraform output -raw github_deploy_role_arn`.
- Android signing secrets (`ANDROID_KEYSTORE_BASE64`, etc.) are GitHub secrets,
  managed outside AWS.

## 5. Verify
`https://eha-mda-dashboard.ehealthnigeria.org` serves the dashboard; `/api/health`
is green; `/apk` serves the latest APK; the ALB target is healthy.

---

## Teardown (for reference — how this stack was taken down)

```bash
# 1. Disable RDS deletion protection (Terraform won't do this during destroy)
aws rds modify-db-instance --db-instance-identifier mda-dashboard-db \
  --no-deletion-protection --apply-immediately --region us-east-1

# 2. Empty the ECR repo and destroy the main stack. Because data was already
#    migrated off, skip the final snapshot to avoid snapshot storage cost:
#    temporarily set skip_final_snapshot=true + deletion_protection=false in
#    database.tf (local, uncommitted), then:
terraform destroy

# 3. Delete out-of-band resources NOT in Terraform state (they're in code for
#    bring-back, but were never imported, so destroy doesn't touch them):
#    - S3 bucket eha-mda-dashboard-apk (empty + delete)
#    - IAM policy mda-dashboard-dev-tunnel (detach from ben-dev-tunnel first)
#    - secrets app-dev-password + onprem-database-url (force delete)

# 4. Tear down the state backend:
#    empty eha-mda-dashboard-tfstate (all versions), then `cd bootstrap && terraform destroy`
```

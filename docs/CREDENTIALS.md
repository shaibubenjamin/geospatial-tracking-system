# Credentials & Deployment Runbook

This document is the single source of truth for **how** the SARMAAN MDA
dashboard gets deployed and **where** every secret lives. It contains
**no actual passwords** — everything sensitive is stored in AWS Secrets
Manager and pointed to from here. Anyone with the right AWS access can
fetch the live value at any time.

---

## 1. AWS account, region, and ownership

| Thing            | Value                                                           |
|------------------|-----------------------------------------------------------------|
| AWS account ID   | `387526361725`                                                  |
| Region           | `us-east-1`                                                     |
| Owner            | eHealth Africa — SARMAAN team, Sokoto State                     |
| Live URL         | https://eha-mda-dashboard.ehealthnigeria.org                    |
| GitHub repo      | https://github.com/shaibubenjamin/geospatial-tracking-system    |

If you cannot run `aws sts get-caller-identity` and see account
`387526361725`, you do not have access — ask the owner.

---

## 2. Branch model

| Branch | Purpose | Who pushes |
|--------|---------|------------|
| `main` | What is currently deployed to prod. Pushes auto-trigger the Deploy workflow. | PR merges from `dev` only (emergency hotfixes excepted). |
| `dev`  | Integration / staging branch. All new work lands here first. | Anyone, via PR or direct push. |

**Golden rule:** after a deploy, `git checkout dev` and `git pull --ff-only`
so the working tree is on the next batch of work, not on prod-tracking.

---

## 3. Normal deployment flow

```text
  feature work          dev (local)
        │
        ▼
  git add … && git commit -m "…"
        │
        ▼
  git push origin dev        ← CI runs (ruff + docker build + terraform validate)
        │
        ▼
  Open PR dev → main          gh pr create --base main --head dev
        │
        ▼
  Review + merge              gh pr merge <#> --merge
        │
        ▼
  push to main fires .github/workflows/deploy.yml:
    1. assume mda-dashboard-github-deploy via GitHub OIDC
    2. build + push image to ECR :<sha7> and :latest
    3. aws ssm send-command on i-0f57573ce98580bfc:
         cd /opt/mda-dashboard
         sudo /home/ec2-user/ecr-login.sh
         sudo docker compose pull && sudo docker compose up -d
         sleep 8 && curl -fsS http://localhost:8080/api/health
    4. Slack/Annotations on success
```

Typical wall-clock time: ~50 s end-to-end.

Watch a run:
```bash
gh run list --workflow=deploy.yml --limit 3
gh run watch       # newest run
gh run view --log  # full logs
```

---

## 4. Emergency / hotfix deploy

Use only when the regular CI on `dev` is itself broken (e.g. the time we
fixed the deploy workflow). Otherwise it bypasses review.

```bash
git checkout main && git pull --ff-only
# edit files
git add … && git commit -m "fix: …"
git push origin main
git checkout dev && git merge main --ff-only && git push origin dev
```

---

## 5. Secrets — where they live

All passwords / keys are in **AWS Secrets Manager** (region `us-east-1`).
Fetch any of them with:

```bash
aws --region us-east-1 secretsmanager get-secret-value \
    --secret-id <name> --query SecretString --output text
```

| Secret name                            | What it is                                              | Used by |
|----------------------------------------|---------------------------------------------------------|---------|
| `mda-dashboard/rds-master-password`    | RDS master (`postgres_admin`) password — DBA only       | `psql` admin via bastion |
| `mda-dashboard/database-url-app`       | Full `DATABASE_URL` + `DATABASE_URL_SYNC` for `app_prod`| EC2 container at runtime |
| `mda-dashboard/app-dev-password`       | Password for the read/write `app_dev` Postgres role     | `scripts/dev-aws.sh` |
| `mda-dashboard/app-secrets`            | `SECRET_KEY`, `SYNC_ENCRYPTION_KEY`, superadmin bootstrap | EC2 container at runtime |
| `mda-dashboard/commcare`               | CommCare HQ username + password (one for the team)      | superadmin sync trigger |

**Never paste a real value into git, Slack, email, or a screenshot.**
Always fetch fresh from Secrets Manager.

---

## 6. Database roles

| Role            | Password lives in                              | Privileges |
|-----------------|------------------------------------------------|------------|
| `postgres_admin`| `mda-dashboard/rds-master-password`            | DBA — extension creation, role mgmt |
| `app_prod`      | `mda-dashboard/database-url-app`               | Full DML on `public` schema. The EC2 container connects as this. |
| `app_dev`       | `mda-dashboard/app-dev-password`               | Same DML grants as `app_prod`, distinct credential so dev/prod sessions can be told apart in logs. Used by `scripts/dev-aws.sh`. |

If `app_dev` was created at SELECT-only and you need to upgrade, run
[`scripts/grant-app-dev-write.sql`](../scripts/grant-app-dev-write.sql)
through the bastion (already done for the current RDS).

---

## 7. Operator logins (the dashboard itself)

The web app has its own user table — these are NOT the AWS / RDS creds
above. Default seeded accounts come from `mda-dashboard/app-secrets`
(env vars `SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD` / `SUPERADMIN_EMAIL`).

| Tier        | Default username | Where the password lives | What they can do |
|-------------|------------------|--------------------------|------------------|
| Superadmin  | `superadmin`     | `mda-dashboard/app-secrets` → `SUPERADMIN_PASSWORD` | Everything — data pipeline / CommCare sync, user mgmt, all admin features |
| Admin       | `admin`          | bootstrap default `admin123` — **must be changed on first login** | Data uploads, geospatial downloads, dashboard |
| Analyst     | `viewer`         | bootstrap default `viewer123` — **must be changed on first login** | Dashboard read-only |

Logged-in users can change their own password from the admin panel →
**Account → Change Password**. Superadmin can also reset any user's
password from the Users tab.

If you ever lose the superadmin password and can't log in:
```bash
# As an emergency fall-back, fetch the bootstrap value from Secrets Manager
aws --region us-east-1 secretsmanager get-secret-value \
    --secret-id mda-dashboard/app-secrets \
    --query SecretString --output text | python3 -c "import sys,json; print(json.load(sys.stdin).get('SUPERADMIN_PASSWORD'))"
```

If that has also been rotated, reset directly in RDS via the bastion:
```sql
-- as postgres_admin
UPDATE users
SET hashed_password = crypt('<new-password>', gen_salt('bf'))
WHERE username = 'superadmin';
```
(Requires the `pgcrypto` extension; alternatively delete the row and let
the lifespan re-seed from the env.)

---

## 8. Dev tunnel and SSH access

### 8a. Dev tunnel (everyday use)

The dev tunnel uses **AWS Systems Manager port-forwarding** — no SSH, no
bastion, no IP allow-list. Auth is via your AWS credentials (`ssm:StartSession`
on the EC2 instance). Works from any network.

```bash
./scripts/dev-aws.sh up        # opens SSM tunnel + writes .env + boots docker
./scripts/dev-aws.sh tunnel    # just the tunnel (foreground, Ctrl-C to close)
./scripts/dev-aws.sh down      # closes everything
```

Prerequisites on the dev laptop:
- AWS CLI v2 (`aws --version`)
- Session Manager Plugin (`session-manager-plugin --version`) —
  install with `brew install --cask session-manager-plugin`
- AWS profile with `ssm:StartSession` on `i-0f57573ce98580bfc` (the owner's
  default admin profile has this; otherwise grant via IAM policy)

If the tunnel fails, check `/tmp/mda-dashboard-tunnel.log`.

### 8b. SSH access (break-glass only)

Kept around for when SSM is unavailable or you need a real shell on the
host. Not used by any automated path.

- Bastion: `75.101.208.163` (public, IP-allowlisted in `terraform.tfvars` →
  `ssh_allowed_cidrs`)
- App EC2: `10.50.10.229` (private; SSH allowed only from the bastion SG)
- SSH private key: `~/.ssh/mda-dashboard-prod` on the owner's laptop.
  This is the only copy — **do not generate replacements without rotating
  the corresponding public key on both hosts**.

To get a shell on the app EC2:
```bash
ssh -A -J ec2-user@75.101.208.163 -i ~/.ssh/mda-dashboard-prod ec2-user@10.50.10.229
```

When your office IP changes, add it to `terraform/terraform.tfvars`
(`ssh_allowed_cidrs`) and `terraform apply`. With SSM as the primary path,
the bastion IP list is no longer a daily-blocker.

---

## 9. Infrastructure changes (terraform)

`terraform apply` is **not** part of CI. Run it manually from the owner's
laptop when adding new AWS resources:

```bash
cd terraform
terraform plan -out tfplan
# eyeball the plan!
terraform apply tfplan
```

State lives in S3 (`eha-mda-dashboard-tfstate`) with a DynamoDB lock
(`eha-mda-dashboard-tflock`). Don't disable backend or run from a clone
without the same backend config.

---

## 10. GitHub repository secrets

Configured under repo Settings → Secrets and variables → Actions:

| Secret name             | What it is                                  | Used by         |
|-------------------------|---------------------------------------------|------------------|
| `MDA_DASHBOARD_SSH_KEY` | Private SSH key for the bastion (legacy)    | Currently unused by deploy.yml — kept for break-glass debugging. Safe to leave or remove. |

The deploy workflow uses GitHub OIDC (no static AWS keys in CI) — the
trust policy in `terraform/github-oidc.tf` only allows the workflow on
`refs/heads/main` of `shaibubenjamin/geospatial-tracking-system` to
assume the deploy role.

---

## 11. Rotating something

1. Rotate the value at its source (RDS console, CommCare HQ UI, etc.)
2. Update the corresponding Secrets Manager entry:
   ```bash
   aws --region us-east-1 secretsmanager put-secret-value \
       --secret-id <name> --secret-string '<new value>'
   ```
3. Roll the EC2 container so it picks up the new value (the `refresh-env.sh`
   script reads from Secrets Manager and rewrites `/opt/mda-dashboard/.env`):
   ```bash
   aws --region us-east-1 ssm send-command \
       --instance-ids i-0f57573ce98580bfc \
       --document-name AWS-RunShellScript \
       --parameters 'commands=["sudo /home/ec2-user/refresh-env.sh && sudo docker compose -f /opt/mda-dashboard/docker-compose.yml up -d --force-recreate api"]'
   ```
4. Verify with `curl -fsS https://eha-mda-dashboard.ehealthnigeria.org/api/health`.

---

## 12. Common one-liners

```bash
# Live API health
curl -fsS https://eha-mda-dashboard.ehealthnigeria.org/api/health

# Recent deploys
gh run list --workflow=deploy.yml --limit 5

# Tail the EC2 app logs
aws --region us-east-1 ssm send-command \
    --instance-ids i-0f57573ce98580bfc \
    --document-name AWS-RunShellScript \
    --parameters 'commands=["sudo docker logs --tail 200 mda-dashboard_api_1"]' \
    --query "Command.CommandId" --output text
# then: aws --region us-east-1 ssm get-command-invocation --command-id <id> --instance-id i-0f57573ce98580bfc

# RDS connection count right now
aws --region us-east-1 cloudwatch get-metric-statistics \
    --namespace AWS/RDS --metric-name DatabaseConnections \
    --dimensions Name=DBInstanceIdentifier,Value=mda-dashboard-db \
    --start-time $(date -u -v-5M +%Y-%m-%dT%H:%M:%S) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
    --period 60 --statistics Maximum
```

---

*Last updated: 2026-05-19.*

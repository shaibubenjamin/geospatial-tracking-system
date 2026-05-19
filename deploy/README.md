# Deploy artifacts for the EC2 host

These files live in `/opt/mda-dashboard/` on the production EC2.

## Files

- **`docker-compose.prod.yml`** — production compose stack. Pulls the FastAPI
  image from ECR, runs Redis, ships container logs to CloudWatch.
- **`refresh-env.sh`** — fetches secrets from AWS Secrets Manager via the
  EC2's IAM role and writes a `.env` file the compose stack reads.

## First-time setup on a fresh EC2

```bash
# 1. Place the files
sudo mkdir -p /opt/mda-dashboard
sudo cp deploy/docker-compose.prod.yml /opt/mda-dashboard/docker-compose.yml
sudo cp deploy/refresh-env.sh /opt/mda-dashboard/refresh-env.sh
sudo chmod +x /opt/mda-dashboard/refresh-env.sh

# 2. Authenticate Docker against ECR (the user-data script created ecr-login.sh)
/home/ec2-user/ecr-login.sh

# 3. Pull the latest image and fetch secrets
sudo /opt/mda-dashboard/refresh-env.sh
cd /opt/mda-dashboard
sudo docker compose pull
sudo docker compose up -d

# 4. Tail logs to confirm clean startup
sudo docker compose logs -f api
```

## Routine deploys

After `git push origin main` triggers the CI workflow to build + push a new
image to ECR with the commit SHA as the tag:

```bash
ssh -i ~/.ssh/mda-dashboard-prod -J ec2-user@<bastion-ip> ec2-user@<app-private-ip>
/home/ec2-user/ecr-login.sh
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps        # confirm "Up", "healthy"
```

Downtime: ~10 seconds while the container restarts. No request loss as long
as the ALB drains the old target before terminating the connection (default
deregistration delay is 5 min, more than enough).

## Rotating secrets

After updating a Secrets Manager entry in the console:

```bash
sudo /opt/mda-dashboard/refresh-env.sh
sudo docker compose restart api
```

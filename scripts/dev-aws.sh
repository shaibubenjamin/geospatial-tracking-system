#!/bin/bash
###############################################################################
# dev-aws.sh — point the local docker-compose at AWS RDS via an SSM tunnel.
#
# Tunnel mechanism: AWS Systems Manager port-forwarding through the prod EC2
# instance. No SSH, no bastion, no IP allow-list. Works from any network as
# long as your AWS credentials can call `ssm:StartSession` on the instance.
#
# Connects as `app_dev`, which has the same read/write grants as `app_prod`
# but a separate credential so dev/prod sessions can be told apart in the
# audit logs. Writes from the dev container land in the same RDS that prod
# reads from — that's intentional: it means new CommCare syncs / data work
# done from the dev laptop show up immediately at
# https://eha-mda-dashboard.ehealthnigeria.org without a redeploy.
#
# In short: this is dev *credentials*, not a dev *database*. Treat the data
# accordingly.
#
# Usage:
#   ./scripts/dev-aws.sh up        # opens tunnel, writes .env, starts compose
#   ./scripts/dev-aws.sh tunnel    # just opens the tunnel (foreground)
#   ./scripts/dev-aws.sh down      # closes tunnel, stops compose, restores .env
#
# Requires:
#   - aws CLI v2 (`aws --version`)
#   - Session Manager Plugin (`session-manager-plugin --version`)
#     install: `brew install --cask session-manager-plugin`
#   - AWS credentials with ssm:StartSession on the prod EC2 instance
#   - No VPN, no SSH key, no IP allow-list — IAM is the only access control
###############################################################################
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

REGION=us-east-1
PROJECT=mda-dashboard
# Tunnel through the dedicated bastion (t3.nano, mda-dashboard-bastion).
# Isolates dev SSM activity from the prod app server — if this host's SSM
# agent wedges or the tunnel host has to be rebooted, prod traffic stays up.
# The bastion's IAM instance profile + AmazonSSMManagedInstanceCore policy
# are defined in terraform/bastion.tf.
EC2_INSTANCE_ID=i-0da32cbe872649cf7
RDS_ENDPOINT=mda-dashboard-db.cixghyv30jr7.us-east-1.rds.amazonaws.com

# Local port the tunnel listens on (chosen not to collide with a local Postgres)
LOCAL_PORT=25432

CMD="${1:-up}"

tunnel_pid_file="/tmp/${PROJECT}-tunnel.pid"
tunnel_log_file="/tmp/${PROJECT}-tunnel.log"

# ── Preflight checks ─────────────────────────────────────────────────────────
require_bin() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "  ✗ Missing required binary: $1" >&2
        echo "    $2" >&2
        exit 1
    fi
}

preflight() {
    require_bin aws "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
    require_bin session-manager-plugin "Install: brew install --cask session-manager-plugin"
    if ! aws --region "$REGION" sts get-caller-identity >/dev/null 2>&1; then
        echo "  ✗ AWS credentials not configured (or expired)." >&2
        echo "    Try: aws sso login --profile <your-profile>" >&2
        exit 1
    fi
}

# ── Tunnel control ───────────────────────────────────────────────────────────
open_tunnel() {
    if [[ -f "$tunnel_pid_file" ]] && kill -0 "$(cat "$tunnel_pid_file")" 2>/dev/null; then
        echo "  Tunnel already open (pid $(cat "$tunnel_pid_file"))"
        return
    fi
    echo "  Opening SSM port-forward localhost:${LOCAL_PORT} → ${RDS_ENDPOINT}:5432 via ${EC2_INSTANCE_ID}..."
    # Background SSM session — logs to /tmp so we can debug if it dies.
    nohup aws ssm start-session \
        --region "$REGION" \
        --target "$EC2_INSTANCE_ID" \
        --document-name AWS-StartPortForwardingSessionToRemoteHost \
        --parameters "{\"host\":[\"${RDS_ENDPOINT}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}" \
        >"$tunnel_log_file" 2>&1 &
    echo $! > "$tunnel_pid_file"

    # Wait up to 15 s for the local port to become reachable
    local i=0
    while ! (echo > "/dev/tcp/127.0.0.1/${LOCAL_PORT}") 2>/dev/null; do
        i=$((i+1))
        if (( i > 30 )); then
            echo "  ✗ Tunnel did not open within 15s. Last 10 lines of $tunnel_log_file:" >&2
            tail -10 "$tunnel_log_file" >&2 || true
            close_tunnel
            exit 1
        fi
        sleep 0.5
    done
    echo "  Tunnel open (pid $(cat "$tunnel_pid_file"))"
}

close_tunnel() {
    if [[ -f "$tunnel_pid_file" ]]; then
        pid=$(cat "$tunnel_pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            echo "  Tunnel closed (pid $pid)"
        fi
        rm -f "$tunnel_pid_file"
    fi
    # Belt-and-braces: kill any stray session-manager-plugin
    pkill -f "session-manager-plugin.*${LOCAL_PORT}" 2>/dev/null || true
}

# ── .env generation ──────────────────────────────────────────────────────────
write_env() {
    echo "  Fetching app_dev password from Secrets Manager..."
    local app_dev_password
    app_dev_password=$(aws secretsmanager get-secret-value \
        --region "$REGION" \
        --secret-id "${PROJECT}/app-dev-password" \
        --query SecretString --output text)

    # On-prem mirror target — fetched so the "Mirror to on-prem" button is
    # always available in the dev container without a manual export.
    # If the secret doesn't exist yet, leave blank and the button stays hidden.
    local onprem_url
    onprem_url=$(aws secretsmanager get-secret-value \
        --region "$REGION" \
        --secret-id "${PROJECT}/onprem-database-url" \
        --query SecretString --output text 2>/dev/null || echo "")

    # Back up an existing .env once
    if [[ -f .env && ! -f .env.local-onprem.bak ]]; then
        cp .env .env.local-onprem.bak
        echo "  Backed up existing .env → .env.local-onprem.bak"
    fi

    cat > .env <<ENVEOF
# Auto-generated by scripts/dev-aws.sh — DO NOT COMMIT
# Last write: $(date -u +%Y-%m-%dT%H:%M:%SZ)
#
# Local docker-compose connects to AWS RDS as app_dev via the SSM tunnel
# on localhost:${LOCAL_PORT}. host.docker.internal lets the container
# reach the Mac's tunnel port.

DATABASE_URL=postgresql+asyncpg://app_dev:${app_dev_password}@host.docker.internal:${LOCAL_PORT}/geospatial_tracking_system
DATABASE_URL_SYNC=postgresql://app_dev:${app_dev_password}@host.docker.internal:${LOCAL_PORT}/geospatial_tracking_system

# Dev-laptop session signing key (separate from prod). Only used for the
# local JWTs you mint when logged into the dev container — never sent to RDS.
SECRET_KEY=dev-secret-for-aws-tunnel-mode-not-prod
ACCESS_TOKEN_EXPIRE_MINUTES=480
ALGORITHM=HS256
ENVIRONMENT=development

# Fernet key — required so the encrypted sync_config password fields in RDS
# decrypt from the dev container. Same key prod uses.
SYNC_ENCRYPTION_KEY=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "${PROJECT}/app-secrets" --query SecretString --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['SYNC_ENCRYPTION_KEY'])")

# Reverse-mirror to on-prem. Fetched from AWS Secrets Manager so the button
# is always available when running dev-aws.sh — no manual export needed.
# When this is blank (Secrets Manager unreachable, etc.) the mirror card
# stays hidden gracefully.
ONPREM_BACKUP_DATABASE_URL=${onprem_url}
ENVEOF
    chmod 600 .env
    echo "  Wrote .env (RDS connection via app_dev / read-write)"
}

# ── Subcommands ──────────────────────────────────────────────────────────────
case "$CMD" in
    up)
        echo "▸ Dev-against-AWS-RDS mode (SSM tunnel)"
        preflight
        open_tunnel
        write_env
        echo "  Starting docker-compose (will read the new .env)..."
        docker compose up -d
        echo
        echo "✓ Local API at http://localhost:8090 — connected to AWS RDS as app_dev (read/write)"
        echo "  Heads up: writes from this container land in the SAME RDS prod reads from."
        echo "  Tail logs:    docker compose logs -f api"
        echo "  Tunnel log:   $tunnel_log_file"
        echo "  Stop mode:    ./scripts/dev-aws.sh down"
        ;;
    tunnel)
        echo "▸ Opening SSM port-forward only (foreground — Ctrl-C to close)"
        preflight
        aws ssm start-session \
            --region "$REGION" \
            --target "$EC2_INSTANCE_ID" \
            --document-name AWS-StartPortForwardingSessionToRemoteHost \
            --parameters "{\"host\":[\"${RDS_ENDPOINT}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}"
        ;;
    down)
        echo "▸ Tearing down dev-AWS mode"
        docker compose down 2>/dev/null || true
        close_tunnel
        if [[ -f .env.local-onprem.bak ]]; then
            mv .env.local-onprem.bak .env
            echo "  Restored .env from backup (on-prem mode)"
        fi
        echo "✓ Done"
        ;;
    *)
        echo "Usage: $0 {up|tunnel|down}" >&2
        exit 1
        ;;
esac

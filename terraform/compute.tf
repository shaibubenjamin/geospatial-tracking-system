###############################################################################
# Compute — single t3.medium EC2 running the docker-compose stack
#
# The app pulls its image from ECR (see ecr.tf) and reads runtime secrets from
# Secrets Manager via the IAM instance profile. Updating in prod looks like:
#
#   git push origin main           # CI builds + pushes image to ECR
#   ssh -J bastion ec2-user@<ec2>  # tunnel through the bastion
#   cd /opt/mda-dashboard
#   docker compose pull && docker compose up -d
###############################################################################

# ── SSH key pair ─────────────────────────────────────────────────────────────

resource "aws_key_pair" "operator" {
  key_name   = "${var.project_name}-operator"
  public_key = var.ssh_public_key
}

# ── IAM role for the EC2 instance ────────────────────────────────────────────

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.project_name}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Lets the EC2 read secrets and pull from ECR. SSM is included so we can
# Session-Manager-ssh without an SSH key if the bastion is down (break-glass).
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

data "aws_iam_policy_document" "secrets_read" {
  statement {
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      aws_secretsmanager_secret.db_url_app.arn,
      aws_secretsmanager_secret.app_secrets.arn,
      aws_secretsmanager_secret.commcare.arn,
    ]
  }
}

resource "aws_iam_role_policy" "secrets_read" {
  name   = "${var.project_name}-secrets-read"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.secrets_read.json
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project_name}-ec2"
  role = aws_iam_role.ec2.name
}

# ── User data: install Docker + Compose, place the docker-compose.yml ────────

locals {
  user_data = <<-USERDATA
    #!/bin/bash
    set -euo pipefail

    # OS packages
    dnf update -y
    dnf install -y docker git jq awscli amazon-cloudwatch-agent

    # Docker daemon
    systemctl enable --now docker
    usermod -aG docker ec2-user

    # docker compose (v2 plugin)
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
         -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

    # App directory
    mkdir -p /opt/mda-dashboard
    chown -R ec2-user:ec2-user /opt/mda-dashboard

    # Fetch the app image + secrets are pulled on first boot by a small script.
    # That script is dropped into place via SSH after the EC2 comes up — we
    # don't bake AWS-account-specific details into the AMI image.

    # ECR login helper for the ec2-user
    cat > /home/ec2-user/ecr-login.sh <<'EOF'
    #!/bin/bash
    set -euo pipefail
    REGION="${var.aws_region}"
    ACCOUNT="${data.aws_caller_identity.current.account_id}"
    aws ecr get-login-password --region "$REGION" \
      | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
    EOF
    chmod +x /home/ec2-user/ecr-login.sh
    chown ec2-user:ec2-user /home/ec2-user/ecr-login.sh

    echo "EC2 bootstrap complete." | tee -a /var/log/user-data.log
  USERDATA
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.ec2_instance_type
  subnet_id              = aws_subnet.private_a.id
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name
  key_name               = aws_key_pair.operator.key_name

  user_data                   = local.user_data
  user_data_replace_on_change = false

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  tags = { Name = "${var.project_name}-app" }
}

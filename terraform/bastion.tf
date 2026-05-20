###############################################################################
# Bastion — t3.nano jumphost in the public subnet
#
# Primary use: SSM port-forward target for dev access to RDS (see
# scripts/dev-aws.sh). The instance profile below attaches the standard
# AmazonSSMManagedInstanceCore policy so the SSM agent (pre-installed on
# Amazon Linux 2023) registers with Systems Manager — without this, every
# `aws ssm start-session --target i-...` against the bastion fails with
# TargetNotConnected and the dev tunnel falls back to the prod app server,
# which means a stuck SSM agent on the prod app server can take prod down.
#
# Legacy SSH usage (key + public IP) is preserved for break-glass; SSM is
# the preferred path going forward.
###############################################################################

# IAM role + SSM-managed-instance policy + instance profile for the bastion.
# Mirror of aws_iam_role.ec2 in compute.tf but scoped to the bastion only —
# the bastion never needs ECR / Secrets / CloudWatch like the app server does.
resource "aws_iam_role" "bastion" {
  name = "${var.project_name}-bastion"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "bastion_ssm" {
  role       = aws_iam_role.bastion.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "bastion" {
  name = "${var.project_name}-bastion"
  role = aws_iam_role.bastion.name
}

resource "aws_instance" "bastion" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = "t3.nano" # ~$4/mo, x86 to match the app server's AMI
  subnet_id                   = aws_subnet.public_a.id
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  key_name                    = aws_key_pair.operator.key_name
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.bastion.name

  root_block_device {
    volume_type = "gp3"
    volume_size = 10
    encrypted   = true
  }

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  user_data = <<-USERDATA
    #!/bin/bash
    dnf update -y
    dnf install -y postgresql16 awscli
    # SSM agent ships pre-installed on AL2023 but ensure it's running.
    systemctl enable --now amazon-ssm-agent
    echo "Bastion ready." > /var/log/user-data.log
  USERDATA

  tags = { Name = "${var.project_name}-bastion" }
}

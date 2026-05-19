###############################################################################
# Security groups
#
# Traffic flow:
#   internet  →  ALB-SG (443/80)
#   ALB-SG    →  EC2-SG (8080)
#   bastion-SG (22 from ssh_allowed_cidrs)  →  EC2-SG (22)  +  RDS-SG (5432)
#   EC2-SG    →  RDS-SG (5432)
###############################################################################

resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb"
  description = "Public ingress to the dashboard"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP (redirects to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-alb-sg" }
}

resource "aws_security_group" "ec2" {
  name        = "${var.project_name}-ec2"
  description = "FastAPI app server"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Pulls Docker images, CommCare HQ, OS updates"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-ec2-sg" }
}

resource "aws_security_group_rule" "ec2_from_alb" {
  type                     = "ingress"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.ec2.id
  description              = "App port from ALB only"
}

resource "aws_security_group_rule" "ec2_ssh_from_bastion" {
  type                     = "ingress"
  from_port                = 22
  to_port                  = 22
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.bastion.id
  security_group_id        = aws_security_group.ec2.id
  description              = "SSH from bastion only"
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds"
  description = "PostGIS RDS"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-rds-sg" }
}

resource "aws_security_group_rule" "rds_from_ec2" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ec2.id
  security_group_id        = aws_security_group.rds.id
  description              = "Postgres from the app EC2"
}

resource "aws_security_group_rule" "rds_from_bastion" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.bastion.id
  security_group_id        = aws_security_group.rds.id
  description              = "Postgres from bastion (dev tunnel for building new insight cards against live data)"
}

resource "aws_security_group" "bastion" {
  name        = "${var.project_name}-bastion"
  description = "SSH jumphost"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH from operator IPs only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = length(var.ssh_allowed_cidrs) > 0 ? var.ssh_allowed_cidrs : ["127.0.0.1/32"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-bastion-sg" }
}

###############################################################################
# SARMAAN MDA Dashboard — AWS infrastructure
#
# Single-environment deployment (prod) per the team's decision. Dev workflow
# runs locally via docker-compose and tunnels into this RDS through the bastion.
#
# Bootstrap the state backend FIRST:
#   cd bootstrap && terraform init && terraform apply
#
# Then from this directory:
#   terraform init
#   cp terraform.tfvars.example terraform.tfvars   # fill in secrets
#   terraform plan
#   terraform apply
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  backend "s3" {
    bucket         = "eha-mda-dashboard-tfstate"
    key            = "prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "eha-mda-dashboard-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = "prod"
      ManagedBy   = "terraform"
      Repository  = "shaibubenjamin/geospatial-tracking-system"
    }
  }
}

# Look up the existing ehealthnigeria.org hosted zone (managed outside Terraform).
data "aws_route53_zone" "primary" {
  name         = "ehealthnigeria.org."
  private_zone = false
}

# Latest Amazon Linux 2023 AMI for x86_64 — used for both the app EC2 and the bastion.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

# Used to grant Secrets Manager access to the EC2 IAM role.
data "aws_caller_identity" "current" {}

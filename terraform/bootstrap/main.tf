###############################################################################
# Bootstrap — Terraform state backend
#
# Run ONCE, with local state, to create the S3 bucket + DynamoDB table that
# every subsequent terraform run uses for remote state and locking.
#
#   cd terraform/bootstrap
#   terraform init
#   terraform apply
#
# Then in ../main.tf the backend "s3" block points at these resources, and the
# rest of the configuration is managed remotely.
###############################################################################

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

variable "state_bucket_name" {
  description = "S3 bucket that stores Terraform remote state. Must be globally unique."
  type        = string
  default     = "eha-mda-dashboard-tfstate"
}

variable "lock_table_name" {
  description = "DynamoDB table for Terraform state locking."
  type        = string
  default     = "eha-mda-dashboard-tflock"
}

# ── State bucket ─────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name

  tags = {
    Project     = "mda-dashboard"
    ManagedBy   = "terraform"
    Description = "Terraform remote state for the SARMAAN MDA dashboard"
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── State lock ───────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "tflock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Project   = "mda-dashboard"
    ManagedBy = "terraform"
  }
}

# ── Outputs (copy these into ../main.tf's backend "s3" block) ────────────────

output "state_bucket" {
  value = aws_s3_bucket.tfstate.id
}

output "lock_table" {
  value = aws_dynamodb_table.tflock.id
}

###############################################################################
# Input variables
#
# Fill in terraform.tfvars (gitignored) with environment-specific values.
# Defaults here are the production values.
###############################################################################

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short identifier prefixed to every resource."
  type        = string
  default     = "mda-dashboard"
}

variable "vpc_cidr" {
  description = "CIDR for the platform VPC. Picked to avoid collision with existing 10.0.0.0/16 and 10.42.0.0/16 VPCs in this account."
  type        = string
  default     = "10.50.0.0/16"
}

variable "domain_name" {
  description = "Public FQDN for the dashboard. Will be created as an A record under the existing ehealthnigeria.org Route 53 zone."
  type        = string
  default     = "eha-mda-dashboard.ehealthnigeria.org"
}

variable "ec2_instance_type" {
  description = "Size of the EC2 instance running the FastAPI + Redis docker-compose stack."
  type        = string
  default     = "t3.medium"
}

variable "rds_instance_class" {
  description = "RDS class. db.t4g.medium (Graviton) is the cost-optimised default for the Sokoto-scale settlement_analytics recompute. Bump to db.t3.large or db.t4g.large if recompute starts taking >2 minutes."
  type        = string
  default     = "db.t4g.medium"
}

variable "rds_allocated_storage_gb" {
  description = "Initial RDS storage in GB. Autoscaling enabled up to 200 GB."
  type        = number
  default     = 30
}

variable "rds_engine_version" {
  description = "PostgreSQL version on RDS. Must support PostGIS via the rds.force_ssl parameter group."
  type        = string
  default     = "16.14" # latest 16.x available in us-east-1 as of provisioning; PostGIS 3.4+ ships with it
}

variable "rds_multi_az" {
  description = "Multi-AZ for RDS. Cost goes up significantly (~+$130/mo). Default off per the single-environment plan; flip on later when traffic justifies it."
  type        = bool
  default     = false
}

variable "ssh_allowed_cidrs" {
  description = "CIDRs allowed to SSH into the bastion. Lock to your office / VPN IPs. NEVER use 0.0.0.0/0."
  type        = list(string)
  default     = []
}

variable "ssh_public_key" {
  description = "SSH public key contents (ssh-ed25519 / ssh-rsa) installed on the bastion + app EC2 as the default user."
  type        = string
  sensitive   = true
}

variable "commcare_username" {
  description = "Initial CommCare HQ username; written into Secrets Manager. Rotated via the admin panel afterwards."
  type        = string
  sensitive   = true
  default     = ""
}

variable "commcare_password" {
  description = "Initial CommCare HQ password. Rotated via the admin panel afterwards."
  type        = string
  sensitive   = true
  default     = ""
}

###############################################################################
# Outputs — printed after every successful apply.
###############################################################################

output "domain" {
  description = "Public URL for the dashboard."
  value       = "https://${var.domain_name}"
}

output "alb_dns_name" {
  description = "Raw ALB DNS — use if Route 53 is still propagating."
  value       = aws_lb.main.dns_name
}

output "bastion_public_ip" {
  description = "SSH jumphost IP. Tunnel through this for psql and EC2 admin."
  value       = aws_instance.bastion.public_ip
}

output "app_private_ip" {
  description = "Private IP of the app EC2. Reach via `ssh -J ec2-user@<bastion> ec2-user@<this>`."
  value       = aws_instance.app.private_ip
}

output "rds_endpoint" {
  description = "PostgreSQL connection endpoint. Reach via the bastion."
  value       = aws_db_instance.main.address
}

output "ecr_repository_url" {
  description = "Push the FastAPI image here. The CI workflow does this automatically on merge to main."
  value       = aws_ecr_repository.app.repository_url
}

output "secrets" {
  description = "Secrets Manager entries the app reads at boot."
  value = {
    db_url_app         = aws_secretsmanager_secret.db_url_app.name
    app_secrets        = aws_secretsmanager_secret.app_secrets.name
    commcare           = aws_secretsmanager_secret.commcare.name
    db_master_password = aws_secretsmanager_secret.db_master_password.name
  }
}

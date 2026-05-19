###############################################################################
# Bastion — t4g.nano jumphost in the public subnet
#
# Two uses:
#   1. SSH into the private-subnet EC2 to deploy / debug:
#        ssh -J ec2-user@<bastion-ip> ec2-user@<ec2-private-ip>
#   2. Tunnel into RDS for dev work / building new insight cards against live data:
#        ssh -L 5432:<rds-endpoint>:5432 ec2-user@<bastion-ip>
#        psql "host=localhost user=app_dev dbname=geospatial_tracking_system"
###############################################################################

resource "aws_instance" "bastion" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = "t3.nano" # ~$4/mo, x86 to match the app server's AMI
  subnet_id                   = aws_subnet.public_a.id
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  key_name                    = aws_key_pair.operator.key_name
  associate_public_ip_address = true

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
    echo "Bastion ready." > /var/log/user-data.log
  USERDATA

  tags = { Name = "${var.project_name}-bastion" }
}

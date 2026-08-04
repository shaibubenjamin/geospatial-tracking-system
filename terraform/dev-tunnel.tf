###############################################################################
# Dev RDS tunnel — least-privilege IAM policy
#
# Devs reach the (private) RDS by opening an SSM port-forward through the
# bastion (scripts/dev-aws.sh). Each dev gets their own IAM user with this
# managed policy attached; the policy lets them ONLY:
#   - start an SSM port-forward session to the bastion (and no other instance)
#   - terminate/resume their OWN sessions
#   - start/stop the bastion (dev-aws.sh boots it on demand to save cost)
#   - read the app-dev DB credential from Secrets Manager
#
# NOTE: this policy was originally created out-of-band (AWS CLI) and hardcoded
# the bastion's instance ID (i-0da32cbe872649cf7). Codified here 2026-08-04 and
# rewired to `aws_instance.bastion.arn` so it self-heals to the new bastion on
# any bring-back (the instance ID WILL change). Attaching it to per-dev users
# is a manual onboarding step — see terraform/BRING-BACK.md — because IAM users
# and their access keys are per-person credentials, not shared infrastructure.
###############################################################################

data "aws_iam_policy_document" "dev_tunnel" {
  statement {
    sid     = "StartPortForwardToBastionOnly"
    effect  = "Allow"
    actions = ["ssm:StartSession"]
    resources = [
      aws_instance.bastion.arn,
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:document/AWS-StartPortForwardingSessionToRemoteHost",
    ]
  }

  statement {
    sid     = "ManageOwnSessionsOnly"
    effect  = "Allow"
    actions = ["ssm:TerminateSession", "ssm:ResumeSession"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:session/$${aws:username}-*",
    ]
  }

  statement {
    sid       = "StartStopBastionOnly"
    effect    = "Allow"
    actions   = ["ec2:StartInstances", "ec2:StopInstances"]
    resources = [aws_instance.bastion.arn]
  }

  statement {
    sid    = "DescribeReadOnly"
    effect = "Allow"
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
      "ssm:DescribeInstanceInformation",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "ReadDevDbCredentialOnly"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["${aws_secretsmanager_secret.app_dev_password.arn}*"]
  }
}

resource "aws_iam_policy" "dev_tunnel" {
  name        = "${var.project_name}-dev-tunnel"
  description = "Least-priv SSM port-forward to the bastion for the dev RDS tunnel. Attach to per-dev IAM users."
  policy      = data.aws_iam_policy_document.dev_tunnel.json
}

output "dev_tunnel_policy_arn" {
  description = "Attach this to each dev's IAM user to grant RDS tunnel access. See BRING-BACK.md."
  value       = aws_iam_policy.dev_tunnel.arn
}

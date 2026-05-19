###############################################################################
# GitHub OIDC — lets the deploy workflow assume an AWS role without long-lived
# keys. GitHub's OIDC provider issues a token; AWS verifies it and grants the
# role's permissions.
#
# The role can only be assumed by workflows running on the repo + branch listed
# in the trust policy (shaibubenjamin/geospatial-tracking-system on main).
###############################################################################

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# Fallback: create the provider if it doesn't already exist in the account.
# Comment this out if the data lookup above succeeds.
# resource "aws_iam_openid_connect_provider" "github" {
#   url             = "https://token.actions.githubusercontent.com"
#   client_id_list  = ["sts.amazonaws.com"]
#   thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
# }

data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only the main branch of our repo can assume this role
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:shaibubenjamin/geospatial-tracking-system:ref:refs/heads/main",
        "repo:shaibubenjamin/geospatial-tracking-system:environment:production",
      ]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.project_name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
  description        = "Assumed by the deploy.yml GitHub Actions workflow on push to main"
}

data "aws_iam_policy_document" "github_deploy_perms" {
  # ECR — push the image
  statement {
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["*"]
  }

  # SSM Run Command — used by the deploy workflow to roll the container on
  # the EC2 instance without needing to SSH through the bastion. The EC2's
  # SSM agent + instance role do the rest.
  statement {
    actions = [
      "ssm:SendCommand",
      "ssm:ListCommands",
      "ssm:ListCommandInvocations",
      "ssm:GetCommandInvocation",
      "ssm:DescribeInstanceInformation",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${var.project_name}-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy_perms.json
}

output "github_deploy_role_arn" {
  description = "Set this as the role-to-assume in .github/workflows/deploy.yml"
  value       = aws_iam_role.github_deploy.arn
}

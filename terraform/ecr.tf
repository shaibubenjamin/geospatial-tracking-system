###############################################################################
# ECR — Container registry for the FastAPI image
#
# The CI workflow builds the image and pushes here on every merge to main.
# The EC2 pulls from here on deploy.
###############################################################################

resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE" # allows :latest to move; pin to SHA tags in prod

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = var.project_name }
}

# Keep image history reasonable — keep last 30 SHA-tagged images + latest
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the 30 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      }
    ]
  })
}

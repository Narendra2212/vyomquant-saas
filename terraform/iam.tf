data "aws_caller_identity" "current" {}

resource "aws_iam_role" "ecs_task_execution_role" {
  name = "${var.ecs_task_execution_role_name}-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_role_policy" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets_policy" {
  name = "ecs-task-execution-secrets"
  role = aws_iam_role.ecs_task_execution_role.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = [
          "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:key/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role" "ecs_task_role" {
  name = "${var.ecs_task_role_name}-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# Add minimal permissions for ECS tasks if they need to access S3, DynamoDB, etc.
resource "aws_iam_role_policy" "ecs_task_policy" {
  name = "ecs-task-policy"
  role = aws_iam_role.ecs_task_role.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameters"
        ]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/vyomquant/*"
        ]
      }
    ]
  })
}

# ──────────────────────────────────────────────────────────────────────────────────────
#  CI-02: GitHub Actions OIDC deploy role -- NOT YET WIRED UP, see remaining steps below
# ──────────────────────────────────────────────────────────────────────────────────────
#
# .github/workflows/02-build.yml and 03-deploy.yml authenticate to AWS with
# secrets.AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY -- a long-lived IAM user access key
# that never expires on its own and is not scoped to GitHub Actions at all. If it leaks
# (logged by accident, a compromised runner, a dependency that exfiltrates env vars), the
# credential is valid everywhere it was granted, forever, until someone notices and
# rotates it by hand.
#
# These two resources are the role side of the fix: a role any workflow run FROM THIS
# REPOSITORY's main/master branches can assume via GitHub's own OIDC token (an
# `AssumeRoleWithWebIdentity` STS session, scoped and short-lived, nothing standing). They
# are deliberately NOT referenced by any workflow yet -- this sandbox has no real AWS
# credentials to `terraform apply` them with, and switching 02-build.yml/03-deploy.yml's
# `aws-actions/configure-aws-credentials@v4` step over to `role-to-assume` before the role
# actually exists in AWS would break every build and deploy outright. A dedicated session
# with real AWS access should, in order:
#   1. `terraform apply` this (creates the OIDC provider + role, costs nothing on its own).
#   2. Point both workflows' `configure-aws-credentials@v4` step at
#      `role-to-assume: aws_iam_role.github_actions_deploy.arn` instead of
#      `aws-access-key-id`/`aws-secret-access-key`, and add `permissions: id-token: write`
#      to 03-deploy.yml's jobs (02-build.yml already declares it, unused until now).
#   3. Watch both workflows go green against the role at least once, THEN delete the
#      long-lived IAM user access key from AWS IAM and from the repo's
#      `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` GitHub secrets.
# The IAM policy below is scoped to what scripts/ecs_deploy_and_diagnose.py and
# 02-build.yml's own `aws ecr`/`docker push` steps actually call (grepped both for every
# `aws ecs`/`aws ecr`/`aws logs` subcommand) -- review and tighten further before step 1
# if this account's real IAM boundary conventions call for narrower resource ARNs than
# `ecs:DescribeServices`/`UpdateService`/`RegisterTaskDefinition` allow (those three do not
# support resource-level restriction in IAM at all, per AWS's own service authorization
# reference).
resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list intentionally omitted: AWS has verified GitHub's OIDC endpoint
  # certificate chain automatically, without a pinned thumbprint, since October 2022.
}

resource "aws_iam_role" "github_actions_deploy" {
  name = "VyomQuantGithubActionsDeploy-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # Scoped to the branches 02-build.yml (push: [main, master]) and 03-deploy.yml
            # (workflow_run off 02 Build, plus its own workflow_dispatch) actually run on --
            # not every ref in the repository.
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_repository}:ref:refs/heads/main",
              "repo:${var.github_repository}:ref:refs/heads/master",
            ]
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name = "github-actions-ecr-ecs-deploy"
  role = aws_iam_role.github_actions_deploy.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*" # ecr:GetAuthorizationToken does not support resource-level scoping
      },
      {
        Sid    = "EcrPushPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:DescribeImages",
        ]
        Resource = [
          "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/vyomquant-*"
        ]
      },
      {
        Sid    = "EcsDeploy"
        Effect = "Allow"
        Action = [
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition",
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "ecs:ListTasks",
          "ecs:DescribeTasks",
        ]
        Resource = "*" # none of these six actions support resource-level restriction
      },
      {
        Sid    = "EcsPassRole"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_task_execution_role.arn,
          aws_iam_role.ecs_task_role.arn,
        ]
      },
      {
        Sid    = "DeployLogReading"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:*"
      }
    ]
  })
}

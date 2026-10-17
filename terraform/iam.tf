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
#  CI-02: GitHub Actions OIDC deploy role -- ROLE CREATED, workflows NOT YET SWITCHED OVER
# ──────────────────────────────────────────────────────────────────────────────────────
#
# .github/workflows/02-build.yml and 03-deploy.yml still authenticate to AWS with
# secrets.AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY -- a long-lived IAM user access key
# that never expires on its own and is not scoped to GitHub Actions at all.
#
# The OIDC PROVIDER already existed in this account before this role was created --
# confirmed via real AWS access (ListOpenIDConnectProviders/GetOpenIDConnectProvider) to
# belong to a different project ("deeptech-outreach") that shares this AWS account. GitHub's
# OIDC issuer URL is identical for every account/repo, so AWS allows only ONE such provider
# account-wide -- a `resource "aws_iam_openid_connect_provider"` here would either fail with
# EntityAlreadyExists or, worse, give terraform ownership of a resource another team
# depends on. Referenced as a `data` source instead -- read-only, no risk of terraform ever
# trying to modify or destroy it.
#
# The role itself was created directly via `iam:CreateRole`/`PutRolePolicy` (not through a
# `terraform apply` of this file -- no resource in this account has ever been imported into
# terraform state, so a real apply here would try to recreate the VPC, ALB, etc. from
# scratch; see INF-02). This resource documents the real, live role.
#
# Fixed a real bug while creating it: the permissions policy's `iam:PassRole` resource list
# below used to reference `aws_iam_role.ecs_task_execution_role.arn` /
# `aws_iam_role.ecs_task_role.arn` -- names this terraform has never actually applied. The
# REAL running task definition's own executionRoleArn/taskRoleArn (confirmed via
# DescribeTaskDefinition) is `arn:aws:iam::273709947018:role/ecsTaskExecutionRole`, the
# generic AWS-console-default name, not either of terraform's own. PassRole scoped to the
# terraform names would have granted the role no usable permission at all once a workflow
# tried to register a task definition against the role that's actually in use -- fixed to
# reference the real ARN directly.
#
# Remaining, deliberately NOT done yet, each needing its own verification gate:
#   1. Point both workflows' `configure-aws-credentials@v4` step at
#      `role-to-assume: arn:aws:iam::273709947018:role/VyomQuantGithubActionsDeploy-production`
#      instead of `aws-access-key-id`/`aws-secret-access-key`, and add
#      `permissions: id-token: write` to 03-deploy.yml's jobs (02-build.yml already declares
#      it, unused until now).
#   2. Watch both workflows go green against the role at least once -- a real CI run, not
#      something to simulate.
#   3. ONLY once step 2 is confirmed green, delete the long-lived IAM user access key from
#      AWS IAM and from the repo's `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` GitHub
#      secrets. That access key is also what every AWS call in this remediation effort has
#      been authenticating with -- deleting it before step 2 is confirmed would cut off the
#      very credential being used to verify the switch, with no way to undo it from here.
data "aws_iam_openid_connect_provider" "github_actions" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "github_actions_deploy" {
  name = "VyomQuantGithubActionsDeploy-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.github_actions.arn
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
          # The REAL role in use (see the header comment above) -- not
          # aws_iam_role.ecs_task_execution_role.arn/ecs_task_role.arn, which this
          # terraform has never actually applied.
          "arn:aws:iam::273709947018:role/ecsTaskExecutionRole",
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

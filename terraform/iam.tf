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
# STATUS: the switch is DONE. All five key-using workflows (02-build, 03-deploy,
# 04-nightly-audit, 06-frontend-deploy, 08-secret-history-audit) now assume a role via
# OIDC and no non-comment line in any of them references the static key.
#
# WHAT THE FIRST ATTEMPT GOT WRONG, because the note it left sent the next attempt in the
# wrong direction for days. It failed with "Not authorized to perform
# sts:AssumeRoleWithWebIdentity" and concluded the cause was the shared provider's stale
# ThumbprintList, which would need the owning project's agreement to change. That reading
# was wrong twice: AWS has not validated that thumbprint for
# token.actions.githubusercontent.com since 2023, and a certificate problem cannot produce
# an AUTHORIZATION error anyway -- that message means the token validated and the TRUST
# POLICY did not match. Nothing about the shared provider needed changing.
#
# The real cause: this repository has GitHub's immutable subject claims enabled, so the
# `sub` carries the numeric owner and repository IDs
# (repo:Narendra2212@126683513/vyomquant-saas@1306233497:ref:refs/heads/main) and the
# name-shaped patterns below could never match. Both shapes are now listed -- see the
# comment on the `sub` condition.
#
# THERE ARE NOW TWO ROLES, and the split is load-bearing rather than tidiness. See
# `aws_iam_role.github_actions_audit` below: 08-secret-history-audit runs on
# `pull_request`, whose subject this deploy role deliberately does not trust, because a
# PR-assumable principal that can push images and call ecs:UpdateService means anyone who
# can open a pull request can ship one.
#
# STILL OUTSTANDING, and it is the last piece: the `AWS_ACCESS_KEY_ID` /
# `AWS_SECRET_ACCESS_KEY` GitHub repository secrets should be DELETED now that nothing
# reads them. Deliberately sequenced after watching the switched workflows go green,
# because that same access key is what every AWS call in this remediation effort
# authenticates with. Deleting the repo secrets does not touch the IAM key itself, so
# local tooling keeps working and re-adding them is the rollback.
data "aws_iam_openid_connect_provider" "github_actions" {
  url = "https://token.actions.githubusercontent.com"
}

# CI-02: the read-only role for the secret-history sweep (04-nightly-audit's
# secret-history-sweep job and all of 08-secret-history-audit).
#
# WHY A SECOND ROLE EXISTS AT ALL. 08 runs on `pull_request` and its credential step
# hard-fails without a credential -- by design, because an audit that silently skips its
# own comparison reports clean. A pull-request run's OIDC subject is `...:pull_request`,
# which means sharing the deploy role would have required trusting that subject on a
# principal holding ecr:PutImage and ecs:UpdateService. Someone could then open a pull
# request, edit the workflow in it, and deploy. That is a worse posture than the static
# key this whole change removes, so the wider trust goes on a role that can only read.
#
# Verified by NEGATIVE simulation rather than by inspection: against this role,
# ecs:UpdateService, ecr:PutImage, s3:PutObject and iam:PassRole all evaluate to
# implicitDeny.
resource "aws_iam_role" "github_actions_audit" {
  name                 = "VyomQuantGithubActionsAudit-${var.environment}"
  max_session_duration = 3600

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
            # Branches AND pull_request. Both subject shapes, same reason as the deploy
            # role's: immutable subject claims are a repository toggle.
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_repository_immutable}:ref:refs/heads/main",
              "repo:${var.github_repository_immutable}:ref:refs/heads/master",
              "repo:${var.github_repository_immutable}:pull_request",
              "repo:${var.github_repository}:ref:refs/heads/main",
              "repo:${var.github_repository}:ref:refs/heads/master",
              "repo:${var.github_repository}:pull_request",
            ]
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_actions_audit" {
  name = "github-actions-secret-history-audit"
  role = aws_iam_role.github_actions_audit.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ListSecrets does not support resource-level permissions. Read-only metadata.
        Sid      = "ListEverySecretName"
        Effect   = "Allow"
        Action   = ["secretsmanager:ListSecrets"]
        Resource = "*"
      },
      {
        # The sweep compares live secret VALUES against git history, so GetSecretValue is
        # the point of it. Confined to this project's prefix: the script starts from
        # list-secrets and would otherwise read other projects' secrets in this account.
        Sid    = "ReadOwnSecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecretVersionIds",
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/production/*"
      },
      {
        # Covers CMK-encrypted secrets; a no-op for aws/secretsmanager-encrypted ones.
        # ViaService stops this being a general decrypt grant.
        Sid      = "DecryptSecretsOnly"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
        }
      },
      {
        # audit_secrets_in_git_history.py resolves which secrets the LIVE task definition
        # references, so it can tell a secret that is in use from one that is orphaned.
        Sid      = "ResolveLiveTaskDefinition"
        Effect   = "Allow"
        Action   = ["ecs:DescribeServices", "ecs:DescribeTaskDefinition"]
        Resource = "*"
      },
    ]
  })
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
            # Scoped to the branches 02-build.yml (push: [main, master]), 03-deploy.yml
            # (workflow_run off 02 Build, plus workflow_dispatch) and 06-frontend-deploy
            # actually run on -- not every ref, and deliberately NOT `pull_request`, which
            # is why the separate audit role above exists.
            #
            # BOTH SUBJECT SHAPES ARE LISTED, and the first four lines are the fix for the
            # failure this file's header describes. GitHub issues immutable subject claims
            # for this repository, so the live `sub` is
            #   repo:Narendra2212@126683513/vyomquant-saas@1306233497:ref:refs/heads/main
            # with the owner and repository IDs embedded. Proven by a probe workflow on
            # main printing the claim, not inferred from documentation.
            #
            # The name-shaped patterns stay even though they cannot currently match.
            # Immutable subjects are a repository-level toggle, and a build that breaks
            # because someone flipped it back would be a self-inflicted repeat of the
            # outage this replaced. Two inert lines are cheaper than that.
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_repository_immutable}:ref:refs/heads/main",
              "repo:${var.github_repository_immutable}:ref:refs/heads/master",
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
        Sid    = "EcrDescribeRepositories"
        Effect = "Allow"
        # 03-deploy -> pre_deployment_validation.py -> aws_validation.py checks that
        # vyomquant-api / vyomquant-tee / vyomquant-mds all exist before deploying.
        Action   = ["ecr:DescribeRepositories"]
        Resource = ["arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/vyomquant-*"]
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
          # Added with the workflow switch. ecs_deploy_and_diagnose.py discovers which
          # workers to redeploy with ecs:ListServices rather than from a hardcoded list
          # (WORKER_SERVICE_PREFIX = "vyomquant-worker-" plus the mds sidecar), and
          # aws_validation.py calls ecs:DescribeClusters as a pre-deploy existence check.
          "ecs:ListServices",
          "ecs:DescribeClusters",
        ]
        Resource = "*" # none of these actions support resource-level restriction
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
        # Narrows the grant to passing that role TO ECS, so it cannot be used to hand the
        # role to some other service. Note when verifying this with
        # iam:SimulatePrincipalPolicy: the simulator evaluates conditions against the
        # context it is GIVEN, so without --context-entries for iam:PassedToService this
        # statement can only ever return implicitDeny. That is a simulator artefact, not a
        # missing grant -- it returns `allowed` once the key is supplied, and ECS sets it
        # on the real RegisterTaskDefinition call.
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Sid    = "DeployLogReading"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
        ]
        # RESOURCE "*", and this is a correction. This was scoped to
        # `...:log-group:*`, which satisfies DescribeLogStreams but NOT DescribeLogGroups
        # or GetLogEvents -- measured with iam:SimulatePrincipalPolicy against an
        # unchanged policy while varying only the resource argument. DescribeLogGroups
        # enumerates groups and takes no specific group; GetLogEvents' resource is
        # stream-qualified. The bug was latent because the workflows authenticated as an
        # IAM user holding CloudWatchLogsFullAccess, so this role's log grants were never
        # exercised until the OIDC switch.
        #
        # All three are read-only, and what they read is deploy diagnostics -- the log
        # tail 03-deploy prints when a rollout fails. Scoping this wrong fails closed
        # exactly when someone is trying to find out why production did not come up.
        Resource = "*"
      },
      {
        Sid    = "PreDeploySecretExistenceChecks"
        Effect = "Allow"
        # aws_validation.py asserts every secret the task definition references exists
        # and has a current version. Metadata only -- NOT GetSecretValue, which the
        # deploy path never needs and which the audit role holds separately.
        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecretVersionIds",
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:vyomquant/production/*"
      },
      {
        Sid    = "FrontendBucket"
        Effect = "Allow"
        # 06-frontend-deploy's three `aws s3 sync --delete` passes and its `s3 ls`.
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::vyomquant-frontend"
      },
      {
        Sid      = "FrontendObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectAttributes", "s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::vyomquant-frontend/*"
      },
      {
        Sid    = "FrontendCdn"
        Effect = "Allow"
        Action = [
          "cloudfront:CreateInvalidation",
          # GetInvalidation is NOT redundant: `aws cloudfront wait
          # invalidation-completed` polls it. Granting only CreateInvalidation produces a
          # deploy that publishes the assets and then fails on the wait.
          "cloudfront:GetInvalidation",
          "cloudfront:GetDistribution",
        ]
        Resource = "arn:aws:cloudfront::${data.aws_caller_identity.current.account_id}:distribution/EEOXECPHQ8SR0"
      },
    ]
  })
}

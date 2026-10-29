variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "ap-southeast-1"
}

variable "environment" {
  description = "Environment name (e.g., production, staging)"
  type        = string
  default     = "production"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "domain_name" {
  description = "Domain name for the application (e.g., vyomquant.com)"
  type        = string
  default     = "example.com"
}

variable "enable_custom_domain" {
  description = "Enable Route53 and ACM custom domain configuration"
  type        = bool
  default     = false
}

# A CloudFront viewer certificate MUST live in us-east-1, whatever region the rest of the
# stack is in. `aws_acm_certificate.main` in alb.tf is created by the default provider,
# which is ap-southeast-1, so it CANNOT serve as a CloudFront viewer certificate -- wiring
# it in would fail at apply with InvalidViewerCertificate.
#
# This is a VARIABLE rather than a resource on purpose: the certificate already exists and
# is not managed here (see the header note in cloudfront.tf -- nothing in this directory is
# applied). Declaring a second `aws_acm_certificate` in a us-east-1 provider alias would
# model a certificate that is already issued and validated, and the DNS validation records
# would conflict with the live ones.
variable "cloudfront_certificate_arn" {
  description = "ARN of the us-east-1 ACM certificate CloudFront serves for the custom domain aliases. Must be in us-east-1."
  type        = string
  default     = ""

  validation {
    condition     = var.cloudfront_certificate_arn == "" || can(regex("^arn:aws:acm:us-east-1:", var.cloudfront_certificate_arn))
    error_message = "A CloudFront viewer certificate must be an ACM certificate in us-east-1."
  }
}

# The hostnames the CDN answers on. Empty means "CloudFront default domain only", which is
# what an unconfigured apply should do rather than silently claiming production's names.
variable "cloudfront_aliases" {
  description = "Hostnames served by the CloudFront distribution. Requires cloudfront_certificate_arn when non-empty."
  type        = list(string)
  default     = []
}

variable "ecs_task_execution_role_name" {
  description = "Name of the ECS task execution role"
  type        = string
  default     = "VyomQuantEcsTaskExecutionRole"
}

variable "ecs_task_role_name" {
  description = "Name of the ECS task role"
  type        = string
  default     = "VyomQuantEcsTaskRole"
}

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t4g.micro"
}

# ──────────────────────────────────────────────────────────────────────────────────────
#  SC-20: audit trail and threat detection
# ──────────────────────────────────────────────────────────────────────────────────────

variable "cloudtrail_retention_days" {
  description = "Days to retain CloudTrail log objects before expiry. 365 is the floor most audit regimes assume; lower it only against a written retention policy."
  type        = number
  default     = 365
}

variable "enable_guardduty" {
  description = <<-EOT
    Enable Amazon GuardDuty threat detection.

    DEFAULT false, and the default is about COST, not merit. CloudTrail's first
    management-event trail is free (you pay only S3 storage, cents at this volume), so it
    is enabled unconditionally in cloudtrail.tf. GuardDuty is billed per million CloudTrail
    events and per GB of VPC flow and DNS logs analysed, so switching it on changes the
    monthly bill — that is an account-owner decision, not a default this file should make
    on someone's behalf.

    For a platform holding broker API credentials and moving customer money, turning this
    on is the right call. Set it to true when you have agreed the spend.
  EOT
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "OPS-01: e-mail address subscribed to the production alerts SNS topic. Leave empty to create the topic and alarms without a subscriber (subscribe a pager/Slack integration to the topic ARN output instead)."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "CI-02: GitHub \"owner/repo\" slug trusted by the GitHub Actions OIDC deploy role (see iam.tf's github_actions_deploy) to assume it. Must match this repository exactly -- a wrong value either trusts no workflow (safe but broken) or, in the worst case, trusts the wrong repository."
  type        = string
  default     = "Narendra2212/vyomquant-saas"
}

variable "metrics_scrape_token" {
  description = "API-07: shared-secret header value (X-Metrics-Token) the ALB listener rule requires on /metrics*. The real value lives in Secrets Manager (vyomquant/<environment>/metrics_scrape_token) and the real scraper's own scrape_config, never in source -- this variable has no default on purpose."
  type        = string
  sensitive   = true
}

# ─────────────────────────────────────────────────────────────────────────────────
# CONTAINER IMAGES (INF-02).
#
# ecs.tf previously carried four BARE image names -- "vyomquant-api:latest", each with a
# trailing comment "# ECR URL goes here". A bare name has no registry, so it is not a
# pullable reference at all: an apply would have produced task definitions that cannot
# start. The live task definitions were read to find what production actually uses
# (`ecs describe-task-definition` across every ACTIVE family):
#
#   vyomquant-api:251                 .../vyomquant-api:6b36287f…   (a COMMIT SHA, not latest)
#   vyomquant-mds:5                   .../vyomquant-api:e1dd3b94…   (the API image, not an mds one)
#   vyomquant-worker-{dag,command,backtest,exchange_fills,marketplace_expiry}
#                                     .../vyomquant-api:6b36287f…
#
# So one image serves the API, the MDS and every worker; they differ only by command. The
# `vyomquant-mds` and `vyomquant-worker` ECR repositories exist but nothing in production
# uses them.
# ─────────────────────────────────────────────────────────────────────────────────

variable "image_tag" {
  description = <<-EOT
    The tag to deploy. Production uses the COMMIT SHA, not a moving tag -- CI-02 removed
    the `:latest` push precisely so a task definition names an immutable artifact. The
    default is "latest" only so `terraform plan` can run without a value; a real deploy
    passes the SHA.
  EOT
  type        = string
  default     = "latest"
}

variable "backend_image_repository" {
  description = <<-EOT
    The ECR repository holding the backend image. One image serves the API, the MDS and
    every worker -- measured from the live task definitions, which all reference
    `vyomquant-api` and differ only in the command they run.
  EOT
  type        = string
  default     = "vyomquant-api"
}

variable "simulator_image_repository" {
  description = <<-EOT
    The ECR repository for the exchange simulator.

    MEASURED GAP, recorded rather than hidden behind a default that looks fine: no
    `vyomquant-simulator` repository exists in this account (`ecr describe-repositories`
    returns vyomquant-api, -tee, -mds, -worker, -frontend and nothing else), and no
    simulator task definition family is ACTIVE either. So `aws_ecs_task_definition.simulator`
    and `aws_ecs_service.simulator` in ecs.tf describe a service that has never run. The
    reference below is therefore well-formed but unresolvable until the repository is
    created and an image pushed -- which is a deliberate decision, not a default.
  EOT
  type        = string
  default     = "vyomquant-simulator"
}

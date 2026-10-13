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

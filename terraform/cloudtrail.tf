# ══════════════════════════════════════════════════════════════════════════════════════
#  SC-20 — AUDIT LOGGING THAT THE APPLICATION CANNOT TURN OFF
# ══════════════════════════════════════════════════════════════════════════════════════
#
# Before this file there was no control-plane audit trail in any form: no CloudTrail, no
# Config, no GuardDuty, no Security Hub anywhere in `terraform/` or
# `aerora_quant_platform/infrastructure/`. The practical consequence is that an IAM policy
# change, a security-group rule opened, a secret read out of Secrets Manager or a trail
# deletion left no record anyone could consult afterwards. The application's own audit
# writers (`core/audit_trail.py`, `routers/admin.py`) record application actions into Redis
# lists; they say nothing about who changed the infrastructure, and they are written by the
# same process that would be the subject of an investigation.
#
# WHY THE "CANNOT BE DISABLED BY APP CODE/ROLES" HALF OF SC-20 HOLDS HERE
# ----------------------------------------------------------------------
# The ECS task role (`iam.tf`) carries `ssm:GetParameters` on one path and nothing else.
# The execution role carries `secretsmanager:GetSecretValue` on `vyomquant/*` plus
# `kms:Decrypt`. Neither has `cloudtrail:*`, `s3:DeleteObject` on this bucket, or
# `s3:PutBucketPolicy`, so nothing the application runs can stop, reconfigure or prune the
# trail. `enable_log_file_validation` adds the digest chain, so deletion or alteration of a
# delivered log file is detectable after the fact rather than silent.
#
# COST: a first management-event trail is free. You pay S3 storage, which at this event
# volume is cents per month, falling further under the lifecycle rules below.

# `data.aws_caller_identity.current` is declared once, in iam.tf, and reused here. The
# region comes from `var.aws_region` rather than a `data "aws_region"` source, because that
# is how iam.tf and ecs.tf already compose ARNs, and because the data source's attribute
# was renamed between AWS provider 5.x (`.name`) and 6.x (`.region`) — the variable has no
# such seam and this configuration pins `~> 5.0`.

# ──────────────────────────────────────────────────────────────────────────────────────
#  Log bucket
# ──────────────────────────────────────────────────────────────────────────────────────
#
# Deliberately separate from `aws_s3_bucket.frontend_static` (cloudfront.tf). A log store
# and a public-read-through-CloudFront asset store want opposite policies, and co-locating
# them is how an audit trail ends up world-readable.
resource "aws_s3_bucket" "cloudtrail" {
  bucket = "vyomquant-cloudtrail-${var.environment}-${data.aws_caller_identity.current.account_id}"

  # Never true, including outside prod. `force_destroy` on an audit bucket means a
  # `terraform destroy` silently discards the evidence, which is the one thing this bucket
  # exists to prevent. Removing it has to be deliberate and manual.
  force_destroy = false

  tags = {
    Name    = "vyomquant-cloudtrail-${var.environment}"
    Purpose = "CloudTrail audit log delivery"
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3 rather than SSE-KMS, on purpose. SSE-KMS on a CloudTrail bucket needs a key
      # policy that grants `cloudtrail.amazonaws.com` kms:GenerateDataKey*, and getting it
      # wrong fails the apply with InsufficientEncryptionPolicyException. There is no
      # customer-managed key in this configuration yet (that gap is SC-9, tracked
      # separately), so introducing one here would couple two unrelated fixes. Upgrade this
      # to `aws:kms` with the trail's `kms_key_id` when SC-9 is addressed.
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Versioning plus the lifecycle rules below are what make "cannot be quietly pruned" true
# in practice: an overwrite leaves the prior version behind.
resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  # The provider requires an explicit filter; an empty one means "every object".
  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {}

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = var.cloudtrail_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.cloudtrail]
}

# ──────────────────────────────────────────────────────────────────────────────────────
#  Bucket policy
# ──────────────────────────────────────────────────────────────────────────────────────
#
# THE TRAIL ARN IS BUILT AS A STRING, NOT REFERENCED. This is the one non-obvious thing in
# this file. CloudTrail refuses to create a trail whose bucket policy does not already
# permit it (InsufficientS3BucketPolicyException), and the recommended policy scopes both
# statements with `aws:SourceArn` = the trail ARN. Referencing `aws_cloudtrail.main.arn`
# here would therefore be a dependency cycle: policy needs trail, trail needs policy.
# Composing the ARN from account, region and the trail's own name breaks the cycle, and
# `depends_on` on the trail enforces the ordering that the removed reference used to imply.
locals {
  cloudtrail_name = "vyomquant-trail-${var.environment}"
  cloudtrail_arn = "arn:aws:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${local.cloudtrail_name}"
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSCloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.cloudtrail.arn
        Condition = {
          StringEquals = {
            "aws:SourceArn" = local.cloudtrail_arn
          }
        }
      },
      {
        Sid    = "AWSCloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            # Both conditions are required by the documented policy: the ACL condition so
            # the delivered object is owned by this account, the SourceArn so this bucket
            # accepts deliveries from THIS trail and not from any trail in any account that
            # happens to name it.
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.cloudtrail_arn
          }
        }
      },
      {
        # Defence in depth against the delivery path itself being downgraded.
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.cloudtrail.arn,
          "${aws_s3_bucket.cloudtrail.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })
}

# ──────────────────────────────────────────────────────────────────────────────────────
#  The trail
# ──────────────────────────────────────────────────────────────────────────────────────
resource "aws_cloudtrail" "main" {
  name           = local.cloudtrail_name
  s3_bucket_name = aws_s3_bucket.cloudtrail.id

  # Multi-region so an action taken in a region this stack does not deploy to is still
  # recorded. An attacker who knows the trail is single-region simply works elsewhere.
  is_multi_region_trail = true

  # IAM, STS, CloudFront and Route53 are global services whose events are emitted in
  # us-east-1. Without this, IAM changes — the highest-value events here — are absent.
  include_global_service_events = true

  # The digest chain. Without it a delivered log file can be altered or removed with
  # nothing to indicate it happened.
  enable_log_file_validation = true

  tags = {
    Name    = local.cloudtrail_name
    Purpose = "Control-plane audit trail (SC-20)"
  }

  # See the ARN note above: this replaces the implicit ordering a direct reference would
  # have given, and without it the create races the policy and fails.
  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

# ──────────────────────────────────────────────────────────────────────────────────────
#  GuardDuty (opt-in — see variables.tf for why the default is false)
# ──────────────────────────────────────────────────────────────────────────────────────
resource "aws_guardduty_detector" "main" {
  count = var.enable_guardduty ? 1 : 0

  enable = true

  # Fifteen minutes rather than six hours. A credential-exfiltration finding that lands a
  # quarter of an hour after the fact is actionable; one that lands six hours later is a
  # post-mortem.
  finding_publishing_frequency = "FIFTEEN_MINUTES"

  tags = {
    Name = "vyomquant-guardduty-${var.environment}"
  }
}

output "cloudtrail_bucket_name" {
  description = "S3 bucket receiving CloudTrail logs"
  value       = aws_s3_bucket.cloudtrail.bucket
}

output "cloudtrail_arn" {
  description = "ARN of the control-plane audit trail"
  value       = aws_cloudtrail.main.arn
}

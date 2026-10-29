# ==============================================================================
# terraform/cloudfront.tf - CloudFront CDN & S3 Static Frontend Infrastructure
# ==============================================================================
#
# ##############################################################################
# NOTHING IN THIS DIRECTORY IS APPLIED, AND THIS FILE USED TO BE A TRAP
# ##############################################################################
#
# MEASURED, not assumed: the backend configured in providers.tf
# (s3://vyomquant-terraform-state, key prod/terraform.tfstate) HAS NO STATE OBJECT --
# a HeadObject on that key returns 404, while the bucket itself and the DynamoDB lock
# table both exist. So `terraform apply` has never run against this configuration and
# NOTHING in production is terraform-managed. Production was built by hand and by
# scripts/deploy_worker_services.py; see that script's module docstring, which already
# records the same conclusion from the other direction ("route A is terraform, which
# cannot be applied... (B) is this script").
#
# The resource names here confirm it independently: this file declares the bucket
# `vyomquant-frontend-static-${var.environment}` while the live bucket is
# `vyomquant-frontend`, and alb.tf declares `vyomquant-alb-${var.environment}` while the
# live load balancer is `vyomquant-alb`. These describe a DIFFERENT stack.
#
# WHY THAT MADE THIS FILE DANGEROUS. The live distribution (EEOXECPHQ8SR0) serves the
# frontend from S3 *and* routes five path patterns to the API's load balancer. Until this
# change, this file declared ONLY the S3 origin and ONE default_cache_behavior: no ALB
# origin, no ordered_cache_behavior, no aliases, and the CloudFront default certificate.
#
# An `apply` on empty state would merely have built a parallel stack and collided on
# existing names -- wasteful, not fatal. But the realistic way somebody adopts live
# infrastructure is `terraform import` followed by `apply`, and THAT would have
# reconciled the live distribution down to this file: deleting the ALB origin, all five
# API behaviours, all three aliases and the ACM certificate. The entire API and
# websocket surface would have stopped being reachable through the CDN, and the custom
# domain would have reverted to the CloudFront default name.
#
# So the ALB origin, the five behaviours, the aliases and the certificate are now
# DECLARED below, read from the live distribution rather than guessed. Importing is
# therefore safe with respect to routing. Applying on empty state is still not a way to
# adopt production, and the name divergences above are deliberately left alone: changing
# them would make an unapplied config look adoptable when it is not.
#
# tests/test_inf_cloudfront_declares_the_api_routing.py pins the five path patterns, so
# they cannot quietly disappear from this file again.
# ##############################################################################

locals {
  # The API's origin id, as it exists in the live distribution. Kept as a local so the
  # origin and every behaviour that targets it cannot drift apart.
  alb_origin_id = "ALB-vyomquant-backend"

  # THE PATHS THAT MUST NOT BE SERVED FROM S3, and the whole point of the note above.
  #
  # ORDER IS PRECEDENCE. CloudFront evaluates ordered_cache_behavior top to bottom and
  # takes the first match, so this list is the live distribution's order. `/openapi.json`
  # is last and exact; it is listed even though the API answers it with a 404 by design
  # (production serves no schema), because the 404 must come from the API rather than
  # from the SPA's index.html fallback -- a 200 with an HTML body is a worse answer to a
  # schema request than an honest 404.
  #
  # `/health*` and `/metrics*` are globs, not exact: ALB listener rules in alb.tf gate
  # /metrics on a bearer token and leave /health open, and both have suffixed forms
  # (/health/live, /health/ready).
  cdn_api_path_patterns = [
    "/ws/*",
    "/api/*",
    "/health*",
    "/metrics*",
    "/openapi.json",
  ]
}

# ------------------------------------------------------------------------------
# 1. Private S3 Bucket for Frontend Static Assets
# ------------------------------------------------------------------------------
resource "aws_s3_bucket" "frontend_static" {
  bucket        = "vyomquant-frontend-static-${var.environment}"
  force_destroy = var.environment != "prod"

  tags = {
    Name        = "vyomquant-frontend-static-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

resource "aws_s3_bucket_public_access_block" "frontend_static" {
  bucket = aws_s3_bucket.frontend_static.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend_static" {
  bucket = aws_s3_bucket.frontend_static.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "frontend_static" {
  bucket = aws_s3_bucket.frontend_static.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ------------------------------------------------------------------------------
# 2. CloudFront Origin Access Control (OAC)
# ------------------------------------------------------------------------------
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "vyomquant-frontend-oac-${var.environment}"
  description                       = "OAC for VyomQuant Static Frontend S3 Bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# S3 Bucket Policy granting read permissions to CloudFront OAC
resource "aws_s3_bucket_policy" "frontend_static" {
  bucket = aws_s3_bucket.frontend_static.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontServicePrincipalReadOnly"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.frontend_static.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
          }
        }
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 3. Security Headers & CORS Policy for CDN Responses
# ------------------------------------------------------------------------------
resource "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "vyomquant-frontend-security-headers-${var.environment}"

  cors_config {
    access_control_allow_credentials = false

    access_control_allow_headers {
      items = ["*"]
    }

    access_control_allow_methods {
      items = ["GET", "HEAD", "OPTIONS"]
    }

    access_control_allow_origins {
      items = ["*"]
    }

    origin_override = true
  }

  security_headers_config {
    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
      preload                    = true
    }

    xss_protection {
      mode_block = true
      override   = true
      protection = true
    }
  }
}

# ------------------------------------------------------------------------------
# 4. CloudFront Distribution Configuration
# ------------------------------------------------------------------------------
resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "VyomQuant Static Frontend CDN (${var.environment})"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  # The hostnames served. Empty by default so an unconfigured apply claims nothing.
  aliases = var.cloudfront_aliases

  origin {
    domain_name              = aws_s3_bucket.frontend_static.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
    origin_id                = "S3-${aws_s3_bucket.frontend_static.bucket}"
  }

  # THE API ORIGIN. Absent until this change; see the header note for what its absence
  # would have cost on an import-then-apply.
  #
  # `http-only` matches the live distribution: CloudFront reaches the load balancer over
  # plain HTTP on port 80. That hop is inside AWS but it is NOT encrypted, and raising it
  # to https-only needs an HTTPS listener on the ALB with a certificate it can present --
  # alb.tf declares one only behind `enable_custom_domain`. Recorded rather than silently
  # "improved", because flipping this without that listener breaks every request.
  origin {
    domain_name = aws_lb.main.dns_name
    origin_id   = local.alb_origin_id

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1", "TLSv1.1", "TLSv1.2"]
      origin_read_timeout      = 30
      origin_keepalive_timeout = 5
    }
  }

  # One behaviour per API path pattern, generated from the single list in `locals` so a
  # path cannot be added to one place and forgotten in the other. All five are identical
  # apart from the pattern, which is exactly how they are in the live distribution.
  #
  # cache_policy_id 4135ea2d-… is the AWS managed CachingDisabled policy, and
  # origin_request_policy_id b689b0a8-… is AllViewerExceptHostHeader. Both are REQUIRED
  # rather than incidental:
  #   * CachingDisabled, because caching an authenticated API response at the edge serves
  #     one tenant's data to another. There is no safe TTL for /api/*.
  #   * AllViewerExceptHostHeader, because it forwards the Authorization header (without
  #     it the API sees no credential and answers 401 for everyone) and the WebSocket
  #     upgrade headers, while leaving Host as the origin's own so the ALB routes to the
  #     right target group.
  dynamic "ordered_cache_behavior" {
    for_each = local.cdn_api_path_patterns

    content {
      path_pattern     = ordered_cache_behavior.value
      target_origin_id = local.alb_origin_id

      allowed_methods = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods  = ["GET", "HEAD"]

      compress               = true
      viewer_protocol_policy = "redirect-to-https"

      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    }
  }

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "S3-${aws_s3_bucket.frontend_static.bucket}"

    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    min_ttl                    = 0
    default_ttl                = 86400
    max_ttl                    = 31536000
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }
  }

  # SPA Routing Fallback: Map 404 & 403 errors to index.html for client-side routing
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # The default CloudFront certificate covers *.cloudfront.net only. Serving the custom
  # aliases needs an ACM certificate IN US-EAST-1 (see variables.tf for why it is a
  # variable and not a resource). The two are mutually exclusive, so this switches on
  # whether a certificate was supplied rather than declaring both.
  viewer_certificate {
    cloudfront_default_certificate = var.cloudfront_certificate_arn == ""

    acm_certificate_arn      = var.cloudfront_certificate_arn == "" ? null : var.cloudfront_certificate_arn
    ssl_support_method       = var.cloudfront_certificate_arn == "" ? null : "sni-only"
    minimum_protocol_version = var.cloudfront_certificate_arn == "" ? null : "TLSv1.2_2021"
  }

  # An alias without a matching certificate is refused by CloudFront at apply time with a
  # message that does not name the cause. Caught here instead, where it can say so.
  lifecycle {
    precondition {
      condition     = length(var.cloudfront_aliases) == 0 || var.cloudfront_certificate_arn != ""
      error_message = "cloudfront_aliases is set but cloudfront_certificate_arn is empty. CloudFront cannot serve a custom hostname on the default *.cloudfront.net certificate; supply a us-east-1 ACM certificate ARN."
    }
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

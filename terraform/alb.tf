resource "aws_lb" "main" {
  name               = "vyomquant-alb-${var.environment}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = true

  tags = {
    Environment = var.environment
  }
}

resource "aws_lb_target_group" "api" {
  name        = "vyomquant-api-tg-${var.environment}"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  # INF-01: this pointed at /health, which backend_app/main.py's own health() handler
  # always answers with HTTP 200 -- its "status": "degraded" (Redis/Supabase/QuestDB down)
  # lives only in the JSON body, which this ALB health check (a bare status-code matcher)
  # never reads. A target with no reachable database or Redis still passed every check and
  # kept receiving production traffic. /health/ready (core/readiness.py's
  # evaluate_readiness(), added under OBS-01) actually measures the database and Redis --
  # Supabase/Postgres because every authenticated request resolves identity and RLS
  # through it, Redis because it backs the rate limiter and the distributed execution
  # locks outside a declared local environment -- and returns a real 503 when either is
  # down, which this matcher does read. Its own PROBE_TIMEOUT_SECONDS (3.0s per
  # dependency) fits inside this check's 5s timeout without raising it.
  health_check {
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = "/health/ready"
    matcher             = "200"
  }
}

resource "aws_lb_target_group" "frontend" {
  name        = "vyomquant-web-tg-${var.environment}"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    healthy_threshold   = 3
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = "/"
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.enable_custom_domain ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  # Plaintext fallback: serves the frontend directly over :80 when no custom domain (and
  # therefore no ACM certificate and no HTTPS listener) exists. Retained for local and
  # bootstrap use only — the precondition below keeps it out of production.
  dynamic "default_action" {
    for_each = var.enable_custom_domain ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.frontend.arn
    }
  }

  # SC-7, CORRECTED (INF-06). The previous precondition here refused
  # `enable_custom_domain = false` in a production environment, on the grounds that without
  # an HTTPS listener "every request, including Authorization headers and broker
  # credentials, is served in clear text over port 80". Its PREMISE is true -- there is no
  # HTTPS listener. Its CONCLUSION is not, and the refusal made this configuration unable
  # to plan for the only environment it is written for: with the committed defaults
  # (enable_custom_domain = false, environment = "production") `terraform plan` failed this
  # precondition before it could produce a plan at all.
  #
  # WHY THE CONCLUSION IS WRONG, measured against the running ALB and against the security
  # group declared alongside it in security_groups.tf:
  #
  #   * aws_security_group.alb has exactly ONE ingress rule -- tcp/80 with
  #     `prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]`, described
  #     "HTTP from CloudFront only". It is UNCONDITIONAL: there is no 0.0.0.0/0 path and no
  #     variable that widens it. So this plaintext listener is not reachable from the
  #     internet in any configuration this repository can produce.
  #   * The live ALB matches: vyomquant-alb has a single HTTP:80 listener, and its security
  #     group sg-067b73e186fc1a635 permits tcp/80 only from prefix list pl-31a34658,
  #     annotated "SEC-17: CloudFront origin-facing only".
  #
  # Client TLS therefore terminates at CloudFront, which is the documented entry point
  # (probe https://d7d88qs4jmch.cloudfront.net, not the ALB). No client request reaches
  # port 80 in clear text.
  #
  # WHAT IS GENUINELY WEAKER, stated rather than buried by removing the old guard: the
  # CloudFront-to-origin hop IS plaintext HTTP. It travels inside AWS and is unreachable
  # from the internet, but it is not end-to-end TLS. Adding origin TLS is tracked as its own
  # item rather than implied by this comment.
  #
  # THE GUARD IS REPLACED, NOT DELETED, because measuring the above turned up a real trap in
  # the opposite direction. `enable_custom_domain = true` creates an HTTPS:443 listener
  # (count = 1 below) -- but aws_security_group.alb opens only port 80, so that listener
  # would accept no connection from anywhere. The old precondition demanded exactly that
  # state in production. This one refuses it and says what else has to change first.
  lifecycle {
    precondition {
      # NOTE: error_message is a literal on purpose. Interpolation inside precondition
      # error messages is only accepted from Terraform 1.6 onward, and providers.tf
      # declares required_version = ">= 1.5.0".
      condition     = !var.enable_custom_domain
      error_message = "enable_custom_domain=true creates an HTTPS:443 listener, but aws_security_group.alb (security_groups.tf) opens only tcp/80, and only to the CloudFront origin-facing prefix list -- so that listener would be unreachable and the :80 redirect would point at a dead port. Enabling a direct-to-ALB TLS design requires three changes together: a 443 ingress rule on that security group, a re-pointed outputs.api_endpoint, and a decision about whether CloudFront stays in front. TLS currently terminates at CloudFront; see the comment above this block."
    }
  }
}

data "aws_route53_zone" "main" {
  count        = var.enable_custom_domain ? 1 : 0
  name         = var.domain_name
  private_zone = false
}

resource "aws_acm_certificate" "main" {
  count             = var.enable_custom_domain ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  subject_alternative_names = [
    "*.${var.domain_name}"
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = var.enable_custom_domain ? {
    for dvo in aws_acm_certificate.main[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.main[0].zone_id
}

resource "aws_acm_certificate_validation" "main" {
  count                   = var.enable_custom_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

resource "aws_lb_listener" "https" {
  count             = var.enable_custom_domain ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  # SC-7: TLS 1.2 is the floor. ELBSecurityPolicy-2016-08 (the AWS default, and what stood
  # here) still negotiates TLS 1.0 and 1.1, which fails the constraint and any compliance
  # scan that checks protocol versions. This policy offers TLS 1.3 and 1.2 only.
  ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = aws_acm_certificate_validation.main[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

# API traffic
resource "aws_lb_listener_rule" "api_rule" {
  listener_arn = var.enable_custom_domain ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }
}

# API-07: /metrics (the Prometheus scrape endpoint) was reachable with zero authentication,
# both via CloudFront's own /metrics* cache behavior and directly via this ALB's
# internet-facing public DNS name -- confirmed via real AWS access (DescribeRules showed no
# path-based rule existed at all before this). A CloudFront-only fix would not have closed
# the direct-ALB path, so the gate lives here, the one layer every request passes through
# regardless of entry point. Applied directly via elbv2 CreateRule (not through this
# unapplied terraform source -- no existing resource in this account has ever been imported
# into terraform state, so a real `terraform apply` of this file would try to recreate
# everything from scratch; see INF-02). This resource documents the real, live rules.
# Verified end to end: /metrics without the header -> 403; with it -> 200; every other path
# (e.g. /health) unaffected. The real secret value lives in Secrets Manager
# (vyomquant/production/metrics_scrape_token), never in source.
resource "aws_lb_listener_rule" "metrics_allow_with_token" {
  listener_arn = var.enable_custom_domain ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  priority     = 5

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/metrics*"]
    }
  }

  condition {
    http_header {
      http_header_name = "X-Metrics-Token"
      values           = [var.metrics_scrape_token]
    }
  }
}

resource "aws_lb_listener_rule" "metrics_deny_without_token" {
  listener_arn = var.enable_custom_domain ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  priority     = 6

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }

  condition {
    path_pattern {
      values = ["/metrics*"]
    }
  }
}

# WS-01: the dedicated websocket service (ws_server.py), its Dockerfile, and the terraform
# resources that would have run it as a live AWS service (this target group + listener rule,
# and ecs.tf's aws_ecs_task_definition.websocket/aws_ecs_service.websocket) are removed.
# Confirmed via real AWS access (not guessed): DescribeTargetGroups/DescribeListeners on the
# real vyomquant-alb show only vyomquant-api-tg and vyomquant-web-tg -- no websocket target
# group or /ws/* listener rule has ever been applied, so nothing here could have had live
# traffic. The real realtime path is api_ws/ws_manager.py's ConnectionManager, in-process
# inside the API service; CloudFront's own /ws/* cache behavior already forwards to the API
# target group via the ALB's default action, which is what actually serves it today.

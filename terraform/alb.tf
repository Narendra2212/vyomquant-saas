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

  # SC-7: `enable_custom_domain` defaults to false (variables.tf), and with it false there is
  # no HTTPS listener at all (count = 0 below), the API and WebSocket rules attach to this
  # plaintext listener, and outputs.tf advertises http:// and ws:// endpoints. A production
  # apply in that state carries every request, including Authorization headers and broker
  # credentials, in clear text. Making the redirect unconditional would instead point :80 at
  # a :443 listener that does not exist, so the fix is to refuse the combination rather than
  # to silently rewrite it.
  lifecycle {
    precondition {
      # NOTE: error_message is a literal on purpose. Interpolation inside precondition
      # error messages is only accepted from Terraform 1.6 onward, and providers.tf
      # declares required_version = ">= 1.5.0".
      condition     = var.enable_custom_domain || !contains(["prod", "production"], var.environment)
      error_message = "A prod/production environment requires enable_custom_domain=true. Without it no HTTPS listener is created and every request, including Authorization headers and broker credentials, is served in clear text over port 80."
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
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main[0].certificate_arn

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

# WS-01: the dedicated websocket service (ws_server.py), its Dockerfile, and the terraform
# resources that would have run it as a live AWS service (this target group + listener rule,
# and ecs.tf's aws_ecs_task_definition.websocket/aws_ecs_service.websocket) are removed.
# Confirmed via real AWS access (not guessed): DescribeTargetGroups/DescribeListeners on the
# real vyomquant-alb show only vyomquant-api-tg and vyomquant-web-tg -- no websocket target
# group or /ws/* listener rule has ever been applied, so nothing here could have had live
# traffic. The real realtime path is api_ws/ws_manager.py's ConnectionManager, in-process
# inside the API service; CloudFront's own /ws/* cache behavior already forwards to the API
# target group via the ALB's default action, which is what actually serves it today.

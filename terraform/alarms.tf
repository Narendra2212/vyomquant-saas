# OPS-01: alerting. Before this file the committed production configuration had a log group
# and Container Insights but nothing that ever told a human anything was wrong: a 5xx storm,
# a crash-looping API service, an unhealthy target group, a failed billing webhook or an
# engaged kill switch were all silent unless somebody happened to be reading the logs.
#
# Everything below is alarms + one SNS topic. It changes no running resource.
#
#   - alarms notify aws_sns_topic.alerts (both ALARM and OK);
#   - subscribe a human with var.alert_email, or subscribe a pager / chat integration to the
#     topic ARN (output `alerts_topic_arn`);
#   - missing data is NOT breaching for rate alarms (no traffic is not an outage) and IS
#     breaching for the running-task alarm (no datapoints from Container Insights means the
#     service is not reporting, which is itself the incident).

resource "aws_sns_topic" "alerts" {
  name              = "vyomquant-alerts-${var.environment}"
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Environment = var.environment
  }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── Load balancer / API health ────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "api_target_5xx" {
  alarm_name          = "vyomquant-${var.environment}-api-5xx"
  alarm_description   = "The API returned more than 10 server errors in 5 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy_hosts" {
  alarm_name          = "vyomquant-${var.environment}-api-unhealthy-targets"
  alarm_description   = "At least one API target has failed its health check (readiness 503 included) for 3 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency_p99" {
  alarm_name          = "vyomquant-${var.environment}-api-latency-p99"
  alarm_description   = "API p99 latency above 3s for 10 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p99"
  period              = 300
  evaluation_periods  = 2
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }
}

# ── ECS service liveness (needs Container Insights, enabled on the cluster) ───────────

resource "aws_cloudwatch_metric_alarm" "api_running_tasks" {
  alarm_name          = "vyomquant-${var.environment}-api-running-tasks-below-desired"
  alarm_description   = "The API service is running fewer tasks than it should (crash loop, failed deploy, capacity)."
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.api.name
  }
}

# ── Application events that must page a human ─────────────────────────────────────────
# The application logs these at CRITICAL/ERROR with stable markers. A metric filter turns the
# marker into a count, an alarm turns any non-zero count into a notification.

resource "aws_cloudwatch_log_metric_filter" "critical_logs" {
  name           = "vyomquant-${var.environment}-critical-logs"
  log_group_name = aws_cloudwatch_log_group.ecs.name
  pattern        = "CRITICAL"

  metric_transformation {
    name          = "CriticalLogCount"
    namespace     = "VyomQuant/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "critical_logs" {
  alarm_name          = "vyomquant-${var.environment}-critical-log-events"
  alarm_description   = "Something logged at CRITICAL (billing webhook refused/unreconciled, kill-switch failure, platform guards)."
  namespace           = "VyomQuant/${var.environment}"
  metric_name         = "CriticalLogCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_log_metric_filter" "webhook_failures" {
  name           = "vyomquant-${var.environment}-billing-webhook-failures"
  log_group_name = aws_cloudwatch_log_group.ecs.name
  pattern        = "?\"entitlement processing failed\" ?\"NOT applied\" ?\"Subscription deletion failed\" ?\"Subscription update failed\""

  metric_transformation {
    name          = "BillingWebhookFailureCount"
    namespace     = "VyomQuant/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "webhook_failures" {
  alarm_name          = "vyomquant-${var.environment}-billing-webhook-failures"
  alarm_description   = "A payment webhook was refused or failed to apply an entitlement: money may have moved without access being granted (or the reverse)."
  namespace           = "VyomQuant/${var.environment}"
  metric_name         = "BillingWebhookFailureCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_log_metric_filter" "kill_switch_events" {
  name           = "vyomquant-${var.environment}-kill-switch-events"
  log_group_name = aws_cloudwatch_log_group.ecs.name
  pattern        = "?\"GLOBAL KILL SWITCH\" ?\"Global kill switch\""

  metric_transformation {
    name          = "KillSwitchEventCount"
    namespace     = "VyomQuant/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "kill_switch_events" {
  alarm_name          = "vyomquant-${var.environment}-kill-switch-event"
  alarm_description   = "The global kill switch was engaged (or failed to engage). Trading is halted or may not be."
  namespace           = "VyomQuant/${var.environment}"
  metric_name         = "KillSwitchEventCount"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_log_metric_filter" "audit_write_failures" {
  name           = "vyomquant-${var.environment}-audit-write-failures"
  log_group_name = aws_cloudwatch_log_group.ecs.name
  pattern        = "\"AUDIT DURABLE WRITE\""

  metric_transformation {
    name          = "AuditWriteFailureCount"
    namespace     = "VyomQuant/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "audit_write_failures" {
  alarm_name          = "vyomquant-${var.environment}-audit-write-failures"
  alarm_description   = "The order audit trail could not be written durably (AUD-02): there is a gap in the compliance record."
  namespace           = "VyomQuant/${var.environment}"
  metric_name         = "AuditWriteFailureCount"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

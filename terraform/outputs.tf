output "alb_dns_name" {
  description = "DNS Name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS Cluster"
  value       = aws_ecs_cluster.main.name
}

output "redis_endpoint" {
  description = "Endpoint of the real production ElastiCache replication group (vyomquant-redis-production-cmd), looked up read-only -- see terraform/redis.tf. Primary address only; the live app also uses a reader endpoint (REDIS_REPLICA_URL) this output does not expose."
  value       = data.aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "cloudfront_distribution_id" {
  description = "ID of the CloudFront CDN distribution"
  value       = aws_cloudfront_distribution.frontend.id
}

output "cloudfront_domain_name" {
  description = "Domain name of the CloudFront CDN distribution"
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "s3_frontend_bucket_name" {
  description = "Name of the S3 bucket hosting frontend static assets"
  value       = aws_s3_bucket.frontend_static.bucket
}

output "api_endpoint" {
  description = <<-EOT
    The endpoint for the API.

    INF-06: the non-custom-domain branch used to advertise the ALB's own DNS name over
    http, which is NOT a reachable address. The ALB's security group (security_groups.tf)
    admits tcp/80 only from the CloudFront origin-facing prefix list, so a caller
    resolving that name is dropped. The reachable entry point is the CloudFront
    distribution, where client TLS also terminates -- so this output now names it, over
    https rather than http.
  EOT
  value = (var.enable_custom_domain
    ? "https://${var.domain_name}/api"
  : "https://${aws_cloudfront_distribution.frontend.domain_name}/api")
}

output "alerts_topic_arn" {
  description = "OPS-01: SNS topic that every production alarm notifies. Subscribe a pager / chat integration here."
  value       = aws_sns_topic.alerts.arn
}

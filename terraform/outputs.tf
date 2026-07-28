output "alb_dns_name" {
  description = "DNS Name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS Cluster"
  value       = aws_ecs_cluster.main.name
}

output "redis_endpoint" {
  description = "Endpoint of the ElastiCache Redis cluster"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
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


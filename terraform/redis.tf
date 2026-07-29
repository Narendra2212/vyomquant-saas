resource "aws_elasticache_subnet_group" "redis" {
  name       = "vyomquant-redis-subnet-group-${var.environment}"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "vyomquant-redis-${var.environment}"
  description                = "Redis cluster for VyomQuant"
  node_type                  = var.redis_node_type
  port                       = 6379
  parameter_group_name       = "default.redis7.cluster.on"
  automatic_failover_enabled = true

  # For Multi-AZ
  multi_az_enabled        = true
  num_node_groups         = 1
  replicas_per_node_group = 2

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
}

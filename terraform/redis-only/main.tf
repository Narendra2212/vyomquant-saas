# Redis-only Terraform configuration for existing production VPC
# This deploys ElastiCache Redis into the existing VyomQuant production environment

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-1"
  default_tags {
    tags = {
      Project     = "VyomQuant"
      Environment = "production"
      ManagedBy   = "Terraform"
    }
  }
}

# Data sources for existing production VPC infrastructure
data "aws_vpc" "existing" {
  id = "vpc-0f3e7b3f243fbc133"
}

data "aws_subnet" "private_1" {
  vpc_id     = data.aws_vpc.existing.id
  cidr_block = "10.0.128.0/20"
}

data "aws_subnet" "private_2" {
  vpc_id     = data.aws_vpc.existing.id
  cidr_block = "10.0.144.0/20"
}

data "aws_security_group" "ecs_tasks" {
  id = "sg-0262aed02d8df0356"
}

# Redis security group
resource "aws_security_group" "redis" {
  name        = "vyomquant-redis-sg-production"
  description = "Security group for Redis - allows access only from existing ECS tasks"
  vpc_id      = data.aws_vpc.existing.id

  ingress {
    description     = "Allow Redis from existing ECS tasks"
    protocol        = "tcp"
    from_port       = 6379
    to_port         = 6379
    security_groups = [data.aws_security_group.ecs_tasks.id]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Redis subnet group
resource "aws_elasticache_subnet_group" "redis" {
  name       = "vyomquant-redis-subnet-group-production"
  subnet_ids = [data.aws_subnet.private_1.id, data.aws_subnet.private_2.id]
}

# Redis replication group (cluster mode enabled - existing, managed externally)
# This resource is kept in Terraform for documentation but managed via lifecycle ignore_changes
resource "aws_elasticache_replication_group" "redis_cluster_mode" {
  replication_group_id       = "vyomquant-redis-production"
  description                = "Redis cluster for VyomQuant (cluster mode enabled - deprecated)"
  node_type                  = "cache.t4g.micro"
  port                       = 6379
  parameter_group_name       = "default.redis7.cluster.on"
  automatic_failover_enabled = true

  # For Multi-AZ (cluster mode enabled)
  multi_az_enabled        = true
  num_node_groups         = 1
  replicas_per_node_group = 2

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}

# Redis replication group (cluster mode disabled - new production)
resource "aws_elasticache_replication_group" "redis_cluster_mode_disabled" {
  replication_group_id       = "vyomquant-redis-production-cmd"
  description                = "Redis cluster for VyomQuant (cluster mode disabled - production)"
  node_type                  = "cache.t4g.micro"
  port                       = 6379
  parameter_group_name       = "default.redis7"
  automatic_failover_enabled = true

  # For Multi-AZ (cluster mode disabled - single shard with replicas)
  multi_az_enabled   = true
  num_cache_clusters = 3 # 1 primary + 2 replicas

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
}

output "redis_endpoint" {
  description = "Endpoint of the ElastiCache Redis cluster (cluster mode disabled - new production)"
  value       = aws_elasticache_replication_group.redis_cluster_mode_disabled.primary_endpoint_address
}

output "redis_cluster_mode_endpoint" {
  description = "Endpoint of the ElastiCache Redis cluster (cluster mode enabled - deprecated)"
  value       = aws_elasticache_replication_group.redis_cluster_mode.configuration_endpoint_address
}

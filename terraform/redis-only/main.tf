# Redis-only Terraform configuration for existing production VPC
# This deploys ElastiCache Redis into the existing VyomQuant production environment
#
# ─────────────────────────────────────────────────────────────────────────────────────
# DO NOT `terraform apply` THIS MODULE WITHOUT READING THIS FIRST (INF-02)
# ─────────────────────────────────────────────────────────────────────────────────────
# This module and the ROOT module at terraform/ now BOTH declare the same live security
# group. `aws_security_group.redis` below describes sg-00c4bac4f05c9922b, and the root
# module's own `aws_security_group.redis` was IMPORTED against that exact id -- it is in
# terraform state at s3://vyomquant-terraform-state/prod/terraform.tfstate today.
#
# Two root modules claiming one resource is a conflict, not a duplication: whichever
# applies last wins, and because `name` and `description` are ForceNew on a security
# group, "winning" can mean DESTROY AND RECREATE the group attached to six live
# ElastiCache nodes. This module's own state is not in that bucket (it was applied from a
# separate working directory, per INF-02), so neither side can see the other.
#
# The conflict is recorded rather than resolved here, deliberately. Changing
# `resource "aws_security_group" "redis"` below into a `data` lookup would be the obvious
# tidy-up and is the WRONG move blind: if this module's state exists anywhere, removing
# the resource from its configuration makes the next apply plan a DESTROY of the live
# group. Resolving it safely needs that state located first, which is part of INF-06's
# consolidation step.
#
# What this module still uniquely records, and why it is therefore not deleted: the
# `aws_elasticache_subnet_group` and the DEPRECATED cluster-mode-enabled replication group
# `vyomquant-redis-production`. The root module's redis.tf carries a data source for
# `vyomquant-redis-production-cmd` only -- the one REDIS_URL actually points at -- so
# deleting this file would lose the only description of the other two.
# ─────────────────────────────────────────────────────────────────────────────────────

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

  # CI-05: was open to 0.0.0.0/0. A managed ElastiCache node has no legitimate reason to
  # originate a connection to the public internet; restricted to the real existing VPC's
  # own CIDR (10.0.0.0/16, matching this file's own data "aws_subnet" blocks above:
  # 10.0.128.0/20 and 10.0.144.0/20 both fall within it) -- intra-VPC only, which still
  # covers anything AWS's own managed replication/backup machinery needs.
  egress {
    description = "Intra-VPC only"
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["10.0.0.0/16"]
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

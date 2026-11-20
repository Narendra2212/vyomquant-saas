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

  # SEC-18: AUTH IS NOW ENABLED AND REQUIRED ON THIS GROUP IN THE REAL ACCOUNT, and
  # `auth_token` is deliberately NOT expressed here. Reasons, in order:
  #
  #   1. `ignore_changes = all` below means terraform would ignore the attribute anyway,
  #      so adding it would describe nothing and read as if it did.
  #   2. The value is a live credential. It lives in Secrets Manager
  #      (vyomquant/production/redis_url, embedded in the rediss:// URL's userinfo) and
  #      must never appear in source or in terraform state as a literal.
  #   3. No `terraform plan` can be run against this module from the environment that did
  #      the rollout, and no resource in this account has ever been imported into state
  #      (INF-02/INF-06). Adding an unverifiable attribute to a replication group carrying
  #      live kill-switch and idempotency state is how a future `apply` surprises someone.
  #
  # What was actually applied, 2026-11-01, so this is reproducible:
  #   aws elasticache modify-replication-group --replication-group-id \
  #       vyomquant-redis-production --auth-token <from secret> \
  #       --auth-token-update-strategy ROTATE --apply-immediately     # ~56s
  #   ...then the same with --auth-token-update-strategy SET          # ~17s
  #
  # ROTATE FIRST IS MANDATORY, not a preference: AWS only permits SET after a ROTATE, and
  # ROTATE is the non-disruptive half -- after it the group accepts the new token AND
  # still accepts unauthenticated connections, which is what lets clients be migrated
  # before access is actually withdrawn. SET is the step that withdraws it.
  #
  # THIS GROUP WENT FIRST ON PURPOSE. It is the "deprecated" one, and CloudWatch showed
  # zero GetTypeCmds and zero SetTypeCmds datapoints across seven days while the `-cmd`
  # group below recorded 403,737 gets and 1,592,937 sets. Every task definition points at
  # `-cmd`. So this group could be taken all the way through the DISRUPTIVE step with no
  # application exposure, proving the mechanism before it was aimed at the live one.
  #
  # FOLLOW-UP WORTH TAKING: this group has no identified consumer and is still billing for
  # three cache.t4g.micro nodes. Deleting it is a separate, reversible-only-by-restore
  # decision and was not taken here.
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

  # SEC-18: AUTH IS NOW ENABLED AND REQUIRED ON THIS GROUP TOO -- this is the live one,
  # the group every task definition's REDIS_URL/REDIS_REPLICA_URL points at. Same two
  # commands as the deprecated group above (ROTATE then SET), same reasons for not
  # expressing `auth_token` here, with one addition: unlike that group this resource has
  # NO `ignore_changes`, so an `auth_token` added here really would be planned, and a
  # plan that cannot be run first is exactly the risk not worth taking against the
  # replication group holding kill-switch latch state and order-idempotency locks.
  #
  # THE CLIENT MIGRATION IS THE PART WORTH RECORDING, because the SET step is only safe
  # once it is complete. Between ROTATE and SET, all seven ECS services were moved from a
  # PLAINTEXT `environment` REDIS_URL to a `secrets` entry sourced from
  # vyomquant/production/redis_url: api, mds, and the dag/command/backtest/exchange_fills/
  # marketplace_expiry workers. Secrets Manager rather than a plaintext env var because
  # the URL now carries a credential, and `environment` is readable by anything holding
  # ecs:DescribeTaskDefinition. No application code changed -- core's get_redis_url()
  # preserves the URL's netloc when it appends ?db=N, so an embedded token survives.
  #
  # Two things this rollout broke and then fixed, both worth knowing about:
  #   * scripts/ecs_deploy_and_diagnose.py's validate_critical_config() read REDIS_URL out
  #     of `environment` and compared it to the literal endpoint. Once the value moved to
  #     `secrets` that read returned "" and would have failed EVERY deploy. It now asserts
  #     the secret's identity instead, and rejects a plaintext REDIS_URL as the
  #     regression it would be.
  #   * startup.sh printed the connection URL on every container start -- which, once the
  #     token was in the URL, wrote a live Redis password into CloudWatch Logs. It now
  #     prints scheme/host/port only.
  #
  # Verified after SET: all seven services 1/1 and steady, /api/health reporting
  # redis_primary and redis_replica healthy, and AuthenticationFailures flat at zero.
}

output "redis_endpoint" {
  description = "Endpoint of the ElastiCache Redis cluster (cluster mode disabled - new production)"
  value       = aws_elasticache_replication_group.redis_cluster_mode_disabled.primary_endpoint_address
}

output "redis_cluster_mode_endpoint" {
  description = "Endpoint of the ElastiCache Redis cluster (cluster mode enabled - deprecated)"
  value       = aws_elasticache_replication_group.redis_cluster_mode.configuration_endpoint_address
}

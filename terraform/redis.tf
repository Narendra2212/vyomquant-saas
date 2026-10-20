# terraform/redis.tf
#
# INF-02 correction. This file used to declare a MANAGED `aws_elasticache_replication_group`
# named "redis" with `replication_group_id = "vyomquant-redis-${var.environment}"`
# (-> "vyomquant-redis-production"). That has never matched reality and was never safe to
# apply: the real production ElastiCache replication groups, confirmed directly against AWS
# this session (`elasticache describe-replication-groups`), are
#
#     vyomquant-redis-production       (status=available, cluster-mode enabled, deprecated)
#     vyomquant-redis-production-cmd   (status=available, cluster-mode disabled, the real
#                                        one -- REDIS_URL/REDIS_REPLICA_URL in the live ECS
#                                        task definition point at *this* one's primary/reader
#                                        endpoints)
#
# Both are already declared, correctly, in terraform/redis-only/main.tf -- a SEPARATE root
# module (no `module` block anywhere in this directory references it; confirmed by grep) --
# as `aws_elasticache_replication_group.redis_cluster_mode` and
# `.redis_cluster_mode_disabled`, each with `lifecycle { prevent_destroy = true,
# ignore_changes = all }` because they describe resources this configuration adopts for
# visibility, not ones it creates or may alter.
#
# This root module's own `redis.tf` had no such guard and a DIFFERENT shape (its own subnet
# group, `multi_az_enabled` + `num_node_groups`/`replicas_per_node_group` instead of
# `num_cache_clusters`, no `prevent_destroy`) under the SAME `replication_group_id` one of
# the real resources already uses. Nothing else in this root module ever referenced it for
# real wiring -- the live ECS task gets REDIS_URL from a plain literal environment variable
# (terraform/ecs.tf declares no `aws_elasticache_*` dependency at all), not from this file's
# output. Had this file ever been `terraform apply`'d, AWS would have refused the create
# outright (`ReplicationGroupAlreadyExistsFault` on "vyomquant-redis-production") -- so it
# was never run, not merely unverified.
#
# Replaced with a read-only `data` lookup of the real resource, so `terraform plan` in this
# root module can still surface the live endpoint (via the output below) without ever being
# able to create, modify or destroy it -- no `prevent_destroy`/`ignore_changes` guard needed
# because a data source has no lifecycle to protect.
data "aws_elasticache_replication_group" "redis" {
  replication_group_id = "vyomquant-redis-production-cmd"
}

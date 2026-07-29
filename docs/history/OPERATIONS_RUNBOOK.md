# Operations Runbook

## Service Restarts
To force a restart of an ECS service without changing code:
```bash
aws ecs update-service --cluster vyomquant-cluster-prod --service vyomquant-api-service-prod --force-new-deployment
```

## Scaling Services
If traffic spikes unexpectedly, manually scale the ECS service:
```bash
aws ecs update-service --cluster vyomquant-cluster-prod --service vyomquant-api-service-prod --desired-count 5
```
*(Note: Application Auto Scaling should be configured in Terraform for long-term management).*

## Database Maintenance
Supabase handles automated backups. If you need to run a manual script against production:
1. Fetch the DB URL from Secrets Manager.
2. Connect using `psql` locally or via a bastion host.

## Cache Clearing (Redis)
If the Redis cache is corrupted:
1. Exec into a running ECS task (using ECS Exec).
2. Use `redis-cli -h <redis-endpoint> flushall`.
Warning: This will clear all queues, locks, and cached data!

## Log Investigation
Logs are available in CloudWatch Logs under `/ecs/vyomquant-prod`. Use CloudWatch Logs Insights to run queries:
```
fields @timestamp, @message
| sort @timestamp desc
| limit 20
```

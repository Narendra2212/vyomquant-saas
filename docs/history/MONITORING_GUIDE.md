# Monitoring & Observability Guide

## CloudWatch Dashboards
A comprehensive CloudWatch Dashboard should be configured to monitor:
1. **ALB Metrics**: RequestCount, HTTPCode_Target_5XX_Count, TargetResponseTime.
2. **ECS Metrics**: CPUUtilization, MemoryUtilization per service.
3. **Redis Metrics**: CPUUtilization, EngineCPUUtilization, CurrConnections, NetworkBytesIn/Out.

## CloudWatch Alarms
Key alarms to configure for 24/7 on-call alerting:
- **API 5xx Errors**: If ALB Target 5xx > 1% of total requests over 5 minutes.
- **High CPU**: If ECS Service CPU > 85% for 10 minutes.
- **High Memory**: If ECS Service Memory > 85% for 10 minutes.
- **Redis Memory**: If ElastiCache FreeableMemory < 20% total.

## Application Tracing
FastAPI and internal microservices should utilize OpenTelemetry or AWS X-Ray (if added to containers later) to trace request pathways from ALB -> API -> TEE / MDS.

## Log Structuring
Ensure all Python services emit JSON logs.
`LOG_FORMAT=json` is set in the Dockerfile environment. CloudWatch will automatically parse JSON fields, making it easy to filter by `level="ERROR"`.

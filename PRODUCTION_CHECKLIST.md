# Production Readiness Checklist

Before signing off on the AWS deployment for 1000 concurrent users, ensure every item here is validated.

## Infrastructure
- [ ] VPC created with 3 AZs.
- [ ] NAT Gateways deployed for private subnet internet access.
- [ ] ECS Cluster deployed.
- [ ] ElastiCache Redis is Multi-AZ and accessible from ECS Tasks.
- [ ] ALB is routing traffic correctly to target groups.
- [ ] ACM Certificate is attached to the ALB for HTTPS.
- [ ] Route53 DNS resolves to the ALB.

## Security
- [ ] Docker containers run as a non-root user.
- [ ] IAM Execution Roles have minimal privileges (only Secrets Manager & CloudWatch).
- [ ] WAF is attached to the ALB and monitoring/blocking malicious requests.
- [ ] No hardcoded secrets in ECS Task Definitions (using Secrets Manager).
- [ ] Security Groups restrict internal traffic to only what is necessary (e.g., Redis SG only allows ECS SG).

## Observability
- [ ] CloudWatch Log Groups are capturing logs from API, TEE, MDS, Workers, and Frontend.
- [ ] ECS Container Insights are enabled.
- [ ] Healthchecks are functioning correctly for ALB Target Groups.

## Application Health
- [ ] Supabase connection is established successfully.
- [ ] ElastiCache Redis connection is established successfully.
- [ ] Trading Execution Engine connects to the message broker.
- [ ] Market Data Service streaming is operational.

## CI/CD
- [ ] GitHub Actions pipeline successfully builds and pushes to ECR.
- [ ] Rolling update configurations ensure zero downtime deployments.

# AWS_INFRASTRUCTURE_PLAN

This document outlines the AWS resources required to host the Aerora Quant Platform at various user scale tiers.

## 100 Users (Private Beta)
*Current setup. Cost-optimized for stability rather than massive concurrency.*
- **Backend Compute**: 1x AWS App Runner instance (2 vCPU, 4GB RAM) OR ECS Fargate (small).
- **Database**: Supabase Pro Tier.
- **Frontend**: Vercel or AWS Amplify (Standard).
- **Load Balancer**: AWS ALB (Basic).
- **Caching/Workers**: None (In-memory execution).
- **Estimated Monthly Cost**: ~$100 - $150 / month.

## 500 Users (Early Public Launch)
*Transitioning to horizontal scaling.*
- **Backend Compute (API)**: ECS Fargate Cluster (2-3 tasks behind ALB, 2 vCPU/4GB each).
- **Backend Compute (Workers)**: ECS Fargate Cluster (2 dedicated tasks for Celery/Backtesting).
- **Database**: Supabase Pro Tier + PgBouncer enabled for connection pooling.
- **Cache**: AWS ElastiCache for Redis (t4g.micro).
- **Estimated Monthly Cost**: ~$350 - $500 / month.

## 1,000 Users (Growth Phase)
*Fully distributed and resilient architecture.*
- **Backend Compute (API)**: ECS Fargate Cluster with Auto-Scaling (Target CPU 60%, 3-6 tasks).
- **Backend Compute (Workers)**: ECS Fargate (Auto-Scaling based on SQS/Redis Queue depth).
- **Backend Compute (Trading Engine)**: Dedicated EC2 instances (Compute Optimized `c6g.large`) to ensure zero-latency execution.
- **Database**: Supabase Team/Enterprise Tier (or RDS Postgres `db.m6g.large` with read replicas).
- **Cache**: AWS ElastiCache for Redis (m6g.large) for API caching and pub/sub.
- **Frontend**: CloudFront CDN -> S3.
- **Estimated Monthly Cost**: ~$1,200 - $2,500 / month.

# AWS Deployment Guide

This guide covers the deployment of VyomQuant to AWS ap-southeast-1.

## Prerequisites
1. **AWS CLI** installed and configured (`aws configure`).
2. **Terraform** >= 1.5.0 installed.
3. **Docker** installed for local builds.
4. Active Supabase PostgreSQL database URL and Keys.

## Architecture
- **VPC**: 3 Public, 3 Private Subnets.
- **Compute**: ECS Fargate for API, TEE, MDS, Workers, and Frontend.
- **Caching/State**: ElastiCache Redis (Multi-AZ).
- **Routing**: Application Load Balancer (ALB).
- **Secrets**: AWS Secrets Manager.
- **Security**: AWS WAF & Security Groups.

## Deployment Steps

### Step 1: Push Images to ECR
Create the ECR repositories:
```bash
aws ecr create-repository --repository-name vyomquant-api
aws ecr create-repository --repository-name vyomquant-tee
aws ecr create-repository --repository-name vyomquant-mds
aws ecr create-repository --repository-name vyomquant-workers
aws ecr create-repository --repository-name vyomquant-frontend
```

Authenticate Docker to ECR:
```bash
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-southeast-1.amazonaws.com
```

Build and push (example for API):
```bash
docker build -t vyomquant-api -f Dockerfile.backend .
docker tag vyomquant-api:latest <ACCOUNT_ID>.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:latest
docker push <ACCOUNT_ID>.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:latest
```

### Step 2: Set Secrets in Secrets Manager
Create the secrets expected by Terraform/ECS:
```bash
aws secretsmanager create-secret --name vyomquant/prod/database_url --secret-string "YOUR_SUPABASE_DB_URL"
aws secretsmanager create-secret --name vyomquant/prod/supabase_key --secret-string "YOUR_SUPABASE_KEY"
```

### Step 3: Deploy Terraform Infrastructure
```bash
cd terraform
terraform init
terraform plan
terraform apply
```

### Step 4: Verify Deployment
Navigate to the ALB DNS name output by Terraform. Verify health endpoints:
- `http://<ALB_DNS>/health`

## Updating Services
Trigger a GitHub Actions workflow push to `main` branch to automatically build and deploy new image tags to the ECS cluster.

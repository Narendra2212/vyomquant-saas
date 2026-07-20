# Terraform Guide

## Directory Structure
```
terraform/
├── alb.tf               # Application Load Balancer
├── ecs.tf               # ECS Cluster, Services, and Task Definitions
├── iam.tf               # Task Execution Roles
├── outputs.tf           # Terraform outputs
├── providers.tf         # AWS Provider configuration
├── redis.tf             # ElastiCache Redis cluster
├── secrets.tf           # Secrets Manager Definitions
├── security_groups.tf   # Networking SG configs
├── variables.tf         # Input variables
├── vpc.tf               # VPC and Subnets
└── waf.tf               # WAF WebACL
```

## State Management
State is configured to use S3 and DynamoDB for locking (currently commented out in `providers.tf` for initial bootstrap). To enable remote state:
1. Create an S3 bucket (e.g., `vyomquant-terraform-state`).
2. Create a DynamoDB table (e.g., `vyomquant-terraform-lock`) with partition key `LockID`.
3. Uncomment the `backend "s3"` block in `providers.tf`.

## Applying Changes
1. **Initialize**: `terraform init`
2. **Plan**: `terraform plan -var="environment=prod"`
3. **Apply**: `terraform apply -var="environment=prod"`

## Destroying Infrastructure
To completely remove the deployment:
```bash
terraform destroy
```
Note: Ensure you backup any data in ElastiCache or Supabase before destroying.

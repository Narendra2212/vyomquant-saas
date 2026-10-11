terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # SC-30: remote state must be encrypted and access-controlled. While this block was
  # commented out, state was a local file — no encryption, no locking, no versioning, and
  # no access control beyond the workstation's filesystem. Terraform state holds every
  # resource attribute in plaintext, which for this stack includes Secrets Manager
  # metadata and the full network topology.
  #
  # `encrypt = true` was absent from the commented version and is required: it enables
  # SSE on the state object. `dynamodb_table` provides the state lock that stops two
  # concurrent applies from corrupting state.
  #
  # PREREQUISITES before `terraform init -migrate-state` will succeed:
  #   1. S3 bucket `vyomquant-terraform-state` exists in ap-southeast-1, with versioning
  #      enabled and Block Public Access on.
  #   2. DynamoDB table `vyomquant-terraform-lock` exists with primary key `LockID` (String).
  backend "s3" {
    bucket         = "vyomquant-terraform-state"
    key            = "prod/terraform.tfstate"
    region         = "ap-southeast-1"
    encrypt        = true
    dynamodb_table = "vyomquant-terraform-lock"
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "VyomQuant"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

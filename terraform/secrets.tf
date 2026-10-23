resource "aws_secretsmanager_secret" "database_url" {
  name        = "vyomquant/${var.environment}/database_url"
  description = "Supabase PostgreSQL Database URL"
}

# SEC-01, 2026-10-23: THIS SECRET IS VESTIGIAL AND SHOULD BE REMOVED.
#
# It is declared here, exists in AWS, and has NEVER had a value stored -- zero
# versions, so `get-secret-value` answers ResourceNotFound. Nothing consumes it:
# no task definition in `ecs.tf` or `ecs-task-definition-full.json` references
# it, and the only reader of a `SUPABASE_KEY` env var anywhere is
# `backend_app/validate_paper_trading.py:42`, which treats it as a fallback and
# prefers SUPABASE_SERVICE_ROLE_KEY. The live code paths use
# `supabase_service_role_key` and `supabase_anon_key` instead.
#
# NOT REMOVED IN THIS PASS, deliberately: terraform cannot be planned against
# the real account from here (INF-03 -- the remote state bucket
# s3://vyomquant-terraform-state does not exist), so a destroy could not be
# reviewed before being committed. Delete this block when terraform can next be
# planned, and confirm the plan shows exactly one secret destroyed.
#
# It is NOT a security finding: a secret holding no value has nothing that could
# leak. `scripts/audit_secrets_in_git_history.py` classifies it as `empty` rather
# than as an unreadable coverage gap, which is why that sweep now reports
# complete coverage instead of a standing UNKNOWN.
resource "aws_secretsmanager_secret" "supabase_key" {
  name        = "vyomquant/${var.environment}/supabase_key"
  description = "Supabase API Key"
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name        = "vyomquant/${var.environment}/jwt_secret"
  description = "JWT Secret for Authentication"
}

resource "aws_secretsmanager_secret" "master_encryption_keys" {
  name        = "vyomquant/${var.environment}/master_encryption_keys"
  description = "Master Encryption Keys for Credential Vault"
}

resource "aws_secretsmanager_secret" "credential_vault_salt" {
  name        = "vyomquant/${var.environment}/credential_vault_salt"
  description = "PBKDF2 Key Derivation Salt for Credential Vault"
}

# The actual secret values should be populated outside of Terraform or via secure variables.
# e.g., using `aws secretsmanager put-secret-value` in the deployment scripts.

resource "aws_secretsmanager_secret" "supabase_service_role_key" {
  name        = "vyomquant/${var.environment}/supabase_service_role_key"
  description = "Supabase Service Role Key"
}

resource "aws_secretsmanager_secret" "supabase_anon_key" {
  name        = "vyomquant/${var.environment}/supabase_anon_key"
  description = "Supabase Anonymous Key"
}

resource "aws_secretsmanager_secret" "supabase_jwt_secret" {
  name        = "vyomquant/${var.environment}/supabase_jwt_secret"
  description = "Supabase JWT Secret"
}

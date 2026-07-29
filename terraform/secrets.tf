resource "aws_secretsmanager_secret" "database_url" {
  name        = "vyomquant/${var.environment}/database_url"
  description = "Supabase PostgreSQL Database URL"
}

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

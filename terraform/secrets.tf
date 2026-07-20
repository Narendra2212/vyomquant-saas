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

# The actual secret values should be populated outside of Terraform or via secure variables.
# e.g., using `aws secretsmanager put-secret-value` in the deployment scripts.

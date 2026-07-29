#!/bin/bash
# =============================================================================
#  VyomQuant SaaS — AWS Secrets Manager Bootstrap Script
#  Run this ONCE before deploying the ECS Task Definition.
#  Prerequisites: AWS CLI v2 installed and configured with appropriate IAM
# =============================================================================
set -e

AWS_REGION="${AWS_REGION:-ap-southeast-1}"       # VyomQuant production region
SECRET_PREFIX="vyomquant/production"


echo "Creating VyomQuant secrets in AWS Secrets Manager (region: $AWS_REGION)..."
echo "Secret prefix: $SECRET_PREFIX"
echo ""

# Helper: create or update a secret
upsert_secret() {
    local name="$1"
    local description="$2"
    local value="$3"

    if aws secretsmanager describe-secret \
        --secret-id "${SECRET_PREFIX}/${name}" \
        --region "$AWS_REGION" &>/dev/null; then
        echo "  [UPDATE] ${SECRET_PREFIX}/${name}"
        aws secretsmanager update-secret \
            --secret-id "${SECRET_PREFIX}/${name}" \
            --secret-string "$value" \
            --region "$AWS_REGION" > /dev/null
    else
        echo "  [CREATE] ${SECRET_PREFIX}/${name}"
        aws secretsmanager create-secret \
            --name "${SECRET_PREFIX}/${name}" \
            --description "$description" \
            --secret-string "$value" \
            --region "$AWS_REGION" > /dev/null
    fi
}

# =============================================================================
#  SECTION 1 — Supabase Credentials
#  Find these at: Supabase Dashboard -> Settings -> API
# =============================================================================
echo "--- Supabase Credentials ---"
upsert_secret "supabase_service_role_key" \
    "Supabase service role key (bypasses RLS — server-side only)" \
    "${SUPABASE_SERVICE_ROLE_KEY:?SUPABASE_SERVICE_ROLE_KEY env var must be set}"

upsert_secret "supabase_anon_key" \
    "Supabase anonymous key (safe for RLS-enforced client operations)" \
    "${SUPABASE_ANON_KEY:?SUPABASE_ANON_KEY env var must be set}"

upsert_secret "supabase_jwt_secret" \
    "Supabase JWT secret for HS256 token verification" \
    "${SUPABASE_JWT_SECRET:?SUPABASE_JWT_SECRET env var must be set}"

# =============================================================================
#  SECTION 2 — Database
# =============================================================================
echo ""
echo "--- Database ---"
upsert_secret "database_url" \
    "PostgreSQL connection string (Supabase or RDS)" \
    "${DATABASE_URL:?DATABASE_URL env var must be set}"

# =============================================================================
#  SECTION 3 — Encryption Keys (AES-256 Fernet)
#  Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#  Comma-separate multiple keys for rotation (newest first).
# =============================================================================
echo ""
echo "--- Encryption Keys ---"
upsert_secret "master_encryption_keys" \
    "AES-256 Fernet master encryption keys (comma-separated, newest first)" \
    "${MASTER_ENCRYPTION_KEYS:?MASTER_ENCRYPTION_KEYS env var must be set}"

# =============================================================================
#  SECTION 4 — JWT Secret
# =============================================================================
echo ""
echo "--- JWT ---"
upsert_secret "jwt_secret" \
    "JWT signing secret (minimum 64 characters)" \
    "${JWT_SECRET:?JWT_SECRET env var must be set}"

# =============================================================================
#  SECTION 5 — Payment Gateways (Optional)
# =============================================================================
echo ""
echo "--- Payment Gateways (optional) ---"
if [ -n "$STRIPE_SECRET_KEY" ]; then
    upsert_secret "STRIPE_SECRET_KEY" "Stripe live secret key" "$STRIPE_SECRET_KEY"
    echo "  Created Stripe secret key."
else
    echo "  STRIPE_SECRET_KEY not set — skipping."
fi

if [ -n "$STRIPE_WEBHOOK_SECRET" ]; then
    upsert_secret "STRIPE_WEBHOOK_SECRET" "Stripe webhook signing secret" "$STRIPE_WEBHOOK_SECRET"
fi

if [ -n "$RAZORPAY_KEY_ID" ]; then
    upsert_secret "RAZORPAY_KEY_ID" "Razorpay live key ID" "$RAZORPAY_KEY_ID"
fi

if [ -n "$RAZORPAY_KEY_SECRET" ]; then
    upsert_secret "RAZORPAY_KEY_SECRET" "Razorpay live key secret" "$RAZORPAY_KEY_SECRET"
fi

# =============================================================================
#  SECTION 6 — Exchange API Keys (Optional)
# =============================================================================
echo ""
echo "--- Exchange API Keys (optional) ---"
if [ -n "$EXCHANGE_API_KEY" ]; then
    upsert_secret "EXCHANGE_API_KEY" "Exchange API key" "$EXCHANGE_API_KEY"
    echo "  Created exchange API key."
else
    echo "  EXCHANGE_API_KEY not set — skipping."
fi

if [ -n "$EXCHANGE_API_SECRET" ]; then
    upsert_secret "EXCHANGE_API_SECRET" "Exchange API secret" "$EXCHANGE_API_SECRET"
fi

echo ""
echo "============================================================"
echo " All secrets created/updated in AWS Secrets Manager."
echo " Next steps:"
echo " 1. Note the ARNs below for use in the Task Definition."
echo " 2. Ensure ECS Task Execution Role has"
echo "    secretsmanager:GetSecretValue permission."
echo " 3. Deploy ecs-task-definition-full.json."
echo "============================================================"
echo ""
echo "Secret ARNs:"
aws secretsmanager list-secrets \
    --region "$AWS_REGION" \
    --query "SecretList[?starts_with(Name, '${SECRET_PREFIX}')].{Name:Name,ARN:ARN}" \
    --output table

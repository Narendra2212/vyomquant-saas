#!/usr/bin/env bash
# ==============================================================================
# scripts/invalidate_cdn.sh - CloudFront Cache Invalidation Script
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-southeast-1}"
CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}"
PATHS="${1:-/*}"

echo "======================================================================"
echo "    VyomQuant CloudFront CDN Cache Invalidation Engine               "
echo "======================================================================"

if [ -z "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
    echo "[INFO] Attempting to query CloudFront Distribution ID from Terraform outputs..."
    if command -v terraform &>/dev/null && [ -d "terraform" ]; then
        CLOUDFRONT_DISTRIBUTION_ID=$(cd terraform && terraform output -raw cloudfront_distribution_id 2>/dev/null || echo "")
    fi
fi

if [ -z "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
    echo "[ERROR] CLOUDFRONT_DISTRIBUTION_ID environment variable is missing and could not be retrieved from Terraform."
    echo "Usage: CLOUDFRONT_DISTRIBUTION_ID=<dist-id> ./scripts/invalidate_cdn.sh [path]"
    exit 1
fi

echo "[INFO] Distribution ID: $CLOUDFRONT_DISTRIBUTION_ID"
echo "[INFO] Target Paths:    $PATHS"

INVALIDATION_ID=$(aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "$PATHS" \
    --region "$AWS_REGION" \
    --query "Invalidation.Id" \
    --output text)

echo "[SUCCESS] Created Invalidation Request ID: $INVALIDATION_ID"
echo "[INFO] Waiting for invalidation status to complete..."

aws cloudfront wait invalidation-completed \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --id "$INVALIDATION_ID" \
    --region "$AWS_REGION" || true

echo "[SUCCESS] CloudFront CDN cache invalidation finished."

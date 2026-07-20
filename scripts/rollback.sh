#!/bin/bash
# STEP 8.9: Rollback Script for Trading Platform
#
# Usage: ./scripts/rollback.sh [staging|production] [revision]

set -e

ENVIRONMENT=${1:-staging}
REVISION=${2:-0}  # 0 = previous revision

echo "🔄 STEP 8.9: Rolling back $ENVIRONMENT"

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(staging|production)$ ]]; then
    echo "❌ Error: Environment must be 'staging' or 'production'"
    exit 1
fi

# Perform rollback
kubectl rollout undo deployment/trading-backend \
    -n $ENVIRONMENT \
    --to-revision=$REVISION

# Wait for rollback
kubectl rollout status deployment/trading-backend \
    -n $ENVIRONMENT \
    --timeout=300s

# Verify
echo "✅ Rollback complete!"
kubectl get pods -n $ENVIRONMENT -l app=trading-backend

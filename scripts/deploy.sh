#!/bin/bash
# STEP 8.9: Deployment Script for Trading Platform
#
# Usage: ./scripts/deploy.sh [staging|production]

set -e

ENVIRONMENT=${1:-staging}
IMAGE_TAG=${2:-latest}

echo "🚀 STEP 8.9: Deploying to $ENVIRONMENT"
echo "   Image: $IMAGE_TAG"

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(staging|production)$ ]]; then
    echo "❌ Error: Environment must be 'staging' or 'production'"
    exit 1
fi

# Load environment variables
export $(grep -v '^#' .env.$ENVIRONMENT | xargs)

# =============================================================================
# Pre-deployment checks
# =============================================================================
echo "📋 Running pre-deployment checks..."

# Check kubectl access
if ! kubectl cluster-info > /dev/null 2>&1; then
    echo "❌ Error: Cannot connect to Kubernetes cluster"
    exit 1
fi

# Check namespace exists
if ! kubectl get namespace $ENVIRONMENT > /dev/null 2>&1; then
    echo "❌ Error: Namespace '$ENVIRONMENT' does not exist"
    exit 1
fi

# =============================================================================
# Deploy
# =============================================================================
echo "🚢 Deploying application..."

# Update image tag in deployment
kubectl set image deployment/trading-backend \
    backend=$IMAGE_TAG \
    -n $ENVIRONMENT

# Wait for rollout
kubectl rollout status deployment/trading-backend \
    -n $ENVIRONMENT \
    --timeout=300s

# =============================================================================
# Post-deployment validation
# =============================================================================
echo "✅ Validating deployment..."

# Health check
HEALTH_URL="https://$ENVIRONMENT.trading-platform.example.com/health"
for i in {1..5}; do
    if curl -sf $HEALTH_URL > /dev/null; then
        echo "   ✓ Health check passed"
        break
    fi
    echo "   ⏳ Health check attempt $i/5..."
    sleep 10
done

# Get deployment status
kubectl get pods -n $ENVIRONMENT -l app=trading-backend

# =============================================================================
# Notify
# =============================================================================
echo "🎉 Deployment to $ENVIRONMENT complete!"
echo ""
echo "Endpoints:"
echo "   Health: $HEALTH_URL"
echo "   Metrics: https://$ENVIRONMENT.trading-platform.example.com/metrics"
echo "   API: https://$ENVIRONMENT.trading-platform.example.com/docs"

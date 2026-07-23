#!/usr/bin/env bash
# ==============================================================================
# infra/rollback.sh - Rollback ALB Target Group Attachments for ECS Services
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-southeast-1}"
ECS_CLUSTER="${ECS_CLUSTER:-vyomquant-cluster}"
API_SERVICE="${API_SERVICE:-vyomquant-api-service-cjema2sl}"

echo "======================================================================"
echo "[ROLLBACK] Reverting Load Balancer Attachments on ECS Services"
echo "======================================================================"
echo "[INFO] Region:  $AWS_REGION"
echo "[INFO] Cluster: $ECS_CLUSTER"
echo "[INFO] Service: $API_SERVICE"

read -p "Are you sure you want to detach Target Groups from ECS Service $API_SERVICE? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "[CANCELLED] Rollback process cancelled."
    exit 0
fi

echo "[INFO] Detaching load balancers from ECS Service $API_SERVICE..."
aws ecs update-service \
    --cluster "$ECS_CLUSTER" \
    --service "$API_SERVICE" \
    --load-balancers "[]" \
    --region "$AWS_REGION" >/dev/null

echo "[SUCCESS] Detached load balancers from ECS service $API_SERVICE."

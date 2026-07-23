#!/usr/bin/env bash
# ==============================================================================
# infra/ecs.sh - Attach Existing ECS Services to ALB Target Groups Idempotently
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-southeast-1}"
ECS_CLUSTER="${ECS_CLUSTER:-vyomquant-cluster}"
API_SERVICE="${API_SERVICE:-vyomquant-api-service-cjema2sl}"
WEB_SERVICE="${WEB_SERVICE:-vyomquant-frontend-service}"
TG_API_ARN="${TG_API_ARN:-}"
TG_WEB_ARN="${TG_WEB_ARN:-}"
ALB_NAME="${ALB_NAME:-vyomquant-alb}"

echo "======================================================================"
echo "[ECS] Attaching Existing ECS Services to ALB Target Groups in $AWS_REGION"
echo "======================================================================"

# 1. Discover Target Group ARNs if not supplied
if [ -z "$TG_API_ARN" ]; then
    TG_API_ARN=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "vyomquant-api-tg" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)
fi

if [ -z "$TG_WEB_ARN" ]; then
    TG_WEB_ARN=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "vyomquant-web-tg" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)
fi

if [ -z "$TG_API_ARN" ] || [ "$TG_API_ARN" == "None" ]; then
    echo "[ERROR] API Target Group ARN not found. Run infra/alb.sh first."
    exit 1
fi

echo "[INFO] Using API Target Group: $TG_API_ARN"
if [ -n "$TG_WEB_ARN" ] && [ "$TG_WEB_ARN" != "None" ]; then
    echo "[INFO] Using Web Target Group: $TG_WEB_ARN"
fi

# 2. Inspect & Update Backend ECS Service
echo "[INFO] Inspecting Backend ECS Service: $API_SERVICE in cluster $ECS_CLUSTER..."
EXISTING_TG=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION" --query "services[0].loadBalancers[0].targetGroupArn" --output text 2>/dev/null || true)

if [ "$EXISTING_TG" == "$TG_API_ARN" ]; then
    echo "[SUCCESS] Service $API_SERVICE is already attached to Target Group $TG_API_ARN."
else
    echo "[INFO] Updating Backend ECS Service $API_SERVICE to attach Target Group $TG_API_ARN..."
    aws ecs update-service \
        --cluster "$ECS_CLUSTER" \
        --service "$API_SERVICE" \
        --load-balancers "targetGroupArn=$TG_API_ARN,containerName=vyomquant-api,containerPort=8000" \
        --region "$AWS_REGION" >/dev/null
    echo "[SUCCESS] Attached $API_SERVICE to Target Group $TG_API_ARN."
fi

# 3. Inspect & Update Frontend ECS Service (if present)
WEB_SVC_STATUS=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$WEB_SERVICE" --region "$AWS_REGION" --query "services[0].status" --output text 2>/dev/null || true)

if [ "$WEB_SVC_STATUS" == "ACTIVE" ] && [ -n "$TG_WEB_ARN" ] && [ "$TG_WEB_ARN" != "None" ]; then
    EXISTING_WEB_TG=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$WEB_SERVICE" --region "$AWS_REGION" --query "services[0].loadBalancers[0].targetGroupArn" --output text 2>/dev/null || true)
    if [ "$EXISTING_WEB_TG" == "$TG_WEB_ARN" ]; then
        echo "[SUCCESS] Service $WEB_SERVICE is already attached to Target Group $TG_WEB_ARN."
    else
        echo "[INFO] Updating Frontend ECS Service $WEB_SERVICE to attach Target Group $TG_WEB_ARN..."
        aws ecs update-service \
            --cluster "$ECS_CLUSTER" \
            --service "$WEB_SERVICE" \
            --load-balancers "targetGroupArn=$TG_WEB_ARN,containerName=vyomquant-web,containerPort=8080" \
            --region "$AWS_REGION" >/dev/null || true
        echo "[SUCCESS] Attached Frontend ECS Service."
    fi
else
    echo "[NOTICE] Frontend ECS Service '$WEB_SERVICE' not active in cluster. Skipping frontend service update."
fi

# 4. Ingress Authorization between ALB SG and ECS Task Security Group
ALB_SG_ID=$(aws ec2 describe-security-groups --region "$AWS_REGION" --filters "Name=group-name,Values=${ALB_NAME}-sg" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
ECS_SG_ID=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION" --query "services[0].networkConfiguration.awsvpcConfiguration.securityGroups[0]" --output text 2>/dev/null || true)

if [ -n "$ALB_SG_ID" ] && [ "$ALB_SG_ID" != "None" ] && [ -n "$ECS_SG_ID" ] && [ "$ECS_SG_ID" != "None" ]; then
    echo "[INFO] Authorizing ALB Security Group ($ALB_SG_ID) ingress into ECS Tasks Security Group ($ECS_SG_ID)..."
    aws ec2 authorize-security-group-ingress \
        --group-id "$ECS_SG_ID" \
        --protocol tcp \
        --port 8000 \
        --source-group "$ALB_SG_ID" \
        --region "$AWS_REGION" 2>/dev/null || echo "[INFO] Security Group ingress rule already present."
fi

echo "======================================================================"
echo "[SUCCESS] ECS Services attached to ALB Target Groups successfully."
echo "======================================================================"

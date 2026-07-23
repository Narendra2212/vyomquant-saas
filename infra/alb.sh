#!/usr/bin/env bash
# ==============================================================================
# infra/alb.sh - Idempotent Application Load Balancer & Routing Creation
# ==============================================================================
set -euo pipefail
export MSYS_NO_PATHCONV=1

AWS_REGION="${AWS_REGION:-ap-southeast-1}"
ECS_CLUSTER="${ECS_CLUSTER:-vyomquant-cluster}"
API_SERVICE="${API_SERVICE:-vyomquant-api-service-cjema2sl}"
ALB_NAME="${ALB_NAME:-vyomquant-alb}"
TG_API_NAME="${TG_API_NAME:-vyomquant-api-tg}"
TG_WEB_NAME="${TG_WEB_NAME:-vyomquant-web-tg}"
DOMAIN_NAME="${DOMAIN_NAME:-vyomquant.in}"
VPC_ID="${VPC_ID:-}"
SUBNET_IDS="${SUBNET_IDS:-}"
CERT_ARN="${CERT_ARN:-}"

if [ -f "/c/aerora_quant_backend_updated_final1/python_portable/python.exe" ]; then
    PYTHON_BIN="/c/aerora_quant_backend_updated_final1/python_portable/python.exe"
elif command -v python3 &>/devnull; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

echo "======================================================================"
echo "[ALB] Provisioning / Reusing Application Load Balancer in $AWS_REGION"
echo "======================================================================"

# 1. Discover VPC ID from ECS Service Subnets
if [ -z "$VPC_ID" ]; then
    echo "[INFO] Discovering VPC ID from ECS Service '$API_SERVICE'..."
    SUBNET_SAMPLE=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION" --query "services[0].networkConfiguration.awsvpcConfiguration.subnets[0]" --output text 2>/dev/null || true)
    if [ -n "$SUBNET_SAMPLE" ] && [ "$SUBNET_SAMPLE" != "None" ]; then
        VPC_ID=$(aws ec2 describe-subnets --subnet-ids "$SUBNET_SAMPLE" --region "$AWS_REGION" --query "Subnets[0].VpcId" --output text 2>/dev/null || true)
    fi
fi
if [ -z "$VPC_ID" ] || [ "$VPC_ID" == "None" ]; then
    VPC_ID=$(aws ec2 describe-vpcs --region "$AWS_REGION" --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text 2>/dev/null || true)
fi
if [ -z "$VPC_ID" ] || [ "$VPC_ID" == "None" ]; then
    VPC_ID=$(aws ec2 describe-vpcs --region "$AWS_REGION" --query "Vpcs[0].VpcId" --output text 2>/dev/null || true)
fi
echo "[SUCCESS] Using VPC ID: $VPC_ID"

# 2. Discover Public Subnet IDs for ALB
if [ -z "$SUBNET_IDS" ]; then
    echo "[INFO] Discovering Public Subnets for VPC $VPC_ID..."
    SUBNET_IDS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*public*" --region "$AWS_REGION" --query "Subnets[*].SubnetId" --output text 2>/dev/null || true)
    if [ -z "$SUBNET_IDS" ] || [ "$SUBNET_IDS" == "None" ]; then
        SUBNET_IDS=$(aws ec2 describe-subnets --region "$AWS_REGION" --filters "Name=vpc-id,Values=$VPC_ID" --query "Subnets[?MapPublicIpOnLaunch==\`true\`].SubnetId" --output text 2>/dev/null || true)
    fi
    if [ -z "$SUBNET_IDS" ] || [ "$SUBNET_IDS" == "None" ]; then
        SUBNET_IDS=$(aws ec2 describe-subnets --region "$AWS_REGION" --filters "Name=vpc-id,Values=$VPC_ID" --query "Subnets[*].SubnetId" --output text | tr '\t' ' ' | cut -d' ' -f1-2)
    fi
fi
echo "[SUCCESS] Using Subnet IDs: $SUBNET_IDS"

# 3. Discover ACM Certificate ARN & Status if available
CERT_STATUS="NONE"
if [ -z "$CERT_ARN" ]; then
    EXISTING_CERTS=$(aws acm list-certificates --region "$AWS_REGION" --output json 2>/dev/null || echo "")
    if [ -n "$EXISTING_CERTS" ]; then
        CERT_ARN=$($PYTHON_BIN -c "
import json
data = json.loads('''$EXISTING_CERTS''')
for cert in data.get('CertificateSummaryList', []):
    if cert.get('DomainName') == '$DOMAIN_NAME':
        print(cert.get('CertificateArn'))
        break
" 2>/dev/null || true)
    fi
fi

if [ -n "$CERT_ARN" ] && [ "$CERT_ARN" != "None" ]; then
    echo "[SUCCESS] Using ACM Certificate ARN: $CERT_ARN"
    CERT_DESC=$(aws acm describe-certificate --certificate-arn "$CERT_ARN" --region "$AWS_REGION" --output json 2>/dev/null || echo "")
    if [ -n "$CERT_DESC" ]; then
        CERT_STATUS=$($PYTHON_BIN -c "
import json
data = json.loads('''$CERT_DESC''')
print(data.get('Certificate', {}).get('Status', 'NONE'))
" 2>/dev/null || echo "NONE")
    fi
    echo "[INFO] ACM Certificate Status: $CERT_STATUS"
fi

# 4. Create or Reuse ALB Security Group
ALB_SG_NAME="${ALB_NAME}-sg"
ALB_SG_ID=$(aws ec2 describe-security-groups --region "$AWS_REGION" --filters "Name=group-name,Values=$ALB_SG_NAME" "Name=vpc-id,Values=$VPC_ID" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)

if [ -z "$ALB_SG_ID" ] || [ "$ALB_SG_ID" == "None" ]; then
    echo "[INFO] Creating ALB Security Group $ALB_SG_NAME..."
    ALB_SG_ID=$(aws ec2 create-security-group \
        --group-name "$ALB_SG_NAME" \
        --description "Security Group for VyomQuant ALB" \
        --vpc-id "$VPC_ID" \
        --region "$AWS_REGION" \
        --query "GroupId" --output text)
    
    aws ec2 authorize-security-group-ingress --group-id "$ALB_SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 --region "$AWS_REGION" >/dev/null
    aws ec2 authorize-security-group-ingress --group-id "$ALB_SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 --region "$AWS_REGION" >/dev/null
    echo "[SUCCESS] Created ALB Security Group: $ALB_SG_ID"
else
    echo "[INFO] Reusing ALB Security Group: $ALB_SG_ID"
fi

# 5. Create or Reuse Application Load Balancer
ALB_ARN=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --names "$ALB_NAME" --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || true)

if [ -z "$ALB_ARN" ] || [ "$ALB_ARN" == "None" ]; then
    echo "[INFO] Creating Application Load Balancer $ALB_NAME..."
    SUBNET_ARRAY=($SUBNET_IDS)
    ALB_ARN=$(aws elbv2 create-load-balancer \
        --name "$ALB_NAME" \
        --subnets "${SUBNET_ARRAY[@]}" \
        --security-groups "$ALB_SG_ID" \
        --scheme internet-facing \
        --type application \
        --region "$AWS_REGION" \
        --query "LoadBalancers[0].LoadBalancerArn" --output text)
    echo "[SUCCESS] Created ALB ARN: $ALB_ARN"
else
    echo "[INFO] Reusing existing ALB ARN: $ALB_ARN"
fi

ALB_DNS_NAME=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --load-balancer-arns "$ALB_ARN" --query "LoadBalancers[0].DNSName" --output text)
echo "[SUCCESS] ALB DNS Name: $ALB_DNS_NAME"

# 6. Create or Reuse Backend Target Group (FastAPI Port 8000, /health)
TG_API_ARN=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "$TG_API_NAME" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)
if [ -z "$TG_API_ARN" ] || [ "$TG_API_ARN" == "None" ]; then
    echo "[INFO] Creating Backend Target Group $TG_API_NAME (Port 8000, /health)..."
    TG_API_ARN=$(aws elbv2 create-target-group \
        --name "$TG_API_NAME" \
        --protocol HTTP \
        --port 8000 \
        --vpc-id "$VPC_ID" \
        --target-type ip \
        --health-check-protocol HTTP \
        --health-check-path "/health" \
        --health-check-interval-seconds 30 \
        --health-check-timeout-seconds 5 \
        --healthy-threshold-count 2 \
        --unhealthy-threshold-count 3 \
        --matcher "HttpCode=200" \
        --region "$AWS_REGION" \
        --query "TargetGroups[0].TargetGroupArn" --output text)
    echo "[SUCCESS] Created Backend Target Group: $TG_API_ARN"
else
    echo "[INFO] Reusing Backend Target Group: $TG_API_ARN"
    aws elbv2 modify-target-group --target-group-arn "$TG_API_ARN" --health-check-path "/health" --region "$AWS_REGION" >/dev/null || true
fi

# 7. Create or Reuse Frontend Target Group (Port 3000, /)
TG_WEB_ARN=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "$TG_WEB_NAME" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || true)
if [ -z "$TG_WEB_ARN" ] || [ "$TG_WEB_ARN" == "None" ]; then
    echo "[INFO] Creating Frontend Target Group $TG_WEB_NAME (Port 3000, /)..."
    TG_WEB_ARN=$(aws elbv2 create-target-group \
        --name "$TG_WEB_NAME" \
        --protocol HTTP \
        --port 3000 \
        --vpc-id "$VPC_ID" \
        --target-type ip \
        --health-check-protocol HTTP \
        --health-check-path "/" \
        --health-check-interval-seconds 30 \
        --health-check-timeout-seconds 5 \
        --healthy-threshold-count 2 \
        --unhealthy-threshold-count 3 \
        --matcher "HttpCode=200-399" \
        --region "$AWS_REGION" \
        --query "TargetGroups[0].TargetGroupArn" --output text)
    echo "[SUCCESS] Created Frontend Target Group: $TG_WEB_ARN"
else
    echo "[INFO] Reusing Frontend Target Group: $TG_WEB_ARN"
    aws elbv2 modify-target-group --target-group-arn "$TG_WEB_ARN" --health-check-path "/" --region "$AWS_REGION" >/dev/null || true
fi

# 8. Create or Reuse HTTP Listener (Port 80)
HTTP_LISTENER_ARN=$(aws elbv2 describe-listeners --region "$AWS_REGION" --load-balancer-arn "$ALB_ARN" --query "Listeners[?Port==\`80\`].ListenerArn" --output text 2>/dev/null || true)

if [ -z "$HTTP_LISTENER_ARN" ] || [ "$HTTP_LISTENER_ARN" == "None" ]; then
    echo "[INFO] Creating HTTP Listener (Port 80)..."
    HTTP_LISTENER_ARN=$(aws elbv2 create-listener \
        --load-balancer-arn "$ALB_ARN" \
        --protocol HTTP \
        --port 80 \
        --default-actions "Type=forward,TargetGroupArn=$TG_API_ARN" \
        --region "$AWS_REGION" \
        --query "Listeners[0].ListenerArn" --output text)
    echo "[SUCCESS] Created HTTP Listener: $HTTP_LISTENER_ARN"
else
    echo "[INFO] Reusing HTTP Listener: $HTTP_LISTENER_ARN"
fi

# 9. Create or Reuse HTTPS Listener (Port 443) if Certificate is ISSUED
if [ "$CERT_STATUS" == "ISSUED" ]; then
    HTTPS_LISTENER_ARN=$(aws elbv2 describe-listeners --region "$AWS_REGION" --load-balancer-arn "$ALB_ARN" --query "Listeners[?Port==\`443\`].ListenerArn" --output text 2>/dev/null || true)

    if [ -z "$HTTPS_LISTENER_ARN" ] || [ "$HTTPS_LISTENER_ARN" == "None" ]; then
        echo "[INFO] Creating HTTPS Listener (Port 443) using ISSUED ACM Certificate..."
        HTTPS_LISTENER_ARN=$(aws elbv2 create-listener \
            --load-balancer-arn "$ALB_ARN" \
            --protocol HTTPS \
            --port 443 \
            --certificates CertificateArn="$CERT_ARN" \
            --default-actions "Type=forward,TargetGroupArn=$TG_WEB_ARN" \
            --region "$AWS_REGION" \
            --query "Listeners[0].ListenerArn" --output text)
        echo "[SUCCESS] Created HTTPS Listener: $HTTPS_LISTENER_ARN"
    fi

    # Host-based routing rules on HTTPS listener
    echo "[INFO] Checking Host-based Routing Rules on HTTPS Listener..."
    
    # Rule 1: api.vyomquant.in -> TG_API
    RULE_API_EXISTS=$(aws elbv2 describe-rules --listener-arn "$HTTPS_LISTENER_ARN" --region "$AWS_REGION" --query "Rules[?Conditions[0].HostHeaderConfig.Values[0]==\`api.vyomquant.in\`].RuleArn" --output text 2>/dev/null || true)
    if [ -z "$RULE_API_EXISTS" ] || [ "$RULE_API_EXISTS" == "None" ]; then
        aws elbv2 create-rule \
            --listener-arn "$HTTPS_LISTENER_ARN" \
            --priority 10 \
            --conditions "Field=host-header,HostHeaderConfig={Values=['api.vyomquant.in']}" \
            --actions "Type=forward,TargetGroupArn=$TG_API_ARN" \
            --region "$AWS_REGION" >/dev/null
        echo "[SUCCESS] Added Host-based Rule: api.vyomquant.in -> Backend TG"
    else
        echo "[INFO] Rule for api.vyomquant.in already exists"
    fi

    # Rule 2: vyomquant.in / www.vyomquant.in -> TG_WEB
    RULE_WEB_EXISTS=$(aws elbv2 describe-rules --listener-arn "$HTTPS_LISTENER_ARN" --region "$AWS_REGION" --query "Rules[?Conditions[0].HostHeaderConfig.Values[0]==\`vyomquant.in\`].RuleArn" --output text 2>/dev/null || true)
    if [ -z "$RULE_WEB_EXISTS" ] || [ "$RULE_WEB_EXISTS" == "None" ]; then
        aws elbv2 create-rule \
            --listener-arn "$HTTPS_LISTENER_ARN" \
            --priority 20 \
            --conditions "Field=host-header,HostHeaderConfig={Values=['vyomquant.in','www.vyomquant.in']}" \
            --actions "Type=forward,TargetGroupArn=$TG_WEB_ARN" \
            --region "$AWS_REGION" >/dev/null
        echo "[SUCCESS] Added Host-based Rule: vyomquant.in, www.vyomquant.in -> Frontend TG"
    else
        echo "[INFO] Rule for vyomquant.in already exists"
    fi
else
    echo "[NOTICE] ACM Certificate status is '$CERT_STATUS'. HTTPS Listener (Port 443) will be attached automatically once GoDaddy DNS validation completes and certificate becomes ISSUED."
fi

echo "----------------------------------------------------------------------"
echo "ALB_ARN=$ALB_ARN"
echo "ALB_DNS_NAME=$ALB_DNS_NAME"
echo "TG_API_ARN=$TG_API_ARN"
echo "TG_WEB_ARN=$TG_WEB_ARN"

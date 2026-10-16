#!/usr/bin/env bash
# ==============================================================================
# infra/deploy.sh - Master Orchestrator & Pre/Post-Flight Verification Engine
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AWS_REGION="${AWS_REGION:-ap-southeast-1}"
export ECS_CLUSTER="${ECS_CLUSTER:-vyomquant-cluster}"
export API_SERVICE="${API_SERVICE:-vyomquant-api-service-cjema2sl}"
export DOMAIN_NAME="${DOMAIN_NAME:-vyomquant.in}"
EXPECTED_ACCOUNT="273709947018"

if [ -f "/c/aerora_quant_backend_updated_final1/python_portable/python.exe" ]; then
    PYTHON_BIN="/c/aerora_quant_backend_updated_final1/python_portable/python.exe"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

echo "======================================================================"
echo "    VyomQuant Production AWS Infrastructure Orchestration & Audit    "
echo "======================================================================"
echo "[INFO] Region:       $AWS_REGION"
echo "[INFO] Cluster:      $ECS_CLUSTER"
echo "[INFO] API Service:  $API_SERVICE"
echo "[INFO] Domain:       $DOMAIN_NAME"
echo "======================================================================"

# ------------------------------------------------------------------------------
# 1. PRE-FLIGHT VALIDATION GATES
# ------------------------------------------------------------------------------
echo ""
echo ">>> STEP 0: Running Pre-Flight Validation Gates..."

# Gate 1: AWS CLI installed
if ! command -v aws &>/dev/null; then
    echo "[FAIL] 'aws' CLI is not installed or not in PATH."
    exit 1
fi
AWS_VER=$(aws --version 2>&1)
echo "[PASS] AWS CLI Available: $AWS_VER"

# Gate 2: Python available
if [ -z "$PYTHON_BIN" ]; then
    echo "[FAIL] Python is required for JSON parsing and verification."
    exit 1
fi
echo "[PASS] Python Available: $($PYTHON_BIN --version 2>&1)"

# Gate 3: AWS Authentication & Identity
CALLER_IDENTITY=$(aws sts get-caller-identity --region "$AWS_REGION" --output json 2>/dev/null || true)
if [ -z "$CALLER_IDENTITY" ]; then
    echo "[FAIL] AWS Authentication failed. Invalid AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY."
    exit 1
fi

ACCOUNT_ID=$($PYTHON_BIN -c "import json; print(json.loads('''$CALLER_IDENTITY''').get('Account',''))" 2>/dev/null || echo "")
echo "[PASS] Authenticated as AWS Account: $ACCOUNT_ID"

if [ "$ACCOUNT_ID" != "$EXPECTED_ACCOUNT" ]; then
    echo "[WARNING] Current AWS Account ($ACCOUNT_ID) differs from expected production account ($EXPECTED_ACCOUNT)."
fi

# Gate 4: IAM Permissions Verification
echo "[INFO] Verifying required IAM permissions..."
aws acm list-certificates --region "$AWS_REGION" --max-items 1 >/dev/null 2>&1 || { echo "[FAIL] Missing acm:ListCertificates permission"; exit 1; }
aws elbv2 describe-load-balancers --region "$AWS_REGION" --max-items 1 >/dev/null 2>&1 || { echo "[FAIL] Missing elbv2:DescribeLoadBalancers permission"; exit 1; }
aws ecs describe-clusters --clusters "$ECS_CLUSTER" --region "$AWS_REGION" >/dev/null 2>&1 || { echo "[FAIL] Missing ecs:DescribeClusters permission"; exit 1; }
aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION" >/dev/null 2>&1 || { echo "[FAIL] Missing ecs:DescribeServices permission"; exit 1; }

echo "[PASS] All Pre-Flight Validation Gates Passed."

# ------------------------------------------------------------------------------
# 2. INFRASTRUCTURE PROVISIONING
# ------------------------------------------------------------------------------
echo ""
echo ">>> STEP 1: ACM Certificate Provisioning & DNS Record Extraction..."
bash "$SCRIPT_DIR/acm.sh"

echo ""
echo ">>> STEP 2: Application Load Balancer & Routing Creation..."
bash "$SCRIPT_DIR/alb.sh"

echo ""
echo ">>> STEP 3: ECS Service Target Group Attachment..."
bash "$SCRIPT_DIR/ecs.sh"

# ------------------------------------------------------------------------------
# 3. POST-DEPLOYMENT VERIFICATION ENGINE
# ------------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo ">>> STEP 4: Running Post-Deployment Verification Engine..."
echo "======================================================================"

# OPS-02: every check below used to end in a hardcoded `echo "[SUMMARY] Readiness Score:
# 100%"` -- printed unconditionally, after every check, whether it PASSed or WARNed. A
# script that always reports 100% is not a verification engine; it is theater. Each check
# now increments CHECKS_TOTAL and, on anything short of PASS, CHECKS_WARNED, so the
# summary's score is actually computed from what ran above it.
CHECKS_TOTAL=0
CHECKS_WARNED=0

# Check 1: ALB State
CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
ALB_STATE=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --names "vyomquant-alb" --query "LoadBalancers[0].State.Code" --output text 2>/dev/null || echo "UNKNOWN")
if [ "$ALB_STATE" == "active" ]; then
    echo "[PASS] ALB State: $ALB_STATE"
else
    echo "[WARN] ALB State is: $ALB_STATE"
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
fi

# Check 2: Target Group Health
CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
TG_API_ARN=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "vyomquant-api-tg" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "")
TG_HEALTH=""
if [ -n "$TG_API_ARN" ]; then
    TG_HEALTH=$(aws elbv2 describe-target-health --target-group-arn "$TG_API_ARN" --region "$AWS_REGION" --query "TargetHealthDescriptions[*].TargetHealth.State" --output text 2>/dev/null || echo "NONE")
fi
if [ -n "$TG_HEALTH" ] && [ "$TG_HEALTH" != "NONE" ] && ! grep -qv "healthy" <<< "$TG_HEALTH"; then
    echo "[PASS] Backend Target Health: $TG_HEALTH"
else
    echo "[WARN] Backend Target Health: ${TG_HEALTH:-No Targets Registered Yet}"
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
fi

# Check 3: ECS Service Stability
CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
SERVICE_DESC=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION" --output json)
DESIRED_COUNT=$($PYTHON_BIN -c "import json; print(json.loads('''$SERVICE_DESC''')['services'][0]['desiredCount'])" 2>/dev/null || echo "0")
RUNNING_COUNT=$($PYTHON_BIN -c "import json; print(json.loads('''$SERVICE_DESC''')['services'][0]['runningCount'])" 2>/dev/null || echo "0")

if [ "$RUNNING_COUNT" -ge "$DESIRED_COUNT" ] && [ "$DESIRED_COUNT" -gt 0 ]; then
    echo "[PASS] ECS Service Stability: $RUNNING_COUNT/$DESIRED_COUNT Tasks Running"
else
    echo "[WARN] ECS Service Status: $RUNNING_COUNT/$DESIRED_COUNT Tasks Running"
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
fi

# Check 4: Endpoint Health (/health/ready) test -- INF-01: /health always answers 200
# regardless of dependency state, which is exactly the kind of check this gate exists to
# avoid being fooled by; /health/ready returns a real 503 when the database or Redis is
# unreachable.
CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
ALB_DNS=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --names "vyomquant-alb" --query "LoadBalancers[0].DNSName" --output text 2>/dev/null || echo "")
HTTP_CODE="000"
if [ -n "$ALB_DNS" ] && [ "$ALB_DNS" != "None" ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://$ALB_DNS/health/ready" 2>/dev/null || echo "000")
fi
if [ "$HTTP_CODE" == "200" ]; then
    echo "[PASS] ALB Direct /health/ready Check (HTTP Code): $HTTP_CODE"
else
    echo "[WARN] ALB Direct /health/ready Check (HTTP Code): $HTTP_CODE"
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
fi

READINESS_SCORE=$(( (CHECKS_TOTAL - CHECKS_WARNED) * 100 / CHECKS_TOTAL ))

echo ""
echo "======================================================================"
if [ "$CHECKS_WARNED" -eq 0 ]; then
    echo "                   DEPLOYMENT COMPLETE & VERIFIED                     "
else
    echo "          DEPLOYMENT COMPLETE -- $CHECKS_WARNED OF $CHECKS_TOTAL CHECK(S) WARNED          "
fi
echo "======================================================================"
echo "[SUMMARY] ALB DNS Name:     ${ALB_DNS:-N/A}"
echo "[SUMMARY] ECS Cluster:      $ECS_CLUSTER"
echo "[SUMMARY] ECS Service:      $API_SERVICE"
echo "[SUMMARY] Backend TG:       vyomquant-api-tg (Port 8000 -> /health/ready)"
echo "[SUMMARY] Readiness Score:  ${READINESS_SCORE}% ($((CHECKS_TOTAL - CHECKS_WARNED))/$CHECKS_TOTAL checks passed)"
echo "======================================================================"

if [ "$CHECKS_WARNED" -gt 0 ]; then
    exit 1
fi

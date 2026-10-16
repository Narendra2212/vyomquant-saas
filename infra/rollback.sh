#!/usr/bin/env bash
# ==============================================================================
# infra/rollback.sh - Roll the API ECS service back to its previous task
#                      definition revision
# ==============================================================================
#
# OPS-02: this used to run `aws ecs update-service --load-balancers "[]"` -- which
# DETACHES the ALB from the service entirely. That is not a rollback, it is an outage:
# every existing target is deregistered and nothing new is attached, so the API becomes
# unreachable through the load balancer regardless of which task definition is running,
# and running it a second time to "undo" it does nothing (there is no load balancer left
# to detach, and nothing re-attaches one).
#
# A real rollback keeps the load balancer attached throughout and only changes which
# task definition the service runs -- exactly what scripts/ecs_deploy_and_diagnose.py's
# own rollback_to_task_def() already does automatically when a forward deploy fails
# (03-deploy.yml's self-healing path: `aws ecs update-service --task-definition <arn>`,
# deliberately without --load-balancers or --force-new-deployment, the latter because it
# interferes with the native ECS deployment circuit breaker), and exactly what
# infra/rollback/taskdef-rollback-pointer.txt already recorded by hand for one specific
# past deploy (deployed=vyomquant-api:146, rollback_to=vyomquant-api:145). This script
# generalises that: it reads the CURRENTLY RUNNING task definition from the live service,
# computes the previous revision, and asks for confirmation before switching to it.
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-southeast-1}"
ECS_CLUSTER="${ECS_CLUSTER:-vyomquant-cluster}"
API_SERVICE="${API_SERVICE:-vyomquant-api-service-cjema2sl}"

if command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

echo "======================================================================"
echo "[ROLLBACK] Reverting ECS service $API_SERVICE to its previous task definition"
echo "======================================================================"
echo "[INFO] Region:  $AWS_REGION"
echo "[INFO] Cluster: $ECS_CLUSTER"
echo "[INFO] Service: $API_SERVICE"

SERVICE_DESC=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION" --output json)

CURRENT_TASK_DEF=$($PYTHON_BIN -c "
import json, sys
data = json.loads('''$SERVICE_DESC''')
services = data.get('services') or []
if not services:
    sys.exit(1)
print(services[0]['taskDefinition'])
" 2>/dev/null || echo "")

if [ -z "$CURRENT_TASK_DEF" ]; then
    echo "[FAIL] Could not determine the current task definition for $API_SERVICE. Is the service name/cluster/region correct?"
    exit 1
fi

# arn:aws:ecs:<region>:<account>:task-definition/<family>:<revision>  ->  <family> / <revision>
FAMILY_REVISION="${CURRENT_TASK_DEF##*/}"
FAMILY="${FAMILY_REVISION%:*}"
CURRENT_REVISION="${FAMILY_REVISION##*:}"

if ! [[ "$CURRENT_REVISION" =~ ^[0-9]+$ ]]; then
    echo "[FAIL] Could not parse a revision number out of task definition '$CURRENT_TASK_DEF'."
    exit 1
fi

if [ "$CURRENT_REVISION" -le 1 ]; then
    echo "[FAIL] $FAMILY is at revision $CURRENT_REVISION; there is no earlier revision to roll back to."
    exit 1
fi

# An explicit target (full ARN, or "family:revision") overrides the computed
# previous-revision default -- useful to roll back more than one step.
ROLLBACK_TO="${1:-${FAMILY}:$((CURRENT_REVISION - 1))}"

echo "[INFO] Currently running: $FAMILY:$CURRENT_REVISION"
echo "[INFO] Rollback target:   $ROLLBACK_TO"

read -p "Roll $API_SERVICE back from $FAMILY:$CURRENT_REVISION to $ROLLBACK_TO? The load balancer stays attached throughout. (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "[CANCELLED] Rollback cancelled."
    exit 0
fi

echo "[INFO] Switching $API_SERVICE to $ROLLBACK_TO (load balancer attachment untouched)..."
aws ecs update-service \
    --cluster "$ECS_CLUSTER" \
    --service "$API_SERVICE" \
    --task-definition "$ROLLBACK_TO" \
    --region "$AWS_REGION" >/dev/null

echo "[INFO] Waiting for $API_SERVICE to stabilise on $ROLLBACK_TO (this can take several minutes)..."
if aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services "$API_SERVICE" --region "$AWS_REGION"; then
    echo "[SUCCESS] $API_SERVICE is stable on $ROLLBACK_TO. The load balancer was never detached."
else
    echo "[WARN] $API_SERVICE did not report stable within aws ecs wait's own timeout window."
    echo "       This does NOT necessarily mean the rollback failed -- check directly:"
    echo "       aws ecs describe-services --cluster $ECS_CLUSTER --services $API_SERVICE --region $AWS_REGION"
fi

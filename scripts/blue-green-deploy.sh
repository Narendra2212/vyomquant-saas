#!/bin/bash
# STEP 8.11: Blue-Green Deployment Script
# 
# Manages zero-downtime deployments using blue-green strategy
# 
# Usage:
#   ./scripts/blue-green-deploy.sh deploy <image-tag>    # Deploy new version (green)
#   ./scripts/blue-green-deploy.sh switch               # Switch traffic to green
#   ./scripts/blue-green-deploy.sh rollback             # Rollback to blue
#   ./scripts/blue-green-deploy.sh status               # Show current status
#   ./scripts/blue-green-deploy.sh promote              # Promote green to blue

set -e

NAMESPACE="${NAMESPACE:-production}"
ACTIVE_SERVICE="trading-backend-active"
BLUE_DEPLOYMENT="trading-backend-blue"
GREEN_DEPLOYMENT="trading-backend-green"
BLUE_PREVIEW="trading-backend-blue-preview"
GREEN_PREVIEW="trading-backend-green-preview"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

get_active_color() {
    kubectl get service $ACTIVE_SERVICE -n $NAMESPACE -o jsonpath='{.spec.selector.color}' 2>/dev/null || echo "unknown"
}

get_green_image() {
    kubectl get deployment $GREEN_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "unknown"
}

get_blue_image() {
    kubectl get deployment $BLUE_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "unknown"
}

# =============================================================================
# Deploy New Version (Green)
# =============================================================================

deploy_green() {
    local IMAGE_TAG=$1
    
    if [ -z "$IMAGE_TAG" ]; then
        log_error "Image tag required. Usage: deploy <image-tag>"
        exit 1
    fi
    
    log_info "Starting Blue-Green Deployment"
    log_info "New image: $IMAGE_TAG"
    
    # Check current state
    local ACTIVE_COLOR=$(get_active_color)
    log_info "Current active color: $ACTIVE_COLOR"
    
    if [ "$ACTIVE_COLOR" == "green" ]; then
        log_warning "Green is currently active. Will update green in place."
    fi
    
    # Update green deployment with new image
    log_info "Updating green deployment with image: $IMAGE_TAG"
    kubectl set image deployment/$GREEN_DEPLOYMENT \
        backend=$IMAGE_TAG \
        -n $NAMESPACE
    
    # Scale green to match blue
    local BLUE_REPLICAS=$(kubectl get deployment $BLUE_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.replicas}')
    log_info "Scaling green to $BLUE_REPLICAS replicas"
    kubectl scale deployment/$GREEN_DEPLOYMENT --replicas=$BLUE_REPLICAS -n $NAMESPACE
    
    # Wait for green to be ready
    log_info "Waiting for green deployment to be ready..."
    kubectl rollout status deployment/$GREEN_DEPLOYMENT -n $NAMESPACE --timeout=300s
    
    # Validate green deployment
    log_info "Validating green deployment..."
    validate_green
    
    log_success "Green deployment ready for switch"
    log_info "Run './scripts/blue-green-deploy.sh switch' to cut over traffic"
    
    show_status
}

# =============================================================================
# Validate Green Deployment
# =============================================================================

validate_green() {
    local PREVIEW_URL="http://$GREEN_PREVIEW.$NAMESPACE.svc.cluster.local"
    local MAX_RETRIES=10
    local RETRY=0
    
    log_info "Running health checks on green..."
    
    # Check pods are ready
    kubectl wait --for=condition=ready pod \
        -l version=green -n $NAMESPACE \
        --timeout=120s
    
    # Port-forward for validation (if needed)
    # kubectl port-forward svc/$GREEN_PREVIEW 8080:80 -n $NAMESPACE &
    # local PF_PID=$!
    # sleep 2
    
    # Health checks
    while [ $RETRY -lt $MAX_RETRIES ]; do
        if kubectl exec -n $NAMESPACE deploy/$GREEN_DEPLOYMENT -- \
            curl -sf http://localhost:8000/health > /dev/null 2>&1; then
            log_success "Health check passed ($((RETRY+1))/$MAX_RETRIES)"
            RETRY=$((RETRY+1))
            sleep 2
        else
            log_error "Health check failed"
            # kill $PF_PID 2>/dev/null || true
            exit 1
        fi
    done
    
    # kill $PF_PID 2>/dev/null || true
    
    # Check readiness
    kubectl exec -n $NAMESPACE deploy/$GREEN_DEPLOYMENT -- \
        curl -sf http://localhost:8000/health/ready > /dev/null 2>&1 || {
        log_error "Readiness check failed"
        exit 1
    }
    
    log_success "Green validation complete"
}

# =============================================================================
# Switch Traffic to Green
# =============================================================================

switch_traffic() {
    local ACTIVE_COLOR=$(get_active_color)
    
    if [ "$ACTIVE_COLOR" == "green" ]; then
        log_warning "Green is already active. Nothing to switch."
        exit 0
    fi
    
    log_info "Current active: BLUE"
    log_info "Switching traffic to GREEN..."
    
    # Update service selector to green
    kubectl patch service $ACTIVE_SERVICE -n $NAMESPACE \
        --type='json' \
        -p='[{"op": "replace", "path": "/spec/selector/color", "value": "green"}]'
    
    # Verify switch
    sleep 2
    local NEW_COLOR=$(get_active_color)
    
    if [ "$NEW_COLOR" == "green" ]; then
        log_success "✅ Traffic switched to GREEN"
        
        # Update ingress if needed
        # kubectl annotate ingress trading-backend-ingress \
        #   nginx.ingress.kubernetes.io/canary-weight="0" -n $NAMESPACE
        
        # Send notification
        send_notification "🚀 Traffic switched to GREEN in $NAMESPACE"
    else
        log_error "❌ Switch failed. Active color is still: $NEW_COLOR"
        exit 1
    fi
    
    show_status
}

# =============================================================================
# Rollback to Blue
# =============================================================================

rollback() {
    local ACTIVE_COLOR=$(get_active_color)
    
    if [ "$ACTIVE_COLOR" == "blue" ]; then
        log_warning "Blue is already active. Nothing to rollback."
        exit 0
    fi
    
    log_info "🚨 EMERGENCY ROLLBACK: Switching back to BLUE"
    
    # Immediate switch back to blue
    kubectl patch service $ACTIVE_SERVICE -n $NAMESPACE \
        --type='json' \
        -p='[{"op": "replace", "path": "/spec/selector/color", "value": "blue"}]'
    
    sleep 2
    
    local NEW_COLOR=$(get_active_color)
    if [ "$NEW_COLOR" == "blue" ]; then
        log_success "✅ Rollback complete - Traffic now on BLUE"
        send_notification "🚨 ROLLBACK COMPLETE: Reverted to BLUE in $NAMESPACE"
    else
        log_error "❌ Rollback failed!"
        exit 1
    fi
    
    show_status
}

# =============================================================================
# Promote Green to Blue (After successful deployment)
# =============================================================================

promote() {
    local ACTIVE_COLOR=$(get_active_color)
    local GREEN_IMAGE=$(get_green_image)
    
    if [ "$ACTIVE_COLOR" != "green" ]; then
        log_error "Green is not active. Switch to green first."
        exit 1
    fi
    
    log_info "Promoting GREEN to BLUE (stable version)"
    log_info "Image: $GREEN_IMAGE"
    
    # Update blue deployment to match green
    kubectl set image deployment/$BLUE_DEPLOYMENT \
        backend=$GREEN_IMAGE \
        -n $NAMESPACE
    
    # Scale blue to match green
    local GREEN_REPLICAS=$(kubectl get deployment $GREEN_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.replicas}')
    kubectl scale deployment/$BLUE_DEPLOYMENT --replicas=$GREEN_REPLICAS -n $NAMESPACE
    
    # Wait for blue to be ready
    log_info "Waiting for blue deployment to update..."
    kubectl rollout status deployment/$BLUE_DEPLOYMENT -n $NAMESPACE --timeout=300s
    
    # Switch traffic back to blue (now updated)
    kubectl patch service $ACTIVE_SERVICE -n $NAMESPACE \
        --type='json' \
        -p='[{"op": "replace", "path": "/spec/selector/color", "value": "blue"}]'
    
    # Scale green down
    log_info "Scaling green down to 0"
    kubectl scale deployment/$GREEN_DEPLOYMENT --replicas=0 -n $NAMESPACE
    
    log_success "✅ Promotion complete"
    log_info "Blue is now running the new version"
    log_info "Green is scaled down and ready for next deployment"
    
    send_notification "✅ Deployment promoted: GREEN → BLUE in $NAMESPACE"
    
    show_status
}

# =============================================================================
# Show Current Status
# =============================================================================

show_status() {
    echo ""
    echo "========================================"
    echo "    BLUE-GREEN DEPLOYMENT STATUS"
    echo "========================================"
    echo ""
    
    local ACTIVE_COLOR=$(get_active_color)
    local BLUE_IMAGE=$(get_blue_image)
    local GREEN_IMAGE=$(get_green_image)
    
    local BLUE_REPLICAS=$(kubectl get deployment $BLUE_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.replicas}')
    local GREEN_REPLICAS=$(kubectl get deployment $GREEN_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.spec.replicas}')
    
    local BLUE_READY=$(kubectl get deployment $BLUE_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.status.readyReplicas}')
    local GREEN_READY=$(kubectl get deployment $GREEN_DEPLOYMENT -n $NAMESPACE -o jsonpath='{.status.readyReplicas}')
    
    echo -e "Active Traffic: ${GREEN}$ACTIVE_COLOR${NC}"
    echo ""
    echo "BLUE Deployment:"
    echo "  Replicas: $BLUE_READY/$BLUE_REPLICAS ready"
    echo "  Image: $BLUE_IMAGE"
    echo ""
    echo "GREEN Deployment:"
    echo "  Replicas: $GREEN_READY/$GREEN_REPLICAS ready"
    echo "  Image: $GREEN_IMAGE"
    echo ""
    
    # Show pod status
    echo "Pod Status:"
    kubectl get pods -n $NAMESPACE -l app=trading-backend --show-labels
    
    echo ""
    echo "========================================"
}

# =============================================================================
# Send Notification
# =============================================================================

send_notification() {
    local MESSAGE=$1
    
    # Slack notification (if webhook configured)
    if [ -n "$SLACK_WEBHOOK_URL" ]; then
        curl -s -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"$MESSAGE\"}" \
            "$SLACK_WEBHOOK_URL" > /dev/null || true
    fi
    
    # Log message
    echo "[NOTIFICATION] $MESSAGE"
}

# =============================================================================
# Main Command Handler
# =============================================================================

case "${1:-}" in
    deploy)
        deploy_green "$2"
        ;;
    switch)
        switch_traffic
        ;;
    rollback)
        rollback
        ;;
    status)
        show_status
        ;;
    promote)
        promote
        ;;
    *)
        echo "STEP 8.11: Blue-Green Deployment Script"
        echo ""
        echo "Usage: $0 <command> [options]"
        echo ""
        echo "Commands:"
        echo "  deploy <image-tag>  Deploy new version to green"
        echo "  switch              Switch traffic to green"
        echo "  rollback            Rollback to blue (emergency)"
        echo "  status              Show current status"
        echo "  promote             Promote green to blue (post-deploy)"
        echo ""
        echo "Examples:"
        echo "  $0 deploy ghcr.io/trading-platform/backend:v2.1.0"
        echo "  $0 switch"
        echo "  $0 rollback"
        echo ""
        echo "Environment Variables:"
        echo "  NAMESPACE           Kubernetes namespace (default: production)"
        echo "  SLACK_WEBHOOK_URL   Slack webhook for notifications"
        exit 1
        ;;
esac

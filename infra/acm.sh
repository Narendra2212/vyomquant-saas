#!/usr/bin/env bash
# ==============================================================================
# infra/acm.sh - Idempotent AWS ACM Certificate Provisioning & DNS Validation
# ==============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-southeast-1}"
DOMAIN_NAME="${DOMAIN_NAME:-vyomquant.in}"
SAN_DOMAINS=("www.vyomquant.in" "api.vyomquant.in")

if [ -f "/c/aerora_quant_backend_updated_final1/python_portable/python.exe" ]; then
    PYTHON_BIN="/c/aerora_quant_backend_updated_final1/python_portable/python.exe"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
else
    PYTHON_BIN="python"
fi

echo "======================================================================"
echo "[ACM] Provisioning / Reusing ACM Certificate for $DOMAIN_NAME in $AWS_REGION"
echo "======================================================================"

# Check AWS authentication
if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "[ERROR] Invalid AWS credentials or unauthenticated session."
    exit 1
fi

CERT_ARN=""

# 1. Search for existing ACM certificate covering DOMAIN_NAME
EXISTING_CERTS=$(aws acm list-certificates --region "$AWS_REGION" --output json 2>/dev/null || echo "")

if [ -n "$EXISTING_CERTS" ]; then
    EXISTING_ARN=$($PYTHON_BIN -c "
import json, sys
data = json.loads('''$EXISTING_CERTS''')
for cert in data.get('CertificateSummaryList', []):
    if cert.get('DomainName') == '$DOMAIN_NAME':
        print(cert.get('CertificateArn'))
        sys.exit(0)
" 2>/dev/null || true)

    if [ -n "$EXISTING_ARN" ] && [ "$EXISTING_ARN" != "None" ]; then
        echo "[INFO] Reusing existing ACM certificate: $EXISTING_ARN"
        CERT_ARN="$EXISTING_ARN"
    fi
fi

if [ -z "$CERT_ARN" ]; then
    echo "[INFO] Requesting new ACM certificate for $DOMAIN_NAME..."
    CERT_ARN=$(aws acm request-certificate \
        --domain-name "$DOMAIN_NAME" \
        --subject-alternative-names "${SAN_DOMAINS[@]}" \
        --validation-method DNS \
        --region "$AWS_REGION" \
        --query "CertificateArn" \
        --output text)
    echo "[SUCCESS] Requested ACM Certificate ARN: $CERT_ARN"
fi

# 2. Retrieve Status & DNS Validation Records
CERT_DESC=$(aws acm describe-certificate --certificate-arn "$CERT_ARN" --region "$AWS_REGION" --output json)

STATUS=$($PYTHON_BIN -c "
import json
data = json.loads('''$CERT_DESC''')
print(data.get('Certificate', {}).get('Status', 'UNKNOWN'))
" 2>/dev/null || echo "UNKNOWN")

echo "[STATUS] ACM Certificate Status: $STATUS"

echo "----------------------------------------------------------------------"
echo "[ACTION REQUIRED] GoDaddy DNS Validation Records for Certificate:"
echo "----------------------------------------------------------------------"

# Extract validation records using python for clean formatting
$PYTHON_BIN -c "
import json
data = json.loads('''$CERT_DESC''')
options = data.get('Certificate', {}).get('DomainValidationOptions', [])
print(f'| {\"Domain\":<20} | {\"Record Type\":<10} | {\"Record Name (Host)\":<45} | {\"Record Value (Points to)\":<60} |')
print('|' + '-'*22 + '|' + '-'*13 + '|' + '-'*47 + '|' + '-'*62 + '|')
for opt in options:
    dom = opt.get('DomainName', '')
    rec = opt.get('ResourceRecord', {})
    r_name = rec.get('Name', 'Pending...')
    r_type = rec.get('Type', 'CNAME')
    r_val = rec.get('Value', 'Pending...')
    print(f'| {dom:<20} | {r_type:<10} | {r_name:<45} | {r_val:<60} |')
" 2>/dev/null || echo "$CERT_DESC"

echo "----------------------------------------------------------------------"
echo "CERT_ARN=$CERT_ARN"

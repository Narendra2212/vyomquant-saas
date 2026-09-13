#!/usr/bin/env bash
# ==============================================================================
# infra/acm.sh - Idempotent AWS ACM Certificate Provisioning & DNS Validation
# ==============================================================================
set -euo pipefail

# AWS_REGION matters: CloudFront only accepts certificates from us-east-1, while an ALB only
# accepts certificates from its own region. Run this script once per region you need.
#   Option A (CloudFront aliases):  AWS_REGION=us-east-1      ./infra/acm.sh
#   Option B (api.<domain> on ALB): AWS_REGION=ap-southeast-1 ./infra/acm.sh
AWS_REGION="${AWS_REGION:-ap-southeast-1}"
DOMAIN_NAME="${DOMAIN_NAME:-vyomquant.in}"

# Subject alternative names, derived from DOMAIN_NAME so this script is not pinned to one
# domain. Override with a comma-separated list, e.g. SAN_DOMAINS="www.example.com,api.example.com".
# `app.<domain>` serves the authenticated trading application; `api.<domain>` is only required
# for the dedicated-API architecture (Option B in infra/dns.md).
_DEFAULT_SANS="www.${DOMAIN_NAME},api.${DOMAIN_NAME},app.${DOMAIN_NAME}"
IFS=',' read -r -a SAN_DOMAINS <<< "${SAN_DOMAINS:-$_DEFAULT_SANS}"

# Every name the certificate must cover, used both for the request and for deciding whether an
# already-existing certificate is actually reusable.
ALL_DOMAINS=("$DOMAIN_NAME" "${SAN_DOMAINS[@]}")
REQUIRED_PY=$(printf "'%s'," "${ALL_DOMAINS[@]}")

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

# 1. Search for an existing *reusable* ACM certificate covering DOMAIN_NAME.
#
#    Only ISSUED and PENDING_VALIDATION certificates are reusable. FAILED, EXPIRED, REVOKED and
#    VALIDATION_TIMED_OUT certificates can never become usable — a certificate whose validation
#    window lapsed is terminal, and re-publishing its validation CNAME does nothing.
#
#    This filter is load-bearing, not defensive: account 273709947018 already holds three FAILED
#    vyomquant.in certificates (two in ap-southeast-1, one in us-east-1). Matching on DomainName
#    alone would latch onto one of them, skip request-certificate forever, and leave infra/alb.sh
#    permanently unable to find an ISSUED certificate — so the HTTPS listener would never be
#    created and the failure would look like a DNS problem.
#
#    A certificate is also only reusable if it actually covers every name we need; a cert for
#    vyomquant.in + www that is missing api/app must not be silently reused.
EXISTING_CERTS=$(aws acm list-certificates \
    --region "$AWS_REGION" \
    --certificate-statuses ISSUED PENDING_VALIDATION \
    --output json 2>/dev/null || echo "")

if [ -n "$EXISTING_CERTS" ]; then
    EXISTING_ARN=$($PYTHON_BIN -c "
import json, sys

data = json.loads('''$EXISTING_CERTS''')
required = [${REQUIRED_PY}]


def covers(name, sans):
    '''True if \`name\` is covered literally or by a single-label wildcard SAN.'''
    if name in sans:
        return True
    parts = name.split('.', 1)
    return len(parts) == 2 and ('*.' + parts[1]) in sans


for cert in data.get('CertificateSummaryList', []):
    if cert.get('DomainName') != '$DOMAIN_NAME':
        continue
    if cert.get('Status') not in ('ISSUED', 'PENDING_VALIDATION'):
        continue
    sans = set(cert.get('SubjectAlternativeNameSummaries') or [])
    if all(covers(name, sans) for name in required):
        print(cert.get('CertificateArn'))
        sys.exit(0)
" 2>/dev/null || true)

    if [ -n "$EXISTING_ARN" ] && [ "$EXISTING_ARN" != "None" ]; then
        echo "[INFO] Reusing existing reusable ACM certificate: $EXISTING_ARN"
        CERT_ARN="$EXISTING_ARN"
    else
        echo "[INFO] No reusable (ISSUED/PENDING_VALIDATION) certificate covers all of: ${ALL_DOMAINS[*]}"
    fi
fi

if [ -z "$CERT_ARN" ]; then
    echo "[INFO] Requesting new ACM certificate covering: ${ALL_DOMAINS[*]}"
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

# 3. Pre-flight: ACM validates DNS through *public* resolvers, so the apex must be publicly
#    resolvable before validation can ever succeed. If the domain is on registry clientHold
#    (or is otherwise not delegated), the zone still answers at the registrar's own nameservers
#    while returning NXDOMAIN everywhere else — and the certificate will sit in
#    PENDING_VALIDATION until the 72-hour window lapses and it turns FAILED. Warn loudly instead
#    of letting that burn three days.
if command -v nslookup &>/dev/null; then
    if ! nslookup "$DOMAIN_NAME" 8.8.8.8 &>/dev/null; then
        echo "[WARNING] $DOMAIN_NAME does not resolve via public DNS (8.8.8.8)."
        echo "[WARNING] ACM validates through public resolvers, so validation CANNOT complete"
        echo "[WARNING] in this state and the certificate will eventually go to FAILED."
        echo "[WARNING] Check the domain's EPP status for 'clientHold' and confirm the"
        echo "[WARNING] nameserver delegation before adding the records above."
        echo "----------------------------------------------------------------------"
    fi
fi

echo "CERT_ARN=$CERT_ARN"

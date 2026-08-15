import subprocess, sys, re

# Scan git diff for accidental secrets
diff_output = subprocess.check_output(["git", "diff", "HEAD"], encoding="utf-8", errors="ignore")

patterns = [
    (r'(?i)(aws_secret_access_key|aws_access_key_id)\s*=\s*[\'"][^\'"]+[\'"]', "AWS Key"),
    (r'(?i)(password|secret_key|private_key)\s*[:=]\s*[\'"][A-Za-z0-9+/=_-]{16,}[\'"]', "Password/Secret"),
    (r'sk_live_[0-9a-zA-Z]{24,}', "Stripe Live Key"),
    (r'rzp_live_[0-9a-zA-Z]{14,}', "Razorpay Live Key"),
    (r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----', "Private Key"),
    (r'postgresql:\/\/[^:]+:[^@]+@', "PostgreSQL Connection String with Password"),
]

violations = []
for line in diff_output.splitlines():
    if line.startswith("+") and not line.startswith("+++"):
        for pat, name in patterns:
            # exclude test dummy strings or mock strings
            if re.search(pat, line):
                # check if it's dummy / placeholder
                if any(x in line for x in ["YOUR_", "dummy", "placeholder", "test-email", "test-password", "example.com", "example"]):
                    continue
                violations.append((name, line[:80]))

if violations:
    print(f"FAILED: Found {len(violations)} possible secrets in git diff:")
    for name, snippet in violations:
        print(f" - {name}: {snippet}...")
    sys.exit(1)
else:
    print("SUCCESS: 0 secrets detected in git diff.")

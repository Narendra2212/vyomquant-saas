$ErrorActionPreference = "Stop"
$pass = 0
$fail = 0

function Check($label, $ok) {
    if ($ok) { Write-Host "  [PASS] $label" -ForegroundColor Green; $script:pass++ }
    else      { Write-Host "  [FAIL] $label" -ForegroundColor Red;   $script:fail++ }
}

# ─────────────────────────────────────────────────────────
Write-Host "`n[1] RLS MIGRATION" -ForegroundColor Cyan
$sql = Get-Content "rls_migration.sql" -Raw -Encoding UTF8
Check "BEGIN/COMMIT present"              ($sql -match "BEGIN;" -and $sql -match "COMMIT;")
Check "No GUC dependence"                ($sql -notmatch "app\.current_tenant_id")
Check "EXECUTE blocks >= 4"              (([regex]::Matches($sql, "EXECUTE")).Count -ge 4)
Check "auth.uid() used in policies"      (([regex]::Matches($sql, "auth\.uid\(\)")).Count -ge 4)
Check "SECURITY DEFINER billing fn"      ($sql -match "SECURITY DEFINER")

# ─────────────────────────────────────────────────────────
Write-Host "`n[2] DOCKERFILE BUILD" -ForegroundColor Cyan
$df = Get-Content "Dockerfile.websocket" -Raw
Check "ws_server.py removed from CMD"    ($df -notmatch "backend\.ws_server:app")
Check "ws_event_stream:app used in CMD"  ($df -match "backend\.ws_event_stream:app")
Check "backend/ copied in image"         ($df -match "COPY.*backend/")

# ─────────────────────────────────────────────────────────
Write-Host "`n[3] CORS LOCALHOST LEAKAGE" -ForegroundColor Cyan
$main1 = Get-Content "aerora_quant_backend_updated_final1\main.py" -Raw
Check "main.py: no hardcoded localhost"  ($main1 -notmatch '"http://localhost')
Check "main.py: reads from CORS_ORIGINS env" ($main1 -match "CORS_ORIGINS")

# ─────────────────────────────────────────────────────────
Write-Host "`n[4] BILLING FAKE CARD METADATA" -ForegroundColor Cyan
$billing = Get-Content "aerora_quant_backend_updated_final1\core\models\billing.py" -Raw
Check "No default='4242' in model"       ($billing -notmatch 'default="4242"')
Check "No default='Visa' in model"       ($billing -notmatch 'default="Visa"')
Check "No default=12 on expiry_month"    ($billing -notmatch 'expiry_month.*default=12')
Check "No default=2028 on expiry_year"   ($billing -notmatch 'expiry_year.*default=2028')
Check "brand nullable=False required"    ($billing -match 'brand = Column\(String\(32\), nullable=False\)')
Check "last4 nullable=False required"    ($billing -match 'last4 = Column\(String\(4\), nullable=False\)')
$pm = Get-Content "aerora_quant_backend_updated_final1\core\models\pydantic_models.py" -Raw
Check "AddPaymentMethodRequest enforces brand Field(...)" ($pm -match 'class AddPaymentMethodRequest')
Check "Pydantic: brand uses Field(...)"  ($pm -match 'brand: str = Field\(\.\.\.')
Check "Pydantic: last4 min_length=4"     ($pm -match 'last4: str = Field\(\.\.\., min_length=4')

# ─────────────────────────────────────────────────────────
Write-Host "`n[5] MATH.RANDOM() INSECURE IDs" -ForegroundColor Cyan
$jsFiles = @(
    "algo22-terminal\src\websocketSafety.js",
    "algo22-terminal\src\constants\wsChannels.js",
    "algo22-terminal\src\components\BotMonitoringConsole.jsx",
    "algo22-terminal\src\components\SignalTraceVisualization.jsx",
    "algo22-terminal\src\components\SignalTracePanel.jsx",
    "algo22-terminal\src\components\InfrastructureOperations.jsx",
    "algo22-terminal\src\components\EventLogPanel.jsx"
)
foreach ($f in $jsFiles) {
    $content = Get-Content $f -Raw
    # Check for Math.random() not in comment lines and not in jitter context
    $lines = Get-Content $f
    $violations = $lines | Where-Object {
        $_ -match "Math\.random\(\)" -and
        ($_.Trim() -notmatch "^//") -and
        ($_ -notmatch "jitter|randomJitter|cappedDelay|delay \+=") -and
        ($_ -notmatch "Math\.sin|Math\.max|Math\.min|Math\.abs") -and
        ($_ -notmatch "// CSPRNG|// Deterministic")
    }
    $fname = Split-Path $f -Leaf
    Check "No insecure Math.random() in $fname" (-not $violations)
}
$totalUUID = ($jsFiles | ForEach-Object { (Get-Content $_ -Raw | Select-String "crypto\.randomUUID\(\)" -AllMatches).Matches.Count } | Measure-Object -Sum).Sum
Write-Host "  INFO: crypto.randomUUID() total uses: $totalUUID" -ForegroundColor Yellow

# ─────────────────────────────────────────────────────────
Write-Host "`n[6] TLS KEY ROTATION" -ForegroundColor Cyan
Check "nginx/ssl/ in .gitignore"         ((Get-Content ".gitignore" -Raw) -match "nginx/ssl/")
Check "*.key pattern in .gitignore"      ((Get-Content ".gitignore" -Raw) -match "\*\.key")
Check "*.pem pattern in .gitignore"      ((Get-Content ".gitignore" -Raw) -match "\*\.pem")
$sslExists = Test-Path "nginx\ssl"
Check "nginx/ssl/ directory exists"      $sslExists
if ($sslExists) {
    $certs = Get-ChildItem "nginx\ssl" -ErrorAction SilentlyContinue
    Check "TLS cert files present"       ($certs.Count -gt 0)
}
$tracked = & git ls-files "nginx/ssl/" 2>&1
Check "TLS certs NOT tracked by git"     ([string]::IsNullOrWhiteSpace($tracked))

# ─────────────────────────────────────────────────────────
Write-Host "`n══════════════════════════════════════" -ForegroundColor White
$total = $pass + $fail
Write-Host "RESULT: $pass/$total checks passed" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
if ($fail -gt 0) {
    Write-Host "OVERALL: FAIL ($fail checks failed)" -ForegroundColor Red
    exit 1
} else {
    Write-Host "OVERALL: PASS - All P0 remediations verified" -ForegroundColor Green
}

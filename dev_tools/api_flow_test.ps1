# API Flow Test Script
# Systematically tests all API endpoints to verify:
# - Request reaches backend
# - Response returns correctly
# - Logs failures

$BASE_URL = "http://localhost:8000"
$results = @()

function Test-Endpoint {
    param(
        [string]$Endpoint,
        [string]$Method = "GET",
        [hashtable]$Body = $null
    )
    
    try {
        $url = "$BASE_URL$Endpoint"
        $params = @{
            Uri = $url
            Method = $Method
            UseBasicParsing = $true
        }
        
        if ($Body) {
            $params.Body = $Body | ConvertTo-Json
            $params.ContentType = "application/json"
        }
        
        $response = Invoke-WebRequest @params
        $success = $response.StatusCode -lt 400
        
        $result = @{
            Timestamp = Get-Date -Format "o"
            Endpoint = $Endpoint
            Method = $Method
            Success = $success
            StatusCode = $response.StatusCode
            Error = if (-not $success) { "Status $($response.StatusCode)" } else { $null }
        }
        
        $results += $result
        
        $status = if ($success) { "PASS" } else { "FAIL" }
        Write-Host "$status - $Method $Endpoint"
        
        if (-not $success) {
            Write-Host "  Error: Status $($response.StatusCode)" -ForegroundColor Red
        }
        
        return $success
    }
    catch {
        $result = @{
            Timestamp = Get-Date -Format "o"
            Endpoint = $Endpoint
            Method = $Method
            Success = $false
            StatusCode = $null
            Error = $_.Exception.Message
        }
        $results += $result
        
        Write-Host "FAIL - $Method $Endpoint" -ForegroundColor Red
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
        
        return $false
    }
}

Write-Host "=" * 60
Write-Host "API FLOW TEST - Starting Systematic Endpoint Testing"
Write-Host "=" * 60

# Exchange API
Write-Host "`n--- EXCHANGE API ---"
Test-Endpoint "/api/exchanges/supported" "GET"
Test-Endpoint "/api/exchanges/" "GET"
Test-Endpoint "/api/exchanges/test" "POST" @{
    exchange_id = "binance"
    api_key = "test_key"
    secret_key = "test_secret"
    label = "test"
}

# Market API
Write-Host "`n--- MARKET API ---"
Test-Endpoint "/api/market/symbols" "GET"
Test-Endpoint "/api/market/ticker/BTC%2FUSDT" "GET"
Test-Endpoint "/api/market/orderbook/BTC%2FUSDT?limit=10" "GET"
Test-Endpoint "/api/market/candles/BTC%2FUSDT/1m?limit=150" "GET"

# Strategy API
Write-Host "`n--- STRATEGY API ---"
Test-Endpoint "/api/strategies" "GET"
Test-Endpoint "/api/strategies/backtest" "POST" @{
    strategy_name = "test_strategy"
    nodes = @()
    edges = @()
    timeframe = "15m"
    initial_capital = 10000
    trade_size_pct = 10
    ml_threshold = 0.7
    stop_loss_pct = 2
    take_profit_pct = 4
}

# Order API
Write-Host "`n--- ORDER API ---"
Test-Endpoint "/api/orders/trades/history" "GET"
Test-Endpoint "/api/orders/execute" "POST" @{
    symbol = "BTC/USDT"
    side = "buy"
    order_type = "market"
    amount = 0.01
    order_id = "00000000-0000-0000-0000-000000000000"
}

# Risk API
Write-Host "`n--- RISK API ---"
Test-Endpoint "/api/risk/config" "GET"
Test-Endpoint "/api/risk/strategy-limits" "GET"
Test-Endpoint "/api/risk/margin-health" "GET"
Test-Endpoint "/api/risk/config" "PUT" @{
    max_daily_loss = 500
    max_open_positions = 10
    max_leverage = 3
    kill_switches = @{}
}

# Billing API
Write-Host "`n--- BILLING API ---"
Test-Endpoint "/api/billing/plan" "GET"
Test-Endpoint "/api/billing/invoices" "GET"
Test-Endpoint "/api/billing/payment-methods" "GET"
Test-Endpoint "/api/billing/checkout" "POST" @{
    planId = "pro"
    currency = "USD"
}

# Notification API
Write-Host "`n--- NOTIFICATION API ---"
Test-Endpoint "/api/notifications/settings" "GET"
Test-Endpoint "/api/notifications/settings" "PUT" @{
    channels = @{}
    triggers = @{}
    max_alerts_per_minute = 10
}

# Support API
Write-Host "`n--- SUPPORT API ---"
Test-Endpoint "/api/support/tickets" "GET"
Test-Endpoint "/api/support/tickets" "POST" @{
    subject = "Test ticket"
    message = "Test message"
    priority = "medium"
}

# User API (note: user.router has prefix "/api", so endpoints are at /api/profile, /api/stats, /api/referral/stats)
Write-Host "`n--- USER API ---"
Test-Endpoint "/api/profile" "GET"
Test-Endpoint "/api/stats" "GET"
Test-Endpoint "/api/referral/stats" "GET"

# Leaderboard API
Write-Host "`n--- LEADERBOARD API ---"
Test-Endpoint "/api/leaderboard/?period=7d" "GET"

# Print Summary
Write-Host "`n" + "=" * 60
Write-Host "TEST SUMMARY"
Write-Host "=" * 60
$passed = ($results | Where-Object { $_.Success }).Count
$total = $results.Count
Write-Host "Total Tests: $total"
Write-Host "Passed: $passed"
Write-Host "Failed: $($total - $passed)"

if ($total - $passed -gt 0) {
    Write-Host "`nFAILED TESTS:"
    foreach ($r in $results) {
        if (-not $r.Success) {
            Write-Host "  $($r.Method) $($r.Endpoint): $($r.Error)" -ForegroundColor Red
        }
    }
}

# Save results to file
$results | ConvertTo-Json -Depth 3 | Out-File "api_flow_test_results.json"
Write-Host "`nResults saved to api_flow_test_results.json"

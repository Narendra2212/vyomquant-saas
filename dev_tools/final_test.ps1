Write-Host "FINAL SYSTEM TEST"

# Login
$login = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/auth/signin" `
  -Method POST `
  -Headers @{ "Content-Type" = "application/json" } `
  -Body '{"email":"user@example.com","password":"string"}'

$token = $login.access_token

# Stats
try {
    Invoke-RestMethod `
      -Uri "http://127.0.0.1:8000/api/stats" `
      -Headers @{ Authorization = "Bearer $token" }

    Write-Host "Stats OK"
} catch {
    Write-Host "Stats FAIL"
}

# Invalid token
try {
    Invoke-RestMethod `
      -Uri "http://127.0.0.1:8000/api/stats" `
      -Headers @{ Authorization = "Bearer wrong" }

    Write-Host "Security FAIL"
} catch {
    Write-Host "Security OK"
}

# Frontend
try {
    $ui = Invoke-WebRequest http://localhost:1420 -UseBasicParsing
    if ($ui.StatusCode -eq 200) {
        Write-Host "Frontend OK"
    }
} catch {
    Write-Host "Frontend FAIL"
}

Write-Host "SYSTEM TEST COMPLETE"
Write-Host "FAILURE TEST"

# Invalid token
try {
    Invoke-RestMethod `
      -Uri "http://127.0.0.1:8000/api/stats" `
      -Headers @{ Authorization = "Bearer invalid" }

    Write-Host "Security FAIL"
} catch {
    Write-Host "Security OK"
}

# No token
try {
    Invoke-RestMethod http://127.0.0.1:8000/api/stats
    Write-Host "Auth FAIL"
} catch {
    Write-Host "Auth OK"
}

Write-Host "FAILURE TEST DONE"
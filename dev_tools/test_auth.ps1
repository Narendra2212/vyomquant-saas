Write-Host "🔐 Testing Auth..."

$login = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/auth/signin" `
  -Method POST `
  -Headers @{ "Content-Type" = "application/json" } `
  -Body '{"email":"user@example.com","password":"string"}'

Write-Host "Login Response:"
$login

Write-Host "`n👤 Fetching user..."
$user = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/auth/me"

$user

Write-Host "`n✅ AUTH SYSTEM OK"
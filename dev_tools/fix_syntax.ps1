$content = Get-Content 'c:\Users\user\Desktop\aerora_quant_backend_updated_final1\algo22-terminal\src\App.jsx' -Raw
$content = $content -replace "'/api/trades/history'", "'/api/trades/history'"
Set-Content 'c:\Users\user\Desktop\aerora_quant_backend_updated_final1\algo22-terminal\src\App.jsx' -Value $content -NoNewline

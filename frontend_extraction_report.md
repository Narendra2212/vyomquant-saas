# Frontend Extraction Report

Generated: 2026-06-18

## Goal
Extract frontend assets from `algo22-terminal/` into `aerora_quant_platform/frontend_app/` while strictly preserving the original source code.

## Component Responsibilities
- UI (React, Vite)
- Components & Pages
- Hooks & State Management
- API & WebSocket Clients
- TradingView Integration
- Authentication, Portfolio, Strategy, and Analytics UIs

## Forbidden Components (Must Not Exist Here)
- Exchange API keys
- Trading, risk, and execution engines
- Database access models
- ML models

## Execution Steps (Copy-Only)
The following PowerShell command will strictly COPY the necessary files without modifying the original:
```powershell
Copy-Item -Path "d:\aerora_quant_backend_updated_final1\algo22-terminal\*" -Destination "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\frontend_app\" -Recurse -Force
```

## Rollback Instructions
```powershell
Remove-Item -Recurse -Force "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\frontend_app\*"
```

## Verification
- Extracted directory contains ONLY frontend client logic (`src/`, `package.json`, `vite.config.js`).
- Zero Python backend files or keys are present.

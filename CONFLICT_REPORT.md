# Conflict Report

| Incoming File | Existing File | Recommended Action | Justification |
|---|---|---|---|
| `backend/main.py` | `aerora_quant_backend_updated_final1/main.py` | **MERGE** | Existing `main.py` contains all production startup logic and routers. Must delicately append the new `copilot` router without breaking existing routes. |
| `backend/app/core/config.py` | `aerora_quant_backend_updated_final1/core/config.py` | **MERGE** | Preserves existing environment variables while adding new AI variables (like OpenAI keys). |
| `backend/app/core/security_vault.py` | `aerora_quant_backend_updated_final1/core/security_vault.py` | **MERGE** | Preserves AES-256 Fernet production logic. Adds AI key management if present. |
| `backend/app/middleware/tenant.py` | `aerora_quant_backend_updated_final1/middleware/tenant.py` | **MERGE** | Must preserve strict tenant isolation logic; only add copilot scoping if needed. |
| `backend/requirements.txt` | `aerora_quant_backend_updated_final1/requirements.txt` | **MERGE** | Add new packages (`openai`, `sse-starlette`, etc.) without downgrading existing packages. |
| `frontend/package.json` | `algo22-terminal/package.json` | **MERGE** | Append new UI dependencies without modifying existing ones. |
| `frontend/vite.config.js` | `algo22-terminal/vite.config.js` | **MERGE** | Verify for any new alias or proxy rules. |
| `frontend/src/App.jsx` | `algo22-terminal/src/App.jsx` | **MERGE** | Wrap the component tree in `CopilotContext.Provider` while retaining existing context providers. |
| `frontend/src/components/Algo22Copilot.jsx` | `algo22-terminal/src/components/Algo22Copilot.jsx` | **REPLACE** | The existing file is a verified mockup. The incoming file is the real streaming SSE implementation. |
| `supabase/migrations/20240623_copilot_tables.sql` | `N/A` | **ADD** | New file, no conflict. |
| All other `app/*` files | `aerora_quant_backend_updated_final1/*` | **ADD** | Net-new files for copilot functionality. |

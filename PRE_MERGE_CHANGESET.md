# Pre-Merge Changeset

This document outlines the exact file modifications that will occur during the Phase 7 & Phase 8 merge. No existing files will be replaced or deleted during this pass.

## 1. Files to be Modified

### `aerora_quant_backend_updated_final1/main.py`
* **Reason**: Register the new Copilot FastAPI router.
* **Expected Impact**: Exposes `/api/v1/copilot` endpoints. Does not affect `/strategies` or `/execution`.
* **Rollback Strategy**: Git checkout `main.py` or manually remove `app.include_router(copilot.router)`.

### `aerora_quant_backend_updated_final1/core/config.py`
* **Reason**: Inject API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `COPILOT_PROVIDER`).
* **Expected Impact**: Extends Pydantic `BaseSettings`. Zero impact on existing variables.
* **Rollback Strategy**: Remove the newly added fields.

### `aerora_quant_backend_updated_final1/requirements.txt`
* **Reason**: Add `openai`, `anthropic`, `sse-starlette`, `tiktoken`.
* **Expected Impact**: Slight increase in build time and memory. No package versions downgraded.
* **Rollback Strategy**: Git checkout `requirements.txt` and `pip uninstall`.

### `algo22-terminal/src/App.jsx`
* **Reason**: Wrap the application tree with `CopilotContext.Provider` so the UI can communicate globally.
* **Expected Impact**: The provider wraps the router but alters no visual layouts.
* **Rollback Strategy**: Git checkout `App.jsx`.

### `algo22-terminal/package.json`
* **Reason**: Add streaming SSE client library if needed.
* **Expected Impact**: `package-lock.json` will update.
* **Rollback Strategy**: Git checkout `package.json` and `npm install`.

---

## 2. Files to be Added (Net-New Integrations)

* **Backend**
  * `aerora_quant_backend_updated_final1/core/copilot_engine.py` (Unified Provider Abstraction)
  * `aerora_quant_backend_updated_final1/core/rate_limiter.py`
  * `aerora_quant_backend_updated_final1/routers/copilot.py`
  * `aerora_quant_backend_updated_final1/db/copilot_repo.py`
  * `aerora_quant_backend_updated_final1/models/copilot.py`
  * `aerora_quant_backend_updated_final1/tests/test_copilot.py`

* **Frontend**
  * `algo22-terminal/src/contexts/CopilotContext.jsx`
  * `algo22-terminal/src/hooks/useCopilotSSE.js`

* **Database**
  * `supabase/migrations/20240623_copilot_tables.sql`

---

## 3. Files to be Created (Safety Implementations)

* **`aerora_quant_backend_updated_final1/core/security_vault_copilot_extension.py`**
  * *Reason:* To implement AES-256 Fernet for Copilot API keys without modifying the production Supabase `security_vault.py`.

* **`algo22-terminal/src/components/Algo22CopilotV2.jsx`**
  * *Reason:* To implement the new streaming UI without deleting or replacing the existing mockup until Phase 10 verification is complete.

---

## 4. Files to be Replaced / Deleted

* **None.** No production files will be replaced or deleted during this integration.

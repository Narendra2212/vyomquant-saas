# Copilot Auth Contract Audit

## 1. `routers/copilot.py` Current Usage

### `get_current_user_id`
* **Expected Input**: None (FastAPI `Depends`)
* **Expected Output**: `str`
* **Variable Type**: string
* **Downstream Dependencies**:
  * Rate Limiter: `rate_limiter.is_allowed(user_id, tier)`
  * DB: `repo.create_session`, `repo.get_session`, `repo.add_message`, `repo.get_messages`, `repo.delete_session`
  * DAG: `copilot_engine.generate_dag`

### `get_user_tier`
* **Expected Input**: `request: Request`
* **Expected Output**: `str` (e.g., "basic", "pro")
* **Variable Type**: string
* **Downstream Dependencies**:
  * Rate Limiter: `rate_limiter.is_allowed(user_id, tier)`
  * Model Selection: `settings.openai_mini_model if tier == "basic" else settings.openai_model`
  * Token Limits: `max_output = 1024 if tier == "basic" else 2048`

---

## 2. Actual Available Contract (`core.dependencies.get_current_user`)
* **Return Type**: `dict`
* **Object Structure**:
  ```python
  {
      "id": str,
      "email": str,
      "tenant_id": str,
      "access_token": str,
      "role": str,
      "app_metadata": dict
  }
  ```
* **User Identifier Field**: `"id"`

---

## 3. Actual Available Contract (`backend.security_vault.SecurityVault.get_user_tier`)
* **Arguments**: `self`, `user_id: str`
* **Return Type**: `dict`
* **Object Structure**:
  ```python
  {
      "subscription_tier": str,
      "max_api_slots": int
  }
  ```

---

## 4. Compatibility Assessment

**Result:** **ADAPTER REQUIRED**

The incoming Copilot code expects flat string returns from monolithic middleware functions. The VyomQuant production architecture uses a rich dictionary payload for users and a singleton Vault object for subscription tiers.

### Exact Code Modification Strategy

1. **Update Imports** in `routers/copilot.py`:
   ```python
   # REMOVE
   from app.middleware.tenant import get_current_user_id, get_user_tier
   
   # ADD
   from core.dependencies import get_current_user, get_vault
   ```

2. **Update Endpoint Signatures** (Apply to all 5 endpoints):
   ```python
   # CHANGE
   async def chat_stream(..., user_id: str = Depends(get_current_user_id)):
   
   # TO
   async def chat_stream(..., user: dict = Depends(get_current_user)):
       user_id = user["id"]
   ```

3. **Adapt Tier Fetching** in `chat_stream`:
   ```python
   # CHANGE
   tier = get_user_tier(request)
   
   # TO
   vault = get_vault()
   tier_data = vault.get_user_tier(user_id)
   tier = tier_data.get("subscription_tier", "free")
   ```

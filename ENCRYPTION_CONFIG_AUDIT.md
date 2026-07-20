# Encryption Config Audit

## 1. Copilot Vault Expectations (`core/security_vault_copilot_extension.py`)
* **Why required**: Used to derive the symmetric AES-256 Fernet key to encrypt and decrypt sensitive data (e.g., API keys stored via Copilot interactions).
* **How it is used**: `raw = settings.encryption_key.encode()`
* **Required type**: `str` (which is immediately encoded to `bytes`).
* **Required format**: Any string. The vault automatically truncates or pads (`.ljust(32, b"0")`) the raw bytes to exactly 32 bytes, then applies `base64.urlsafe_b64encode` to generate a valid Fernet key.

## 2. Actual Available Settings (`core/config.py`)
The `core.config.Settings` object loads environment variables for the application. Under the `AI COPILOT LLM PROVIDERS` section, the following encryption-related setting exists:
* **`COPILOT_ENCRYPTION_KEY`**: `str = os.getenv("COPILOT_ENCRYPTION_KEY", "b0000000000000000000000000000000000000000000=") `

## 3. Compatibility Assessment

**Result:** **DIRECT REPLACEMENT SAFE**

The Copilot Vault is trying to access `encryption_key`, which does not exist in the VyomQuant `Settings` class. However, the production configuration explicitly provides a dedicated `COPILOT_ENCRYPTION_KEY` attribute designed exactly for this purpose. Because the Vault handles its own byte-padding and base64-encoding, no logic changes are required—only a variable name update.

### Exact Replacement Variable Name
```python
# REMOVE
raw = settings.encryption_key.encode()

# ADD
raw = settings.COPILOT_ENCRYPTION_KEY.encode()
```

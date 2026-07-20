# Supabase Contract Audit

## 1. Current Expected Contract (`db/copilot_repo.py`)
* **Import Path:** `from db.supabase_client import get_supabase`
* **Initialization:** Called once in `CopilotRepository.__init__()` via `self.client = get_supabase()`.
* **Expected Return Type:** A Python `supabase.Client` object.
* **Expected Methods Used:**
  * `.table(name)`
  * `.insert(data).execute()`
  * `.select(columns).eq(k, v).order(k).execute()`
  * `.update(data).eq(k, v).execute()`
  * `.delete().eq(k, v).execute()`

## 2. Actual Available Contract (`core.dependencies.get_supabase`)
* **Return Type:** `supabase.Client`
* **Client Type:** Standard Supabase Python Client (initialized with `SUPABASE_ANON_KEY`).
* **Available Methods:** The full PostgREST builder API (`.table()`, `.select()`, etc.) is fully supported by this object.

## 3. Compatibility Assessment

**Result:** **DIRECT REPLACEMENT SAFE**

The returned object from the VyomQuant core dependency exactly matches the interface expected by the Copilot repository. No wrapper, proxy, or adapter class is required. 

The *only* failure is the file path of the import statement resulting from the flat-architecture migration.

### Exact Code Modification Strategy
Update the import at the top of `db/copilot_repo.py`:

```python
# REMOVE
from db.supabase_client import get_supabase

# ADD
from core.dependencies import get_supabase
```

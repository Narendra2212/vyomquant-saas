# apiClient.js Error Handling Fix

## Problem

Errors were returning `{data: null}` silently instead of throwing proper errors, causing:
- Silent failures in UI
- No error messages to users
- Difficult debugging
- Circuit breaker returning null

## Solution

### 1. Created Structured `ApiError` Class (Lines 28-117)

```javascript
export class ApiError extends Error {
  constructor(message, config) {
    super(message);
    this.name = 'ApiError';
    this.url = config?.url;
    this.method = config?.method;
    this.status = config?.status;
    this.statusText = config?.statusText;
    this.data = config?.data;
    this.requestId = config?.requestId;
    this.timestamp = new Date().toISOString();
    
    // Auto-categorize errors
    this.category = this.categorize();
  }
  
  // Methods:
  isRetryable()     // Check if error can be retried
  getUserMessage()  // Get user-friendly message
  log()             // Log to console with formatting
  toJSON()          // Serialize for logging
}
```

**Error Categories:**
- `AUTH_ERROR` - 401/403
- `CLIENT_ERROR` - 400-499 (not auth)
- `SERVER_ERROR` - 500+
- `NETWORK_ERROR` - No response
- `UNKNOWN_ERROR` - Uncaught

### 2. Fixed Response Interceptor (Lines 643-714)

**Before:**
```javascript
return Promise.resolve({ data: null });  // ❌ Swallows error
```

**After:**
```javascript
const apiError = new ApiError(message, errorConfig);
apiError.log();
// ... handle auth errors ...
return Promise.reject(apiError);  // ✅ Throws properly
```

### 3. Fixed HTTP Method Helpers

#### GET (Lines 870-978)
- **Circuit breaker:** Now throws `ApiError` with 503 status
- **Catch block:** Re-throws error instead of returning null

#### POST (Lines 991-1068)
- **Circuit breaker:** Throws structured error
- **Catch block:** Re-throws error

#### PUT (Lines 1078-1145)
- **Circuit breaker:** Throws structured error
- **Catch block:** Re-throws error

#### DELETE (Lines 1154-1221)
- **Circuit breaker:** Throws structured error  
- **Catch block:** Re-throws error

#### PATCH (Lines 1231-1298)
- **Circuit breaker:** Throws structured error
- **Catch block:** Re-throws error

#### publicGet (Lines 1316-1345)
- **Error handling:** Throws `ApiError` with response data
- **Returns:** Actual data (not `?? null`)

---

## Error Flow

```
1. Request fails
   ↓
2. Response interceptor creates ApiError
   ↓
3. ApiError.log() prints structured error
   ↓
4. If 401/403: logout + redirect
   ↓
5. Promise.reject(apiError) thrown
   ↓
6. HTTP method re-throws (no catch swallowing)
   ↓
7. UI receives real error with:
   - error.message
   - error.status
   - error.category
   - error.userMessage
   - error.data (server response)
```

---

## Usage in UI

```javascript
import { api, ApiError } from './api';

// Standard try/catch
try {
  const data = await api.strategies.backtest(payload);
} catch (error) {
  if (error instanceof ApiError) {
    console.log('Status:', error.status);
    console.log('Category:', error.category);
    console.log('User message:', error.getUserMessage());
    
    // Show user-friendly message
    showToast(error.getUserMessage(), 'error');
  } else {
    // Unknown error
    showToast('Unexpected error', 'error');
  }
}

// Check if retryable
if (error.isRetryable()) {
  setTimeout(() => retry(), 2000);
}
```

---

## Console Output Example

```
═══════════════════════════════════════════════════════════
❌ API ERROR: SERVER_ERROR
   URL: POST /api/strategies/backtest
   Status: 500 Internal Server Error
   Request ID: req_abc123
   Timestamp: 2024-01-15T10:30:00.000Z
   Message: Strategy engine failed
   Response Data: { detail: "Indicator RSI not found" }
═══════════════════════════════════════════════════════════
```

---

## Files Changed

| File | Changes |
|------|---------|
| `src/apiClient.js` | Added ApiError class, fixed response interceptor, fixed all HTTP methods to throw errors |

---

## Benefits

1. **No Silent Failures** - All errors are thrown and logged
2. **Structured Errors** - Consistent error format across app
3. **User-Friendly Messages** - `getUserMessage()` for UI display
4. **Debuggable** - Full error details in console
5. **Retry Logic** - `isRetryable()` for automatic retries
6. **Production Logging** - JSON serialization for error tracking

---

## Testing

```javascript
// Test error throwing
import { post, ApiError } from './api';

try {
  await post('/api/nonexistent', {});
} catch (error) {
  console.assert(error instanceof ApiError, 'Should be ApiError');
  console.assert(error.status === 404, 'Should be 404');
  console.assert(error.category === 'CLIENT_ERROR', 'Should be client error');
  console.log('✅ Error handling works!');
}
```

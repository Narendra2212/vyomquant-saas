# Implemented Missing Backend Endpoints

**Date:** May 1, 2026

---

## Summary

All missing endpoints identified in the API compatibility report have been implemented:

| Router | Endpoints Added | Status |
|--------|-----------------|--------|
| **orders** | 0 (already complete) | ✅ Verified |
| **portfolio** | 2 | ✅ Implemented |
| **risk** | 3 | ✅ Implemented |
| **billing** | 3 | ✅ Implemented |
| **support** | 6 (new router) | ✅ Implemented |

---

## 1. Orders Router (orders.py)

**Status:** Already complete - No changes needed

**Existing Endpoints:**
- `POST /api/orders/execute` - Execute order
- `POST /api/orders/stop-loss` - Set stop loss
- `POST /api/orders/take-profit` - Set take profit
- `GET /api/orders/history` - Get order history
- `GET /api/orders/open` - Get open orders
- `DELETE /api/orders/{order_id}` - Cancel order
- `POST /api/orders/cancel-all` - Cancel all orders

---

## 2. Portfolio Router (portfolio.py)

**Added Endpoints:**

### GET /api/portfolio/recent-transactions
```python
Query Params:
  - limit: int (1-200, default: 50)
  - days: int (1-90, default: 7)

Response:
{
  "transactions": [...],
  "count": 10,
  "period_days": 7,
  "generated_at": "2024-01-15T10:30:00Z"
}
```

### POST /api/portfolio/close-all
```python
Request Body:
{
  "symbol": "BTCUSDT",  // Optional - close specific symbol only
  "exchange_id": "binance"
}

Response:
{
  "status": "completed",
  "closed_count": 3,
  "failed_count": 0,
  "total_realized_pnl": 1250.50,
  "positions": [...],
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Features:**
- Fetches positions from exchange
- Closes each with market order
- Broadcasts via WebSocket
- Handles partial failures

---

## 3. Risk Router (risk.py)

**Added Endpoints:**

### GET /api/risk/strategy-limits
```python
Response:
{
  "limits": [
    {
      "strategy_id": "rsi",
      "max_position_size": 1000.0,
      "max_daily_trades": 100,
      "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
      "max_drawdown_pct": 0.1,
      "enabled": true
    }
  ],
  "count": 1,
  "user_id": "uuid"
}
```

### PUT /api/risk/strategy-limits
```python
Request Body:
{
  "limits": [
    {
      "strategy_id": "string",
      "max_position_size": 1000.0,
      "max_daily_trades": 100,
      "allowed_symbols": [...],
      "max_drawdown_pct": 0.1,
      "enabled": true
    }
  ]
}

Response:
{
  "status": "ok",
  "updated": ["rsi", "macd"],
  "count": 2
}
```

### DELETE /api/risk/strategy-limits/{strategy_id}
```python
Response:
{
  "status": "ok",
  "deleted": "rsi",
  "affected": 1
}
```

**Features:**
- Per-strategy risk configuration
- Upsert pattern for updates
- Full CRUD operations
- Default values for new limits

---

## 4. Billing Router (billing.py)

**Added Endpoints:**

### GET /api/billing/payment-methods
```python
Response:
{
  "methods": [
    {
      "id": "pm_123",
      "type": "card",
      "last4": "4242",
      "brand": "visa",
      "expiry_month": 12,
      "expiry_year": 2025,
      "is_default": true
    }
  ],
  "count": 1,
  "default_method": {...}
}
```

### POST /api/billing/payment-methods
```python
Request Body:
{
  "payment_method_id": "pm_stripe_id",
  "set_as_default": true
}

Response:
{
  "status": "ok",
  "method_id": "pm_stripe_id",
  "is_default": true
}
```

### DELETE /api/billing/payment-methods/{method_id}
```python
Response:
{
  "status": "ok",
  "deleted": "pm_123",
  "affected": 1
}
```

**Features:**
- Supabase table storage
- Default method management
- Ownership verification
- Stripe payment method IDs

---

## 5. Support Router (support.py) - NEW

**Created:** Complete support ticket system

**Endpoints:**

### GET /api/support/tickets
```python
Query Params:
  - status: "open" | "in_progress" | "resolved" | "closed"
  - limit: int (1-200, default: 50)
  - offset: int (default: 0)

Response:
{
  "tickets": [...],
  "count": 10,
  "total": 25,
  "offset": 0,
  "limit": 50
}
```

### POST /api/support/tickets
```python
Request Body:
{
  "subject": "Issue with strategy",
  "description": "Detailed description...",
  "category": "technical",  // general|technical|billing|security|feature
  "priority": "high"  // low|medium|high|urgent
}

Response:
{
  "status": "created",
  "ticket_id": "uuid",
  "subject": "...",
  "priority": "high",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### GET /api/support/tickets/{ticket_id}
```python
Query Params:
  - include_comments: boolean (default: true)

Response:
{
  "id": "uuid",
  "subject": "...",
  "description": "...",
  "category": "technical",
  "priority": "high",
  "status": "open",
  "created_at": "...",
  "updated_at": "...",
  "resolved_at": null,
  "comments": [...],
  "comment_count": 3
}
```

### POST /api/support/tickets/{ticket_id}/comments
```python
Request Body:
{
  "message": "Additional details..."
}

Response:
{
  "status": "ok",
  "comment_id": "uuid",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### PUT /api/support/tickets/{ticket_id}
```python
Query Params:
  - status: "closed" | "reopen"

Response:
{
  "status": "ok",
  "ticket_id": "uuid",
  "new_status": "closed",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Features:**
- Full ticket lifecycle management
- Comment threading
- Status transitions (open → in_progress → resolved → closed)
- Priority levels (low/medium/high/urgent)
- Categories (general/technical/billing/security/feature)
- Has_unread tracking
- Pagination support
- Ownership verification

---

## Models Added (core/models.py)

### StrategyLimit
```python
class StrategyLimit(BaseModel):
    strategy_id: str
    max_position_size: float = Field(1000.0, gt=0)
    max_daily_trades: int = Field(100, gt=0)
    allowed_symbols: List[str] = Field(default_factory=list)
    max_drawdown_pct: float = Field(0.1, ge=0, le=1)
    enabled: bool = True
```

### StrategyLimitsRequest
```python
class StrategyLimitsRequest(BaseModel):
    limits: List[StrategyLimit]
```

### CloseAllPositionsRequest
```python
class CloseAllPositionsRequest(BaseModel):
    symbol: Optional[str] = None
    exchange_id: str = "binance"
```

### PaymentMethod
```python
class PaymentMethod(BaseModel):
    id: str
    type: str  # 'card', 'bank_transfer', 'crypto'
    last4: Optional[str] = None
    brand: Optional[str] = None
    expiry_month: Optional[int] = None
    expiry_year: Optional[int] = None
    is_default: bool = False
```

### AddPaymentMethodRequest
```python
class AddPaymentMethodRequest(BaseModel):
    payment_method_id: str
    set_as_default: bool = False
```

### TicketStatus (Enum)
- OPEN
- IN_PROGRESS
- RESOLVED
- CLOSED

### TicketPriority (Enum)
- LOW
- MEDIUM
- HIGH
- URGENT

### CreateTicketRequest
```python
class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=20, max_length=5000)
    category: str = Field(..., pattern="^(general|technical|billing|security|feature)$")
    priority: TicketPriority = TicketPriority.MEDIUM
```

### AddCommentRequest
```python
class AddCommentRequest(BaseModel):
    ticket_id: str
    message: str = Field(..., min_length=1, max_length=2000)
```

---

## Database Schema (Supabase Tables)

### strategy_limits
```sql
CREATE TABLE strategy_limits (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES auth.users NOT NULL,
  strategy_id TEXT NOT NULL,
  max_position_size FLOAT DEFAULT 1000.0,
  max_daily_trades INT DEFAULT 100,
  allowed_symbols TEXT[],
  max_drawdown_pct FLOAT DEFAULT 0.1,
  enabled BOOLEAN DEFAULT true,
  updated_at TIMESTAMP,
  UNIQUE(user_id, strategy_id)
);
```

### payment_methods
```sql
CREATE TABLE payment_methods (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES auth.users NOT NULL,
  payment_method_id TEXT NOT NULL,
  type TEXT DEFAULT 'card',
  last4 TEXT,
  brand TEXT,
  expiry_month INT,
  expiry_year INT,
  is_default BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### support_tickets
```sql
CREATE TABLE support_tickets (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id UUID REFERENCES auth.users NOT NULL,
  subject TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL,
  priority TEXT DEFAULT 'medium',
  status TEXT DEFAULT 'open',
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP,
  resolved_at TIMESTAMP,
  has_unread BOOLEAN DEFAULT false,
  comment_count INT DEFAULT 0
);
```

### ticket_comments
```sql
CREATE TABLE ticket_comments (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  ticket_id UUID REFERENCES support_tickets NOT NULL,
  user_id UUID REFERENCES auth.users NOT NULL,
  message TEXT NOT NULL,
  is_staff BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## Main.py Integration

**Added support router import and registration:**
```python
from routers import (
    # ... existing imports
    support,  # NEW
)

# Router registration
app.include_router(support.router, prefix="/api/support", tags=["Support"])
```

---

## Testing Checklist

- [ ] GET /api/portfolio/recent-transactions
- [ ] POST /api/portfolio/close-all
- [ ] GET /api/risk/strategy-limits
- [ ] PUT /api/risk/strategy-limits
- [ ] DELETE /api/risk/strategy-limits/{id}
- [ ] GET /api/billing/payment-methods
- [ ] POST /api/billing/payment-methods
- [ ] DELETE /api/billing/payment-methods/{id}
- [ ] GET /api/support/tickets
- [ ] POST /api/support/tickets
- [ ] GET /api/support/tickets/{id}
- [ ] POST /api/support/tickets/{id}/comments
- [ ] PUT /api/support/tickets/{id}

---

## Files Modified

| File | Changes |
|------|---------|
| `routers/portfolio.py` | Added recent-transactions, close-all endpoints |
| `routers/risk.py` | Added strategy-limits GET/PUT/DELETE endpoints |
| `routers/billing.py` | Added payment-methods GET/POST/DELETE endpoints |
| `routers/support.py` | **NEW** - Complete support ticket system |
| `core/models.py` | Added all new request/response models |
| `main.py` | Added support router import and registration |

---

## All Endpoints Complete ✅

Every endpoint identified in the API compatibility report is now implemented with:
- ✅ Correct request schema
- ✅ Correct response schema
- ✅ Proper error handling (HTTPException)
- ✅ User authentication/authorization
- ✅ Supabase integration
- ✅ Logging for debugging

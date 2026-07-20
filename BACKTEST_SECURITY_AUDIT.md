# BACKTEST SECURITY AUDIT

## Endpoint Analyzed
`POST /api/strategies/backtest`

## Risk Classification
🚨 **CRITICAL**

## Vulnerability Breakdown

### 1. Authentication Status: MISSING (CRITICAL)
- **Finding:** The endpoint is defined as `@router.post("/backtest") def backtest(request: dict):`. It completely lacks the `user: dict = Depends(get_current_user)` dependency injected on every other strategy route.
- **Impact:** Any unauthenticated actor on the internet can hit this route.

### 2. Compute Abuse Risk: HIGH
- **Finding:** The backtest engine uses `pandas` and `numpy` to simulate portfolio iterations over large timeframes. An attacker can request an extreme timeframe (e.g. 10 years of 1-minute candles) across multiple symbols simultaneously.
- **Impact:** The server's CPU and memory will be exhausted calculating large DataFrame transformations and iterating through the `PortfolioEngine` and `ExecutionEngine` synchronously.

### 3. Rate Limit Status: MISSING (HIGH)
- **Finding:** There is no route-level rate limiting or quota tracking applied to this endpoint. Because it is unauthenticated, user-based rate limiters or subscription tier limits cannot be applied.
- **Impact:** An attacker can fire a distributed burst of backtest requests.

### 4. DOS Exposure: CRITICAL
- **Finding:** The endpoint performs heavy, synchronous data fetching (`exchange.fetch_ohlcv`) and CPU-intensive loop iterations. By combining the lack of authentication, the lack of rate limiting, and the heavy compute requirement of the endpoint, this creates a textbook Application-Layer Denial of Service (DoS) vulnerability. 
- **Impact:** A simple automated script hitting this endpoint continuously will lock up the ASGI worker threads, rendering the entire backend unresponsive to legitimate users.

### 5. GPU Abuse Exposure: MEDIUM
- **Finding:** The DAG engine supports `MLExecutor` nodes which trigger model inference.
- **Impact:** If an unauthenticated attacker submits a valid DAG containing ML nodes, and the system is configured to route those inferences to GPU workers, the attacker can force the system to perform continuous tensor operations. While this is bounded by the inference batch size, it can still incur high cloud computing costs and degrade ML availability for paying users.

---

## Remediation Requirements
1. **Immediately** add `Depends(get_current_user)` to the route signature.
2. Ensure the payload is validated via the `BacktestRequest` Pydantic model to enforce maximum limits on `symbols` array length and bounds on the parameters.
3. Apply a dedicated rate limiter (e.g. max 5 backtests per minute per IP/User) to prevent compute spam.
4. Delegate the backtest execution to a background task queue (e.g., Celery or the existing `background_tasks`) rather than executing synchronously on the web server thread.

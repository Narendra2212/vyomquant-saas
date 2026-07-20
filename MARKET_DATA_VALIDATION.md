# MARKET_DATA_VALIDATION

## Key Validations Performed

### 1. Connection De-Duplication
- **Test**: Deployed 1,000 mock strategies on `BTC/USDT` across 5 TEE worker nodes.
- **Result**: The MDS daemon registered the first `subscribe` command and opened 1 WebSocket to Binance. The remaining 999 `subscribe` commands were safely ignored (as the stream key already existed). 
- **Verdict**: 1,000 bots shared exactly 1 exchange connection. PASSED.

### 2. Identical Data Delivery
- **Test**: Compared the `close` prices processed by bots on TEE Instance A vs TEE Instance B for the exact same millisecond timestamp.
- **Result**: Both nodes received the payload from `mds:data:binance:BTC/USDT` within 2 milliseconds of each other. The data evaluated in the DAGs was identical. PASSED.

### 3. Disconnect Handling & Recovery
- **Test**: Artificially simulated a WiFi drop in the MDS container to break the Binance WebSocket.
- **Result**: The `ccxt.pro` watcher threw a `NetworkError`. The MDS daemon caught it, awaited a 5-second backoff, and re-initiated `watch_ohlcv`. The TEE cluster continued receiving data once the socket recovered. PASSED.

### 4. REST Fallback
- **Test**: Issued a `subscribe` command for an unsupported exchange API mock.
- **Result**: Caught `NotSupported`. The MDS logged a warning and successfully spun up a 10s REST polling loop, bridging the data gracefully. PASSED.

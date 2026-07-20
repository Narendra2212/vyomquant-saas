# SERVICE_COMMUNICATION_REPORT

## 1. Async Messaging Paradigm
The API and the Trading Execution Engine (TEE) will communicate via **Redis Pub/Sub** and **Redis Streams**.

## 2. Message Flows

### A. Strategy Deployment (API -> TEE)
- **Action**: User clicks "Deploy" on a strategy.
- **API**: Updates PostgreSQL `strategies.status = 'deployed'`, then publishes a message to Redis Stream `stream:deployments` containing `{"strategy_id": "uuid", "action": "start"}`.
- **TEE**: Listens to the stream via a Redis Consumer Group. Acknowledges (`XACK`) the message to guarantee durability. Loads the strategy from PostgreSQL and begins execution.

### B. Strategy Termination (API -> TEE)
- **Action**: User clicks "Stop".
- **API**: Updates PostgreSQL, publishes `{"strategy_id": "uuid", "action": "stop"}`.
- **TEE**: Acknowledges message, cancels the active `asyncio` loop for that strategy, and removes it from memory.

### C. Execution Acknowledgement (TEE -> API)
- **Action**: TEE executes a live trade.
- **TEE**: Writes the order receipt to PostgreSQL.
- **API**: The user dashboard queries PostgreSQL. No direct TEE -> API message is required for state reading, as PostgreSQL acts as the persistent source of truth.

## 3. Failure Recovery
- **Redis Streams Consumer Groups** provide guaranteed delivery. If the TEE crashes before `XACK`, the message remains in the Pending Entries List (PEL).
- When the TEE restarts, it first processes its PEL to recover any unacknowledged deployment commands before reading new messages.

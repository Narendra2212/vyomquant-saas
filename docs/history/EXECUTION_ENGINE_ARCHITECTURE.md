# EXECUTION_ENGINE_ARCHITECTURE

## Overview
To support 1,000 active users, the `UnifiedExecutionEngine` must be extracted from the monolithic FastAPI application into a standalone microservice: the **Trading Execution Engine (TEE)**.

## Architecture

### 1. Separation of Concerns
- **FastAPI API Service**: Handles auth, dashboard UI, strategy CRUD, backtesting (via `rq`), and marketplace browsing. 
- **Trading Execution Engine (TEE)**: A pure Python daemon that loads deployed strategies from PostgreSQL, subscribes to market data, evaluates DAG indicators, and routes orders via CCXT.

### 2. Deployment Topology
- The TEE will be deployed as a headless Docker container.
- It will run on compute-optimized instances (e.g., AWS ECS Fargate or EC2 `c6g.large`) separate from the Web API.
- Health checks will be exposed via a lightweight internal HTTP server (e.g., `aiohttp` on port 8080) for orchestration.

### 3. Scaling & Leader Election
- **Duplicate Execution Prevention**: To prevent two engine instances from executing the same trade for the same user, the TEE must implement a Sharding or Leader Election model.
- **Implementation**: We will use Redis Locks (`Redlock` algorithm) or PostgreSQL advisory locks. Each bot/strategy ID will be hashed and assigned to a specific TEE worker node (Consistent Hashing), ensuring only one engine evaluates a specific strategy's state at any given second.

### 4. Market Data Ingestion
- **Current State**: REST API polling.
- **Required State**: WebSockets. At 1,000 users, REST polling for 500+ active paper trading bots will hit CCXT/Binance rate limits instantly. The TEE must maintain a single WebSocket connection per exchange/symbol pair, multiplexing the data feed to all loaded strategies.

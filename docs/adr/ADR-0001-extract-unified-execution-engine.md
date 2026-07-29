# ADR-0001: Extract Unified Execution Engine

## Status
Accepted

## Context
The VyomQuant trading architecture originally ran all processes (REST APIs, WebSockets, and trading execution loops) inside a monolithic FastAPI application. As user load models approached 1,000+ active users, this monolith presented a critical bottleneck. A single long-running strategy compilation or order matching loop could stall the Python `asyncio` event loop, causing API timeouts and missed WebSocket ticks for all users on the node.

## Decision
We decided to extract the `UnifiedExecutionEngine` into a standalone microservice (Trading Execution Engine / TEE). The FastAPI application is now strictly an API gateway and WebSocket router. Trading signals, strategy compilations, and executions are dispatched to the TEE workers via Redis Streams (`event_bus.py`). 

## Consequences
- **Positive**: Complete isolation between web traffic and trading execution. API latency is stable under high load.
- **Positive**: We can horizontally scale the TEE workers independently of the web API based on execution load.
- **Negative**: Increased deployment complexity (requires separate ECS task definitions for web and workers).
- **Negative**: Strict reliance on Redis Streams for inter-process communication introduces a dependency on Redis cluster uptime for trading.

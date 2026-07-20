# PERFORMANCE MONITORING AUDIT

## Objective
Inventory the performance tracking, system latency telemetry, and Prometheus metrics embedded within the DAG backend framework.

## Prometheus Metrics Inventory (`core/metrics.py`)
The system utilizes the Prometheus client to export several crucial metrics:
* `aerora_http_requests_total`: Tracks raw HTTP request throughput by method, endpoint, and status code.
* `aerora_http_request_duration_seconds`: Histogram mapping HTTP request latencies.
* `aerora_execution_attempts_total`: Tracks live execution occurrences.
* `aerora_execution_success_total`: Tracks successful trade executions.
* `aerora_duplicate_execution_skipped_total`: Tracks idempotency guardrail trigger counts.
* `aerora_redis_connected`: Infrastructure connection gauge.
* `aerora_postgres_connected`: Infrastructure connection gauge.
* `aerora_system_health`: High-level system health gauge.

## Performance Timings (Latency & Duration)

| Metric | Status | Notes |
|--------|--------|-------|
| **API Latency** | ✅ Present | Tracked globally via the `aerora_http_request_duration_seconds` Prometheus histogram. |
| **Backtest Duration** | ❌ Missing | No timing decorator or telemetry event captures the time spent completing a full backtest simulation in `_run_backtest_sync`. |
| **DAG Execution Duration** | ❌ Missing | No telemetry records the execution duration of the DAG computation block. |
| **Exchange Fetch Duration** | ❌ Missing | No granular telemetry records how long CCXT takes to fetch OHLCV bars. |

## Verdict: ⚠️ PARTIAL
While the platform provides excellent high-level API and Execution Prometheus metrics, the internal sub-components of the Backtest Engine are a complete black box. Latency spikes in backtesting, DAG processing, or exchange fetching will invisibly inflate `aerora_http_request_duration_seconds` without providing granular insight into the bottleneck. Advanced tracing spans must be added around CCXT fetch blocks and the `DAGEngine.execute_dag` loops.

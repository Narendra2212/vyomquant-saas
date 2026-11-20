# Observability Audit

## Sentry (org `aerora-dynamics-private-limited`, project `vyomquant-api-production`)

**Ingestion: PASS.** Events the audit itself caused showed up within seconds:
- the forged-webhook 403, at 03:16;
- the 0-bar backtest, at 03:05;
- the invalid-timeframe 500, on 11-19.

**Top unresolved issues, last 7 days, by frequency:**

| Issue | Events | Last seen | Assessment |
|---|---|---|---|
| 18 / 19 `PendingRollbackError` (watchdog session) | 6,732 | 11-14 | Same shared-session family as TXN-IDLE-01; the fix ends each cycle's transaction |
| 1G TRADE-07 recovery alert (owner testnet BTC/USDC) | 1,315 | 11-16 | Repeats every cycle for one recovered execution. Alert should de-duplicate (P3). |
| 10 / 1M PaperPersistenceError (PGRST002 / 521) | 1,074 | 11-17 | PostgREST schema-cache outages during the 11-15/17 incidents |
| 1N plan-expiry sweep APIError | 304 | 11-17 | Same window |
| 1X / 1Z pooler `terminating connection` / `ECIRCUITBREAKER too many authentication failures` | 431 | 11-17 | The DB credential-rotation incident of 11-15/16 |
| S naive vs aware datetime in `reconciliation_scheduler._needs_re_escalation` | 97 | 11-14 | Not recurring since |
| 4 "PRODUCTION MODE: Live trading enabled!" | ongoing | 11-19 | Informational logged at error level. Downgrade it (P3, alert noise). |

**PII:** issue titles carry user UUIDs and execution ids, but no emails, keys or tokens. One title includes a client IP (the webhook allowlist rejection). Acceptable for operations, but consider scrubbing it.

**Gaps (P3):**
- No issue is assigned.
- Several escalating issues have no alert rule tied to them.
- TRADE-07 alerts fire per cycle rather than once per execution.

## CloudWatch / ECS

**BLOCKED.** The AWS credentials available to the audit are expired; STS refused the call on 2026-11-20. Not verified:
- log retention;
- metric alarms on the ECS services;
- the 5xx / latency alarms;
- the backend task's `RESUME_LIVE_DEPLOYMENTS=true` at runtime (it was confirmed in the task definition earlier).

**Owner action:** refresh the AWS credentials and re-run the OBS-02 rows.

## Client telemetry

The frontend reports `SLOW_API_REQUEST` for calls over 1 s. A full crawl of 16 app pages logged 38 such events and nothing else: no page errors and no failed API calls (PERF-01).

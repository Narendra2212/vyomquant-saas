# INCIDENT_RESPONSE_PLAYBOOK

## P0 Incidents (Absolute Blockers)

### Trading Safety Issue (Errant Orders, Slippage loops)
- **Detection**: Kill Switch logs, User reports of phantom trades, sudden PnL drops.
- **Immediate Response**: Activate `AERORA_MODE=safe`. Shut down `UnifiedExecutionEngine` clusters.
- **Escalation**: CTO / Lead Trading Engineer immediately.
- **Recovery**: Patch engine logic. Run `EXCHANGE_RECOVERY_VALIDATION` to sync states.
- **Postmortem**: Required within 24 hours. Root cause analysis on algorithmic failure.

### Authentication Bypass / Data Breach
- **Detection**: Unusual API access patterns, users reporting seeing other users' data.
- **Immediate Response**: Revoke Supabase API keys. Scale API to 0. Force logout all sessions.
- **Escalation**: CTO / Security Lead.
- **Recovery**: Identify RLS failure or JWT leak. Patch, audit logs for exposure, notify affected users.
- **Postmortem**: Required.

### Data Corruption
- **Detection**: Database constraint failures, null pointers in UI, clone lineage broken.
- **Immediate Response**: Read-only mode activated.
- **Escalation**: Lead Backend Engineer.
- **Recovery**: Restore from Supabase PITR to the hour prior to corruption.
- **Postmortem**: Required.

---

## P1 Incidents (Severe Degradation)

### Backend Outage (API 502/503)
- **Detection**: Pingdom/Datadog alerts, Sentry spikes.
- **Immediate Response**: Check Docker container health. Restart ECS/Render tasks. Check DB connections.
- **Escalation**: DevOps.
- **Recovery**: If out of memory (OOM), scale up instance size. If DB locked, terminate idle queries.
- **Postmortem**: Required within 72 hours.

### Database Outage
- **Detection**: Supabase console alerts, 100% API failure rate with timeout errors.
- **Immediate Response**: Contact Supabase Support. Check connection pooler limits.
- **Escalation**: DevOps.
- **Recovery**: Increase connection limits or upgrade tier.
- **Postmortem**: Required.

### Marketplace Failure
- **Detection**: Users cannot publish or browse strategies.
- **Immediate Response**: Ensure it's not a global API outage. Rollback latest Marketplace deployment.
- **Escalation**: Lead Backend.
- **Recovery**: Fix SQL syntax/indexing issues.
- **Postmortem**: Brief slack summary.

---

## P2 Incidents (Moderate/Minor)

### UI Bug
- **Detection**: User reports (e.g. Button doesn't click, chart doesn't render).
- **Immediate Response**: Log in JIRA.
- **Escalation**: Frontend Team.
- **Recovery**: Fix in the next weekly sprint.
- **Postmortem**: None.

### Performance Degradation
- **Detection**: Backtests taking > 3s, Marketplace load taking > 1s.
- **Immediate Response**: Monitor, no immediate halt required.
- **Escalation**: Backend Team.
- **Recovery**: Implement Redis caching or optimize Numba compilation cache.
- **Postmortem**: None.

# PLATFORM_BUG_REGISTER

The following bugs and gaps were identified during Sprint 3.0 Runtime Certification.

### BUG-301: Missing Strategy Cover Images Storage Pipeline
- **Severity**: Medium
- **Root Cause**: The Supabase `strategy-images` bucket exists, but the backend `library.py` endpoints do not accept file uploads, and the frontend Marketplace does not submit `FormData`. 
- **Reproduction**: N/A (Feature deferred).
- **Recommended Fix**: Add a `POST /api/library/{id}/image` endpoint using Supabase Storage Python client, and update the UI to allow image upload during Publishing.

### BUG-302: N+1 Query in Marketplace Browse (Author Aliases)
- **Severity**: Low
- **Root Cause**: To fetch the author name for each strategy card, the API maps over the strategy results and executes individual profile lookups.
- **Reproduction**: Hit `GET /api/library`. Note that if `limit=50`, 51 database queries are executed.
- **Recommended Fix**: Add a denormalized `author_alias` column to `library_strategies` updated via Postgres Trigger, or use a SQL `JOIN` across schemas if permissions allow.

### BUG-303: Synchronous Rating Recalculation
- **Severity**: Low
- **Root Cause**: When a user rates a strategy, `_recompute_avg_rating()` runs synchronously before returning the API response, adding latency to the user experience.
- **Reproduction**: Submit a rating via `POST /api/library/{id}/rate`. Observe minor latency spike.
- **Recommended Fix**: Offload this operation to a background worker (e.g., Celery) or a background task (`BackgroundTasks` in FastAPI).

### BUG-304: Missing Redis Caching
- **Severity**: Medium
- **Root Cause**: Explicitly skipped in V1 implementation due to strict focus on database foundations.
- **Reproduction**: Refreshing the Marketplace homepage repeatedly hits the primary Postgres database with expensive `ORDER BY` operations.
- **Recommended Fix**: Implement Redis caching with 60s TTL for public marketplace queries in the upcoming Metrics Worker sprint.

### BUG-305: Notification Provider Missing
- **Severity**: Low
- **Root Cause**: The AlertEngine currently only logs to the console `[AlertEngine] No providers configured!`.
- **Reproduction**: Trigger an execution error and check logs.
- **Recommended Fix**: Implement SMTP / SendGrid / Telegram plugins for the AlertEngine.

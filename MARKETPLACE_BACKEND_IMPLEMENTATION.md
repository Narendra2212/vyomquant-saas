# MARKETPLACE_BACKEND_IMPLEMENTATION

## Implemented Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/library` | Optional | Browse catalogue (paginated, filterable, sortable) |
| `GET` | `/api/library/me` | Required | My published strategies |
| `GET` | `/api/library/{id}` | Optional | Strategy detail + equity curve + recent ratings |
| `POST` | `/api/library` | Required | Publish strategy |
| `DELETE` | `/api/library/{id}` | Required (author) | Soft unpublish |
| `POST` | `/api/library/{id}/clone` | Required | Clone into user's builder |
| `POST` | `/api/library/{id}/rate` | Required (verified cloner) | Submit / update rating |
| `PATCH` | `/api/library/admin/{id}` | Admin only | Approve / reject / feature |
| `GET` | `/api/library/admin/pending` | Admin only | Pending moderation queue |

## Files Modified

| File | Change |
|---|---|
| [`backend_app/routers/library.py`](file:///c:/aerora_quant_backend_updated_final1/backend_app/routers/library.py) | **NEW** — Full library router, 9 endpoints |
| [`backend_app/main.py`](file:///c:/aerora_quant_backend_updated_final1/backend_app/main.py) | Added `library` to router imports; registered at `/api/library` |

## Authentication

- **Browse (`GET /api/library`, `GET /api/library/{id}`)**: Auth is optional. Authenticated users receive `user_has_cloned` and `user_rating` enrichment. Unauthenticated users receive the full public catalogue.
- **All write endpoints**: Require a valid Supabase JWT via `Depends(get_current_user)`. Tokens are verified using `decode_token_local()` from the existing `auth_middleware`.
- **Admin endpoints**: Require `app_metadata.role == "admin"` via `Depends(get_admin_user)`. No service-role bearer accepted as a human admin credential.

## Authorization

- **Publish**: Verifies `strategy.user_id == auth.uid()` before inserting.
- **Unpublish**: Verifies `library_strategy.author_id == auth.uid()` before updating.
- **Clone**: Blocks `library_strategy.author_id == auth.uid()` (self-clone prevention).
- **Rate**: Verifies `library_ratings` row exists with `is_verified_clone=TRUE` for the user before accepting a rating.
- **Admin Moderate**: Gated by `get_admin_user()` — 403 if not admin role.

## Database Operations

| Endpoint | Tables read | Tables written |
|---|---|---|
| Browse | `library_strategies`, `library_ratings` (auth enrich) | — |
| Detail | `library_strategies`, `library_ratings` | — |
| Publish | `strategies` (ownership), `library_strategies` (dup check) | `library_strategies` (INSERT) |
| Unpublish | `library_strategies` | `library_strategies.is_active = FALSE` |
| Clone | `library_strategies`, `strategies` (DAG fetch, service-role) | `strategies` (INSERT), `library_strategies.clone_count++`, `library_ratings` (UPSERT) |
| Rate | `library_strategies`, `library_ratings` (clone check) | `library_ratings` (UPSERT), `library_strategies.avg_rating` (recompute) |
| Admin Moderate | `library_strategies` | `library_strategies` (UPDATE) |
| Admin Pending | `library_strategies` | — |

## Transactions

- **Clone flow**: The new `strategies` row is inserted first. `clone_count` increment and `library_ratings` verified-clone marker are applied after. If the increment or rating marker fails after a successful INSERT, failures are logged but do not roll back the clone — this is intentional to preserve the cloned strategy. The metrics worker (Sprint 2A.3) will reconcile counts.
- **Publish flow**: All validation runs before the single INSERT into `library_strategies`. No partial state is possible.
- **Rate flow**: Upsert is atomic at the DB level (ON CONFLICT). `avg_rating` recompute runs after the upsert and is retried-on-failure in the metrics worker.

## Security Checks

- **UUID validation**: All path parameter `library_id` and `strategy_id` values are validated through `_safe_uuid()` which calls `UUID(str(value))` — rejects any non-UUID injection attempt with HTTP 422.
- **Input validation**: All request bodies use Pydantic models with field constraints (`max_length`, `ge`, `le`, `validator`).
- **Service-role scope**: The service-role client (`_get_service_client()`) is used only for:
  1. Cross-user DAG fetch during clone (tightly scoped to `source_strategy_id` row)
  2. `clone_count` and `avg_rating` writes (background metric updates)
  3. Admin author alias resolution
  Never used for user-facing authenticated write operations.
- **SQL injection**: All DB access is via the Supabase Python client's chained query builder. No raw SQL strings with user input.
- **Tenant isolation**: Ownership checks use `user_id` extracted from the validated JWT, not from the request body.

## Error Handling

All endpoints return structured HTTP error codes:

| Code | Scenarios |
|---|---|
| `400` | Missing backtest result; no action nodes in DAG |
| `401` | Missing or invalid JWT on protected endpoints |
| `403` | Not the strategy owner; not the library entry author; trying to clone own strategy; rating without cloning; not admin |
| `404` | Strategy not found; library entry not found; source strategy deleted |
| `409` | Strategy already published; strategy already unpublished; already cloned |
| `422` | Invalid UUID in path; Pydantic validation failure (bad rating value, invalid tag, etc.) |
| `500` | Supabase connectivity failure; unexpected DB error |

## Known Limitations

1. **Redis caching is not implemented** (deferred to Sprint 2A.3 background workers). All browse requests hit Supabase directly. This is correct for MVP; the metrics worker sprint will add Redis.
2. **Rate limiting** (per-user publish/clone/rate limits) is not yet enforced at the API layer. Deferred to Sprint 2A.3 or infrastructure layer (API gateway).
3. **Admin `GET /api/admin/library/pending`** endpoint uses the path `/admin/pending` on the library router. Because the library router is mounted at `/api/library`, the full path is `/api/library/admin/pending`. This matches the design document.
4. **Author alias**: Currently fetched individually per item during browse (N+1). Acceptable for MVP page sizes (≤50). The metrics worker sprint should denormalize the alias into `library_strategies`.

## Remaining Work

- Sprint 2A.3: Frontend integration (StrategyMarketplace.jsx mock replacement).
- Sprint 2A.4: Background metrics worker (`library_metrics_worker.py` — Redis caching, clone_count/avg_rating aggregation).
- Sprint 2A.5: Telemetry events (`strategy_published`, `strategy_cloned`, `strategy_rated`).
- Rate limiting on publish / clone endpoints.

**BACKEND COMPLETE**

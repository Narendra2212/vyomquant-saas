# CONNECTION_POOLING_REPORT

## PgBouncer Enforcement
- **Component**: `backend_app/core/database_pool.py`.
- **Change**: Added logic to dynamically rewrite `SUPABASE_DB_URL` if it detects direct port `5432` on `supabase.com` or `supabase.co` domains.
- **Rewrite Rule**: Swaps `:5432` to `:6543` and appends `?pgbouncer=true&pool_mode=transaction`.

## Impact
- **Connection Exhaustion**: Prevents the PostgreSQL instance from hitting its strict logical connection limit.
- **Transaction Safety**: `pool_mode=transaction` is explicitly forced to ensure SQLAlchemy's standard commit/rollback lifecycle operates safely through the pooler without contaminating states across different requests.

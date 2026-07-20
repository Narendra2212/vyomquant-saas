# PRODUCTION_LAUNCH_CHECKLIST

## Phase 1: Environment & Secrets Provisioning
- [ ] Provision Production Supabase Project (Enable PITR).
- [ ] Rotate all Supabase JWT secrets.
- [ ] Provision Sentry DSN for Backend and Frontend.
- [ ] Generate AES-256 `MASTER_ENCRYPTION_KEYS` for the `SecurityVault`.
- [ ] Set `AERORA_MODE=live` to disable all sandbox logic.

## Phase 2: Database & Migrations
- [ ] Run `alembic upgrade head` on the production database.
- [ ] Execute `Supabase` RLS policies.
- [ ] Manually publish 5 "Gold Standard" strategies to the marketplace so it isn't empty on launch day.

## Phase 3: Infrastructure Deployment
- [ ] Deploy Frontend to Vercel/Cloudflare Pages (with `VITE_SENTRY_DSN` and `VITE_SUPABASE_URL`).
- [ ] Deploy Backend to ECS/Render (with all ENV secrets).
- [ ] Map DNS (`app.algo22.io` to Frontend, `api.algo22.io` to Backend).
- [ ] Verify CORS allows `app.algo22.io`.
- [ ] Ensure Load Balancer forces HTTPS (`Strict-Transport-Security`).

## Phase 4: Final Smoke Test
- [ ] Register a test user.
- [ ] Verify Sentry receives a test exception.
- [ ] Verify API rate limiting blocks a script attempting to hit `/api/library` > 20 times in 1 minute.
- [ ] Verify Paper Trading can execute a 1-node DAG order.

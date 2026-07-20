# PRIVATE_BETA_LAUNCH_PLAN

## Launch Prerequisites
1. **Infrastructure**: Provision production PostgreSQL (Supabase), FastAPI clusters (e.g., AWS ECS / Render), and static UI hosting (Vercel).
2. **Environment Variables**: Configure all strict production secrets (disable `DEV_MODE`).
3. **Data Seeding**: Create 5-10 "Gold Standard" strategies published by Aerora Admins so the Marketplace is not empty on Day 1.
4. **Telemetry**: (Optional but recommended) Hook up a basic Sentry project for error trapping.

## Known Limitations
- Strategy cover images are not supported.
- Marketplace search executes direct SQL `ilike` operations (no Redis caching/ElasticSearch).
- Password resets might require manual support requests if the UI form is incomplete.

## Rollback Plan
If a severe state-corruption or execution loop bug is detected:
1. **Kill Switch**: Trigger the Global Kill Request to immediately halt all UnifiedExecutionEngine instances and block new trades.
2. **Database Snapshot**: Revert the Supabase database to the last hourly PITR (Point-In-Time-Recovery) snapshot if required.
3. **App Revert**: Redeploy the `v0.9.x` stable Docker image tags.

## Monitoring Recommendations
- Monitor FastAPI HTTP 500 error rates.
- Monitor execution slippage metrics in the `order_executions` table.
- Monitor Database CPU utilization during heavy backtest loads.

## Support Recommendations
- Establish a private Discord/Slack for the 100 beta testers.
- Have at least 1 Admin monitoring the `GET /api/library/admin/pending` queue daily to approve high-quality community strategies quickly.

## Success Metrics (First 100 Users)
- **Adoption**: > 50% of users successfully create or clone at least 1 strategy.
- **Engagement**: > 20 published strategies hit the marketplace within 14 days.
- **Stability**: Zero unhandled order execution mismatches. Zero unauthorized access incidents. System uptime > 99.5%.

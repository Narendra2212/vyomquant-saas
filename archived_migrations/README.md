# Archived Legacy Migration Files

> **NOTICE**: The database schema evolution for this repository has been consolidated into a single source of truth using **Alembic** under [`backend_app/alembic/`](../backend_app/alembic/).

The files in this directory are preserved for historical reference and audit purposes. They represent the legacy, multi-source migration scripts that were previously scattered across the repository before consolidation:

1. `root_migrations/`: Formerly in `/migrations/` (DAG tasks, execution records, strategy library, RLS policies).
2. `supabase_migrations/`: Formerly in `/supabase/migrations/` (copilot sessions and messages).
3. `terminal_supabase_migrations/`: Formerly in `/algo22-terminal/supabase/migrations/` (waitlist tables and fields).

All tables, indexes, constraints, ENUM types, triggers, and RLS policies defined in these legacy files have been fully incorporated into the Alembic version history in `backend_app/alembic/versions/`.

**Do not execute or add new SQL scripts to this directory.** All future database migrations must be created and executed using Alembic:
```bash
# Generate a new migration
alembic revision --autogenerate -m "description"

# Apply pending migrations
alembic upgrade head
```

# Final Database Execution Plan

**Target Database:** Supabase

## Migration: `20240623_copilot_tables.sql`

### 1. New Tables
* `copilot_sessions`
* `copilot_messages`

### 2. Indexes
* `idx_copilot_sessions_user_id` on `copilot_sessions(user_id)`
* `idx_copilot_messages_session_id` on `copilot_messages(session_id)`

### 3. Foreign Keys
* `copilot_sessions.user_id` -> `auth.users(id) ON DELETE CASCADE`
* `copilot_messages.session_id` -> `copilot_sessions(id) ON DELETE CASCADE`

### 4. RLS Policies
* Enabled on both tables.
* SELECT, INSERT, UPDATE, DELETE restricted to `auth.uid() = user_id`.

## Rollback SQL
```sql
DROP TABLE IF EXISTS copilot_messages CASCADE;
DROP TABLE IF EXISTS copilot_sessions CASCADE;
```

## Status
Pending User Approval before applying.

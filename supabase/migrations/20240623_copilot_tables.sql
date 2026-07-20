-- VyomQuant Copilot tables with RLS

-- Sessions table
CREATE TABLE IF NOT EXISTS copilot_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Messages table
CREATE TABLE IF NOT EXISTS copilot_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES copilot_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    context_snapshot JSONB DEFAULT NULL,
    token_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_copilot_sessions_user_id ON copilot_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_copilot_messages_session_id ON copilot_messages(session_id);

-- RLS Policies
ALTER TABLE copilot_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE copilot_messages ENABLE ROW LEVEL SECURITY;

-- Sessions policies
CREATE POLICY "Users can view own sessions" ON copilot_sessions
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own sessions" ON copilot_sessions
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own sessions" ON copilot_sessions
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own sessions" ON copilot_sessions
    FOR DELETE USING (auth.uid() = user_id);

-- Messages policies
CREATE POLICY "Users can view messages in own sessions" ON copilot_messages
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM copilot_sessions s
            WHERE s.id = copilot_messages.session_id AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can insert messages in own sessions" ON copilot_messages
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM copilot_sessions s
            WHERE s.id = copilot_messages.session_id AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can delete messages in own sessions" ON copilot_messages
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM copilot_sessions s
            WHERE s.id = copilot_messages.session_id AND s.user_id = auth.uid()
        )
    );

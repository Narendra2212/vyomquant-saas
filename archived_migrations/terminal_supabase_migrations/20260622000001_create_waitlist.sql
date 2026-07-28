-- Migration: Create waitlist table with Telegram support
-- Date: 2026-06-22

CREATE TABLE IF NOT EXISTS public.waitlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    telegram TEXT,
    experience_level TEXT NOT NULL CHECK (experience_level IN ('beginner', 'intermediate', 'advanced', 'professional')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'invited', 'converted')),
    notes TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    ip_address INET
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_waitlist_created_at ON public.waitlist(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_waitlist_status ON public.waitlist(status);
CREATE INDEX IF NOT EXISTS idx_waitlist_experience ON public.waitlist(experience_level);
CREATE INDEX IF NOT EXISTS idx_waitlist_email ON public.waitlist(email);

-- Row Level Security (RLS) policies
ALTER TABLE public.waitlist ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public insert" ON public.waitlist
    FOR INSERT TO anon, authenticated
    WITH CHECK (true);

CREATE POLICY "Allow users to read own entry" ON public.waitlist
    FOR SELECT TO authenticated
    USING (auth.uid()::text = id::text);

CREATE POLICY "Allow admin read all" ON public.waitlist
    FOR SELECT TO authenticated
    USING (auth.jwt() ->> 'role' = 'admin');

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

-- Allow anyone to insert (public waitlist signup)
CREATE POLICY "Allow public insert" ON public.waitlist
    FOR INSERT TO anon, authenticated
    WITH CHECK (true);

-- Allow authenticated users to read their own entry
CREATE POLICY "Allow users to read own entry" ON public.waitlist
    FOR SELECT TO authenticated
    USING (auth.uid()::text = id::text);

-- Admin policy: allow service role or specific admin role to read all
CREATE POLICY "Allow admin read all" ON public.waitlist
    FOR SELECT TO authenticated
    USING (auth.jwt() ->> 'role' = 'admin');

-- Comments for documentation
COMMENT ON TABLE public.waitlist IS 'Waitlist entries for VyomQuant early access';
COMMENT ON COLUMN public.waitlist.telegram IS 'Optional Telegram username or phone number for direct contact';
COMMENT ON COLUMN public.waitlist.experience_level IS 'Trading experience: beginner, intermediate, advanced, professional';
COMMENT ON COLUMN public.waitlist.status IS 'Lifecycle: pending, approved, invited, converted';

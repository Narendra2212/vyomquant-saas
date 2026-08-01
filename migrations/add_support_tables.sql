-- ══════════════════════════════════════════════════════════════════════════
-- SUPPORT TICKETS SYSTEM MIGRATION
-- ══════════════════════════════════════════════════════════════════════════
-- 
-- Creates support_tickets and ticket_comments tables for institutional support system
-- Includes proper indexes, constraints, foreign keys, and RLS policies
-- 
-- ══════════════════════════════════════════════════════════════════════════

-- Create support_tickets table
CREATE TABLE IF NOT EXISTS support_tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  subject VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  category VARCHAR(50) NOT NULL CHECK (category IN ('general', 'technical', 'billing', 'security', 'feature')),
  priority VARCHAR(20) NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
  status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  resolved_at TIMESTAMP WITH TIME ZONE,
  has_unread BOOLEAN DEFAULT FALSE,
  comment_count INTEGER DEFAULT 0
);

-- Create ticket_comments table
CREATE TABLE IF NOT EXISTS ticket_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  message TEXT NOT NULL,
  is_staff BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_support_tickets_user_id ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status);
CREATE INDEX IF NOT EXISTS idx_support_tickets_category ON support_tickets(category);
CREATE INDEX IF NOT EXISTS idx_support_tickets_priority ON support_tickets(priority);
CREATE INDEX IF NOT EXISTS idx_support_tickets_created_at ON support_tickets(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_tickets_user_status ON support_tickets(user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket_id ON ticket_comments(ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_user_id ON ticket_comments(user_id);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_created_at ON ticket_comments(created_at DESC);

-- Enable Row Level Security
ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket_comments ENABLE ROW LEVEL SECURITY;

-- RLS Policies for support_tickets
CREATE POLICY "Users can view own tickets" 
ON support_tickets FOR SELECT 
USING (auth.uid() = user_id);

CREATE POLICY "Users can create own tickets"
ON support_tickets FOR INSERT
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own tickets"
ON support_tickets FOR UPDATE
USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own tickets"
ON support_tickets FOR DELETE
USING (auth.uid() = user_id);

-- RLS Policies for ticket_comments
CREATE POLICY "Users can view comments on own tickets"
ON ticket_comments FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM support_tickets 
    WHERE support_tickets.id = ticket_comments.ticket_id 
    AND support_tickets.user_id = auth.uid()
  )
);

CREATE POLICY "Users can create comments on own tickets"
ON ticket_comments FOR INSERT
WITH CHECK (
  EXISTS (
    SELECT 1 FROM support_tickets 
    WHERE support_tickets.id = ticket_comments.ticket_id 
    AND support_tickets.user_id = auth.uid()
  )
);

-- Grant necessary permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON support_tickets TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON ticket_comments TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE support_tickets_id_seq TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE ticket_comments_id_seq TO authenticated;

-- Add helpful comments
COMMENT ON TABLE support_tickets IS 'User support tickets for institutional help desk system';
COMMENT ON TABLE ticket_comments IS 'Comments and replies for support tickets';
COMMENT ON COLUMN support_tickets.category IS 'Ticket category: general, technical, billing, security, feature';
COMMENT ON COLUMN support_tickets.priority IS 'Ticket priority: low, medium, high, urgent';
COMMENT ON COLUMN support_tickets.status IS 'Ticket status: open, in_progress, resolved, closed';
COMMENT ON COLUMN ticket_comments.is_staff IS 'True if comment is from support staff';

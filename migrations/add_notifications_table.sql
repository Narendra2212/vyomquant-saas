-- ══════════════════════════════════════════════════════════════════════════
-- NOTIFICATIONS TABLE MIGRATION
-- ══════════════════════════════════════════════════════════════════════════
-- 
-- Creates the notifications table for institutional notification system
-- Supports real-time trading alerts, system events, and user notifications
-- 
-- Categories: trade, strategy, risk, security, billing, system
-- Severity: info, warning, critical, emergency
-- 
-- ══════════════════════════════════════════════════════════════════════════

-- Create notifications table
CREATE TABLE IF NOT EXISTS notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  type VARCHAR(50) NOT NULL,
  category VARCHAR(50) NOT NULL,
  severity VARCHAR(20) NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  strategy_id UUID,
  exchange VARCHAR(50),
  metadata JSONB DEFAULT '{}',
  read BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(read);
CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_category ON notifications(category);
CREATE INDEX IF NOT EXISTS idx_notifications_severity ON notifications(severity);

-- Enable Row Level Security
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- RLS Policies: Users can only access their own notifications
CREATE POLICY "Users can view own notifications" 
ON notifications FOR SELECT 
USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own notifications"
ON notifications FOR INSERT
WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own notifications"
ON notifications FOR UPDATE
USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own notifications"
ON notifications FOR DELETE
USING (auth.uid() = user_id);

-- Grant necessary permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON notifications TO authenticated;
GRANT USAGE, SELECT ON SEQUENCE notifications_id_seq TO authenticated;

-- Add helpful comment
COMMENT ON TABLE notifications IS 'User notifications for trading events, system alerts, and account updates';
COMMENT ON COLUMN notifications.type IS 'Specific notification type (e.g., order_filled, strategy_started)';
COMMENT ON COLUMN notifications.category IS 'High-level category (trade, strategy, risk, security, billing, system)';
COMMENT ON COLUMN notifications.severity IS 'Alert severity (info, warning, critical, emergency)';
COMMENT ON COLUMN notifications.metadata IS 'Additional structured data for the notification';

-- ══════════════════════════════════════════════════════════════════════════
-- MIGRATION: 006_reconcile_production_database.sql
-- DESCRIPTION: Complete database forensic reconciliation for VyomQuant SaaS
-- AUTHOR: Senior Production Database & Security Engineer
-- DATE: 2026-08-14
-- SAFETY: Fully idempotent, additive only, non-destructive, with strict RLS
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 1: SECURE PROFILES TABLE (ENABLE RLS & POLICIES)
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "profiles_authenticated_owner" ON public.profiles;
    DROP POLICY IF EXISTS "profiles_owner_access" ON public.profiles;
    DROP POLICY IF EXISTS "profiles_service_role" ON public.profiles;
    DROP POLICY IF EXISTS "profiles_public_read" ON public.profiles;
    
    CREATE POLICY "profiles_owner_access" ON public.profiles
        FOR ALL
        TO authenticated
        USING (auth.uid() = id)
        WITH CHECK (auth.uid() = id);

    CREATE POLICY "profiles_service_role" ON public.profiles
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 2: ADD MISSING COLUMNS TO EXISTING TABLES (NON-DESTRUCTIVE)
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS available_discounts INTEGER DEFAULT 0 NOT NULL;
ALTER TABLE public.strategies ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE NOT NULL;
ALTER TABLE public.strategies ADD COLUMN IF NOT EXISTS dag_config JSONB;
ALTER TABLE public.library_strategies ADD COLUMN IF NOT EXISTS subscriber_count INTEGER DEFAULT 0 NOT NULL;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 3: MARKETPLACE & SUBSCRIPTION TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.library_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
    strategy_id UUID REFERENCES public.library_strategies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    subscription_tier TEXT NOT NULL DEFAULT 'standard',
    price_paid NUMERIC(10, 2) DEFAULT 0.00,
    currency TEXT DEFAULT 'USD',
    payment_provider TEXT,
    payment_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    auto_renew BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_library_subscriptions_user_lib UNIQUE (library_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_lib_subs_user ON public.library_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_lib_subs_lib ON public.library_subscriptions(library_id);
CREATE INDEX IF NOT EXISTS idx_lib_subs_status ON public.library_subscriptions(status);

ALTER TABLE public.library_subscriptions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "lib_subs_owner_access" ON public.library_subscriptions;
    DROP POLICY IF EXISTS "lib_subs_service_role" ON public.library_subscriptions;
    
    CREATE POLICY "lib_subs_owner_access" ON public.library_subscriptions
        FOR ALL
        TO authenticated
        USING (auth.uid() = user_id)
        WITH CHECK (auth.uid() = user_id);

    CREATE POLICY "lib_subs_service_role" ON public.library_subscriptions
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.deployment_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    library_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
    granted_via TEXT NOT NULL DEFAULT 'subscription',
    subscription_id UUID REFERENCES public.library_subscriptions(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_deployment_permissions UNIQUE (user_id, library_id, is_active)
);

CREATE INDEX IF NOT EXISTS idx_deploy_perm_user ON public.deployment_permissions(user_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_deploy_perm_lib ON public.deployment_permissions(library_id) WHERE is_active = TRUE;

ALTER TABLE public.deployment_permissions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "deploy_perm_owner_access" ON public.deployment_permissions;
    DROP POLICY IF EXISTS "deploy_perm_service_role" ON public.deployment_permissions;
    
    CREATE POLICY "deploy_perm_owner_access" ON public.deployment_permissions
        FOR ALL
        TO authenticated
        USING (auth.uid() = user_id)
        WITH CHECK (auth.uid() = user_id);

    CREATE POLICY "deploy_perm_service_role" ON public.deployment_permissions
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 4: RISK SETTINGS & STRATEGY LIMITS TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.risk_settings (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL UNIQUE,
    max_daily_loss DECIMAL(10, 2) NOT NULL DEFAULT 500.00,
    max_positions INTEGER NOT NULL DEFAULT 10,
    max_leverage INTEGER NOT NULL DEFAULT 3,
    kill_switches JSONB DEFAULT '[]'::jsonb,
    require_stop_loss BOOLEAN DEFAULT TRUE,
    auto_hedging BOOLEAN DEFAULT FALSE,
    kill_switch_enabled BOOLEAN DEFAULT FALSE,
    max_drawdown_pct DECIMAL(5, 4) DEFAULT 0.1500,
    daily_loss_limit_pct DECIMAL(5, 4) DEFAULT 0.0500,
    max_position_size_usd DECIMAL(10, 2) DEFAULT 5000.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_settings_user ON public.risk_settings(user_id);
ALTER TABLE public.risk_settings ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "risk_settings_owner_access" ON public.risk_settings;
    DROP POLICY IF EXISTS "risk_settings_service_role" ON public.risk_settings;
    
    CREATE POLICY "risk_settings_owner_access" ON public.risk_settings
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "risk_settings_service_role" ON public.risk_settings
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.strategy_limits (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    max_position_size DECIMAL(10, 2) NOT NULL DEFAULT 1000.00,
    max_daily_trades INTEGER NOT NULL DEFAULT 100,
    allowed_symbols TEXT[] DEFAULT '{}',
    max_drawdown_pct DECIMAL(5, 4) NOT NULL DEFAULT 0.1000,
    max_allocation_usd DECIMAL(10, 2) DEFAULT 10000.00,
    max_leverage INTEGER DEFAULT 3,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_strategy_limits UNIQUE(user_id, strategy_id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_limits_user ON public.strategy_limits(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_limits_strat ON public.strategy_limits(strategy_id);
ALTER TABLE public.strategy_limits ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "strategy_limits_owner_access" ON public.strategy_limits;
    DROP POLICY IF EXISTS "strategy_limits_service_role" ON public.strategy_limits;
    
    CREATE POLICY "strategy_limits_owner_access" ON public.strategy_limits
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "strategy_limits_service_role" ON public.strategy_limits
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.risk_settings_audit (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_values JSONB,
    new_values JSONB,
    changed_by TEXT NOT NULL,
    ip_address TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_audit_user ON public.risk_settings_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_risk_audit_created ON public.risk_settings_audit(created_at DESC);
ALTER TABLE public.risk_settings_audit ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "risk_audit_owner_read" ON public.risk_settings_audit;
    DROP POLICY IF EXISTS "risk_audit_service_role" ON public.risk_settings_audit;
    
    CREATE POLICY "risk_audit_owner_read" ON public.risk_settings_audit
        FOR SELECT
        TO authenticated
        USING (auth.uid()::text = user_id);

    CREATE POLICY "risk_audit_service_role" ON public.risk_settings_audit
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 5: NOTIFICATIONS & USER SETTINGS TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL DEFAULT 'system',
    category VARCHAR(50) NOT NULL DEFAULT 'system',
    severity VARCHAR(20) NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    strategy_id UUID,
    exchange VARCHAR(50),
    metadata JSONB DEFAULT '{}'::jsonb,
    read BOOLEAN DEFAULT FALSE,
    link VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON public.notifications(user_id, read);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON public.notifications(created_at DESC);
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "notifications_owner_access" ON public.notifications;
    DROP POLICY IF EXISTS "notifications_service_role" ON public.notifications;
    
    CREATE POLICY "notifications_owner_access" ON public.notifications
        FOR ALL
        TO authenticated
        USING (auth.uid() = user_id)
        WITH CHECK (auth.uid() = user_id);

    CREATE POLICY "notifications_service_role" ON public.notifications
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.notification_settings (
    user_id TEXT PRIMARY KEY,
    channels JSONB DEFAULT '{"email": true, "telegram": false, "in_app": true, "push": true}'::jsonb,
    events JSONB DEFAULT '{"trade_fills": true, "stop_loss": true, "risk_alerts": true, "system": true}'::jsonb,
    email_alerts BOOLEAN DEFAULT TRUE,
    telegram_alerts BOOLEAN DEFAULT FALSE,
    sms_alerts BOOLEAN DEFAULT FALSE,
    push_notifications BOOLEAN DEFAULT TRUE,
    webhook_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.notification_settings ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "notif_settings_owner_access" ON public.notification_settings;
    DROP POLICY IF EXISTS "notif_settings_service_role" ON public.notification_settings;
    
    CREATE POLICY "notif_settings_owner_access" ON public.notification_settings
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "notif_settings_service_role" ON public.notification_settings
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.security_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    ip_address VARCHAR(45),
    user_agent TEXT,
    details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_security_logs_user ON public.security_logs(user_id, created_at DESC);
ALTER TABLE public.security_logs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "security_logs_owner_read" ON public.security_logs;
    DROP POLICY IF EXISTS "security_logs_service_role" ON public.security_logs;
    
    CREATE POLICY "security_logs_owner_read" ON public.security_logs
        FOR SELECT
        TO authenticated
        USING (auth.uid()::text = user_id);

    CREATE POLICY "security_logs_service_role" ON public.security_logs
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 6: SUPPORT TICKETS & COMMENTS TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.support_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    subject VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(50) NOT NULL DEFAULT 'general',
    priority VARCHAR(20) NOT NULL DEFAULT 'medium',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    has_unread BOOLEAN DEFAULT FALSE,
    comment_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON public.support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON public.support_tickets(status);
ALTER TABLE public.support_tickets ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "support_tickets_owner_access" ON public.support_tickets;
    DROP POLICY IF EXISTS "support_tickets_service_role" ON public.support_tickets;
    
    CREATE POLICY "support_tickets_owner_access" ON public.support_tickets
        FOR ALL
        TO authenticated
        USING (auth.uid() = user_id)
        WITH CHECK (auth.uid() = user_id);

    CREATE POLICY "support_tickets_service_role" ON public.support_tickets
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.ticket_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id UUID NOT NULL REFERENCES public.support_tickets(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    comment TEXT,
    is_staff BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket ON public.ticket_comments(ticket_id);
ALTER TABLE public.ticket_comments ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "ticket_comments_owner_access" ON public.ticket_comments;
    DROP POLICY IF EXISTS "ticket_comments_service_role" ON public.ticket_comments;
    
    CREATE POLICY "ticket_comments_owner_access" ON public.ticket_comments
        FOR ALL
        TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM public.support_tickets 
                WHERE public.support_tickets.id = ticket_comments.ticket_id 
                AND public.support_tickets.user_id = auth.uid()
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM public.support_tickets 
                WHERE public.support_tickets.id = ticket_comments.ticket_id 
                AND public.support_tickets.user_id = auth.uid()
            )
        );

    CREATE POLICY "ticket_comments_service_role" ON public.ticket_comments
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 7: STRATEGY BACKTESTS & RESEARCH REPORTS
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.strategy_backtests (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    blueprint JSONB,
    dataset TEXT,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    initial_capital NUMERIC(15, 2) DEFAULT 10000.00,
    commission NUMERIC(6, 4) DEFAULT 0.001,
    slippage NUMERIC(6, 4) DEFAULT 0.0005,
    status VARCHAR(50) DEFAULT 'pending',
    total_return NUMERIC(15, 2),
    total_return_pct NUMERIC(10, 4),
    win_rate NUMERIC(6, 4),
    max_drawdown NUMERIC(10, 4),
    sharpe_ratio NUMERIC(8, 4),
    sortino_ratio NUMERIC(8, 4),
    profit_factor NUMERIC(8, 4),
    total_trades INTEGER,
    metrics JSONB DEFAULT '{}'::jsonb,
    trades JSONB DEFAULT '[]'::jsonb,
    equity_curve JSONB DEFAULT '[]'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strat_backtests_user ON public.strategy_backtests(user_id);
CREATE INDEX IF NOT EXISTS idx_strat_backtests_strat ON public.strategy_backtests(strategy_id);
ALTER TABLE public.strategy_backtests ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "backtests_owner_access" ON public.strategy_backtests;
    DROP POLICY IF EXISTS "backtests_service_role" ON public.strategy_backtests;
    
    CREATE POLICY "backtests_owner_access" ON public.strategy_backtests
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "backtests_service_role" ON public.strategy_backtests
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.strategy_research_reports (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    report_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb,
    in_sample_metrics JSONB DEFAULT '{}'::jsonb,
    out_of_sample_metrics JSONB DEFAULT '{}'::jsonb,
    walk_forward_metrics JSONB DEFAULT '{}'::jsonb,
    benchmark_comparison JSONB DEFAULT '{}'::jsonb,
    strategy_score NUMERIC(6, 4),
    overall_quality_score NUMERIC(6, 4),
    warnings JSONB DEFAULT '[]'::jsonb,
    deployment_approved BOOLEAN DEFAULT FALSE,
    deployment_gate_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strat_reports_user ON public.strategy_research_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_strat_reports_strat ON public.strategy_research_reports(strategy_id);
ALTER TABLE public.strategy_research_reports ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "reports_owner_access" ON public.strategy_research_reports;
    DROP POLICY IF EXISTS "reports_service_role" ON public.strategy_research_reports;
    
    CREATE POLICY "reports_owner_access" ON public.strategy_research_reports
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "reports_service_role" ON public.strategy_research_reports
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 8: REFERRAL SYSTEM NORMALIZED TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.referral_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_referral_codes_user UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON public.referral_codes(code);
CREATE INDEX IF NOT EXISTS idx_referral_codes_user ON public.referral_codes(user_id);
ALTER TABLE public.referral_codes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "referral_codes_select" ON public.referral_codes;
    DROP POLICY IF EXISTS "referral_codes_insert" ON public.referral_codes;
    DROP POLICY IF EXISTS "referral_codes_service" ON public.referral_codes;
    
    CREATE POLICY "referral_codes_select" ON public.referral_codes
        FOR SELECT
        TO authenticated
        USING (user_id = auth.uid() OR is_active = TRUE);

    CREATE POLICY "referral_codes_insert" ON public.referral_codes
        FOR INSERT
        TO authenticated
        WITH CHECK (user_id = auth.uid());

    CREATE POLICY "referral_codes_service" ON public.referral_codes
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.referral_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    referred_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    referral_code_id UUID REFERENCES public.referral_codes(id) ON DELETE SET NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'cancelled')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_referral_relationships_referred UNIQUE (referred_id),
    CONSTRAINT chk_no_self_referral CHECK (referrer_id != referred_id)
);

CREATE INDEX IF NOT EXISTS idx_referral_rel_referrer ON public.referral_relationships(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_rel_referred ON public.referral_relationships(referred_id);
ALTER TABLE public.referral_relationships ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "referral_rel_owner_access" ON public.referral_relationships;
    DROP POLICY IF EXISTS "referral_rel_service" ON public.referral_relationships;
    
    CREATE POLICY "referral_rel_owner_access" ON public.referral_relationships
        FOR SELECT
        TO authenticated
        USING (referrer_id = auth.uid() OR referred_id = auth.uid());

    CREATE POLICY "referral_rel_service" ON public.referral_relationships
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.referral_commissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    referred_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    referral_relationship_id UUID REFERENCES public.referral_relationships(id) ON DELETE SET NULL,
    payment_id VARCHAR(100) NOT NULL,
    subscription_tier VARCHAR(50) NOT NULL,
    payment_amount_usd DECIMAL(10, 2) NOT NULL,
    commission_rate DECIMAL(5, 4) DEFAULT 0.2000,
    commission_amount_usd DECIMAL(10, 2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'paid', 'reversed')),
    reversal_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    reversed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ref_comm_referrer ON public.referral_commissions(referrer_id);
ALTER TABLE public.referral_commissions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "ref_comm_owner_read" ON public.referral_commissions;
    DROP POLICY IF EXISTS "ref_comm_service" ON public.referral_commissions;
    
    CREATE POLICY "ref_comm_owner_read" ON public.referral_commissions
        FOR SELECT
        TO authenticated
        USING (referrer_id = auth.uid());

    CREATE POLICY "ref_comm_service" ON public.referral_commissions
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.referral_wallets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    balance_usd DECIMAL(10, 2) DEFAULT 0.00,
    total_earned_usd DECIMAL(10, 2) DEFAULT 0.00,
    pending_usd DECIMAL(10, 2) DEFAULT 0.00,
    payout_method VARCHAR(50),
    payout_details JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_referral_wallets_user UNIQUE (user_id)
);

ALTER TABLE public.referral_wallets ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "ref_wallets_owner_access" ON public.referral_wallets;
    DROP POLICY IF EXISTS "ref_wallets_service" ON public.referral_wallets;
    
    CREATE POLICY "ref_wallets_owner_access" ON public.referral_wallets
        FOR ALL
        TO authenticated
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid());

    CREATE POLICY "ref_wallets_service" ON public.referral_wallets
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.referral_payouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    amount_usd DECIMAL(10, 2) NOT NULL,
    payout_method VARCHAR(50) NOT NULL,
    payout_details JSONB DEFAULT '{}'::jsonb,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'rejected')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ref_payouts_user ON public.referral_payouts(user_id);
ALTER TABLE public.referral_payouts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "ref_payouts_owner_access" ON public.referral_payouts;
    DROP POLICY IF EXISTS "ref_payouts_service" ON public.referral_payouts;
    
    CREATE POLICY "ref_payouts_owner_access" ON public.referral_payouts
        FOR ALL
        TO authenticated
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid());

    CREATE POLICY "ref_payouts_service" ON public.referral_payouts
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.referral_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL UNIQUE,
    referral_code VARCHAR(50) NOT NULL UNIQUE,
    total_referrals INTEGER DEFAULT 0,
    active_referrals INTEGER DEFAULT 0,
    pending_earnings DECIMAL(10, 2) DEFAULT 0.00,
    approved_earnings DECIMAL(10, 2) DEFAULT 0.00,
    paid_earnings DECIMAL(10, 2) DEFAULT 0.00,
    lifetime_earnings DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.referral_profiles ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "ref_profiles_owner_access" ON public.referral_profiles;
    DROP POLICY IF EXISTS "ref_profiles_service" ON public.referral_profiles;
    
    CREATE POLICY "ref_profiles_owner_access" ON public.referral_profiles
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "ref_profiles_service" ON public.referral_profiles
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

CREATE TABLE IF NOT EXISTS public.exchange_connections (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    exchange_id TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    connection_status TEXT DEFAULT 'disconnected',
    last_connected_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_exchange_connections_user_exchange UNIQUE (user_id, exchange_id)
);

CREATE INDEX IF NOT EXISTS idx_exchange_conn_user ON public.exchange_connections(user_id);
ALTER TABLE public.exchange_connections ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "exchange_conn_owner_access" ON public.exchange_connections;
    DROP POLICY IF EXISTS "exchange_conn_service" ON public.exchange_connections;
    
    CREATE POLICY "exchange_conn_owner_access" ON public.exchange_connections
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "exchange_conn_service" ON public.exchange_connections
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 9: BILLING INVOICES TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.billing_invoices (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    amount NUMERIC(10, 2) DEFAULT 0.00,
    currency VARCHAR(10) DEFAULT 'USD',
    status VARCHAR(50) DEFAULT 'paid',
    invoice_url TEXT,
    invoice_pdf TEXT,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_billing_invoices_user ON public.billing_invoices(user_id);
ALTER TABLE public.billing_invoices ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "billing_invoices_owner_access" ON public.billing_invoices;
    DROP POLICY IF EXISTS "billing_invoices_service" ON public.billing_invoices;
    
    CREATE POLICY "billing_invoices_owner_access" ON public.billing_invoices
        FOR ALL
        TO authenticated
        USING (auth.uid()::text = user_id)
        WITH CHECK (auth.uid()::text = user_id);

    CREATE POLICY "billing_invoices_service" ON public.billing_invoices
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 10: SECURE STORED FUNCTIONS & RPCS
-- ══════════════════════════════════════════════════════════════════════════

-- 1. increment_ml_addon
CREATE OR REPLACE FUNCTION public.increment_ml_addon(target_user_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE public.profiles
    SET ml_addons_purchased = COALESCE(ml_addons_purchased, 0) + 1
    WHERE id = target_user_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 2. create_referral_code_for_user
CREATE OR REPLACE FUNCTION public.create_referral_code_for_user(user_uuid UUID)
RETURNS VARCHAR AS $$
DECLARE
    new_code VARCHAR(20);
    existing_code VARCHAR(20);
    profile_record RECORD;
BEGIN
    SELECT code INTO existing_code FROM public.referral_codes WHERE user_id = user_uuid;
    IF existing_code IS NOT NULL THEN
        RETURN existing_code;
    END IF;
    
    SELECT full_name, email INTO profile_record FROM public.profiles WHERE id = user_uuid;
    
    IF profile_record.full_name IS NOT NULL AND length(trim(profile_record.full_name)) >= 2 THEN
        new_code := upper(regexp_replace(profile_record.full_name, '[^a-zA-Z0-9]', '', 'g'));
        new_code := substring(new_code from 1 for 6) || upper(substring(md5(random()::text) from 1 for 4));
    ELSE
        new_code := 'VQ' || upper(substring(md5(user_uuid::text || random()::text) from 1 for 6));
    END IF;
    
    WHILE EXISTS (SELECT 1 FROM public.referral_codes WHERE code = new_code) LOOP
        new_code := 'VQ' || upper(substring(md5(random()::text) from 1 for 6));
    END LOOP;
    
    INSERT INTO public.referral_codes (user_id, code)
    VALUES (user_uuid, new_code)
    ON CONFLICT (user_id) DO UPDATE SET is_active = TRUE;
    
    INSERT INTO public.referral_wallets (user_id, balance_usd, total_earned_usd, pending_usd)
    VALUES (user_uuid, 0.00, 0.00, 0.00)
    ON CONFLICT (user_id) DO NOTHING;
    
    RETURN new_code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 3. process_referral_commission
CREATE OR REPLACE FUNCTION public.process_referral_commission(
    p_payment_id VARCHAR,
    p_referred_user_id UUID,
    p_subscription_tier VARCHAR,
    p_payment_amount_usd NUMERIC
)
RETURNS UUID AS $$
DECLARE
    v_relationship_id UUID;
    v_referrer_id UUID;
    v_commission_amount NUMERIC(10, 2);
    v_commission_id UUID;
    v_commission_rate NUMERIC(5, 4) := 0.2000;
BEGIN
    SELECT id, referrer_id INTO v_relationship_id, v_referrer_id
    FROM public.referral_relationships
    WHERE referred_id = p_referred_user_id AND status = 'active';
    
    IF v_referrer_id IS NULL THEN
        RETURN NULL;
    END IF;
    
    IF EXISTS (SELECT 1 FROM public.referral_commissions WHERE payment_id = p_payment_id) THEN
        RETURN NULL;
    END IF;
    
    v_commission_amount := round(p_payment_amount_usd * v_commission_rate, 2);
    
    INSERT INTO public.referral_commissions (
        referrer_id,
        referred_id,
        referral_relationship_id,
        payment_id,
        subscription_tier,
        payment_amount_usd,
        commission_rate,
        commission_amount_usd,
        status
    ) VALUES (
        v_referrer_id,
        p_referred_user_id,
        v_relationship_id,
        p_payment_id,
        p_subscription_tier,
        p_payment_amount_usd,
        v_commission_rate,
        v_commission_amount,
        'approved'
    ) RETURNING id INTO v_commission_id;
    
    INSERT INTO public.referral_wallets (user_id, balance_usd, total_earned_usd)
    VALUES (v_referrer_id, v_commission_amount, v_commission_amount)
    ON CONFLICT (user_id) DO UPDATE SET
        balance_usd = public.referral_wallets.balance_usd + v_commission_amount,
        total_earned_usd = public.referral_wallets.total_earned_usd + v_commission_amount,
        updated_at = NOW();
        
    RETURN v_commission_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4. reverse_referral_commission
CREATE OR REPLACE FUNCTION public.reverse_referral_commission(
    p_payment_id VARCHAR,
    p_reversal_reason TEXT DEFAULT 'Refund processed'
)
RETURNS BOOLEAN AS $$
DECLARE
    v_commission RECORD;
BEGIN
    SELECT * INTO v_commission
    FROM public.referral_commissions
    WHERE payment_id = p_payment_id AND status IN ('pending', 'approved');
    
    IF v_commission.id IS NULL THEN
        RETURN FALSE;
    END IF;
    
    UPDATE public.referral_commissions
    SET status = 'reversed',
        reversal_reason = p_reversal_reason,
        reversed_at = NOW(),
        updated_at = NOW()
    WHERE id = v_commission.id;
    
    UPDATE public.referral_wallets
    SET balance_usd = GREATEST(0, balance_usd - v_commission.commission_amount_usd),
        total_earned_usd = GREATEST(0, total_earned_usd - v_commission.commission_amount_usd),
        updated_at = NOW()
    WHERE user_id = v_commission.referrer_id;
    
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 11: POLICIES FOR EXISTING DATA TABLES (ORDERS, FILLS, POSITIONS, ETC.)
-- ══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'orders') THEN
        DROP POLICY IF EXISTS "orders_owner_access" ON public.orders;
        DROP POLICY IF EXISTS "orders_service_role" ON public.orders;
        CREATE POLICY "orders_owner_access" ON public.orders FOR ALL TO authenticated USING (auth.uid()::text = tenant_id) WITH CHECK (auth.uid()::text = tenant_id);
        CREATE POLICY "orders_service_role" ON public.orders FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'fills') THEN
        DROP POLICY IF EXISTS "fills_owner_access" ON public.fills;
        DROP POLICY IF EXISTS "fills_service_role" ON public.fills;
        CREATE POLICY "fills_owner_access" ON public.fills FOR ALL TO authenticated USING (auth.uid()::text = tenant_id) WITH CHECK (auth.uid()::text = tenant_id);
        CREATE POLICY "fills_service_role" ON public.fills FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'positions') THEN
        DROP POLICY IF EXISTS "positions_owner_access" ON public.positions;
        DROP POLICY IF EXISTS "positions_service_role" ON public.positions;
        CREATE POLICY "positions_owner_access" ON public.positions FOR ALL TO authenticated USING (auth.uid()::text = tenant_id) WITH CHECK (auth.uid()::text = tenant_id);
        CREATE POLICY "positions_service_role" ON public.positions FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'invoices') THEN
        DROP POLICY IF EXISTS "invoices_owner_access" ON public.invoices;
        DROP POLICY IF EXISTS "invoices_service_role" ON public.invoices;
        CREATE POLICY "invoices_owner_access" ON public.invoices FOR SELECT TO authenticated USING (auth.uid()::text = user_id);
        CREATE POLICY "invoices_service_role" ON public.invoices FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'subscriptions') THEN
        DROP POLICY IF EXISTS "subscriptions_owner_access" ON public.subscriptions;
        DROP POLICY IF EXISTS "subscriptions_service_role" ON public.subscriptions;
        CREATE POLICY "subscriptions_owner_access" ON public.subscriptions FOR SELECT TO authenticated USING (auth.uid()::text = user_id);
        CREATE POLICY "subscriptions_service_role" ON public.subscriptions FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 12: KEY PERFORMANCE INDEXES
-- ══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_strategies_user_status ON public.strategies(user_id, status);
CREATE INDEX IF NOT EXISTS idx_library_strategies_active_rating ON public.library_strategies(is_active, avg_rating DESC);
CREATE INDEX IF NOT EXISTS idx_signals_strategy_generated ON public.signals(strategy_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_id ON public.profiles(id);
CREATE INDEX IF NOT EXISTS idx_exchange_keys_user_id ON public.exchange_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_execution_records_tenant_created ON public.execution_records(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dag_tasks_tenant_status ON public.dag_tasks(tenant_id, status);

COMMIT;

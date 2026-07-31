-- 010_create_deployment_permissions.sql
-- Create deployment permissions table for marketplace strategy deployment authorization

CREATE TABLE IF NOT EXISTS public.deployment_permissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  library_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
  
  granted_via TEXT NOT NULL, -- 'subscription' or 'ownership'
  subscription_id UUID REFERENCES public.library_subscriptions(id) ON DELETE SET NULL,
  
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  
  CONSTRAINT valid_granted_via CHECK (granted_via IN ('subscription', 'ownership')),
  CONSTRAINT deployment_permissions_user_library_idx UNIQUE (user_id, library_id, is_active)
);

CREATE INDEX IF NOT EXISTS idx_deployment_permissions_user ON public.deployment_permissions(user_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_deployment_permissions_library ON public.deployment_permissions(library_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_deployment_permissions_subscription ON public.deployment_permissions(subscription_id);

CREATE OR REPLACE FUNCTION update_deployment_permissions_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_deployment_permissions_updated_at ON public.deployment_permissions;
CREATE TRIGGER trg_deployment_permissions_updated_at
  BEFORE UPDATE ON public.deployment_permissions
  FOR EACH ROW EXECUTE FUNCTION update_deployment_permissions_updated_at();

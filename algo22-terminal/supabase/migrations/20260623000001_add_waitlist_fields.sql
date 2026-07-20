-- Migration: Add trader_type and monthly_volume to waitlist
-- Date: 2026-06-23

ALTER TABLE public.waitlist 
ADD COLUMN IF NOT EXISTS trader_type TEXT CHECK (trader_type IN ('retail', 'discretionary', 'prop', 'institutional')),
ADD COLUMN IF NOT EXISTS monthly_volume TEXT CHECK (monthly_volume IN ('<100k', '100k-1M', '1M-10M', '>10M'));

COMMENT ON COLUMN public.waitlist.trader_type IS 'Type of trader: retail, discretionary, prop, institutional';
COMMENT ON COLUMN public.waitlist.monthly_volume IS 'Expected monthly trading volume';

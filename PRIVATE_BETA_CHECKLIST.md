# PRIVATE_BETA_CHECKLIST

## 1. USER ONBOARDING
- [x] Registration
- [x] Login
- [x] Logout
- [x] Session persistence
- [x] User profile creation
- [ ] Password reset (API exists, UI form needs verification)
- [ ] Token refresh (Handled by Supabase SDK natively, UI edge-cases untested)

## 2. CORE TRADING WORKFLOW
- [x] Create strategy
- [x] Save strategy
- [x] Reload strategy
- [x] Backtest strategy
- [x] Publish strategy
- [x] Clone strategy
- [x] Paper trade strategy
- [x] View analytics
- [x] Delete/unpublish strategy

## 3. DATA INTEGRITY
- [x] No orphaned records
- [x] Clone lineage preserved
- [x] No duplicate strategy IDs
- [x] No duplicate marketplace entries
- [x] Backtest results remain immutable after publishing
- [x] Soft deletes preserve references

## 4. SECURITY
- [x] JWT verification
- [x] RLS enforcement
- [x] Ownership enforcement
- [x] Admin permissions
- [x] Cross-user isolation
- [x] Sensitive information exposure
- [x] UUID Validation
- [ ] Rate abuse protection (Not yet enforced at API Gateway)

## 5. FAILURE RECOVERY
- [x] API restart
- [x] Database restart
- [x] Invalid / Expired Token
- [x] Supabase timeout
- [x] Deleted Strategy / User
- [ ] WebSocket disconnect (Needs UX confirmation for reconnection banner)
- [ ] Storage failure (Images bucket unlinked)

## 6. USER EXPERIENCE
- [x] No dead navigation
- [x] No blocking UI states
- [x] No broken forms
- [x] No endless loading
- [x] Clear error messages
- [x] Consistent success messages

## 7. OPERATIONAL READINESS
- [x] Environment variables
- [x] Secrets management
- [x] Migration history
- [x] Rollback procedures
- [x] Health endpoints
- [x] Docker configuration
- [ ] External Logging/Telemetry

## 8. DOCUMENTATION
- [x] README
- [x] Architecture documentation
- [x] Known limitations
- [x] API documentation
- [ ] Environment setup / Deployment guide (Needs polish for production deployment)

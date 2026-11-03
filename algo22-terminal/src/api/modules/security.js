/**
 * Security API Module — SEC-24 MFA backup codes
 *
 * Endpoints: /api/security/mfa/backup-codes/*
 *
 * `routers/security.py` on the backend. These exist alongside, and never in
 * place of, Supabase's own `supabase.auth.mfa.*` calls — this is the recovery
 * path for a trader who cannot reach a real MFA challenge at all (lost
 * device, no other session), not a replacement for the challenge itself.
 */
import { post } from '../../apiClient';

export const securityApi = {
  /**
   * Issue a fresh set of ten backup codes, invalidating any earlier set.
   * Requires an AAL2 (fully MFA-verified) session — call this right after a
   * successful `supabase.auth.mfa.verify`.
   * @returns {Promise<{codes: string[], count: number}>}
   */
  generateBackupCodes: () => post('/api/security/mfa/backup-codes/generate'),

  /**
   * Consume one backup code to recover a lost 2FA device. Works at AAL1 —
   * the session a trader holds right after signing in with just their
   * password, before (or instead of) completing a real MFA challenge.
   * @param {string} code
   * @returns {Promise<{success: boolean, unenrolled_factors: string[]}>}
   */
  verifyBackupCode: (code) => post('/api/security/mfa/backup-codes/verify', { code }),
};

export default securityApi;

/**
 * gates/index — the entitlement gate surface.
 *
 *   import { FeatureGate, UsageLimit, UpgradePrompt } from '../components/gates';
 *
 * Four components and three hooks, all of which RENDER the server's entitlement verdict and none of
 * which decide it. Every gated action is independently refused by
 * `backend_app/core/subscription_dependencies.py`, so nothing exported here is a security control —
 * it is the difference between a trader seeing a locked panel with a reason and a trader pressing a
 * button that 403s.
 *
 * Named re-exports rather than `export *`, matching `components/ds/index.js`: under `export *` a
 * name exported by two modules resolves to nothing, silently.
 */

export {
  CapacityNotice,
  FeatureGate,
  PlanGate,
  UsageLimit,
  useAllowance,
  useFeature,
} from './FeatureGate';

export {
  LockedCommand,
  SALES_ROUTE,
  UPGRADE_ROUTE,
  UpgradePrompt,
  upgradeDestination,
} from './UpgradePrompt';

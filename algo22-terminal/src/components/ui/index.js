/**
 * src/components/ui/index.js
 * Unified Design System Primitive Hub for VyomQuant Terminal.
 *
 * The four Tailwind utility components below, and nothing else.
 *
 * Until task 27.2 this file also carried `export * from '../ui-legacy/primitives'`,
 * which re-exported the shim's whole 36-name surface — `C`, the `Table*` family,
 * the skeletons, `usePolling`, `LoadingOverlay`, `PnLBadge` and the rest. Nothing
 * imports this barrel, so that line was not serving a caller: it was the only
 * thing keeping 25 unreferenced exports reachable, and therefore the only thing
 * standing between the shim and deletion. The eleven exports that DID have
 * callers now live in `components/common/primitives.jsx` and are imported from
 * there directly, which is how the other consumers already did it.
 *
 * Nothing new goes in here. A shared primitive belongs in `components/ds/*`, and
 * its consumers import the module, not a barrel — a barrel makes an unused export
 * look used and hides which surfaces a page actually depends on.
 */

export { Accordion } from './Accordion';
export { Badge } from './Badge';
export { Button } from './Button';
export { Card } from './Card';

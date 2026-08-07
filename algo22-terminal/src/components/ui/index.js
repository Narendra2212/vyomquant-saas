/**
 * src/components/ui/index.js
 * Unified Design System Primitive Hub for VyomQuant Terminal.
 * Reconciles Tailwind utility components with legacy primitives.
 */

export { Accordion } from './Accordion';
export { Badge } from './Badge';
export { Button } from './Button';
export { Card } from './Card';

// Re-export all primitive elements, C token object, and shared hooks
export * from '../ui-legacy/primitives';

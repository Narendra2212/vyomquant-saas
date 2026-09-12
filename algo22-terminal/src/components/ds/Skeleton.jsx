/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Skeleton — the shimmer atom
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. design.md §5.2. Requirements 14.2, 18.5.
 *
 * One rectangle. It has no opinion about what it stands in for — `ds/LoadingState`
 * composes it into the seven shapes real content takes, sized from that module's
 * `SKELETON_GEOMETRY` so that arrival shifts nothing (Requirement 14.2).
 *
 * WHAT IT FIXES ABOUT `SkeletonLine` (`ui-legacy/primitives.jsx`)
 * -------------------------------------------------------------
 *   1. **The animation is declared once.** `SkeletonLine` renders
 *      `<style>{'@keyframes shimmer{…}'}</style>` as a CHILD of every bar, so a
 *      5×8 table skeleton injects forty-one identical global keyframe blocks and
 *      re-evaluates them on every render. The keyframes now live in
 *      `src/styles/ds.css` (imported once from `index.css`) and this component
 *      renders one element with one class.
 *   2. **Reduced motion is honoured deliberately.** `tokens.css`'s global block
 *      collapses `animation-duration` to 0.01ms, which freezes the shimmer
 *      gradient at frame one — a bright band parked off the right edge. `ds.css`
 *      switches the gradient off entirely instead, leaving a flat block.
 *   3. **No colour here.** `SkeletonLine` builds a `linear-gradient` from `C.bg3`
 *      and `C.bg4` inline. The gradient is in `ds.css` reading `var(--color-…)`;
 *      this file contains no colour of any kind, so there is nothing to drift.
 *
 * IT IS `aria-hidden` BY DEFAULT, ON PURPOSE
 * -----------------------------------------
 * A shimmer bar carries no information — announcing "blank, blank, blank" forty
 * times is worse than silence. The accessible name for a load belongs to the
 * enclosing `LoadingState`, which is the single `role="status"` region per load.
 * Callers composing skeletons outside `LoadingState` must provide that region
 * themselves; passing `aria-hidden={false}` here is possible but is almost always
 * the wrong answer.
 */

import { token } from '../../design/tokens';

/**
 * A single shimmering placeholder rectangle.
 *
 * @param {Object} props
 * @param {number|string} [props.width] Any CSS length. Defaults to filling the parent.
 * @param {number|string} [props.height] Any CSS length. Numbers are pixels.
 * @param {number|string} [props.radius] Overrides the default `--radius-sm`. `'full'` for a pill.
 * @param {boolean} [props.circle] Square + fully rounded, for avatar and dot placeholders.
 * @param {string} [props.className] Composed after the base class, so it wins.
 * @param {Object} [props.style] Merged last, so a caller can override any of the above.
 */
export function Skeleton({
  width = '100%',
  height = 12,
  radius,
  circle = false,
  className = '',
  style,
  ...rest
}) {
  const side = circle ? height : width;
  const borderRadius = circle ? token.radius.full : radius;

  return (
    <span
      className={`ds-skeleton ${className}`.trim()}
      aria-hidden="true"
      style={{
        width: side,
        height,
        // `undefined` leaves `ds.css`'s `--radius-sm` in place rather than
        // overriding it with an empty declaration.
        ...(borderRadius === undefined ? null : { borderRadius }),
        ...style,
      }}
      {...rest}
    />
  );
}

export default Skeleton;

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/SectionHeader — a heading for a region inside a page
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.1, §5.3.
 * Requirements 1.2, 18.4.
 *
 * Renames and retokens `PanelTitle` from `ui-legacy/primitives.jsx`. §5.3's table calls
 * this row "Rename + retoken", and that is exactly what it is: the layout is
 * `PanelTitle`'s — title with an optional subtitle beneath it, an optional `right` slot
 * pushed to the far end — and the API is `PanelTitle`'s `{ title, sub, right }` with
 * `sub` spelled out and a `level`.
 *
 * WHAT THE RETOKEN CHANGES
 * ------------------------
 * `PanelTitle` hardcoded every value it used: `fontSize: 13`, `fontSize: 10`,
 * `letterSpacing: -0.3`, `fontFamily: "monospace"`, `C.t1`, `C.t2` and `C.space.md`.
 * Six of those seven are outside the type scale in `tokens.css`, so the panel titles
 * across the app render at sizes the scale does not contain, and the 10px monospace
 * subtitle is below the smallest token (`--text-micro`, also 10px, but with a line
 * height that fits). Here:
 *
 *   `level={2}` → `--text-section` (18px)  `level={3}` → `--text-title` (14px)
 *   subtitle    → `--text-small` (11px), sans, `content-secondary`
 *
 * The negative letter-spacing is dropped — it was applied at 13px, where it costs
 * legibility and buys nothing — and the subtitle is no longer monospace: it is prose,
 * and mono is reserved for figures and identifiers.
 *
 * WHY THE HEADING LEVEL IS A PROP AND NOT A GUESS
 * ----------------------------------------------
 * `PanelTitle` always rendered `<h3>` and `SectionH` always rendered `<h2>`, which is
 * why pages that use both produce a heading outline with a level skipped whenever a
 * `PanelTitle` sits directly under the page title. Requirement 18's screen-reader
 * navigation depends on that outline being real, so the level is the caller's — `2` for
 * a section directly under the page's `<h1>`, `3` for a heading inside such a section.
 *
 * `ds/Panel` renders its own heading and does not use this component; a `SectionHeader`
 * is for a region of a page that is not itself a panel — a group of panels, a form
 * section, the header of a `ui/Card` that has not yet migrated.
 */

import { assertContract, hasText } from './devAssert';

/** The two levels §5.1 names, and the token each renders at. */
const LEVEL_CLASS = Object.freeze({
  2: 'text-section',
  3: 'text-title',
});

/**
 * A section heading.
 *
 * @param {Object} props
 * @param {string} props.title Required — a heading with no text is not a heading, and it
 *   is what names the region for a screen reader (Requirement 18.4).
 * @param {string} [props.subtitle] One line beneath the title. `PanelTitle`'s `sub`.
 * @param {React.ReactNode} [props.right] The far-end slot: a filter, a count, a
 *   `ds/CommandButton`, a `ds/TradingEnvironmentBadge`.
 * @param {2|3} [props.level] `2` renders `<h2>` at `--text-section`, `3` renders `<h3>`
 *   at `--text-title`. Default `2`.
 * @param {string} [props.className]
 */
export function SectionHeader({ title, subtitle, right, level = 2, className = '', ...rest }) {
  assertContract(
    hasText(title),
    'SectionHeader: `title` is required. It is what names this region for a screen reader '
      + '(Requirement 18.4); a heading with no text is an empty landmark in the outline. For a '
      + 'row of controls with no heading, use a plain element rather than this component.',
  );

  const known = level === 2 || level === 3;
  assertContract(
    known,
    `SectionHeader: \`level\` must be 2 or 3, received ${JSON.stringify(level)}. The heading `
      + 'outline is what a screen reader navigates by, so the level is the page\'s decision: 2 '
      + 'for a section under the page `<h1>`, 3 for a heading inside one.',
  );
  // A level nobody understands falls to 2 rather than to a `<div>`: a heading that is
  // possibly at the wrong depth still navigates; a non-heading does not appear at all.
  const resolvedLevel = known ? level : 2;
  const Heading = resolvedLevel === 3 ? 'h3' : 'h2';

  return (
    <div
      data-ds="section-header"
      data-ds-level={resolvedLevel}
      className={`mb-3 flex items-start justify-between gap-3 ${className}`.trim()}
      {...rest}
    >
      <div className="min-w-0">
        {hasText(title) ? (
          <Heading
            className={`truncate ${LEVEL_CLASS[resolvedLevel]} font-semibold text-content-primary`}
          >
            {title}
          </Heading>
        ) : null}
        {hasText(subtitle) ? (
          <p className="mt-1 text-small text-content-secondary">{subtitle}</p>
        ) : null}
      </div>
      {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
    </div>
  );
}

export default SectionHeader;

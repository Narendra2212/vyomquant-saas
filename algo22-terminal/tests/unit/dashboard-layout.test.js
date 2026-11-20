/**
 * Dashboard layout invariants — task 02 (dashboard layout remediation).
 *
 * ===========================================================================
 * WHY A SOURCE SCAN AND NOT A RENDER TEST
 * ===========================================================================
 * The three defects this pins are geometric, and jsdom has no layout engine:
 * `getBoundingClientRect` returns zeros, no stylesheet is applied, and no
 * element ever overflows. A render test asserting "the row is not 900px tall"
 * would pass on the broken code as readily as on the fixed code, which is the
 * one failure mode a guard must not have. So this reads the source, the way
 * `guards/dead-tailwind`, `guards/no-colour-literals` and `guards/nav-contract`
 * do, and asserts the class vocabulary that produces the geometry.
 *
 * Comments are blanked first (`stripComments`). That matters here more than
 * usual: Dashboard.jsx explains each of these decisions in prose directly above
 * the class list, and prose mentioning `max-h-72` must not be what satisfies a
 * test looking for `max-h-72`.
 *
 * ===========================================================================
 * WHAT WAS WRONG, SO A FUTURE READER CAN JUDGE WHETHER THIS STILL MATTERS
 * ===========================================================================
 * 1. The strategies list was an unbounded `<ul>` — one `<li>` per deployment.
 *    It is the only region on the page whose height grows with account size,
 *    and row 1 is a CSS grid, so a 13-strategy account made that one cell
 *    ~900px tall and stretched "Open positions" and "System & exchange health"
 *    to match it: two panels of mostly empty space, and Recent signals/orders
 *    and the equity curve pushed a full screen below the fold.
 *
 * 2. `grid-cols-4` was unconditional on both the tier-1 figures and row 1. At
 *    1024px that left the outer row-1 panels ~200px wide, which is narrow
 *    enough that `Panel`'s `truncate`d header rendered "System & exchang…".
 *
 * 3. `col-span-2` was likewise unconditional, so it kept spanning columns in a
 *    grid that no longer had them once the row stacked.
 *
 * None of this is about taste: each assertion below names a class whose absence
 * reintroduces a measured defect.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { SRC, list, stripComments } from './guards/source-scan.js';

const DASHBOARD = path.join(SRC, 'pages', 'Dashboard.jsx');

/** Source with comments blanked, so prose cannot satisfy a class assertion. */
const code = stripComments(readFileSync(DASHBOARD, 'utf8'));

/** Every `className="..."` string literal on the page. */
const classLists = [...code.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)]
  .map((m) => (m[1] ?? m[2] ?? '').replace(/\s+/g, ' ').trim())
  .filter(Boolean);

describe('dashboard layout', () => {
  it('bounds the strategies list so account size cannot drive the row height', () => {
    // The list is identified by what it is, not by position: the `<ul>` that is
    // a flex column of gap-2 rows is the fleet list. If that shell is
    // restructured this fails loudly rather than silently matching nothing.
    const candidates = classLists.filter(
      (c) => c.includes('flex') && c.includes('flex-col') && c.includes('gap-2'),
    );

    expect(
      candidates.length,
      'no flex-col/gap-2 class list found in Dashboard.jsx — the strategies '
        + 'list shell was restructured, so this guard needs re-pointing rather '
        + 'than deleting.',
    ).toBeGreaterThan(0);

    const bounded = candidates.filter(
      (c) => /\bmax-h-/.test(c) && /\boverflow-y-(auto|scroll)\b/.test(c),
    );

    expect(
      bounded.length,
      'The strategies list must cap its own height and scroll inside the '
        + 'panel. Without a `max-h-*` and an `overflow-y-auto` it grows one row '
        + 'per deployment, and because row 1 is a grid, every panel beside it '
        + 'is stretched to match.\nflex-col/gap-2 lists found:\n'
        + list(candidates),
    ).toBeGreaterThan(0);
  });

  it('declares the width at which a three-or-more column grid applies', () => {
    // A bare `grid-cols-2` is fine: two short figures side by side survive a
    // narrow panel. Three or more in a page-level row does not, so N >= 3 must
    // name its breakpoint.
    const offenders = [];
    for (const c of classLists) {
      for (const m of c.matchAll(/(?:^|\s)(?:([a-z0-9]+):)?grid-cols-(\d+)/g)) {
        const [, prefix, n] = m;
        if (!prefix && Number(n) >= 3) offenders.push(`grid-cols-${n}  in: ${c}`);
      }
    }

    expect(
      offenders,
      'An unprefixed `grid-cols-3`+ applies at every viewport. On this page '
        + 'that produced ~200px columns at 1024px and a truncated panel title. '
        + 'Give it a breakpoint (the page uses `xl:` for its dense row) and a '
        + 'stacked default.\n' + list(offenders),
    ).toEqual([]);
  });

  it('scopes column spans to the breakpoint whose grid actually has columns', () => {
    const offenders = classLists
      .filter((c) => /(?:^|\s)col-span-\d/.test(c))
      .map((c) => `col-span without a breakpoint, in: ${c}`);

    expect(
      offenders,
      'A `col-span-*` with no breakpoint keeps spanning after the row has '
        + 'stacked to one column, which is how an empty grid cell appears '
        + 'beside a panel.\n' + list(offenders),
    ).toEqual([]);
  });
});

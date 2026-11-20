/**
 * ═══════════════════════════════════════════════════════════════════════════
 * strategiesCardGrid.test.js — STRAT-CARDS-01
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE DEFECT
 * ---------------------------------------------------------------------------
 * Task 14 reported that the Strategies page "uses long horizontal strategy strips, resulting
 * in inefficient visual presentation".
 *
 * The entries were never strips. Each owned-or-subscribed entry is already a `ds/Panel` in a
 * CSS grid. The grid's track definition was `repeat(auto-fit, minmax(300px, 1fr))`, and
 * `auto-fit` COLLAPSES tracks that hold no item and hands the freed space to the tracks that
 * remain. With one entry that leaves one track, whose `1fr` maximum stretches it across the
 * container — a full-width band.
 *
 * `auto-fill` keeps the empty tracks, so a single entry occupies one track of several and
 * stays the ~300px card the `minmax` asks for.
 *
 * So the reported symptom is real, the diagnosis is a one-keyword grid bug, and the fix
 * changes no markup: the cards were already cards.
 *
 * WHY THIS IS A SOURCE TEST
 * ---------------------------------------------------------------------------
 * jsdom implements no CSS grid. `getComputedStyle` returns the declared string and resolves
 * no tracks, so a rendered assertion could only read back the same inline style this file
 * reads from source — while paying to mount a 2,600-line page. The geometry that would prove
 * the fix (one card is narrower than its container) needs a real layout engine and belongs in
 * the Playwright sweep under `tests/e2e/`.
 *
 * What a source test CAN hold is the keyword, and the keyword IS the defect.
 */

import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { SRC, stripComments } from '../guards/source-scan.js';

/**
 * Comments stripped, string literals kept.
 *
 * Both halves matter here, and this file is the reason: the prose above and in
 * `Strategies.jsx` discusses `auto-fit` at length, deliberately, so an un-stripped scan would
 * find the very keyword it is asserting absent. And the track definition IS a string literal,
 * so `codeOnly()` — which masks string spans — would hide it and pass vacuously.
 */
const code = (relative) => stripComments(fs.readFileSync(path.join(SRC, relative), 'utf8'));

const STRATEGIES = 'pages/Strategies.jsx';

describe('STRAT-CARDS-01: the entry grid keeps card-sized tracks', () => {
  it('declares the grid with auto-fill', () => {
    expect(
      code(STRATEGIES),
      'the entry grid must keep its empty tracks, or a single entry stretches to the full '
        + 'container width and reads as a banner rather than a card',
    ).toMatch(/repeat\(auto-fill,\s*minmax\(300px,\s*1fr\)\)/);
  });

  it('does not use auto-fit anywhere on the page', () => {
    /*
      Asserted across the whole file rather than just the one constant. `auto-fit` is the
      defect, and a second grid on this page acquiring it later would reproduce the same
      banner for the same reason — there is no layout on this page that wants its tracks
      collapsed to the item count.
    */
    const offenders = code(STRATEGIES)
      .split('\n')
      .map((line, i) => [i + 1, line])
      .filter(([, line]) => line.includes('auto-fit'));

    expect(
      offenders,
      '`auto-fit` collapses empty tracks and stretches the survivors, so a collection whose '
        + 'item count the user controls changes SHAPE with the data: two entries read as '
        + `cards, one reads as a banner.\n${offenders.map(([n, l]) => `  :${n}  ${l.trim()}`).join('\n')}`,
    ).toEqual([]);
  });

  it('keeps a minimum card width, so the tracks stay cards rather than columns', () => {
    /*
      The `minmax` floor is what makes this a card grid at all. Without a minimum, `1fr`
      tracks would divide the container evenly however narrow that made them, and the fix
      above would trade a single wide banner for several unreadably thin columns.
    */
    expect(code(STRATEGIES)).toMatch(/minmax\(300px,/);
  });
});

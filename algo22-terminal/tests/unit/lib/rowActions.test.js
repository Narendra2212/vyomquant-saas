/**
 * `lib/rowActions.js` — vyomquant-ui-redesign task 17.2. design.md §7.2.
 * Requirements 4.2, 4.3.
 *
 * The example suite. `rowActions.property.test.js` holds Properties 6 and 7 over the whole
 * input space; this file pins the handful of specific answers the requirement names, so a
 * change that keeps the properties true while reclassifying `deploy_paper` still fails.
 */

import { describe, expect, it } from 'vitest';

import {
  ROW_ACTION,
  SEPARATED_ROW_ACTIONS,
  isSeparatedAction,
  ownerRowActions,
  partitionRowActions,
  renderedActionIds,
  separationOf,
} from '../../../src/lib/rowActions';

/** The page's own catalogue, in the page's own declaration order. */
const CATALOGUE = {
  [ROW_ACTION.RUN_BACKTEST]: { label: 'Backtest' },
  [ROW_ACTION.EDIT]: { label: 'Edit' },
  [ROW_ACTION.CLONE]: { label: 'Duplicate' },
  [ROW_ACTION.RENAME]: { label: 'Rename' },
  [ROW_ACTION.SIGNAL_TRACE]: { label: 'Signal Trace' },
  [ROW_ACTION.PAUSE]: { label: 'Pause' },
  [ROW_ACTION.DEPLOY_LIVE]: { label: 'Deploy live' },
  [ROW_ACTION.ARCHIVE]: { label: 'Archive strategy' },
};

describe('the classifier (Requirement 4.3)', () => {
  it('accepts the live deployment and both spellings of the archive', () => {
    expect(separationOf('deploy_live')).toBe('live');
    expect(separationOf('delete')).toBe('destructive');
    expect(separationOf('archive')).toBe('destructive');
    expect([...SEPARATED_ROW_ACTIONS].sort()).toEqual(['archive', 'delete', 'deploy_live']);
  });

  it('rejects a paper target — it is not a live transition', () => {
    expect(isSeparatedAction('deploy_paper')).toBe(false);
    expect(isSeparatedAction('start_paper')).toBe(false);
  });

  it('rejects pause: stopping a strategy is the safe direction', () => {
    expect(isSeparatedAction('pause')).toBe(false);
  });

  it('rejects everything else, including a name that merely contains one', () => {
    for (const id of ['edit', 'clone', 'rename', 'run_backtest', 'signal_trace']) {
      expect(isSeparatedAction(id), id).toBe(false);
    }
    // No substring matching: neither of these is the operation.
    expect(isSeparatedAction('undelete')).toBe(false);
    expect(isSeparatedAction('delete_draft')).toBe(false);
  });

  it('tolerates casing and padding, and answers for any non-string without throwing', () => {
    expect(isSeparatedAction(' Deploy_Live ')).toBe(true);
    expect(isSeparatedAction('ARCHIVE')).toBe(true);
    for (const value of [null, undefined, 7, {}, [], true]) {
      expect(isSeparatedAction(value)).toBe(false);
    }
  });
});

describe('the partition (Requirement 4.2)', () => {
  it('splits the owner row into five inline controls and the separated pair', () => {
    const { inline, separated } = partitionRowActions(
      ownerRowActions({ id: 's-1', status: 'stopped' }),
      CATALOGUE,
    );

    expect(inline).toEqual(['run_backtest', 'edit', 'clone', 'rename', 'signal_trace']);
    expect(separated).toEqual(['deploy_live', 'archive']);
  });

  it('offers Pause instead of the deployment while the strategy is running', () => {
    const { inline, separated } = partitionRowActions(
      ownerRowActions({ id: 's-1', status: 'running' }),
      CATALOGUE,
    );

    expect(inline).toContain('pause');
    expect(inline).not.toContain('deploy_live');
    // The archive is still separated; pausing did not make it safe.
    expect(separated).toEqual(['archive']);
  });

  it('drops an id the catalogue does not describe, and one it declares as null', () => {
    const { inline, separated } = partitionRowActions(
      ['edit', 'open_in_builder', 'archive', 'constructor', 'toString'],
      { ...CATALOGUE, archive: null },
    );

    expect(inline).toEqual(['edit']);
    // Declared with nothing behind it, so there is no control to render.
    expect(separated).toEqual([]);
  });

  it('renders in catalogue order, not in the order the offer arrives', () => {
    const rendered = renderedActionIds(
      partitionRowActions(['archive', 'rename', 'edit', 'run_backtest'], CATALOGUE),
    );
    expect(rendered).toEqual(['run_backtest', 'edit', 'rename', 'archive']);
  });

  it('collapses duplicates and survives an offer that is not a list', () => {
    expect(renderedActionIds(partitionRowActions(['edit', 'edit', 'edit'], CATALOGUE)))
      .toEqual(['edit']);
    for (const value of [null, undefined, 'edit', 42, {}]) {
      expect(renderedActionIds(partitionRowActions(value, CATALOGUE))).toEqual([]);
    }
  });

  it('returns frozen lists, so a consumer cannot widen the offer after the fact', () => {
    const partition = partitionRowActions(['edit'], CATALOGUE);
    expect(Object.isFrozen(partition)).toBe(true);
    expect(Object.isFrozen(partition.inline)).toBe(true);
    expect(() => partition.inline.push('deploy_live')).toThrow();
  });
});

describe('the owner list\'s offer', () => {
  it('is the seven controls the row carries, and is frozen', () => {
    const offered = ownerRowActions({ status: 'draft' });
    expect(offered).toHaveLength(7);
    expect(Object.isFrozen(offered)).toBe(true);
  });

  it('answers for a row that is missing, null or not an object', () => {
    for (const row of [null, undefined, 42, 'running', []]) {
      const offered = ownerRowActions(row);
      expect(offered).toContain('deploy_live');
      expect(offered).not.toContain('pause');
    }
  });
});

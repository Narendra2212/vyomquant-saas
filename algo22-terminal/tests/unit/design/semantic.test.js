/**
 * Unit tests for `src/design/semantic.js` — tasks 5.1.
 *
 * These are the plain example tests. Property 1 (totality + group distinctness) and
 * Property 22 (four-axis environment distinctness) are tasks 5.2 and 6.8 and are not
 * written here.
 */

import { describe, it, expect } from 'vitest';
import { token } from '../../../src/design/tokens';
import {
  statusToken,
  pnlToken,
  ENVIRONMENT,
  ENVIRONMENT_IDS,
  environmentTreatment,
  resolveEnvironment,
  STATUS_VOCABULARY,
  REQUIRED_STATUS_GROUPS,
  STAGE_BAND,
  STAGE_BANDS,
  DECLARED_STAGE_BANDS,
  BLOCK_CATEGORIES,
  BLOCK_CATEGORY_STAGE,
  stageBandFor,
  stageIconFor,
} from '../../../src/design/semantic';

describe('statusToken', () => {
  it('resolves every declared vocabulary value to a defined token triple', () => {
    for (const word of STATUS_VOCABULARY) {
      const result = statusToken(word);
      expect(result.group, word).toBeTypeOf('string');
      expect(result.fg, word).toBeTypeOf('string');
      expect(result.wash, word).toBeTypeOf('string');
    }
  });

  it('maps the §4.1 table rows to their groups', () => {
    expect(statusToken('running').group).toBe('live');
    expect(statusToken('HEALTHY').group).toBe('live');
    expect(statusToken('paired').group).toBe('connected');
    expect(statusToken('filled').group).toBe('profit');
    expect(statusToken('short').group).toBe('loss');
    expect(statusToken('BLOCKED').group).toBe('error');
    expect(statusToken('partially_filled').group).toBe('warning');
    expect(statusToken('draft').group).toBe('neutral');
  });

  it('is case- and whitespace-insensitive', () => {
    expect(statusToken('  DiScOnNeCtEd  ')).toEqual(statusToken('disconnected'));
  });

  it('resolves anything unrecognised to neutral, never undefined', () => {
    const inputs = ['', '   ', 'zzz', 'NOT_A_STATE', null, undefined, 0, 1, NaN, {}, [], true];
    for (const input of inputs) {
      const result = statusToken(input);
      expect(result.group, String(input)).toBe('neutral');
      expect(result.fg).toBe(token.status.neutral.fg);
      expect(result.wash).toBe(token.status.neutral.wash);
    }
  });

  it('does not resolve inherited Object.prototype keys', () => {
    for (const key of ['constructor', 'toString', 'hasOwnProperty', '__proto__']) {
      expect(statusToken(key).group, key).toBe('neutral');
    }
  });

  it('returns tokens that exist in the generated token set', () => {
    for (const word of [...STATUS_VOCABULARY, 'unrecognised']) {
      const { group, fg, wash } = statusToken(word);
      expect(token.status[group]).toBeDefined();
      expect(fg).toBe(token.status[group].fg);
      expect(wash).toBe(token.status[group].wash);
    }
  });

  it('gives the six requirement-named groups six distinct group names', () => {
    // The distinctness axis this module can guarantee. `fg`/`wash` are NOT pairwise
    // distinct: `tokens.css` deliberately gives live/connected/profit one green and
    // loss/error one red, so there are two hue families across the six.
    expect(new Set(REQUIRED_STATUS_GROUPS).size).toBe(6);
    const groups = REQUIRED_STATUS_GROUPS.map((g) => statusToken(g).group);
    expect(new Set(groups).size).toBe(6);
  });
});

describe('pnlToken', () => {
  it('treats zero as neutral, not profit', () => {
    expect(pnlToken(0).group).toBe('neutral');
    expect(pnlToken(-0).group).toBe('neutral');
  });

  it('maps positive to profit and negative to loss', () => {
    expect(pnlToken(0.01).group).toBe('profit');
    expect(pnlToken(12345.67).group).toBe('profit');
    expect(pnlToken(-0.01).group).toBe('loss');
    expect(pnlToken(-12345.67).group).toBe('loss');
  });

  it('treats non-finite and non-numeric input as neutral', () => {
    for (const input of [NaN, Infinity, -Infinity, null, undefined, '5', '-5', {}, []]) {
      expect(pnlToken(input).group, String(input)).toBe('neutral');
    }
  });
});

describe('ENVIRONMENT', () => {
  it('differs on four independent axes for every pair', () => {
    const axes = ['fg', 'label', 'icon', 'border'];
    for (const a of ENVIRONMENT_IDS) {
      for (const b of ENVIRONMENT_IDS) {
        if (a === b) continue;
        for (const axis of axes) {
          expect(ENVIRONMENT[a][axis], `${a} vs ${b} on ${axis}`).not.toBe(ENVIRONMENT[b][axis]);
        }
      }
    }
  });

  it('carries the token wash alongside the hue', () => {
    expect(ENVIRONMENT.LIVE.wash).toBe(token.env.live.wash);
    expect(ENVIRONMENT.PAPER.wash).toBe(token.env.paper.wash);
    expect(ENVIRONMENT.BACKTEST.wash).toBe(token.env.backtest.wash);
  });
});

describe('environmentTreatment', () => {
  it('resolves the three server spellings', () => {
    expect(environmentTreatment('LIVE')).toBe(ENVIRONMENT.LIVE);
    expect(environmentTreatment('paper')).toBe(ENVIRONMENT.PAPER);
    expect(environmentTreatment(' Backtest ')).toBe(ENVIRONMENT.BACKTEST);
  });

  it('returns null when the server did not say', () => {
    for (const input of ['', '  ', 'SIMULATED', 'live-ish', null, undefined, 0, {}, []]) {
      expect(environmentTreatment(input), String(input)).toBeNull();
    }
  });
});

describe('resolveEnvironment', () => {
  it('prefers a backtest context over everything else', () => {
    expect(
      resolveEnvironment({ backtestContext: { id: 'bt_1' }, serverEnvironment: 'LIVE' }),
    ).toBe(ENVIRONMENT.BACKTEST);
  });

  it('uses a named server environment next', () => {
    expect(resolveEnvironment({ serverEnvironment: 'LIVE', isSimulated: true })).toBe(
      ENVIRONMENT.LIVE,
    );
    expect(resolveEnvironment({ serverEnvironment: 'PAPER' })).toBe(ENVIRONMENT.PAPER);
  });

  it('falls back to PAPER only when the server explicitly said simulated', () => {
    expect(resolveEnvironment({ isSimulated: true })).toBe(ENVIRONMENT.PAPER);
    for (const truthy of ['true', 'false', 1, {}, []]) {
      expect(resolveEnvironment({ isSimulated: truthy }), String(truthy)).toBeNull();
    }
  });

  it('returns null rather than guessing, for every empty input', () => {
    expect(resolveEnvironment()).toBeNull();
    expect(resolveEnvironment({})).toBeNull();
    expect(resolveEnvironment({ serverEnvironment: null, isSimulated: false })).toBeNull();
    expect(resolveEnvironment({ serverEnvironment: 'REAL' })).toBeNull();
  });
});

describe('stage bands', () => {
  it('declares five Requirement 5.1 stages plus a neutral sixth', () => {
    expect(DECLARED_STAGE_BANDS).toHaveLength(5);
    expect(DECLARED_STAGE_BANDS.map((b) => b.label)).toEqual([
      'Market data',
      'Transform',
      'Logic',
      'Model',
      'Action',
    ]);
    expect(STAGE_BANDS).toHaveLength(6);
    expect(STAGE_BANDS[5]).toBe(STAGE_BAND.UNRESOLVED);
    expect(STAGE_BANDS.map((b) => b.order)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('covers all seven backend BlockCategory values exactly once', () => {
    expect(Object.keys(BLOCK_CATEGORY_STAGE).sort()).toEqual([...BLOCK_CATEGORIES].sort());
    const covered = DECLARED_STAGE_BANDS.flatMap((b) => b.categories);
    expect(covered).toHaveLength(7);
    expect(new Set(covered).size).toBe(7);
  });

  it('resolves each category to its §9.1 band and icon', () => {
    expect(stageBandFor('DATA')).toBe(STAGE_BAND.MARKET_DATA);
    expect(stageBandFor('INDICATOR')).toBe(STAGE_BAND.TRANSFORM);
    expect(stageBandFor('math')).toBe(STAGE_BAND.TRANSFORM);
    expect(stageBandFor('FEATURE_ENGINEERING')).toBe(STAGE_BAND.TRANSFORM);
    expect(stageBandFor('LOGIC')).toBe(STAGE_BAND.LOGIC);
    expect(stageBandFor('ML_DL')).toBe(STAGE_BAND.MODEL);
    expect(stageBandFor('ACTION')).toBe(STAGE_BAND.ACTION);

    expect(stageIconFor('DATA')).toBe('Database');
    expect(stageIconFor('MATH')).toBe('Sigma');
    expect(stageIconFor('ML_DL')).toBe('Brain');
    expect(stageIconFor('ACTION')).toBe('Zap');
  });

  it('resolves an unknown category to the Unresolved band rather than hiding it', () => {
    for (const input of ['', '  ', 'QUANTUM', null, undefined, 7, {}, 'constructor']) {
      const band = stageBandFor(input);
      expect(band, String(input)).toBe(STAGE_BAND.UNRESOLVED);
      expect(band.fg).toBe(token.status.neutral.fg);
      expect(stageIconFor(input)).toBe('HelpCircle');
    }
  });

  it('gives stage 4 a distinct border rather than a new hue', () => {
    expect(STAGE_BAND.MODEL.fg).toBe(STAGE_BAND.MARKET_DATA.fg);
    expect(STAGE_BAND.MODEL.border).not.toBe(STAGE_BAND.MARKET_DATA.border);
  });
});

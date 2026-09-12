/**
 * `ds/overlayRegistry` — vyomquant-ui-redesign task 6.20.
 * Requirement 17.3. design.md §11.6.
 *
 * The single-overlay rule, both halves of it: the development throw and the production
 * no-op. Task 6.21 owns Property 34, which drives generated open/close sequences through
 * rendered overlays; this file pins the module's own contract, including the cases a
 * generated sequence is unlikely to reach — a malformed descriptor, a release by the wrong
 * id, and the StrictMode re-request.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import {
  OVERLAY_KIND,
  OVERLAY_KINDS,
  OverlayConflictError,
  currentOverlay,
  isOverlayOpen,
  releaseOverlay,
  requestOverlay,
  resetOverlayRegistry,
} from '../../../src/components/ds/overlayRegistry';

const DIALOG = { id: 'dialog-1', kind: OVERLAY_KIND.DIALOG, label: 'Stop live deployment' };
const DRAWER = { id: 'drawer-1', kind: OVERLAY_KIND.DRAWER, label: 'Account menu' };

beforeEach(() => {
  resetOverlayRegistry();
  // Vitest runs with `mode: 'test'`, so `import.meta.env.DEV` is true by default and the
  // development half is what runs unless a case stubs it. Pinned explicitly so a change to
  // that default cannot silently flip which half the suite is testing.
  vi.stubEnv('DEV', true);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  resetOverlayRegistry();
});

describe('overlayRegistry: the vocabulary', () => {
  it('names exactly the two kinds of overlay in this design system', () => {
    expect(OVERLAY_KINDS).toEqual(['dialog', 'drawer']);
  });
});

describe('overlayRegistry: one claim at a time', () => {
  it('starts empty', () => {
    expect(isOverlayOpen()).toBe(false);
    expect(currentOverlay()).toBeNull();
  });

  it('grants the first request and records it', () => {
    expect(requestOverlay(DIALOG)).toBe(true);
    expect(isOverlayOpen()).toBe(true);
    expect(currentOverlay()).toEqual({
      id: 'dialog-1',
      kind: 'dialog',
      label: 'Stop live deployment',
    });
  });

  it('hands out a copy, so the claim cannot be mutated through the reader', () => {
    requestOverlay(DIALOG);
    const read = currentOverlay();
    read.id = 'tampered';
    expect(currentOverlay().id).toBe('dialog-1');
  });

  it('throws in development when a second overlay asks to open', () => {
    requestOverlay(DIALOG);
    expect(() => requestOverlay(DRAWER)).toThrow(OverlayConflictError);
    // The first overlay keeps its claim. The newcomer is the one refused — the dialog
    // already on screen may be holding a live-deploy acknowledgement.
    expect(currentOverlay().id).toBe('dialog-1');
  });

  it('carries both descriptors on the thrown error', () => {
    requestOverlay(DIALOG);
    let thrown;
    try {
      requestOverlay(DRAWER);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(OverlayConflictError);
    expect(thrown.current.id).toBe('dialog-1');
    expect(thrown.requested.id).toBe('drawer-1');
    expect(thrown.message).toContain('Requirement 17.3');
  });

  it('is a logged no-op in production', () => {
    vi.stubEnv('DEV', false);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});

    requestOverlay(DIALOG);
    expect(requestOverlay(DRAWER)).toBe(false);

    expect(currentOverlay().id).toBe('dialog-1');
    expect(logged).toHaveBeenCalledTimes(1);
    expect(logged.mock.calls[0][0]).toContain('overlayRegistry');
  });

  it('refuses a second overlay of the same kind, not just a different one', () => {
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    requestOverlay(DIALOG);
    expect(requestOverlay({ id: 'dialog-2', kind: OVERLAY_KIND.DIALOG })).toBe(false);
  });

  it('lets the drawer open once the dialog has released', () => {
    requestOverlay(DIALOG);
    expect(releaseOverlay('dialog-1')).toBe(true);
    expect(isOverlayOpen()).toBe(false);
    expect(requestOverlay(DRAWER)).toBe(true);
  });
});

describe('overlayRegistry: re-requesting the same id is idempotent', () => {
  it('grants a repeat request from the id that already holds the claim', () => {
    // React 18 StrictMode mounts effects, tears them down and mounts them again. A remount
    // that re-requests must not read as a second overlay.
    expect(requestOverlay(DIALOG)).toBe(true);
    expect(requestOverlay(DIALOG)).toBe(true);
    expect(currentOverlay().id).toBe('dialog-1');
  });

  it('refreshes the recorded label on a repeat request', () => {
    // §8.3's deploy flow changes the title between steps.
    requestOverlay(DIALOG);
    requestOverlay({ ...DIALOG, label: 'Deploy to live trading' });
    expect(currentOverlay().label).toBe('Deploy to live trading');
  });
});

describe('overlayRegistry: releases are id-scoped', () => {
  it('ignores a release from an id that does not hold the claim', () => {
    // This is what makes a refused overlay's effect cleanup harmless: both components
    // release unconditionally, and a denied one must not hand the registry away.
    requestOverlay(DIALOG);
    expect(releaseOverlay('drawer-1')).toBe(false);
    expect(currentOverlay().id).toBe('dialog-1');
  });

  it('ignores a release when nothing is open, and a release with no usable id', () => {
    expect(releaseOverlay('dialog-1')).toBe(false);
    requestOverlay(DIALOG);
    expect(releaseOverlay('')).toBe(false);
    expect(releaseOverlay(null)).toBe(false);
    expect(releaseOverlay(undefined)).toBe(false);
    expect(currentOverlay().id).toBe('dialog-1');
  });

  it('is safe to release twice', () => {
    requestOverlay(DIALOG);
    expect(releaseOverlay('dialog-1')).toBe(true);
    expect(releaseOverlay('dialog-1')).toBe(false);
  });
});

describe('overlayRegistry: malformed descriptors', () => {
  const malformed = [
    ['no descriptor at all', undefined],
    ['null', null],
    ['a string instead of an object', 'dialog'],
    ['no id', { kind: 'dialog' }],
    ['a blank id', { id: '   ', kind: 'dialog' }],
    ['a non-string id', { id: 7, kind: 'dialog' }],
    ['no kind', { id: 'dialog-1' }],
    ['an unknown kind', { id: 'dialog-1', kind: 'popover' }],
  ];

  it.each(malformed)('throws in development: %s', (_name, descriptor) => {
    expect(() => requestOverlay(descriptor)).toThrow(OverlayConflictError);
    expect(isOverlayOpen()).toBe(false);
  });

  it.each(malformed)('is a logged no-op in production: %s', (_name, descriptor) => {
    vi.stubEnv('DEV', false);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(requestOverlay(descriptor)).toBe(false);
    expect(isOverlayOpen()).toBe(false);
    expect(logged).toHaveBeenCalledTimes(1);
  });

  it('normalises a usable descriptor rather than rejecting it', () => {
    expect(requestOverlay({ id: '  dialog-1  ', kind: '  DIALOG  ', label: '  Delete  ' })).toBe(true);
    expect(currentOverlay()).toEqual({ id: 'dialog-1', kind: 'dialog', label: 'Delete' });
  });

  it('treats a blank label as no label rather than as an empty one', () => {
    requestOverlay({ id: 'dialog-1', kind: 'dialog', label: '   ' });
    expect(currentOverlay().label).toBeNull();
  });
});

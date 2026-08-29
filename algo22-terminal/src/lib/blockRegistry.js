/**
 * blockRegistry.js — palette *presentation* only (closes SB-03 and SB-04).
 *
 * What this file used to be
 * -------------------------
 * A hand-maintained catalogue of 33 block definitions: names, descriptions, ports,
 * parameter lists with defaults and ranges, and per-block validators. That catalogue was a
 * second source of truth competing with the engine, and it drifted, exactly as a duplicated
 * fact always does:
 *
 * * SB-03 — `BlockCategories.FEATURE_ENGINEERING` was declared and rendered as a palette
 *   section, but not one block was ever authored into it. The section was permanently empty
 *   while `feature_engineering.py` computed dozens of features.
 * * SB-04 — the catalogue offered seven indicators and six models. `wma`, `hma`, `catboost`
 *   and `autoencoder` are implemented in the engine and were unselectable in the UI.
 *
 * What this file is now
 * ---------------------
 * Presentation, and nothing a strategy's meaning can depend on:
 *
 * * `StreamTypes` — the port-type vocabulary, *generated* from `PORT_TYPES` in
 *   `canonicalGraph.js`, which is the frontend's single mirror of the backend `PortType`
 *   enum (`backend_app/backend/strategy_dag/schema.py`). Generated rather than retyped: a
 *   second hand-written list here would be the same drift defect in miniature. The live
 *   vocabulary served with the registry is available from
 *   `registryClient.getPortTypes()`, and the backend remains authoritative over both.
 * * `BlockCategories` — the seven canonical category ids, generated from
 *   `BLOCK_CATEGORIES` in `canonicalGraph.js`. These are the ids the registry serves in
 *   `categories[].id`, so a presentation lookup keyed on them cannot miss a served
 *   category. Note this replaced a legacy eight-key lowercase vocabulary
 *   (`indicators`, `ml`, `dl`, …) whose shape is what made the SB-04 category mapping
 *   possible in the first place.
 * * A category → icon/colour map, plus the two lookups the canvas and palette call.
 *
 * Where blocks come from now
 * --------------------------
 * `registryClient.js`, from `GET /api/strategy-operations/registry/blocks`. This module
 * exports **zero block definitions** and must never gain one again: a local block list is
 * the drift mechanism behind SB-03 and SB-04, and a local list used as a *fallback* when
 * the registry is unreachable is that same defect wearing a helpful face. The palette fails
 * closed instead (Requirement 4.12).
 *
 * Transitional shims: gone
 * ------------------------
 * `BlockRegistry`, `getBlockByType`, `getBlocksByCategory` and `getAllBlocks` existed as
 * empty, fail-closed stubs only so `StrategyBuilder.jsx` kept building while its palette was
 * still reading them. Task 3.6 re-pointed that palette at `registryClient.js` and deleted the
 * stubs with their last call sites, so there is no longer a name in this module that a caller
 * could mistake for a block catalogue. `LEGACY_CATEGORY_ALIASES` went with them: the only
 * response that spoke the lowercase `indicators` / `ml` / `dl` vocabulary was the deprecated
 * `GET /api/strategies/blocks`, and nothing calls it any more.
 */

import { Activity, Brain, Check, Cpu, Database, GitBranch, Settings } from 'lucide-react';
import { C } from '../components/ui-legacy/primitives';
import { BLOCK_CATEGORIES, PORT_TYPES } from './canonicalGraph';

/** Identity map over a frozen list of ids: `{ SIGNAL: 'SIGNAL', … }`. */
const identityMap = (ids) =>
  Object.freeze(
    ids.reduce((map, id) => {
      map[id] = id;
      return map;
    }, {}),
  );

/**
 * The port-type vocabulary, generated from the frontend's mirror of the backend `PortType`
 * enum. Keys and values are identical, so `StreamTypes.SIGNAL === 'SIGNAL'` is the same
 * string the registry, the validator and the compiler all speak.
 */
export const StreamTypes = identityMap(PORT_TYPES);

/** The seven canonical category ids, as served in `categories[].id`. */
export const BlockCategories = identityMap(BLOCK_CATEGORIES);

/**
 * Category → icon and colour. Presentation only: no ports, no parameters, no defaults,
 * nothing that could change what a strategy does.
 */
export const CATEGORY_PRESENTATION = Object.freeze({
  DATA: Object.freeze({ icon: Database, color: C.t2 }),
  INDICATOR: Object.freeze({ icon: Activity, color: C.accent }),
  MATH: Object.freeze({ icon: GitBranch, color: C.gold }),
  LOGIC: Object.freeze({ icon: Settings, color: C.gold }),
  FEATURE_ENGINEERING: Object.freeze({ icon: Cpu, color: C.cyan }),
  ML_DL: Object.freeze({ icon: Brain, color: C.purple }),
  ACTION: Object.freeze({ icon: Check, color: C.green }),
});

/** Neutral presentation for an id this map does not know. Never a thrown error: an unknown
 * category is a display question, and refusing to draw an icon would hide a block that the
 * backend says exists. */
const FALLBACK_PRESENTATION = Object.freeze({ icon: Activity, color: C.t2 });

/**
 * Canonical category id for `category`, or null when it names no known category.
 *
 * Only the seven ids the registry serves in `categories[].id` resolve. The legacy lowercase
 * spellings (`indicators`, `ml`, `dl`, …) deliberately do not: that eight-key vocabulary is what
 * made the SB-04 category mapping possible, and nothing in the frontend speaks it now.
 */
export const normalizeCategoryId = (category) => {
  if (typeof category !== 'string' || category.trim() === '') return null;
  const upper = category.trim().toUpperCase();
  return Object.prototype.hasOwnProperty.call(CATEGORY_PRESENTATION, upper) ? upper : null;
};

/** `{ icon, color }` for a served category id. */
export const getCategoryPresentation = (category) => {
  const id = normalizeCategoryId(category);
  return id === null ? FALLBACK_PRESENTATION : CATEGORY_PRESENTATION[id];
};

/** The lucide icon component for a category. */
export const getCategoryIcon = (category) => getCategoryPresentation(category).icon;

/** The accent colour for a category. */
export const getCategoryColor = (category) => getCategoryPresentation(category).color;

// No block lookup lives here. `registryClient.getDescriptor(blockId)`,
// `registryClient.getBlocksByCategory(categoryId)` and `registryClient.getPaletteSections()` are
// the only ways to reach a block descriptor, and they answer from the served registry or not at
// all (Requirement 4.12).

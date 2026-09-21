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
 * * A category → icon/colour map, plus the two lookups the canvas and palette call. Both
 *   are now *derived* from `design/semantic.js`'s stage band declaration (`§9.1`,
 *   Requirement 5.1) rather than chosen here, so the palette icon and the canvas icon for a
 *   category cannot disagree — see the mapping note on `CATEGORY_PRESENTATION` below.
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

import { Activity, Brain, Cpu, Database, GitBranch, HelpCircle, Sigma, Zap } from 'lucide-react';
import { stageBandFor, stageIconFor } from '../design/semantic';
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
 * The lucide components for the icon *names* `design/semantic.js` declares.
 *
 * `semantic.js` names icons as strings rather than importing React, so that guards, tests and
 * non-component code can read the stage band table. This object is where those names become
 * components, the same resolution `ds/Panel.jsx` performs for the environment glyphs.
 */
const STAGE_ICONS = Object.freeze({
  Database,
  Activity,
  Sigma,
  Cpu,
  GitBranch,
  Brain,
  Zap,
  HelpCircle,
});

/**
 * Category → icon and colour. Presentation only: no ports, no parameters, no defaults,
 * nothing that could change what a strategy does.
 *
 * **The seven categories over five stage bands** (`design.md §9.1`, Requirement 5.1). The
 * backend serves seven `BlockCategory` values and Requirement 5.1 names five data-flow
 * stages, so three categories collapse into one band:
 *
 * | Stage band          | Categories                              | Why they collapse |
 * | ------------------- | --------------------------------------- | ----------------- |
 * | 1 · Market data     | `DATA`                                  | the only source stage — every graph starts here |
 * | 2 · Transform       | `INDICATOR`, `MATH`, `FEATURE_ENGINEERING` | all three read a series and return a series; a trader reads them as one step, and the engine treats them as one too (they share the port-type vocabulary) |
 * | 3 · Logic          | `LOGIC`                                 | the only stage that turns numbers into a decision |
 * | 4 · Model          | `ML_DL`                                 | inference is its own stage because it can fail for reasons no other stage has (no trained model, warming) |
 * | 5 · Action         | `ACTION`                                | the only stage with an external effect |
 *
 * The five stages stay authoritative for *layout* — lane order, lane header, stage number —
 * and the seven categories stay authoritative for *identity*: the name and icon below are
 * per category, not per band, which is why `FEATURE_ENGINEERING` still draws `Cpu` inside
 * stage 2 rather than borrowing `INDICATOR`'s `Activity`.
 *
 * Derived from `stageBandFor`/`stageIconFor` rather than restated, so this map cannot drift
 * from the one the canvas draws. The colour is a *band* colour and is spent on an edge — a
 * border, a rule, an icon — never on a node body (Requirement 1.5).
 */
export const CATEGORY_PRESENTATION = Object.freeze(
  BLOCK_CATEGORIES.reduce((map, category) => {
    map[category] = Object.freeze({
      icon: STAGE_ICONS[stageIconFor(category)],
      color: stageBandFor(category).fg,
    });
    return map;
  }, {}),
);

/** Neutral presentation for an id this map does not know. Never a thrown error: an unknown
 * category is a display question, and refusing to draw an icon would hide a block that the
 * backend says exists. It resolves to §9.1's neutral sixth "Unresolved" band, whose
 * `HelpCircle` is the same "we do not know" glyph an unconfirmed environment gets, so one
 * unknown reads the same way everywhere. */
const FALLBACK_PRESENTATION = Object.freeze({
  icon: STAGE_ICONS[stageIconFor(null)],
  color: stageBandFor(null).fg,
});

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

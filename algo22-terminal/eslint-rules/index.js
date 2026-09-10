/**
 * The `vyom` ESLint plugin — local, no dependency.
 *
 * design.md §17.3 permits exactly one new dependency for this initiative
 * (`fast-check`), so the design-system guards that belong in the linter are
 * defined here as a flat-config plugin object rather than published as a
 * package.
 */
import noNewLegacyToken from './no-new-legacy-token.js';

export default {
  meta: { name: 'eslint-plugin-vyom', version: '1.0.0' },
  rules: {
    'no-new-legacy-token': noNewLegacyToken,
  },
};

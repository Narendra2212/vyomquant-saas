/**
 * The `vyom` ESLint plugin — local, no dependency.
 *
 * design.md §17.3 permits exactly one new dependency for this initiative
 * (`fast-check`), so the design-system guards that belong in the linter are
 * defined here as a flat-config plugin object rather than published as a
 * package.
 *
 * ===========================================================================
 * THIS PLUGIN CURRENTLY HAS NO RULES
 * ===========================================================================
 * It held exactly one, `no-new-legacy-token`, which kept the `C` compatibility
 * shim in `src/components/ui-legacy/primitives.jsx` closed and derived: adding
 * a key to `C`, or writing a colour literal where a `token.*` reference
 * belonged, was an error. Task 27.2's final stage B deleted the shim, so the
 * rule's one scoped file no longer exists and the rule went with it — along
 * with its registration in `eslint.config.js`, which was a `files:` block
 * naming that single path and nothing else.
 *
 * The module is kept rather than deleted because it is the declared home for
 * local lint rules under §17.3, and a future design-system rule belongs here
 * rather than in a new file with a new plugin name. `rules` is deliberately an
 * empty object and not a removal of the key: a flat-config plugin object with
 * no `rules` is still valid, but an empty `rules` says "none right now" instead
 * of "this is not a rule-bearing plugin".
 *
 * Nothing imports this module today. `eslint-rules/a11y-ratchet.js` is a
 * separate module and is still imported by `eslint.config.js`, so this
 * directory remains live.
 */

export default {
  meta: { name: 'eslint-plugin-vyom', version: '1.0.0' },
  rules: {},
};

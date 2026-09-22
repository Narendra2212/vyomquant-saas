import js from "@eslint/js";
import globals from "globals";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import reactHooks from "eslint-plugin-react-hooks";
import {
  A11Y_ENFORCED_GLOBS,
  A11Y_PAGE_WAIVERS,
  a11yRules,
} from "./eslint-rules/a11y-ratchet.js";

export default [
  js.configs.recommended,
  // `eslint-plugin-jsx-a11y-x` exposes its flat configs under `configs`, not
  // `flatConfigs` (that name is the upstream `eslint-plugin-jsx-a11y`'s). And
  // `configs.recommended` is a single flat-config object, not an array, so it is
  // listed directly rather than spread.
  jsxA11y.configs.recommended,
  {
    files: ["src/**/*.{js,jsx,ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      // The whole browser global surface, from the `globals` package, rather
      // than a hand-maintained subset. The previous hand-written list omitted
      // `URLSearchParams`, `crypto`, `AbortController`, `navigator`, `Blob`,
      // `requestAnimationFrame` and a dozen others, so `no-undef` reported ~69
      // false positives across `src/` -- noise that would drown out the real
      // a11y findings once Requirement 18 raises `jsx-a11y-x` to error.
      // Note: no `process` here. This is a browser bundle; nothing under
      // `src/` reads `process.*` (Vite exposes `import.meta.env` instead), so
      // declaring it would only hide a genuine mistake.
      globals: globals.browser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "no-unused-vars": "warn",
      "no-undef": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-console": "off",
    },
  },
  {
    // ── The accessibility ratchet, half one (task 6.27, Requirements 18.1, 18.4) ──
    //
    // Every `jsx-a11y-x` rule at `error` for `src/components/ds/**` and
    // `src/pages/**`, keeping the preset's options and adding the rules it
    // leaves off — two of which, `control-has-associated-label` and
    // `no-aria-hidden-on-focusable`, ARE Requirement 18's criteria. See
    // eslint-rules/a11y-ratchet.js for what was measured before this landed and
    // why the exemption list is a deny-list rather than an allowlist.
    //
    // `ds/` is clean under this set and stays clean: the guard at
    // tests/unit/guards/a11y-ratchet.test.js asserts zero, so a `div onClick`
    // added to a primitive fails the suite as well as the lint.
    files: A11Y_ENFORCED_GLOBS,
    rules: a11yRules("error"),
  },
  // ── The accessibility ratchet, half two: the shrinking waiver — GONE ──
  //
  // A second block sat here holding `Object.keys(A11Y_PAGE_WAIVERS)` at `warn`.
  // Five in-scope pages carried 25 findings between them when it landed at task
  // 6.27; `Portfolio`, `Strategies`, `SignalTrace` and `Backtester` cleared theirs
  // and deleted their lines (25 → 23 → 21 → 18 → 8 → 4), and
  // retail-ui-simplification task 5.3 cleared the last four on
  // `StrategyMarketplace.jsx` — two rules on each of two card `div`s that had an
  // `onClick` and no keyboard path at all.
  //
  // `A11Y_PAGE_WAIVERS` is empty now, so this block had to go rather than stay:
  // flat config rejects an empty `files` array, and a block that waives nothing
  // would only be a place for the next waiver to appear without argument. Every
  // file under `A11Y_ENFORCED_GLOBS` is now held at `error` with no exception,
  // which is what the block above always said it was for. Adding a waiver again
  // means writing the block back and saying why, and
  // `tests/unit/guards/a11y-ratchet.test.js` is what measures whether it was
  // needed — it asserts zero findings on every unwaived in-scope file, so a page
  // that regresses fails the suite rather than quietly landing at `warn`.
  //
  // The `A11Y_PAGE_WAIVERS` import stays: the guard reads it from here to check
  // that this config and the recorded debt describe the same tree.
  // A block registering the local `vyom` plugin for
  // `src/components/ui-legacy/primitives.jsx` was here. It turned on one rule,
  // `vyom/no-new-legacy-token`, which kept the `C` compatibility shim closed and
  // derived (design.md §3.4, Requirement 1.1) by making a new key on `C`, or a
  // colour literal where a `token.*` reference belonged, an error.
  //
  // Task 27.2's final stage B deleted the shim. The block's `files` named that
  // one path, so it matched nothing afterwards, and the rule it enabled was
  // deleted with the file it guarded. The `vyom` import went too — this was its
  // only use. `eslint-rules/index.js` is kept as the declared home for local
  // rules under §17.3 and now exports an empty `rules`; see its docblock.
  {
    // Vitest runs with `globals: true` (vitest.config.js) and `environment:
    // 'jsdom'`, so test files legitimately see both the browser surface and the
    // injected test API. `globals` ships no `vitest` set, so the test API is
    // listed explicitly. Covers `tests/unit/**` (including
    // `tests/unit/guards/**`) and co-located `src/**/__tests__/**` suites.
    files: [
      "tests/**/*.{js,jsx,ts,tsx}",
      "src/**/__tests__/**/*.{js,jsx,ts,tsx}",
      "**/*.{test,spec}.{js,jsx,ts,tsx}",
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.node,
        suite: "readonly",
        describe: "readonly",
        it: "readonly",
        test: "readonly",
        expect: "readonly",
        expectTypeOf: "readonly",
        assert: "readonly",
        chai: "readonly",
        vi: "readonly",
        vitest: "readonly",
        beforeAll: "readonly",
        afterAll: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly",
        onTestFailed: "readonly",
        onTestFinished: "readonly",
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-undef": "error",
    },
  },
  {
    // Build/tooling code that runs under Node, not in the browser:
    // `scripts/gen-tokens.mjs` (the token generator invoked by `npm run
    // tokens` / `prebuild`), the Vite/Vitest/PostCSS/ESLint config files, and
    // the local ESLint rules. These need `process`, `console`, `Buffer`, and
    // the `node:` module surface.
    files: [
      "scripts/**/*.{js,mjs,cjs}",
      "eslint-rules/**/*.{js,mjs,cjs}",
      "*.config.{js,mjs,cjs,ts}",
      "eslint.config.js",
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.node,
    },
    rules: {
      "no-unused-vars": "warn",
      "no-undef": "error",
      "no-console": "off",
    },
  },
];

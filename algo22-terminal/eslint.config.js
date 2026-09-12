import js from "@eslint/js";
import globals from "globals";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import reactHooks from "eslint-plugin-react-hooks";
import vyom from "./eslint-rules/index.js";
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
  {
    // ── The accessibility ratchet, half two: the shrinking waiver ──
    //
    // These five in-scope pages carry 25 findings between them and are rebuilt
    // in M7-M9. `warn` keeps every one of them in the report — they are not
    // ignored, and the count is recorded per file in `A11Y_PAGE_WAIVERS` — while
    // keeping the debt of a page that has not been migrated yet out of the
    // build's error count. The page tasks own these; task 6.27 does not.
    //
    // A page NOT listed here is at `error`, which is the whole point: a page
    // rebuilt by its migration task is held to the rule the moment its waiver
    // line goes, and a page added tomorrow is held to it with no action at all.
    // The guard requires each recorded count to be exact and forbids a `0`
    // entry, so a cleared page must have its line deleted in the same commit.
    // When the last line goes, delete this block.
    files: Object.keys(A11Y_PAGE_WAIVERS),
    rules: a11yRules("warn"),
  },
  {
    // The `C` compatibility shim is CLOSED and DERIVED (design.md §3.4,
    // Requirement 1.1). Adding a key to it, or writing a literal instead of a
    // `token.*` reference, is an error — the token belongs in
    // src/styles/tokens.css. Scoped to the one file allowed to declare `C`.
    files: ["src/components/ui-legacy/primitives.jsx"],
    plugins: { vyom },
    rules: {
      "vyom/no-new-legacy-token": "error",
    },
  },
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

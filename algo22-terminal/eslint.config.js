import js from "@eslint/js";

export default [
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx,ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        process: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        Promise: "readonly",
        fetch: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        WebSocket: "readonly",
        URL: "readonly",
        FormData: "readonly",
        Event: "readonly",
        CustomEvent: "readonly",
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-undef": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-console": "off",
    },
  },
];

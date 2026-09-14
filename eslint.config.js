import js from "@eslint/js";
import tseslint from "typescript-eslint";

// Flat config (ESLint 9). Framework packages and the app entry share one
// config: recommended JS + TS rules, browser globals, JSX-aware parsing.
export default tseslint.config(
  {
    ignores: [
      ".venv/**",
      "node_modules/**",
      "public/build/**",
      "packages/*/dist/**",
      "playwright-report/**",
      "test-results/**",
      "storage/**",
      "database/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{js,mjs,jsx,ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        history: "readonly",
        location: "readonly",
        navigator: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        FormData: "readonly",
        Headers: "readonly",
        Request: "readonly",
        Response: "readonly",
        HTMLElement: "readonly",
        HTMLAnchorElement: "readonly",
        Event: "readonly",
        CustomEvent: "readonly",
        addEventListener: "readonly",
        removeEventListener: "readonly",
        requestAnimationFrame: "readonly",
        AbortController: "readonly",
        DOMParser: "readonly",
        self: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
      },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    // Node-side config files (vite/eslint/playwright configs).
    files: ["*.config.js", "*.config.mjs", "*.config.ts"],
    languageOptions: {
      globals: {
        process: "readonly",
        __dirname: "readonly",
      },
    },
  },
  {
    // Playwright's global setup also runs under Node (not a browser page).
    files: ["e2e/**/*.mjs"],
    languageOptions: {
      globals: {
        process: "readonly",
        __dirname: "readonly",
      },
    },
  },
);

/**
 * Minimal ESLint config for the web client. Lives next to package.json
 * so the `pnpm lint` command in the root package.json works without
 * reaching up to the repo root.
 *
 * Style: a defensive baseline — `eslint:recommended` plus the
 * TypeScript-ESLint recommended rules. React-specific rules are
 * enabled but only fire on `.tsx`. Test files (anything under
 * `__tests__/` or matching `*.test.ts(x)`) are exempt from the
 * stricter "no-explicit-any" rule because tests need stubs and
 * fakes.
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2020: true,
    node: true,
  },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["@typescript-eslint", "react-hooks", "react-refresh"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
  ],
  settings: {
    react: { version: "detect" },
  },
  rules: {
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "warn",
    "react-refresh/only-export-components": "off",
    "@typescript-eslint/no-unused-vars": [
      "warn",
      {
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
      },
    ],
    "@typescript-eslint/no-explicit-any": "warn",
    "@typescript-eslint/ban-ts-comment": "off",
    "no-empty": ["error", { allowEmptyCatch: true }],
  },
  overrides: [
    {
      files: [
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/__tests__/**/*.ts",
        "**/__tests__/**/*.tsx",
        "tests/**/*.ts",
        "tests/**/*.tsx",
        "tests/setup.ts",
      ],
      rules: {
        "@typescript-eslint/no-explicit-any": "off",
        "@typescript-eslint/no-non-null-assertion": "off",
      },
    },
    {
      files: ["**/*.tsx"],
      plugins: ["react-hooks", "react-refresh"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  ],
  ignorePatterns: [
    "dist/**",
    "node_modules/**",
    "*.cjs",
    "*.config.js",
    "*.config.ts",
    "vite.config.ts",
    "vite.config.d.ts",
    "postcss.config.js",
    "tailwind.config.js",
  ],
};

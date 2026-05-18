'use strict'

/** @type {import('eslint').Linter.Config} */
module.exports = {
  root: true,
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2020,
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'local'],
  extends: ['plugin:@typescript-eslint/recommended'],
  rules: {
    // Hard-ban inline hex color literals and Tailwind arbitrary hex values.
    // Use CSS variables from src/styles/tokens.css instead.
    // To exempt a single unavoidable line:
    //   // eslint-disable-next-line local/no-hex-color -- <reason>
    'local/no-hex-color': 'error',
  },
  ignorePatterns: ['dist/', 'node_modules/', 'vite.config.*'],
}

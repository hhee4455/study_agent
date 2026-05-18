'use strict'

/**
 * ESLint rule: no-hex-color
 *
 * Reports any string literal that contains an inline hex color (#rgb / #rrggbb / #rrggbbaa)
 * or a Tailwind arbitrary hex color (bg-[#...], text-[#...], etc.).
 * Use CSS variables (var(--color-*)) defined in src/styles/tokens.css instead.
 *
 * Disable a single line only when truly unavoidable:
 *   // eslint-disable-next-line local/no-hex-color -- <reason>
 */

const HEX_RE = /#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/
const TAILWIND_HEX_RE = /\[#[0-9a-fA-F]+\]/

/** @type {import('eslint').Rule.RuleModule} */
module.exports = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow inline hex color literals and Tailwind arbitrary hex colors. Use CSS variables (var(--color-*)) instead.',
      recommended: true,
    },
    messages: {
      hexLiteral:
        'Inline hex color "{{ value }}" is not allowed. Use a design token: var(--color-*) from src/styles/tokens.css.',
      tailwindHex:
        'Tailwind arbitrary hex color "{{ value }}" is not allowed. Use a Tailwind semantic class or var(--color-*) instead.',
    },
    schema: [],
  },

  create(context) {
    function checkString(node, raw) {
      if (typeof raw !== 'string') return
      if (TAILWIND_HEX_RE.test(raw)) {
        context.report({ node, messageId: 'tailwindHex', data: { value: raw } })
      } else if (HEX_RE.test(raw)) {
        context.report({ node, messageId: 'hexLiteral', data: { value: raw } })
      }
    }

    return {
      Literal(node) {
        checkString(node, node.value)
      },
      TemplateLiteral(node) {
        for (const quasi of node.quasis) {
          checkString(quasi, quasi.value.raw)
        }
      },
    }
  },
}

/**
 * vyom/no-new-legacy-token
 *
 * Keeps the `C` compatibility shim in `src/components/ui-legacy/primitives.jsx`
 * CLOSED and DERIVED, which is what design.md §3.4 promises and what
 * Requirement 1.1 needs to stay true over time.
 *
 * `C` is not a token source any more — `src/styles/tokens.css` is, via the
 * generated `src/design/tokens.js`. The shim exists only so that the ~700 legacy
 * `C.*` call sites keep resolving while pages migrate to `components/ds/*`, and
 * it is deleted at the end of step M9. Two things therefore have to hold:
 *
 *   1. It may only SHRINK. A key that is not in the frozen inventory below is a
 *      lint error, so a new token cannot be introduced here instead of in
 *      `tokens.css`. Removing a key is always allowed.
 *   2. It may not hold a literal. Every value must come from `token.*`, so
 *      `C.profit` and `var(--color-status-profit)` cannot drift apart. The only
 *      literal permitted is `'none'` inside `glow` and `gradient`, which
 *      Requirement 1.5 retires wholesale.
 *
 * It also asserts the object is wrapped in `Object.freeze`, since a frozen
 * object is what makes (1) enforceable at runtime as well as at lint time.
 */

/** Every top-level key `C` carried when the shim was derived. Closed set. */
const ALLOWED = new Set([
  'bg', 'bg0', 'bg1', 'bg2', 'bg3', 'bg4',
  'border', 'borderLight', 'borderHover',
  'cyan', 'cyanL', 'cyanD', 'cyanDim', 'accent', 'accentHover', 'accentMuted', 'blue',
  'profit', 'green', 'greenD', 'profitDark', 'profitBg',
  'loss', 'red', 'lossDark', 'lossBg',
  't1', 't2', 't3', 't4',
  'warning', 'gold', 'purple', 'orange',
  'shadow', 'shadowMd', 'shadowLg',
  'glow', 'gradient', 'space', 'radius',
]);

/** Nested groups, with their own closed key sets. */
const ALLOWED_NESTED = {
  glow: new Set(['profit', 'loss', 'accent', 'warning', 'gold', 'purple']),
  gradient: new Set(['profit', 'loss', 'accent', 'card', 'shine']),
  space: new Set(['xs', 'sm', 'md', 'lg', 'xl', 'xxl']),
  radius: new Set(['sm', 'md', 'lg', 'xl']),
};

/**
 * The two groups Requirement 1.5 retires. Their values are the literal `'none'`
 * rather than a `token.*` reference, because there is no token for "no glow".
 */
const RETIRED_GROUPS = new Set(['glow', 'gradient']);

const staticKey = (property) => {
  if (property.type !== 'Property' || property.computed) return null;
  if (property.key.type === 'Identifier') return property.key.name;
  if (property.key.type === 'Literal') return String(property.key.value);
  return null;
};

/** `Object.freeze(<object literal>)` → the object literal, else null. */
const unwrapFreeze = (node) => {
  if (!node || node.type !== 'CallExpression') return null;
  const { callee } = node;
  if (
    callee.type !== 'MemberExpression' ||
    callee.object.type !== 'Identifier' ||
    callee.object.name !== 'Object' ||
    callee.property.type !== 'Identifier' ||
    callee.property.name !== 'freeze'
  ) {
    return null;
  }
  const [arg] = node.arguments;
  return arg && arg.type === 'ObjectExpression' ? arg : null;
};

/** Does this expression read from the generated `token` object? */
const derivesFromToken = (node) => {
  let cursor = node;
  // `legacyPx(token.space['1'])` and friends: look through call arguments.
  if (cursor.type === 'CallExpression') {
    return cursor.arguments.some((arg) => derivesFromToken(arg));
  }
  while (cursor && cursor.type === 'MemberExpression') cursor = cursor.object;
  return Boolean(cursor) && cursor.type === 'Identifier' && cursor.name === 'token';
};

export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'The legacy `C` shim is closed and derived: no new keys, no literals, must stay frozen.',
    },
    schema: [],
    messages: {
      newKey:
        '`C.{{key}}` is not in the legacy shim inventory. `C` is a closed compatibility shim (design.md §3.4) and may only shrink — declare the token in src/styles/tokens.css instead.',
      newNestedKey:
        '`C.{{group}}.{{key}}` is not in the legacy shim inventory. `C` is a closed compatibility shim (design.md §3.4) and may only shrink.',
      literalValue:
        '`C.{{key}}` must be derived from `token.*` (src/design/tokens.js), not written as a literal — Requirement 1.1 allows exactly one token source.',
      retiredValue:
        "`C.{{group}}.{{key}}` must be the literal 'none'. Requirement 1.5 retires decorative glows and gradients.",
      notFrozen:
        '`C` must be wrapped in `Object.freeze(...)` so the shim cannot grow at runtime (design.md §3.4).',
    },
  },

  create(context) {
    const checkGroup = (group, objectNode) => {
      const allowed = ALLOWED_NESTED[group];
      const retired = RETIRED_GROUPS.has(group);

      for (const property of objectNode.properties) {
        const key = staticKey(property);
        if (key === null) continue;
        if (!allowed.has(key)) {
          context.report({ node: property, messageId: 'newNestedKey', data: { group, key } });
          continue;
        }
        if (retired) {
          const isNone = property.value.type === 'Literal' && property.value.value === 'none';
          if (!isNone) {
            context.report({ node: property, messageId: 'retiredValue', data: { group, key } });
          }
        } else if (!derivesFromToken(property.value)) {
          context.report({
            node: property,
            messageId: 'literalValue',
            data: { key: `${group}.${key}` },
          });
        }
      }
    };

    return {
      VariableDeclarator(node) {
        if (node.id.type !== 'Identifier' || node.id.name !== 'C') return;
        if (!node.init) return;

        const objectNode = unwrapFreeze(node.init);
        if (!objectNode) {
          context.report({ node, messageId: 'notFrozen' });
          return;
        }

        for (const property of objectNode.properties) {
          const key = staticKey(property);
          if (key === null) continue;

          if (!ALLOWED.has(key)) {
            context.report({ node: property, messageId: 'newKey', data: { key } });
            continue;
          }

          if (Object.prototype.hasOwnProperty.call(ALLOWED_NESTED, key)) {
            const nested = unwrapFreeze(property.value);
            if (!nested) {
              context.report({ node: property, messageId: 'notFrozen' });
              continue;
            }
            checkGroup(key, nested);
            continue;
          }

          if (!derivesFromToken(property.value)) {
            context.report({ node: property, messageId: 'literalValue', data: { key } });
          }
        }
      },
    };
  },
};

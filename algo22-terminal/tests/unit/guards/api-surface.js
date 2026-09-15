/**
 * `api-surface` — the two halves of the API contract, read off disk.
 *
 * Not a `*.test.js`, so vitest does not collect it. It exists so that
 * `api-paths.test.js` can hold the *rules* and this file can hold the *reading*:
 * the FastAPI route table on one side, every path the browser bundle constructs on
 * the other, in one shape that can be compared.
 *
 * ===========================================================================
 * WHAT IT READS, AND WHY THAT IS HARDER THAN IT LOOKS
 * ===========================================================================
 * **Backend.** `backend_app/main.py` decides where every router lives. There is no
 * single prefix: `/api/auth`, `/api/exchanges`, `/api/orders`, bare `/api`,
 * `/api/v1/copilot`, `/api/internal/persistence`, `/health`, and two routers that
 * carry their prefix on the `APIRouter(...)` constructor instead of the mount. A
 * guard that assumed `/api` would mis-locate half the surface, so the mount table
 * is derived from the `include_router(...)` calls and the import statements that
 * name their arguments — including the `from … import router as X` aliases, whose
 * modules live outside `backend_app/routers/` altogether.
 *
 * **Frontend.** Paths are template literals, assembled at four different levels:
 *
 *   * plain — `get('/api/portfolio/summary')`;
 *   * interpolated — `get(`/api/strategies/${encodeURIComponent(id)}`)`;
 *   * `+`-folded across two lines, because the line got long;
 *   * ternary-suffixed — `` `/api/orders/history${limit ? `?limit=${limit}` : ''}` ``,
 *     where the *query string itself* is one branch of a conditional.
 *
 * The last one is why interpolations are not simply blanked to a placeholder. A
 * blanked `${…}` turns that literal into `/api/orders/history{}`, which matches no
 * route and reads as a defect when the code is correct. Ternaries whose branches
 * are literals are therefore *expanded* into both concrete spellings and each is
 * checked. Everything else becomes `{}`, which is exactly right for a path
 * parameter and is reported as unresolvable when it lands anywhere else.
 *
 * ===========================================================================
 * NEVER SKIP WHAT IT CANNOT PARSE
 * ===========================================================================
 * Six defects reached production one at a time because nothing compared these two
 * lists. A guard that silently drops the call sites it cannot read would leave the
 * seventh in exactly the same place, so:
 *
 *   * a recognised transport call (`get`/`post`/`put`/`patch`/`del`/`fetch`/
 *     `apiCall`) whose URL mentions `/api` but does not reduce to a path is
 *     returned in `unresolvable`, not dropped;
 *   * every `/api` occurrence in code must be accounted for by a collected string
 *     or template literal, so a path hidden in a construct the scanner does not
 *     understand shows up as `unaccounted` rather than as nothing at all;
 *   * a route decorator whose path argument cannot be reduced to a literal is
 *     returned in `unresolvedDecorators`, because a hole in the reference set
 *     reads as a client defect.
 *
 * `source-scan.js`'s header explains why none of this is a tokeniser and why
 * `__dirname` is used instead of `import.meta.url`. Both apply here unchanged.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { collect, isTestFile, stripComments, TERMINAL_ROOT, toPosix } from './source-scan.js';

/** `<repo>/algo22-terminal`, and its parent. */
export const TERMINAL = TERMINAL_ROOT;
export const REPO_ROOT = path.resolve(TERMINAL_ROOT, '..');

const BACKEND = path.join(REPO_ROOT, 'backend_app');
const MAIN_PY = path.join(BACKEND, 'main.py');

/** The HTTP verbs a FastAPI decorator can name, and that the client can issue. */
const VERBS = ['get', 'post', 'put', 'patch', 'delete'];

/* ══ Literal scanning ══════════════════════════════════════════════════════ */

/**
 * The index just past the literal starting at `i`, which must be a quote.
 *
 * Backticks recurse through `${…}` so that a nested template — the ternary
 * suffix form — does not terminate the outer one at its own first backtick. A
 * `'`/`"` literal that does not close on its own line is treated as ending at
 * the newline: unbalanced quotes in JSX prose must not swallow the file, which
 * is the failure `source-scan.js`'s header records.
 *
 * @param {string} src
 * @param {number} i
 * @returns {number}
 */
function literalEnd(src, i) {
  const quote = src[i];
  let j = i + 1;
  while (j < src.length) {
    const c = src[j];
    if (c === '\\') {
      j += 2;
      continue;
    }
    if (c === quote) return j + 1;
    if (quote === '`' && c === '$' && src[j + 1] === '{') {
      let depth = 1;
      j += 2;
      while (j < src.length && depth > 0) {
        const d = src[j];
        if (d === '\\') {
          j += 2;
          continue;
        }
        if (d === '"' || d === "'" || d === '`') {
          j = literalEnd(src, j);
          continue;
        }
        if (d === '{') depth += 1;
        else if (d === '}') depth -= 1;
        j += 1;
      }
      continue;
    }
    if (quote !== '`' && c === '\n') return j;
    j += 1;
  }
  return src.length;
}

/**
 * Whether `src[i]` opens a string literal.
 *
 * A `'` or `"` preceded by a word character is an apostrophe in JSX prose
 * (`Don't`), not an opener — reading it as one swallows the rest of the file, which
 * is the exact failure `source-scan.js`'s header records. The one exception is a
 * Python string prefix: `f"One of {a}, {b}"` is a literal, and treating it as code
 * puts its comma at depth zero and splits a signature in half.
 */
const opensLiteral = (src, i) => {
  if (src[i] === '`') return true;
  if (src[i] !== "'" && src[i] !== '"') return false;
  const prev = src[i - 1] ?? '';
  if (!/[\w$]/.test(prev)) return true;
  return /[frbu]/i.test(prev) && !/[\w$]/.test(src[i - 2] ?? '');
};

/**
 * `code` with the *contents* of every literal blanked, delimiters kept, length and
 * line breaks preserved.
 *
 * Used only for brace and paren matching, where a `{` inside a template is not a
 * block and a `(` inside a string is not a call.
 *
 * @param {string} code
 * @returns {string}
 */
export function maskLiterals(code) {
  const chars = code.split('');
  for (let i = 0; i < code.length; i += 1) {
    if (!opensLiteral(code, i)) continue;
    const end = literalEnd(code, i);
    for (let k = i + 1; k < end - 1; k += 1) {
      if (chars[k] !== '\n') chars[k] = ' ';
    }
    i = end - 1;
  }
  return chars.join('');
}

/**
 * The name a literal is bound to, if any.
 *
 * `const NAME = '…'` is the common form. The looser `NAME = '…'` arm is needed for
 * `contexts/CopilotContext.jsx`, whose base path is a **destructured prop default**
 * — `({ children, apiBaseUrl = "/api/v1/copilot" })` — and is interpolated into
 * four `fetch` calls. Without the name, that literal reads as an address that no
 * router serves instead of as the base path it is.
 */
const BINDING_BEFORE =
  /(?:(?:export\s+)?(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*$/;

/**
 * Every complete literal in `code`, with `+`-joined runs folded into one body.
 *
 * The fold is required, not cosmetic: this codebase writes long paths as
 *
 *     `/api/strategies/${encodeURIComponent(id)}`
 *       + `/versions/${encodeURIComponent(version)}/deploy`
 *
 * and reading those as two literals would see two fragments, neither of which is
 * a route — the "cannot resolve" state this guard must never enter quietly.
 *
 * @param {string} code Comment-stripped source.
 * @returns {Array<{body: string, raw: string, start: number, end: number,
 *   line: number, bindingName: string|null}>}
 */
export function foldedLiterals(code) {
  const spans = [];
  for (let i = 0; i < code.length; i += 1) {
    if (!opensLiteral(code, i)) continue;
    const end = literalEnd(code, i);
    spans.push({ start: i, end, body: code.slice(i + 1, end - 1) });
    i = end - 1;
  }

  const out = [];
  for (let i = 0; i < spans.length; i += 1) {
    const { start } = spans[i];
    let { body, end } = spans[i];

    while (i + 1 < spans.length && /^\s*\+\s*$/.test(code.slice(end, spans[i + 1].start))) {
      i += 1;
      body += spans[i].body;
      end = spans[i].end;
    }

    const before = BINDING_BEFORE.exec(code.slice(Math.max(0, start - 160), start));
    out.push({
      body,
      raw: code.slice(start, end),
      start,
      end,
      line: code.slice(0, start).split('\n').length,
      bindingName: before ? before[1] : null,
    });
  }
  return out;
}

/* ══ Template resolution ═══════════════════════════════════════════════════ */

/**
 * The interpolations of one template body, as `{start, end, inner}` spans.
 *
 * Brace-matched rather than regex-matched, so a nested template
 * (`${a ? `?x=${a}` : ''}`) is one span rather than a mismatch.
 *
 * @param {string} body
 * @returns {Array<{start: number, end: number, inner: string}>}
 */
function interpolations(body) {
  const spans = [];
  for (let i = 0; i < body.length - 1; i += 1) {
    if (body[i] !== '$' || body[i + 1] !== '{') continue;
    let depth = 1;
    let j = i + 2;
    while (j < body.length && depth > 0) {
      const c = body[j];
      if (c === '\\') {
        j += 2;
        continue;
      }
      if (c === '"' || c === "'" || c === '`') {
        j = literalEnd(body, j);
        continue;
      }
      if (c === '{') depth += 1;
      else if (c === '}') depth -= 1;
      j += 1;
    }
    spans.push({ start: i, end: j, inner: body.slice(i + 2, j - 1) });
    i = j - 1;
  }
  return spans;
}

/** `COND ? <literal> : <literal>` — the conditional query-suffix form. */
const TERNARY_OF_LITERALS =
  /^[^?]*\?\s*(`(?:[^`\\]|\\.|\$\{[^{}]*\})*`|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*:\s*(`(?:[^`\\]|\\.|\$\{[^{}]*\})*`|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s*$/;

/**
 * Every concrete spelling one template body can produce, as a `{}`-placeholder
 * string.
 *
 * Three reductions, applied until nothing changes:
 *
 *   1. `${IDENT}` becomes the body of the path constant `IDENT` names, if one is
 *      known. `REGISTRY_BLOCKS_PATH = `${REGISTRY_BASE_PATH}/blocks`` is why:
 *      without it the marker lives in one constant and the routes built from it
 *      are invisible.
 *   2. `${COND ? 'a' : 'b'}` becomes two candidates, `a` and `b`. This is the
 *      conditional query suffix, and expanding it is what keeps a correct
 *      `` `…/history${limit ? `?limit=${limit}` : ''}` `` from reading as a defect.
 *   3. anything else becomes `{}` — right for a path parameter, and reported as
 *      unresolvable anywhere else.
 *
 * @param {string} body
 * @param {Map<string, string>} constants
 * @returns {string[]}
 */
export function candidates(body, constants) {
  let frontier = [body];
  for (let round = 0; round < 12; round += 1) {
    const next = [];
    let changed = false;
    for (const value of frontier) {
      const spans = interpolations(value);
      let handled = false;
      for (const span of spans) {
        const name = span.inner.trim();
        if (constants.has(name)) {
          next.push(value.slice(0, span.start) + constants.get(name) + value.slice(span.end));
          handled = true;
          break;
        }
        const ternary = TERNARY_OF_LITERALS.exec(span.inner);
        if (ternary) {
          for (const branch of [ternary[1], ternary[2]]) {
            next.push(
              value.slice(0, span.start) + branch.slice(1, -1) + value.slice(span.end),
            );
          }
          handled = true;
          break;
        }
      }
      if (handled) changed = true;
      else next.push(value);
    }
    frontier = [...new Set(next)];
    if (!changed) break;
  }

  return [
    ...new Set(
      frontier.map((value) => {
        const spans = interpolations(value);
        let out = '';
        let cursor = 0;
        for (const span of spans) {
          out += value.slice(cursor, span.start) + '{}';
          cursor = span.end;
        }
        return out + value.slice(cursor);
      }),
    ),
  ];
}

/** `/api`, optionally behind one host placeholder, then a path and maybe a query. */
const ANCHORED = /^(?:\{\})?(\/api(?:\/[^?\s]*)?)(?:\?([^\s]*))?$/;

/** A literal value that is itself an API path, and so may stand for a name. */
const PATH_BINDING = /^(?:\$\{[^{}]*\})?\/api\b/;

/**
 * Split one candidate into `{path, query}`, or `null` if it is not a path.
 *
 * A trailing `{}` glued to the end of a segment (`/api/paper/sessions{}`) is an
 * **opaque suffix**, not a path parameter: it is a helper that returns a query
 * string (`sessionListQuery(params)`). The path before it is known and is checked;
 * the query is marked opaque rather than invented.
 *
 * @param {string} candidate
 * @returns {{path: string, query: string|null, opaqueSuffix: boolean}|null}
 */
export function splitCandidate(candidate) {
  const m = ANCHORED.exec(candidate);
  if (!m) return null;
  let route = m[1];
  let opaqueSuffix = false;
  if (route.endsWith('{}') && !route.endsWith('/{}')) {
    route = route.slice(0, -2);
    opaqueSuffix = true;
  }
  return { path: route, query: m[2] ?? null, opaqueSuffix };
}

/** Top-level `+` operands of `text`, or one element when there is no top-level `+`. */
function splitPlus(text) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (c === '"' || c === "'" || c === '`') {
      i = literalEnd(text, i) - 1;
      continue;
    }
    if (c === '(' || c === '[' || c === '{') depth += 1;
    else if (c === ')' || c === ']' || c === '}') depth -= 1;
    else if (c === '+' && depth === 0 && text[i + 1] !== '+' && text[i - 1] !== '+') {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

/** `cond ? a : b` split at the top level, or `null`. Not `?.`, not `??`. */
function splitTernary(text) {
  let depth = 0;
  let mark = -1;
  let nested = 0;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (c === '"' || c === "'" || c === '`') {
      i = literalEnd(text, i) - 1;
      continue;
    }
    if (c === '(' || c === '[' || c === '{') depth += 1;
    else if (c === ')' || c === ']' || c === '}') depth -= 1;
    else if (depth !== 0) continue;
    else if (c === '?') {
      if (text[i + 1] === '.' || text[i + 1] === '?' || text[i - 1] === '?') continue;
      if (mark < 0) mark = i;
      else nested += 1;
    } else if (c === ':' && mark >= 0) {
      if (nested > 0) nested -= 1;
      else return { then: text.slice(mark + 1, i), otherwise: text.slice(i + 1) };
    }
  }
  return null;
}

/**
 * Every string one expression can evaluate to, as template bodies, or `null` when
 * this file cannot say.
 *
 * Four forms, because all four appear as the URL argument of a real call:
 *
 *   * a literal — `get('/api/portfolio/summary')`;
 *   * a `+`-fold — `post(`/api/strategies/${id}` + `/versions/${v}/deploy`, …)`,
 *     which is how every long path in `api/modules/strategies.js` is written and
 *     which reads as two unusable fragments if the concatenation is not followed;
 *   * a ternary — `get(currency ? `…?currency=${c}` : '…')` in
 *     `api/modules/billing.js`, where the two branches are two different paths;
 *   * an identifier bound to one of the above, which is how
 *     `api/typed-client.ts`'s notification list builds its URL before passing it on.
 *
 * @param {string} expr
 * @param {Map<string, string>} constants
 * @param {number} [depth]
 * @returns {string[]|null}
 */
export function stringExpressions(expr, constants, depth = 0) {
  const text = expr.trim();
  if (depth > 6 || text.length === 0) return null;

  const ternary = splitTernary(text);
  if (ternary) {
    const left = stringExpressions(ternary.then, constants, depth + 1);
    const right = stringExpressions(ternary.otherwise, constants, depth + 1);
    return left && right ? [...new Set([...left, ...right])] : null;
  }

  const parts = splitPlus(text);
  if (parts.length > 1) {
    let joined = [''];
    for (const part of parts) {
      const values = stringExpressions(part, constants, depth + 1);
      if (!values) return null;
      joined = joined.flatMap((prefix) => values.map((value) => prefix + value));
    }
    return [...new Set(joined)];
  }

  if (opensLiteral(text, 0) && literalEnd(text, 0) === text.length) return [text.slice(1, -1)];
  if (/^[A-Za-z_$][\w$]*$/.test(text) && constants.has(text)) return [constants.get(text)];
  return null;
}

/** Literal `name=` pairs in a query string; `{}` placeholders contribute nothing. */
export const queryNamesOf = (query) =>
  query === null
    ? []
    : query
        .split('&')
        .map((pair) => pair.split('=')[0].trim())
        .filter((name) => name.length > 0 && /^[A-Za-z_][\w.-]*$/.test(name));

/* ══ Backend: the mount table ══════════════════════════════════════════════ */

/**
 * `name` → `{module, attr}` for every `from … import …` in `main.py`.
 *
 * Three forms all appear: a parenthesised list of sibling modules
 * (`from backend_app.routers import (admin, analytics, …)`), a renamed attribute
 * (`from backend_app.backend.state_persistence import router as persistence_router`)
 * and the same spread over a backslash continuation. `attr === null` marks a name
 * that is a module rather than a router object.
 *
 * @param {string} source
 * @returns {Map<string, {module: string, attr: string|null}>}
 */
function importedNames(source) {
  const named = new Map();

  for (const m of source.matchAll(/^[ \t]*from\s+([\w.]+)\s+import\s+\(([^)]*)\)/gm)) {
    for (const raw of m[2].split(',')) {
      const name = raw.trim();
      if (name) named.set(name, { module: `${m[1]}.${name}`, attr: null });
    }
  }

  // Leading whitespace is allowed: `execution_router` is imported inside a `try:`
  // block, so an anchored `^from` misses it and the router it mounts vanishes.
  for (const m of source.matchAll(
    /^[ \t]*from\s+([\w.]+)\s+import\s+(?:\\\s*\n\s*)?([\w]+)(?:\s+as\s+([\w]+))?/gm,
  )) {
    const [, module, attr, alias] = m;
    named.set(alias ?? attr, { module, attr });
  }

  return named;
}

/** A dotted module path to the file that holds it. */
const moduleFile = (dotted) => path.join(REPO_ROOT, ...dotted.split('.')) + '.py';

/**
 * Every mount `main.py` declares, as `{file, routerVar, prefix}`.
 *
 * The prefix is the mount's own plus whatever the `APIRouter(prefix=…)`
 * constructor adds — `routers/dag_tasks.py` carries `/api/dag/tasks` there and is
 * mounted with no prefix at all, so reading only one of the two would place it at
 * the server root.
 *
 * @returns {{mounts: Array<Object>, unresolvedMounts: string[]}}
 */
export function mountTable() {
  const source = readFileSync(MAIN_PY, 'utf8');
  const named = importedNames(source);
  const mounts = [];
  const unresolvedMounts = [];

  const seen = new Set();
  for (const m of source.matchAll(
    /^[ \t]*app\.include_router\(\s*([A-Za-z_][\w.]*)\s*(?:,\s*prefix=["']([^"']*)["'])?/gm,
  )) {
    const [, expr, mountPrefix = ''] = m;
    const [head, tail] = expr.split('.');
    const imported = named.get(head);

    if (!imported) {
      unresolvedMounts.push(`main.py: include_router(${expr}, …) — ${head} is not imported here`);
      continue;
    }

    const routerVar = tail ?? imported.attr;
    if (!routerVar) {
      unresolvedMounts.push(`main.py: include_router(${expr}, …) — no router attribute to read`);
      continue;
    }

    const file = moduleFile(imported.module);
    const relative = toPosix(path.relative(REPO_ROOT, file));
    const key = `${relative}|${routerVar}|${mountPrefix}`;
    if (seen.has(key)) continue;
    seen.add(key);

    let routerSource;
    try {
      routerSource = readFileSync(file, 'utf8');
    } catch {
      unresolvedMounts.push(`main.py mounts ${expr} but ${relative} is unreadable`);
      continue;
    }

    let selfPrefix = '';
    const ctor = new RegExp(`^${routerVar}\\s*=\\s*APIRouter\\(([^)]*)\\)`, 'm').exec(routerSource);
    if (ctor) {
      const declared = /prefix\s*=\s*["']([^"']*)["']/.exec(ctor[1]);
      if (declared) selfPrefix = declared[1];
    }

    mounts.push({
      file,
      relative,
      routerVar,
      prefix: mountPrefix + selfPrefix,
      source: routerSource,
    });
  }

  return { mounts, unresolvedMounts };
}

/* ══ Backend: the declared routes ══════════════════════════════════════════ */

/** The balanced argument text of the call whose `(` is at `open`. */
function argText(code, open) {
  let depth = 0;
  let i = open;
  while (i < code.length) {
    const c = code[i];
    if (c === '"' || c === "'" || c === '`') {
      i = literalEnd(code, i);
      continue;
    }
    if (c === '(' || c === '[' || c === '{') depth += 1;
    else if (c === ')' || c === ']' || c === '}') {
      depth -= 1;
      if (depth === 0) return code.slice(open + 1, i);
    }
    i += 1;
  }
  return null;
}

/** Split argument text on top-level commas. */
function splitArgs(text) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (c === '"' || c === "'" || c === '`') {
      i = literalEnd(text, i) - 1;
      continue;
    }
    if (c === '(' || c === '[' || c === '{') depth += 1;
    else if (c === ')' || c === ']' || c === '}') depth -= 1;
    else if (c === ',' && depth === 0) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts.map((p) => p.trim()).filter((p) => p.length > 0);
}

/** `NAME = ("…", "…")` / `NAME = ["…"]` — a module-level tuple of paths. */
const PATH_TUPLE = /^([A-Za-z_][\w]*)\s*=\s*[([]([\s\S]*?)[)\]]/gm;
/** A Python string literal. */
const PY_STRING = /"([^"\n]*)"|'([^'\n]*)'/g;

/**
 * The parameter chunks of the `def` whose name starts at `defAt`, and its name.
 *
 * @param {string} source
 * @param {number} defAt Index of the `d` in `def`.
 * @returns {{name: string, params: string[]}|null}
 */
function signatureAt(source, defAt) {
  const head = /^def\s+([A-Za-z_]\w*)\s*\(/.exec(source.slice(defAt, defAt + 200));
  if (!head) return null;
  const open = defAt + head[0].length - 1;
  const text = argText(source, open);
  if (text === null) return null;
  return { name: head[1], params: splitArgs(text) };
}

/**
 * The query parameters one signature declares, split by whether FastAPI will
 * refuse the request without them.
 *
 * Only `Query(...)` — `Query` with `...` as its first argument, i.e. no default —
 * counts as required, and that is a deliberate narrowing. A bare annotated
 * parameter with no default is *also* required by FastAPI, but telling
 * `symbol: str` (a query parameter) apart from `body: CancelOrderRequest` (a JSON
 * body), `request: Request` and `user: dict = Depends(...)` needs the type
 * resolution a text scan does not have. `Query(...)` is unambiguous, it is the
 * form this codebase uses for every required query parameter it has, and it is
 * the form all three of defects 4-6 turned on.
 *
 * @param {string[]} params
 * @returns {{required: string[], optional: string[]}}
 */
function queryParams(params) {
  const required = [];
  const optional = [];
  for (const chunk of params) {
    const m = /^([A-Za-z_]\w*)\s*:[\s\S]*?=\s*Query\(([\s\S]*)$/.exec(chunk);
    if (!m) continue;
    if (/^\s*\.\.\./.test(m[2])) required.push(m[1]);
    else optional.push(m[1]);
  }
  return { required, optional };
}

/** `{param}` → `{}`, matching the frontend's placeholder. */
export const normalisePath = (value) => value.replace(/\{[^{}]*\}/g, '{}');

/**
 * Every route the mounted routers declare, plus the decorators that could not be
 * reduced to a path.
 *
 * A stack of decorators on one function all resolve to the same handler, which is
 * how `execute_backtest`'s two spellings and the preflight's `_PREFLIGHT_PATHS`
 * pair both end up in the table with the same query parameters. `main.py`'s own
 * `@app.get(...)` routes are included at no prefix — `/api/stats` is one of them,
 * and `api/modules/user.js` calls it.
 *
 * @returns {{routes: Array<Object>, unresolvedDecorators: string[]}}
 */
export function declaredRoutes() {
  const { mounts, unresolvedMounts } = mountTable();
  const routes = [];
  const unresolvedDecorators = [...unresolvedMounts];

  const units = mounts.map((mount) => {
    // A router spliced into the mounted one shares its prefix. `routers/library.py`
    // declares every literal-segment route on `literal_router` and splices it in
    // front, so its 30-odd routes are invisible without this.
    const vars = new Set([mount.routerVar]);
    for (const m of mount.source.matchAll(
      new RegExp(`${mount.routerVar}\\.routes\\[:0\\]\\s*=\\s*([A-Za-z_]\\w*)\\.routes`, 'g'),
    )) {
      vars.add(m[1]);
    }
    return { ...mount, vars };
  });

  units.push({
    file: MAIN_PY,
    relative: 'backend_app/main.py',
    prefix: '',
    source: readFileSync(MAIN_PY, 'utf8'),
    vars: new Set(['app']),
  });

  for (const unit of units) {
    const { source } = unit;

    const tuples = new Map();
    for (const m of source.matchAll(PATH_TUPLE)) {
      const items = [...m[2].matchAll(PY_STRING)].map((s) => s[1] ?? s[2]);
      if (items.length) tuples.set(m[1], items);
    }

    const decorator = new RegExp(
      `^[ \\t]*@(${[...unit.vars].join('|')})\\.(${VERBS.join('|')})\\(`,
      'gm',
    );

    /** @type {Array<{method: string, declared: string}>} */
    let pending = [];
    const events = [];
    for (const m of source.matchAll(decorator)) {
      events.push({ kind: 'decorator', index: m.index, method: m[2], open: m.index + m[0].length - 1 });
    }
    for (const m of source.matchAll(/^[ \t]*(?:async\s+)?def\s/gm)) {
      events.push({ kind: 'def', index: m.index + m[0].indexOf('def') });
    }
    events.sort((a, b) => a.index - b.index);

    for (const event of events) {
      if (event.kind === 'decorator') {
        const text = argText(source, event.open);
        const first = text === null ? null : splitArgs(text)[0] ?? '""';
        let declared = null;
        const literal = first === null ? null : /^["']([^"']*)["']$/.exec(first);
        const indexed = first === null ? null : /^([A-Za-z_]\w*)\[(\d+)\]$/.exec(first);
        if (literal) declared = literal[1];
        else if (indexed && tuples.has(indexed[1])) {
          declared = tuples.get(indexed[1])[Number(indexed[2])];
        }
        if (declared == null) {
          unresolvedDecorators.push(
            `${unit.relative}: @${event.method}(${(first ?? '?').slice(0, 60)})`,
          );
          continue;
        }
        pending.push({ method: event.method.toUpperCase(), declared });
        continue;
      }

      if (pending.length === 0) continue;
      const signature = signatureAt(source, event.index);
      const query = signature ? queryParams(signature.params) : { required: [], optional: [] };
      for (const { method, declared } of pending) {
        routes.push({
          method,
          full: normalisePath(unit.prefix + declared),
          module: unit.relative,
          handler: signature ? signature.name : '?',
          declared,
          requiredQuery: query.required,
          optionalQuery: query.optional,
        });
      }
      pending = [];
    }
  }

  return { routes, unresolvedDecorators };
}

/* ══ Frontend: the call sites ══════════════════════════════════════════════ */

/** The trees this guard reads, under `src/`. Every file in them that is not a test. */
export const SCANNED_DIRS = [
  'api',
  'components',
  'contexts',
  'design',
  'hooks',
  'lib',
  'pages',
  'shell',
  'utils',
];

/** The transports. `del` is `apiClient`'s DELETE; `apiCall` is `typed-client`'s wrapper. */
const HELPER_METHODS = { get: 'GET', post: 'POST', put: 'PUT', patch: 'PATCH', del: 'DELETE' };

/** `get(`, `post(`, … — never `params.get(`, never `getCacheKey(`. */
const HELPER_CALL = new RegExp(
  `(?<![.\\w$])(${Object.keys(HELPER_METHODS).join('|')})\\s*\\(`,
  'g',
);
/** `fetch(` and `apiCall<…>(`, the two other shapes that reach the network. */
const FETCH_CALL = /(?<![.\w$])fetch\s*\(/g;
const APICALL_CALL = /(?<![.\w$])apiCall\s*(?:<[^()]*?>)?\s*\(/g;

/**
 * The innermost *function body* containing `index`, as `[start, end)`.
 *
 * A function body, not merely the innermost braces: a `{` preceded by `=` or `,` is
 * an object literal, and harvesting `params.set('…')` calls out of a whole
 * `ordersApi = { … }` object would credit one method with a parameter a sibling
 * method sends. When the call is not inside a braced function body at all
 * (`getSummary: () => get('/api/portfolio/summary')`) the range is empty, which is
 * correct: there is no statement in which a parameter could have been set.
 *
 * @param {string} masked Source with literal contents blanked.
 * @param {number} index
 * @returns {[number, number]}
 */
function enclosingBlock(masked, index) {
  let depth = 0;
  let start = -1;
  for (let i = index; i >= 0; i -= 1) {
    const c = masked[i];
    if (c === '}') depth += 1;
    else if (c === '{') {
      if (depth === 0) {
        start = i;
        break;
      }
      depth -= 1;
    }
  }
  if (start < 0) return [0, 0];
  const before = masked.slice(Math.max(0, start - 8), start).trimEnd();
  if (!before.endsWith('=>') && !before.endsWith(')')) return [start, start];

  depth = 0;
  let end = masked.length;
  for (let i = start; i < masked.length; i += 1) {
    const c = masked[i];
    if (c === '{') depth += 1;
    else if (c === '}') {
      depth -= 1;
      if (depth === 0) {
        end = i + 1;
        break;
      }
    }
  }
  return [start, end];
}

/**
 * The query parameter names an enclosing block puts into a `URLSearchParams`, and
 * whether it also puts something opaque in.
 *
 * `?${params}` says nothing on its own. What the block around it says is
 * `params.set('exchange_id', exchangeId)` — a name this guard can read — or
 * `new URLSearchParams(filters)`, a caller-supplied bag it cannot. The first is
 * evidence the parameter is sent; the second is an admission that the request is
 * not knowable from this file, and is reported as such rather than assumed fine.
 *
 * @param {string} block
 * @returns {{names: string[], opaque: boolean}}
 */
function searchParamsIn(block) {
  const names = [];
  for (const m of block.matchAll(/\.(?:set|append)\(\s*['"]([^'"]+)['"]/g)) names.push(m[1]);
  for (const m of block.matchAll(/\bnew\s+URLSearchParams\(\s*([^)]*)\)/g)) {
    const arg = m[1].trim();
    if (arg.length > 0 && !/^\{\s*\}$/.test(arg)) return { names, opaque: true };
  }
  return { names, opaque: false };
}

/** The keys of a `params: { … }` object in a config argument. */
function configParamNames(configText) {
  if (!configText) return [];
  const at = configText.indexOf('params');
  if (at < 0) return [];
  const open = configText.indexOf('{', at);
  if (open < 0) return [];
  const text = argText(configText, open);
  if (text === null) return [];
  return splitArgs(text)
    .map((chunk) => chunk.split(':')[0].trim().replace(/^['"]|['"]$/g, ''))
    .filter((name) => /^[A-Za-z_][\w.-]*$/.test(name));
}

/**
 * Every path the browser bundle constructs, and everything about it that could
 * not be read.
 *
 * @returns {{calls: Array<Object>, literals: Array<Object>, unresolvable: string[],
 *   unaccounted: string[], filesRead: number}}
 */
export function clientPaths() {
  const files = SCANNED_DIRS.flatMap((dir) => {
    const full = path.join(TERMINAL, 'src', dir);
    try {
      return collect(full, ['.js', '.jsx', '.ts', '.tsx']);
    } catch {
      return [];
    }
  }).filter((file) => !isTestFile(toPosix(path.relative(TERMINAL, file))));

  const parsed = files.map((file) => {
    const code = stripComments(readFileSync(file, 'utf8'));
    return {
      file,
      relative: toPosix(path.relative(TERMINAL, file)),
      code,
      masked: maskLiterals(code),
      literals: foldedLiterals(code),
    };
  });

  // Pass 1: every *path* binding in the tree, so a cross-file `${IDENT}` resolves.
  //
  // Path-shaped only, and that restriction is load-bearing. Substituting any name
  // bound to any string turns `${id}` into whichever `id = "…"` the tree happens to
  // hold — a className, a timeframe, an enum value — and then reports the invented
  // path as a defect. A binding qualifies only if its own value is already anchored
  // at `/api`, optionally behind one host placeholder, which is exactly the two
  // cases that need it: `registryClient.REGISTRY_BASE_PATH` and
  // `CopilotContext`'s `apiBaseUrl` prop default.
  const global = new Map();
  for (const unit of parsed) {
    for (const literal of unit.literals) {
      if (!literal.bindingName || global.has(literal.bindingName)) continue;
      if (!PATH_BINDING.test(literal.body)) continue;
      global.set(literal.bindingName, literal.body);
    }
  }

  const calls = [];
  const literals = [];
  const unresolvable = [];
  const unaccounted = [];

  for (const unit of parsed) {
    const { code, masked, relative } = unit;
    const constants = new Map(global);
    for (const literal of unit.literals) {
      if (literal.bindingName && PATH_BINDING.test(literal.body)) {
        constants.set(literal.bindingName, literal.body);
      }
    }

    const lineAt = (index) => code.slice(0, index).split('\n').length;

    /**
     * A bare identifier URL argument, resolved against bindings **in scope at the
     * call** rather than against the file.
     *
     * `api/typed-client.ts` is why the scope matters. Its generic dispatcher is
     * `apiCall(method, url, …)`, whose body reads `await post(url, …)` in five
     * branches, and one of its callers holds `const url = `/api/notifications…``
     * three hundred lines away. A file-wide lookup ties the dispatcher's five
     * branches to that one path and reports POST, PUT and PATCH
     * `/api/notifications` as defects — none of which exists. A parameter is not
     * the caller's local, so the binding must be inside the same function body and
     * ahead of the call.
     */
    const scopedBinding = (ident, index) => {
      const [blockStart] = enclosingBlock(masked, index);
      return (
        unit.literals.find(
          (literal) =>
            literal.bindingName === ident && literal.start >= blockStart && literal.start < index,
        ) ?? null
      );
    };

    /** One recognised transport call. */
    const record = (index, method, urlArg, configText) => {
      const where = `${relative}:${lineAt(index)}`;
      const ident = /^[A-Za-z_$][\w$]*$/.test(urlArg.trim()) ? urlArg.trim() : null;
      const scoped = ident ? scopedBinding(ident, index) : null;
      // An identifier with no binding in scope is a parameter, and a parameter has no
      // path. The literal it will be given is still checked where it is written; only
      // the verb is unknowable here.
      const bodies = ident
        ? (scoped && [scoped.body]) || null
        : stringExpressions(urlArg, constants);

      if (bodies === null) {
        if (/\/api\b/.test(urlArg)) {
          unresolvable.push(
            `${where}: ${method} ${urlArg.replace(/\s+/g, ' ').slice(0, 90)} — url does not ` +
              `reduce to a string`,
          );
        }
        return;
      }

      const resolved = [...new Set(bodies.flatMap((body) => candidates(body, constants)))];
      if (!resolved.some((c) => /\/api\b/.test(c))) return;

      const split = resolved.map((c) => splitCandidate(c));
      if (split.some((s) => s === null)) {
        unresolvable.push(
          `${where}: ${method} ${resolved.filter((c, i) => split[i] === null).join(' | ').slice(0, 120)}`,
        );
        return;
      }

      const [blockStart, blockEnd] = enclosingBlock(masked, index);
      const fromBlock = searchParamsIn(code.slice(blockStart, blockEnd));
      const fromConfig = configParamNames(configText);
      const names = new Set([
        ...fromBlock.names,
        ...fromConfig,
        ...split.flatMap((s) => queryNamesOf(s.query)),
      ]);

      // `opaque` is a claim about what cannot be known, and it is kept narrow so the
      // report says which of two different things is wrong. A `?${params}` query is
      // NOT opaque when the block around it sets its keys by name — that is the
      // strongest evidence available, and calling it opaque would downgrade
      // "this parameter is not sent" to "it might be". It IS opaque when the bag
      // comes from the caller (`new URLSearchParams(filters)`), when a helper returns
      // the whole suffix (`${sessionListQuery(params)}`), or when the query is
      // interpolated and nothing names a single key.
      const opaque =
        fromBlock.opaque ||
        split.some((s) => s.opaqueSuffix) ||
        (split.some((s) => s.query !== null && s.query.includes('{}')) &&
          fromBlock.names.length === 0 &&
          fromConfig.length === 0);

      calls.push({
        where,
        method,
        paths: [...new Set(split.map((s) => s.path))],
        queryNames: [...names],
        queryOpaque: opaque,
      });
    };

    // Matched against `masked`, so `get(` written inside a string or a doc example
    // is not read as a call; the arguments are then read from `code`, where the
    // literals are intact. `maskLiterals` preserves length, so the offsets agree.
    const scan = (pattern, handle) => {
      for (const m of masked.matchAll(pattern)) {
        const open = m.index + m[0].length - 1;
        const text = argText(code, open);
        if (text === null) continue;
        handle(m, splitArgs(text));
      }
    };

    scan(HELPER_CALL, (m, args) => {
      const method = HELPER_METHODS[m[1]];
      const configIndex = method === 'GET' || method === 'DELETE' ? 1 : 2;
      record(m.index, method, args[0] ?? '', args[configIndex]);
    });

    scan(FETCH_CALL, (m, args) => {
      const options = args[1] ?? '';
      const declared = /method\s*:\s*['"]([A-Za-z]+)['"]/.exec(options);
      record(m.index, (declared ? declared[1] : 'GET').toUpperCase(), args[0] ?? '', options);
    });

    // `apiCall(method, url, data?, validator?)` — the verb is the first argument and
    // there is no axios config, so a query parameter can only reach the server through
    // the URL. No config argument is passed on.
    scan(APICALL_CALL, (m, args) => {
      // `async function apiCall<TRequest, TResponse>(` is the declaration, not a call.
      if (/\bfunction\s*$/.test(masked.slice(Math.max(0, m.index - 12), m.index))) return;
      const verb = /^['"]([A-Za-z]+)['"]$/.exec(args[0] ?? '');
      if (!verb) {
        unresolvable.push(`${relative}:${lineAt(m.index)}: apiCall method is not a literal`);
        return;
      }
      record(m.index, verb[1].toUpperCase(), args[1] ?? '', undefined);
    });

    // Every anchored path literal, whether or not a recognised call consumes it.
    // `lib/deployFlow.js` states a route as data and `lib/registryClient.js` exports
    // a base path; neither is a call, and both address the server.
    for (const literal of unit.literals) {
      for (const candidate of candidates(literal.body, constants)) {
        const split = splitCandidate(candidate);
        if (!split) continue;
        literals.push({
          where: `${relative}:${literal.line}`,
          path: split.path,
          bindingName: literal.bindingName,
          raw: literal.body.length > 110 ? `${literal.body.slice(0, 110)}…` : literal.body,
        });
      }
    }

    // `masked` has every literal's contents blanked, so an `/api` still visible in
    // it is an `/api` that no literal explains — a path in a construct this scanner
    // does not understand. Skipping those quietly is what let six defects through.
    //
    // `\/` is neutralised first. An escaped slash cannot be part of a URL path, and
    // regex literals are not masked (`source-scan.js`'s header explains why nothing
    // here tokenises: in JSX, `</div>` and a regex are indistinguishable). The one
    // case is `design/errorCopy.js`'s `/https?:\/\/\S+\/api\//`, a redaction pattern
    // for endpoint URLs in error text — a rule *about* paths, not a path.
    const outsideLiterals = masked.replace(/\\\//g, '  ').split('/api').length - 1;
    if (outsideLiterals > 0) {
      unaccounted.push(
        `${relative}: ${outsideLiterals} occurrence(s) of /api outside any string or ` +
          `template literal — the scanner cannot see the path they belong to`,
      );
    }
  }

  return { calls, literals, unresolvable, unaccounted, filesRead: parsed.length };
}

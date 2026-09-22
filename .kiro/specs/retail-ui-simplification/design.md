# Design Document — Retail UI Simplification

`algo22-terminal` (React 18 + Vite + Tailwind CSS v4). Implements the 22 requirements in
`.kiro/specs/retail-ui-simplification/requirements.md`.

This design adopts a convention it does not own. Where it disagrees with
`.kiro/specs/vyomquant-ui-redesign/design.md`, that document governs and the disagreement is
recorded here rather than resolved in code (Requirement 17.5). Every measurement lives in
requirements.md; this document cites clause numbers and does not restate counts, except where a
count was re-measured for a decision and came out different — those are marked
**[RE-MEASURED]** and reported as corrections, not as new facts about the same thing.

## Overview

### Section index

Section numbers are unchanged. Every `§N` and `§N.M` reference in this document still resolves;
the third column names the `##` heading each numbered section now sits under.

| N | Section | `##` heading | Requirements |
| --- | --- | --- | --- |
| 1 | Overview — three causes, three remedies | Overview | framing |
| 2 | The px-to-step mapping | Architecture | 1, 2, 3 |
| 3 | The font-size ratchet | Architecture | 1.3, 1.4, 1.5, 18 |
| 4 | Change ordering | Architecture | 22, 4.5, 9.3, 12, 13 |
| 5 | Preservation per change class | Correctness Properties | 16, 18, 19, 20 |
| 6 | The zero-versus-unavailable hazard | Data Models | 19, 7.2, 8.3 |
| 7 | Marketplace — §7.1–§7.4, §7.6 | Components and Interfaces | 10, 12, 3.4 |
| 7 | Marketplace — §7.5, the read path | Error Handling | 11 |
| 8 | Landing investigation | Landing investigation | 13, 14, 15 |
| 9 | Interactivity, honestly | Interactivity, honestly | 7, 8, 20 |
| 10 | Verification without a build | Testing Strategy | 18, 22 |

### 1.1 Three causes

The request that opened this spec was one complaint: the product is hard for a retail
customer to read. requirements.md's central claim is that the complaint has three causes with
three unrelated remedies, and that the expensive failure mode of this pass is treating them as
one.

| # | Cause | Shape of the remedy | What goes wrong if it is merged with the others |
| --- | --- | --- | --- |
| 1 | **Typography was never centralised.** Colour was ratcheted to zero on eleven pages and held there; font size was left to each page's author (requirements §1.2). | Mechanical. Adopt the seven steps that already exist and add the guard that was never written. | A mechanical sweep applied to cause 3 deletes prose. A judgement applied to cause 1 produces an eighth step. |
| 2 | **Eleven of twenty-two pages never adopted the convention**, and are outside the accessibility ratchet's enforcement entirely. Marketplace is a twelfth case — inside the enforced set, styled from the previous generation (requirements §1.4, §1.6). | Migration, not redesign. Adopt `usePanelState`, `pageFields`, `ds/*`, `errorCopy`. Add nothing. | Migration reads as licence to improve, and the pass acquires a second design system (Requirement 17). |
| 3 | **The adopted pages are consistent, verbose and dense.** Live Trading's standing prose, the badge's four stacked treatments, Paper Trading's explanations below the legibility floor (requirements §1.5, §1.7, §1.8). | Copy and hierarchy. Move prose behind disclosure; keep every distinction it draws. | The clutter-reduction argument reaches a not-available reason, and the pass re-introduces the defect `production-launch-hardening` removed (Requirement 19.3). |

The middle group is the one a careless scope misses. `PaperTrading.jsx`, `StrategyBuilder.jsx`
and `StrategyDetail.jsx` sit inside `In_Scope_Pages`, are at zero colour literals, and hold
104 of the 401 unitless sizes between them (requirements §1.4). A remedy scoped by the phrase
"unmigrated pages" skips all three. Requirement 4.6 exists for exactly that reason and this
design keys every rule on a *measured property of the file*, never on which list the file is
on.

### 1.2 Three remedies, and what each one is allowed to touch

- **Cause 1 → §2 and §3.** A role-keyed mapping from every observed px value onto the seven
  declared steps, and a decreasing per-file budget that mirrors `no-colour-literals` exactly.
  Touches style objects and class strings. Touches no copy and no structure.
- **Cause 2 → §5.3 and §7.** Per-page adoption in Requirement 4.5's order, placed as commits
  8–21 by §4.3; Marketplace, the twelfth case, is §7, and its read path is §7.5. Touches read
  paths and JSX structure. Adds no module, no primitive and no panel state.
- **Cause 3 → §5.5 and §9, bounded by §6.** Placement and wording. Touches copy strings and
  disclosure structure. Touches no figure, no reason and no confirmation.

The landing surface is handled in §8 as an investigation with a recorded result, because
requirements §1.9 records that the symptom has not been described, has not been reproduced, and
was not found by static reading of all fourteen rendered sections. Requirement 13 asks for a
reproduction. This design does not invent a defect list to fix.

### 1.3 The one thing this design adds

Nothing, structurally. The token layer exists (`src/styles/tokens.css`). The seven steps exist
and are already annotated with their intended roles (`tokens.css:69`–`:75`). The primitives,
the panel states, the availability layer and the error copy all exist. The four budget guards
exist and one of them is the template for the fifth.

The single new artefact in this spec is a guard file pair —
`tests/unit/guards/absolute-font-sizes.budget.js` and `.test.js` — and it is new only in the
sense that it was never written. Its shape is dictated: §3 mirrors
`no-colour-literals.{budget,test}.js` line for line, on the same `source-scan.js` helper.

---

## Architecture

### 2. The px-to-step mapping

This is the most consequential decision in the document. Fourteen files carry 451 absolute
font sizes; every one of them needs an answer, and the answer has to be derivable by a second
implementer without re-litigating it.

### 2.1 The seven steps already declare their roles

`tokens.css:69`–`:75` does not merely declare seven numbers. Each step carries an inline annotation
naming what it is for:

| Step | Value | At a 16px default | `tokens.css` annotation |
| --- | --- | --- | --- |
| `--text-micro` | 0.625rem | 10px | `labels, chips` (`:69`) |
| `--text-small` | 0.6875rem | 11px | `table body` (`:70`) |
| `--text-body` | 0.8125rem | 13px | `default` (`:71`) |
| `--text-title` | 0.875rem | 14px | `panel title` (`:72`) |
| `--text-section` | 1.125rem | 18px | `section head` (`:73`) |
| `--text-page` | 1.5rem | 24px | `page title` (`:74`) |
| `--text-figure` | 1.75rem | 28px | `tier-1 metric` (`:75`) |

That table is the mapping's foundation and it is not invented here. The scale was designed as
a **role scale that happens to be ordered by size**, and the annotations are the contract. A
mapping keyed on role is therefore reading the token file as written; a mapping keyed on the
current number is ignoring the right-hand column.

### 2.2 Decision D1 — the mapping is keyed on role, not on the current number

**Decision.** Every call site is classified by the role the text plays, and the role selects
the step. The current px value is *evidence* about the role and is never the input to the
mapping.

**Alternative considered: numeric nearest-step.** Requirement 1.1's literal wording — "a call
site whose size is not in the `Type_Scale` SHALL move to the nearest step" — reads as a
numeric rule. It lost for three reasons, each independently sufficient.

1. **It is not a function.** Requirement 2.3 sends 9px *prose* up to `body` (13px, +44%) and
   9px *labels* to `micro` (10px, +11%). One input, two outputs. A numeric rule cannot express
   that, so any numeric rule is provably wrong on `PaperTrading.jsx` before it is applied to
   anything else.
2. **It is not total.** `PaperTrading.jsx:2882` is `fontSize: 16`, equidistant from `title`
   (14) and `section` (18). "Nearest" has no answer. A rule with an undefined case is a rule
   two implementers resolve differently.
3. **It preserves the defect.** The pages that bypassed the scale chose their own numbers.
   Mapping each to its nearest step ratifies those choices and produces a tree that satisfies
   the guard while looking exactly as inconsistent as it does today — nine different label
   sizes, each now a token.

**Alternative considered: a checked-in per-call-site lookup table** (`file:line → step`),
generated once and asserted by a test. Rejected under Requirement 17.1: a 451-row table that
decides font sizes *is* a second design system, in table form. It also cannot be reviewed —
only trusted — because nothing in it states why any row says what it says.

**Recorded disagreement (Requirement 17.5).** Requirement 1.1's nearest-step wording and
Requirements 2.3/2.4's role wording disagree on real call sites in this tree:

| Call site | Under nearest-step | Under role | Δ |
| --- | --- | --- | --- |
| `PaperTrading.jsx:2355` — `fontSize: '1.25rem'` on the page `<h1>` | 20px → `section` (18px), a *shrink* | page title → `page` (24px) | two steps apart, opposite directions |
| `PaperTrading.jsx:2882` — `fontSize: 16` on the latency figure | undefined (tie) | panel value → `section` | one is undefined |
| `PaperTrading.jsx:434` vs `:2885` — both `fontSize: 9` | both → `micro` | `micro` and `body` respectively | 3px apart |

This design resolves it: **role governs. Nearest-step is a tie-break of last resort, used only
when the role is `value` and its tier cannot be determined from the page, and ties resolve
up.** Ties resolve up rather than down because Requirement 2.2 sets a floor and no ceiling, and
Requirement 2.5 already decided which side yields when the two conflict. The resolution is
recorded here rather than applied silently because it narrows a clause in an approved
requirements document.

### 2.3 The roles

Six roles were requested. The tree forces eight. The two additions are named as findings, not
as scope creep — each has call sites in `PaperTrading.jsx` today that none of the six covers.

| Role | Test that identifies it | Step | Why |
| --- | --- | --- | --- |
| **chip** | Bordered or filled pill. Three words or fewer. Never wraps. Carries a state, not a measurement. | `micro` | `tokens.css:69` says chips. Requirement 3.3's three-word rule is the same boundary from the all-caps side, which is a useful cross-check: a "chip" that fails the three-word test is a sentence wearing a border. |
| **label** | Names an adjacent value. Column header, field label, eyebrow, table caption. Remove it and a value loses its name; remove the value and the label is meaningless. | `micro` | `tokens.css:69`. |
| **table cell** | A value in a row, in a column with siblings, where vertical alignment across rows carries meaning. | `small` | `tokens.css:70` says table body. This is the one role whose step is justified by density rather than by legibility, and it is the reason `small` exists at all. |
| **value** | A figure the panel exists to report. Has a unit, a sign or a magnitude. | `figure` (tier-1 hero), `title` (panel-level), `body` (in-panel secondary) | `tokens.css:75` reserves `figure` for tier-1. Tier comes from `design/pageHierarchy.js`, which already declares it — not from a judgement made per call site. |
| **sentence** | Contains a finite verb, or ends in a full stop, or can wrap onto a second line as prose. | `body`, never lower | Requirement 2.3. `tokens.css:71` calls `body` the default; a sentence is the default case. |
| **heading** | Names a region that contains other elements. | `title` (panel), `section` (section), `page` (page `<h1>`) | `tokens.css:72`–`:74`, by nesting depth. |
| **control text** *(added)* | Text inside an `<input>`, `<select>` or `<textarea>` — text the trader types or reads back while typing. | `body` | `PaperTrading.jsx:449` (`fieldStyle`, 11px) has no slot in Requirement 2.4's chip/table-cell/sentence trichotomy. It is not a cell: it has no column. It is not a chip: it is editable. Form-control text at `small` is below the page's own default, which is backwards — this is the one place where a misread costs a wrong number typed into a capital field. |
| **chart tick** *(added)* | SVG text inside a chart, sized by a prop rather than a class. | `micro`, set in the primitive | `PaperTrading.jsx:3197`, `:3198`, `:3263`, `:3264`, `:3308`, `:3309`, `:3346`, `:3347` are `fontSize={9}` — a JSX prop, a fourth syntax (see §3.2). `ds/Chart.jsx:722` and `:729` hold the migrated pages' equivalent at `fontSize: 10`, and `:719`'s own comment already says "axis ticks are text". Neither is a page-level style object. |

**Not a role: a unit or suffix.** `PaperTrading.jsx:713` is `fontSize: 11` on the unit `<span>`
beside an 18px figure. The intent is a *relative* contrast, not an absolute size. Rule: a unit,
suffix or sign glyph takes **one step below its figure's step**, and moves when the figure
moves. Making it a role of its own would pin it, and the next time the figure's tier changed
the pair would drift. This costs no new token and no new step.

### 2.4 The decision procedure

Ordered. First match wins. The order is load-bearing and is stated so that the same call site
resolves the same way twice.

1. **Is it a sentence?** Finite verb, or terminal full stop, or wraps as prose. → `sentence`.
   → `body` minimum. *Asked first* because it is the only question whose answer overrides the
   current number, and because getting it wrong is the failure Requirement 2.3 names: 23 of
   `PaperTrading.jsx`'s 43 sizes are 9px and some of them are multi-sentence explanations of
   substantive behaviour.
2. **Is it a chart tick or axis label?** → `chart tick`. → `micro`, and the edit belongs in the
   primitive, not the page.
3. **Is it editable?** Inside an `<input>`, `<select>` or `<textarea>`. → `control text`. →
   `body`.
4. **Does it name something else on the screen?** There is an adjacent value it describes. →
   `label`. → `micro`.
5. **Is it inside a pill, with three words or fewer, carrying a state?** → `chip`. → `micro`.
6. **Is it in a table column with sibling cells?** → `table cell`. → `small`. (Note the order
   against 4: a `<th>` is a label, not a cell, so 4 catches it first. That is deliberate —
   requirements 2.3 puts `thStyle` in the label set.)
7. **Is it a figure the panel exists to report?** → `value`. → tier from
   `design/pageHierarchy.js`: tier-1 → `figure`, panel-level → `title`, secondary → `body`.
8. **Does it name a region containing other elements?** → `heading`. → `page` / `section` /
   `title` by nesting depth.
9. **None of the above.** Do not guess and do not fall back to nearest-step. Record the call
   site and raise it. A call site that answers none of nine questions is evidence the role set
   is short, which is a finding (Requirement 4.4's shape, applied to typography).

**The procedure runs on rendered text, not on style objects, and one call site can be more than
one role.** `PaperTrading.jsx:3367` sets `fontSize: 9` on a flex container holding two chart
legend labels (`Buy fill — triangle`, `Sell fill — diamond`) and one sentence about where fill
markers are placed. Q1 sends the sentence to `body` and Q4 sends the labels to `micro`, so the
container **loses its `fontSize` entirely** and each child takes its own step. A per-style-object
procedure would have had to pick one, and picking either is wrong for the other two children.
This is also why the ratchet in §3 counts occurrences rather than elements: one declaration can
be one violation and three resolutions.

Each resolution is recorded per call site as Requirement 2.4 requires. The record is a comment
on the line, in the form `// fontSize: 9 → --text-body (sentence)`. That form matters for §3:
the audit trail is prose *about* the construct the guard forbids, so comment blanking is not a
nicety.

### 2.5 Some text grows, and the layout yields

Requirement 2.5 already decided this: where removing a below-floor size causes an overflow,
**the layout changes and the size does not**. Two consequences worth stating plainly, because
they are what makes the pass expensive.

**Growth is the normal case, not the exception.** 41 of `PaperTrading.jsx`'s 43 sizes are at or
below 11px and the floor is 10px, so almost every resolution is upward. A pass that produces no
layout changes has not done the work — it has found a way to keep the current sizes while
renaming them.

**Shrinks are legal but rare, and never cross the floor.** A 16px value resolving to `title`
(14px) is a legitimate shrink. A shrink is illegal when it is chosen to avoid a layout change;
the tell is a shrink that appears in the same diff as an overflow that would otherwise have
needed a column removed.

Layout yield mechanisms, in order of preference:

1. **Let it wrap.** Remove `whiteSpace: 'nowrap'`. `PaperTrading.jsx:569`'s chip and `:471`'s
   cell both carry it. This is first because it is the cheapest and the most reversible.
2. **Spend spacing.** Increase the container's padding or gap *from the declared spacing
   scale*. `--spacing-1` through `--spacing-12` exist (`tokens.css:78`–`:80`); a
   `padding: '1px 5px'` (`:568`) is already off-grid and moving it onto the grid is a fix, not
   a cost.
3. **Reduce columns at that breakpoint.** `PaperTrading.jsx` already has the machinery —
   `gridColumns(220)` at `:2713` and the `singleColumn` flag — so this is an existing
   parameter, not a new mechanism.
4. **Truncate with a `title` attribute.** Last resort, and **only for identifiers** — an order
   id, a symbol, a hash. Never for prose: a truncated sentence is a sentence whose substance
   was removed, which Requirement 19.6 forbids and Requirement 7.2 forbids again.

Forbidden as yields: re-adding a px size; adding an eighth step (Requirement 1.1 names this as
the defect, not the escape); `transform: scale()` or `zoom` on a text container, both of which
reintroduce device-pixel pinning through a different door and neither of which the ratchet can
see.

**[JUDGEMENT] flag.** "Did it overflow?" is a visual question and this pass has no build step
(§10). The proxy is three-part and mechanical: the file's ratchet count decreases;
the diff adds no `fontSize`, no `text-[…px]` and no fixed `width`/`height` in px on a text
container; and the page's own test file plus `tests/unit/ds/` stay green. That proxy cannot see
a clipped chip. It can see every way of faking the fix, which is the property worth having.

### 2.6 Worked example — `PaperTrading.jsx`

The distribution (requirements §1.5): `9` ×23, `11` ×10, `10` ×8, `8` ×1, `16` ×1, plus
`missing ? 13 : 18` as one conditional and one `'1.25rem'`. Applying §2.4:

| Cohort | Resolution | Representative call sites |
| --- | --- | --- |
| **9px, ×4** — label style objects | Q4 → `label` → **`micro`** (+1px) | `labelStyle` (`:434`), `thStyle` (`:460`), `tableCaptionStyle` (`:478`), `stackedLabelStyle` (`:503`) |
| **9px, ×1** — the `Explainer` eyebrow | Q4 → `label` → **`micro`** | `:751` (`fontWeight: 900`, uppercase, names the block beneath it) |
| **9px, ×17** — hints, notes, footnotes, explanations | Q1 → `sentence` → **`body`** (+4px, +44%) | `:630`, `:716`, `:765`, `:2481`, `:2556`, `:2647`, `:2810`, `:2813`, `:2833`, `:2836`, `:2885`, `:2972`, `:3227`, `:3283`, `:3407`, `:3434`, `:3488` |
| **9px, ×1** — mixed-role container | declaration removed; children split `micro` / `body` | `:3367` (see the note in §2.4) |
| **11px, ×1** — form control | Q3 → `control text` → **`body`** | `fieldStyle` (`:449`) |
| **11px, ×2** — table values | Q6 → `table cell` → **`small`** (no change) | `tdStyle` (`:469`), `stackedValueStyle` (`:513`) |
| **11px, ×1** — unit suffix | not a role → one step below its figure | `:713` |
| **11px, ×6** — panel state text and page subtitle | Q1 → `sentence` → **`body`** | `:761`, `:811`, `:824`, `:834`, `:2359`, `:3040` |
| **10px, ×3** — status pills | Q5 → `chip` → **`micro`** (no change) | `StatusPill` (`:590`), `:2379`, `:2940` |
| **10px, ×5** — report lists, tooltip body, prose blocks, the error line | Q1 → `sentence` → **`body`** | `:1045`, `:2713`, `:2760`, `:3004`, `:3515` (`role="alert"`) |
| **8px, ×1** — `SimulatedTag` | Q5 → `chip` → **`micro`** (+2px, +25%) | `:558` |
| **16px, ×1** — latency figure | Q7 → `value`, panel-level; tie resolves up → **`section`** | `:2882` |
| **13/18 conditional** | one role, one step — see below | `:707` |
| **`'1.25rem'`** | Q8 → `heading`, page `<h1>` → **`page`** | `:2355` |
| **`fontSize={9}` ×8** *(uncounted)* | Q2 → `chart tick` → **`micro`**, in the primitive | `:3197`, `:3198`, `:3263`, `:3264`, `:3308`, `:3309`, `:3346`, `:3347` |

Four of those rows carry the whole argument.

**`:707`, the conditional, is the case that proves the role keying.**
`fontSize: missing ? 13 : 18` looks like two roles. It is not. Both arms render the same
element — the panel's reported value — and the only difference is *which string*: the figure
when the read succeeded, the not-available marker plus its reason when it did not. The 13 exists
because the marker is longer than a number and 18px overflowed; `wordBreak: 'break-all'` two
lines below (`:709`) is the standing evidence that it overflowed anyway. So the conditional is a
layout workaround wearing a typography mask, which is precisely what Requirement 2.5 names. It
collapses to **one step**, and it collapses to the *value's* step rather than to 13, because
Requirement 19.3 forbids shrinking a reason to reduce clutter. The layout yields by mechanism 1
and 2 above. A numeric mapping would have sent the two arms to `body` and `section` and locked
the workaround in as a token.

**`:2885` versus `:434`, both 9px, three steps apart.** `:434` is `labelStyle` — uppercase
(`:438`), `letterSpacing: 2` (`:437`), `fontWeight: 900`, `display: block`, `marginBottom: 6`. Every property says
"this names the thing under it". `:2885` is the sentence *A negative value means the clocks
disagree and is shown as recorded* — a two-clause explanation of a safety-relevant behaviour
that `production-launch-hardening` required the page to state. Same number, opposite roles,
`micro` and `body`. This pair is why §2.2's decision is not a stylistic preference.

**`:558`, the 8px chip, is the layout-yield case.** `SimulatedTag` is 8px monospace bold caps
with `letterSpacing: 1.5`, `padding: '1px 5px'` (`:568`), `whiteSpace: 'nowrap'` (`:569`) and a
9px `FlaskConical` glyph beside the word (`:572`). At `micro` the word grows 25% and the icon
must follow to 10 to stay optically matched, and 1px of vertical padding stops being enough.
Nothing about that is avoidable by choosing a different step: the tag renders at 19 call sites,
beside every figure and in every panel title (`:693`, `:720`, `:2914`, `:3253`…), and it only
fits today because it is below the floor.

**The eight `fontSize={9}` axis props are outside Requirement 1's scope as written.** They are
a JSX prop, not an object-literal property, so none of Requirement 1.2's three named syntaxes
matches them; they are not in Requirement 1.4's seed count of 43 for this file. Requirement
2.4's claim that 41 of 43 sit at or below 11px is right about the 43 and short of the file's
real below-floor total, which is 49. §3.2 adds the fourth pattern. The *edit*, though, is not on
this page: the four charts pass `fontSize` to recharts' `<XAxis>`/`<YAxis>`, and the migrated
pages' equivalent treatment is `ds/Chart.jsx:722` (`TICK`, `fontSize: 10`) and `:729`
(`AXIS_LABEL`, `fontSize: 10`). That is one shared declaration behind every chart on eleven
pages, and `tokens.css:69`'s own annotation is what it should read. Two consequences: the page
task cannot satisfy Requirement 2.1 for these call sites by itself, and `ds/Chart.jsx` needs a
budget entry the seed list does not give it (§3.5).

---

### 3. The font-size ratchet

Requirement 1.3 asks for a per-file budget "constructed the same way `no-colour-literals.budget.js`
is and sharing its `source-scan.js` helper". That is a specification of the artefact, not a hint,
and this section holds it to the letter. The reason to mirror rather than improve is
requirements §1.2's diagnosis: colour got a ratchet and held at zero on eleven pages; font size
got a convention and 451 absolute sizes. The mechanism that worked is the one to copy.

### 3.1 Shape

Two files, `tests/unit/guards/absolute-font-sizes.budget.js` and
`tests/unit/guards/absolute-font-sizes.test.js`, importing from `./source-scan.js`:

`SRC`, `collect`, `isTestFile`, `list`, `relToSrc`, `stripComments`.

Not `maskStrings` and not `codeOnly`. `source-scan.js`'s own docblock states the rule: use the
string mask "when the thing being detected is a *code construct* and a mention of it inside a
string literal … is not an instance of it", and do not use it "when the thing being detected
lives inside strings by nature (`no-colour-literals`' `"#ef4444"`, `dead-tailwind`'s
`className="…"`)". Both halves apply here at once. `text-[10px]` lives inside a `className`
string; `fontSize: 11` lives in code. Masking strings would blind the guard to the entire
Tailwind syntax — all 30 of `StrategyMarketplace.jsx`'s and all 16 on the landing surface — so
strings stay intact, exactly as the colour guard leaves them.

`SCAN_ROOTS` and `SCANNED_EXTENSIONS` are the colour guard's: `['pages', 'components', 'lib']`
and `['.js', '.jsx', '.ts', '.tsx', '.css']`. `TOKEN_LAYER_FILES` is imported from the colour
budget rather than re-declared — `styles/tokens.css` must be out of scope here for the same
reason it is out of scope there: a file whose job is to declare font sizes cannot be policed by
a rule that forbids them. `isTestFile` keeps the guard from arguing with tests, and
incidentally keeps it from counting its own budget file's header.

### 3.2 Detection — four syntaxes, and what must not match

Requirement 1.2 names three. The tree has four. **[RE-MEASURED]** `PaperTrading.jsx` holds 8
occurrences of a JSX-prop form that none of the three patterns reaches, and it is the only file
in `src/` that does.

```js
/** `fontSize: 11`. The lookbehind is the guard's own idiom — see FUNCTIONAL_COLOUR. */
const FONT_SIZE_NUMERIC = /(?<![\w$])fontSize\s*:\s*(\d+)(?![\d.])/g;

/** `fontSize: '14px'` / `fontSize: "14px"`. The `px` is what excludes rem. */
const FONT_SIZE_QUOTED = /(?<![\w$])fontSize\s*:\s*['"](\d+(?:\.\d+)?)px['"]/g;

/** `text-[10px]`. Tailwind arbitrary value. */
const TAILWIND_ARBITRARY = /(?<![\w-])text-\[(\d+(?:\.\d+)?)px\]/g;

/** `fontSize={9}`. The fourth syntax — recharts axis props, PaperTrading.jsx only. */
const FONT_SIZE_PROP = /(?<![\w$])fontSize\s*=\s*\{\s*(\d+)(?![\d.])\s*\}/g;
```

Four decisions inside those patterns.

**The `(?<![\w$])` lookbehind** is copied from `no-colour-literals.test.js`'s
`FUNCTIONAL_COLOUR` (`/(?<![\w$-])(?:rgba?|hsla?)\s*\(/gi`) and from `source-scan.js`'s
`STRING_SPAN`. It stops `baseFontSize:`, `minFontSize:` and `tickFontSize:` counting as
`fontSize:`. Alternative considered: `\b`. Lost because `\b` does not fire between `e` and `F`
in `baseFontSize` — it looks correct and is not.

**The `(?![\d.])` lookahead** stops `fontSize: 10.5` counting as `10`. It changes no number
today (no fractional px exists in the tree) and it means a fractional value reports as
uncounted rather than as a wrong count — a miss the "accounts for every file" assertion catches,
rather than a silent mis-parse.

**The value is captured, not just counted.** The colour guard counts. This one captures the
digits so the failure message can name them: `PaperTrading.jsx — 51 absolute sizes (9×31,
11×10, 10×8, 8×1, 16×1)`. An implementer clearing a file needs to know which sizes are left, and
§2's procedure takes the number as evidence about the role — so a message that carries the
distribution is a message that shortens the work.

**`lineComments: path.extname(full) !== '.css'`** is carried over verbatim. `//` is not a
comment in CSS, and `text-[10px]` can appear in a `.css` `@apply`. Same option, same reason,
same line.

What must **not** match, and the mechanism that excludes each:

| Must not match | Example | Excluded by |
| --- | --- | --- |
| rem values | `fontSize: '1.25rem'` (`PaperTrading.jsx:2355`) | the literal `px` in `FONT_SIZE_QUOTED` |
| rem arbitrary classes | `text-[0.625rem]` | the literal `px` in `TAILWIND_ARBITRARY` |
| token references | `fontSize: token.text.body`, `fontSize: 'var(--text-body)'` | `FONT_SIZE_NUMERIC` requires a digit immediately after the colon; an identifier is not a digit |
| token classes | `text-micro`, `text-body` | `TAILWIND_ARBITRARY` requires `[`…`px]` |
| commented-out sizes | `// fontSize: 11 — was, now --text-small` | `stripComments` |
| `lineHeight` | `lineHeight: 1.2` (`PaperTrading.jsx:708`) | the `fontSize` anchor — no pattern matches without it |
| `letterSpacing` | `letterSpacing: 2` (`:437`) | same |
| `borderRadius` | `borderRadius: 12` | same |
| icon and stroke sizes | `size={9} strokeWidth={2.5}` (`:572`) | same — and note `size={9}` is why the anchor matters: it is a device-pixel number inside the very chip whose size *is* counted |
| the token layer's own declarations | `--text-micro: 0.625rem` | `TOKEN_LAYER_FILES`, imported from the colour budget |
| tests | `expect(style.fontSize).toBe(11)` | `isTestFile` |

### 3.3 Comment blanking, and why it is not optional here

`source-scan.js`'s `stripComments` blanks comments while preserving length and line breaks, so
reported line numbers still match the file on disk. It is deliberately not a tokeniser, and its
header records the two failures that shaped it: an earlier tokeniser read the apostrophe in JSX
prose (`Don't`) as a string opener, swallowed the rest of `StrategyBuilder.jsx` and lost 33 of
its 148 `C.` references — seeding a budget 22% low; and an earlier `[...source]` spread
collapsed surrogate pairs, so in `pages/Strategies.jsx` (18 emoji) every blank after the first
landed up to 18 characters left of its comment, counting the comment's contents *and* missing
an equal run of real code.

Both failures matter to this guard specifically, and one of them matters more than it did to the
colour guard.

`no-colour-literals` needed comment blanking so that a header documenting a retired
`#FFB74D` did not score a literal. Reasonable, and its self-test `ignores a colour named in a
comment` pins it. This guard needs it for a stronger reason: **§2.4 requires every migrated call
site to carry a comment naming the size it came from.** The migration's audit trail — the thing
Requirement 2.4 asks for so the resolution is "reviewable rather than uniform" — is 451 lines of
prose about the construct the guard forbids. Without blanking, a file cleared correctly and
documented as Requirement 2.4 demands would measure *the same count it started with*, and the
only way to make the guard pass would be to delete the record. A guard that punishes writing
down what you did is a guard that gets the record deleted.

The same provision covers two other false positives already in the tree: `ds/Chart.jsx`'s
docblocks (`:291`, `:294`, `:719`, `:726`) discuss tick sizes in prose, and this design document's
own §2.6 will be quoted into the budget file's header.

Mirrored self-tests, in the colour guard's section-1 style — these are not decoration, since a
silently broken pattern makes the whole guard pass vacuously and the seeded numbers drift into
fiction:

```js
it('ignores a size named in a comment', () => {
  expect(countAbsoluteFontSizes('/* was fontSize: 9 — now --text-body */')).toBe(0);
  expect(countAbsoluteFontSizes('const a = 1; // text-[10px] retired')).toBe(0);
  expect(countAbsoluteFontSizes(['/**', ' * fontSize: 11 and text-[9px]', ' */'].join('\n'))).toBe(0);
});

it('still counts a size on a line that also carries a comment', () => {
  expect(countAbsoluteFontSizes('fontSize: 11, // table body')).toBe(1);
});

it('is not confused by an apostrophe in JSX text', () => {
  expect(countAbsoluteFontSizes("<p>Don't stop</p><b style={{ fontSize: 9 }} />")).toBe(1);
});

it('does not count a relative or tokenised size', () => {
  expect(countAbsoluteFontSizes("fontSize: '1.25rem'")).toBe(0);
  expect(countAbsoluteFontSizes("className='text-[0.625rem] text-micro'")).toBe(0);
  expect(countAbsoluteFontSizes('fontSize: token.text.body')).toBe(0);
});

it('does not count a neighbouring numeric style property', () => {
  expect(countAbsoluteFontSizes('lineHeight: 1.2, letterSpacing: 2, borderRadius: 12')).toBe(0);
  expect(countAbsoluteFontSizes('<FlaskConical size={9} strokeWidth={2.5} />')).toBe(0);
  expect(countAbsoluteFontSizes('const baseFontSize = 11;')).toBe(0);
});
```

### 3.4 The assertions

The colour guard's section 3, one for one. Each catches something the others do not.

| Assertion | Catches |
| --- | --- |
| `is well formed` | a non-integer, a negative, a Windows-slashed key |
| `names only files that still exist` | an entry left behind by a deleted file (Requirement 14 deletes `pages/Landing.jsx` and eight landing components) |
| `accounts for every file that still carries an absolute size` | **the new-violation direction, and the reintroduction direction** — see the deletion rule below |
| `holds every file at or below its budget` | a size added to a file that is supposed to be shrinking |
| `requires progress to be recorded, not banked` | the under-budget direction: sizes removed, budget left high, headroom open for them to creep back |
| `has an entry for every file it claims to track, and no strays` | a budget entry naming a file outside `SCAN_ROOTS` |

The exact assertion in both directions is the point, and `no-colour-literals.test.js`'s header
states why: "Under a `<=` assertion a contributor could clear forty literals from Dashboard.jsx,
leave the budget at 315, and the next contributor could put forty back without CI noticing.
Progress has to be *recorded* to count, not banked."

**Delete the entry when it reaches zero.** Requirement 1.5 inverts the colour budget's rule,
which keeps `pages/Dashboard.jsx: 0` and deletes an entry only when the file is deleted. The
inversion works mechanically, and it is worth spelling out how, because it moves which
assertion holds a cleared file:

- Colour: entry stays at `0`. `holds every file at or below its budget` fails a reintroduction.
- Font size: entry is deleted. `accounts for every file that still carries an absolute size`
  fails a reintroduction, as an *unbudgeted* file.

Both hold the line. Requirement 1.5's argument for the inversion — "a file with no entry is a
file held at zero by default" — is correct, and it buys one real thing: the in-scope/out-of-scope
split that `no-colour-literals` needed at task 27.3 is unnecessary here, because a deleted entry
already means "held at zero" and a surviving entry already means "still has debt". No second
map, no disjointness assertion, no `it.skip`.

It costs one thing, and the failure message is where it is paid. The colour guard's unbudgeted
message ends "If this file is genuinely out of scope for the redesign, add an entry to
`…budget.js` with a note naming the task that clears it." For this budget that advice is wrong:
for a *cleared* file, adding an entry back is the thing being prevented. The message must
distinguish the two cases it now covers:

```
Absolute font sizes in files with no budget entry.

A file with no entry is held at zero — see Requirement 1.5. If this file was
previously cleared, a size has come back: express it as a Type_Scale step from
src/styles/tokens.css. Re-adding a budget entry is not the remedy.

If this file is new to the tree and genuinely carries debt, seed an entry with the
change that clears it.
```

**Where the seed comes from.** Requirements §1.1's per-file table and §1.4's enumeration.
Measured, not estimated — the same standing that `no-colour-literals.budget.js` claims for its
own 38 files and 1467 literals.

### 3.5 The seed list is short against the scope Requirement 1.3 mandates **[RE-MEASURED]**

Requirement 1.4 seeds 18 entries: the 14 files under `src/pages/` that carry sizes, plus
`ScreenshotsSection` 12, `Hero` 2, `DownloadSection` 1, `HowItWorks` 1 on the landing surface.
Requirement 1.3 says the guard is constructed the same way as `no-colour-literals` and shares
its helper. That guard scans `pages`, `components` and `lib`.

Those two clauses cannot both be satisfied by the seed as written. Running §3.2's four patterns
over the colour guard's three roots, with comments stripped, reproduces every one of
requirements §1.1's page numbers exactly — `Profile` 79, `StrategyDetail` 61,
`ExchangeManager` 52, `Landing` 41, `Billing` 39, `StrategyMarketplace` 30, `AuthPage` 29,
`Wizard` 24, `RiskSettings` 16, `LegalPage` 15, `TwoFA` 12, `SecurityLogs` 7,
`UpdatePasswordPage` 3, and the four landing entries 12/2/1/1 — which is the non-vacuity check
this design owes its own patterns. It also finds two things the seed does not cover.

**`PaperTrading.jsx` measures 51, not 43.** The difference is §2.6's eight `fontSize={9}` axis
props. Its seed entry is 8 short, and the correction belongs in the same commit as the fourth
pattern.

**Eleven files under `components/` carry 155 sizes with no seed entry:**

| File | Count | Note |
| --- | --- | --- |
| `components/SupportCenter.jsx` | 69 | trader-reachable; one of the two page-local `C` objects the redesign's §1.1 recorded as G4 |
| `components/DeploymentConsole.jsx` | 19 | |
| `components/NotificationCenter.jsx` | 17 | the other G4 file |
| `components/ResearchConsole.jsx` | 17 | |
| `components/common/primitives.jsx` | 12 | |
| `components/FirstTradeWizard.jsx` | 7 | first-run surface — the one a new retail account sees first |
| `components/DeployPreflightPanel.jsx` | 5 | |
| `components/waitlist/WaitlistForm.jsx` | 5 | `text-[Npx]` |
| `components/ds/Chart.jsx` | 2 | `:722` `TICK`, `:729` `AXIS_LABEL` — the chart-tick treatment for all eleven migrated pages |
| `components/download/DownloadPage.jsx` | 1 | `text-[Npx]` |
| `components/admin/AdminDashboard.jsx` | 1 | `text-[Npx]` |

`components/ds/SectionHeader.jsx` is deliberately absent and is worth one line: it measures **2
before comment stripping and 0 after**, because `:17`'s docblock records the values `PanelTitle`
used to hardcode (`fontSize: 13`, `fontSize: 10`). It gets no entry. It is also §3.3's argument
standing in the tree already — a file that documented its own migration correctly would have been
the guard's first false positive.

Left unseeded, `accounts for every file that still carries an absolute size` fails on the guard's
first commit — which Requirement 22.4 requires to be green ("seeded at today's counts and
therefore green on the first commit").

**Decision.** Keep the colour guard's three roots, extend the seed by these eleven entries, and
correct `PaperTrading.jsx` to 51.

**Alternative considered: narrow `SCAN_ROOTS` to `pages` and `components/landing` so the scope
matches Requirement 1.4's list.** Rejected. It would leave `ds/Chart.jsx:722` permanently
invisible — one declaration that sets the axis tick size on every chart on all eleven migrated
pages — and that is the same asymmetry requirements §1.2 diagnoses as the cause of this whole
requirement: one axis enforced where it is rendered, the other left to whoever wrote the file.
It would also leave `FirstTradeWizard.jsx` and `SupportCenter.jsx` outside a guard whose stated
purpose (Requirement 1.3) is that a reintroduced size fails without anyone having to notice.

The extension changes no committed number's meaning and fails nothing: those 155 sizes are in
the tree today either way. What it changes is a seeded list in an approved requirements
document, so it is recorded here as a correction to Requirement 1.4 rather than applied
silently, and it needs the requester's acknowledgement before the guard lands. Nothing in this
spec is scheduled to lower most of the eleven, which is fine — a ratchet has a resting position,
and `no-colour-literals.budget.js`'s header says so about `pages/ExchangeManager.jsx: 128`.

### 3.6 What the ratchet cannot see

Four blind spots. Each is named with what covers it, because a guard whose limits are
undocumented gets trusted past them. The second is the one that matters.

**1. Off-scale relative sizes.** `fontSize: '1.25rem'` (`PaperTrading.jsx:2355`) is 20px, is not
one of the seven steps, and carries no `px` — so no pattern matches it. It satisfies
Requirement 2.1 (it scales with the reader) and violates Requirement 1.1 (a size declared
outside the token layer). **[JUDGEMENT]:** counting px only is the mirror Requirement 1.3 asks
for, and widening to "any unit not resolving to a declared step" means the guard reading and
parsing `tokens.css` — a materially different guard. The proxy is narrower and honest: the one
off-scale rem site in the tree is named in the budget file's header and cleared by the
`PaperTrading` task, and `no-local-tokens.test.js` is the guard that owns "a page declared its
own value". If a second off-scale rem appears, this blind spot is the reason and it is written
down here.

**2. Tailwind's built-in size classes.** **[RE-MEASURED]** `src/` carries **434** occurrences of
`text-xs` / `text-sm` / `text-base` / `text-lg` / `text-xl` / `text-2xl` and friends, outside
tests — including **41 in `StrategyMarketplace.jsx`** and 43 in `ScreenshotsSection.jsx`. They
resolve: `tokens.css:14`'s `@theme static` block declares the seven steps but does not reset the
namespace with `--text-*: initial`, so Tailwind v4's defaults are extended rather than
replaced, and `dist/assets/index-CbEZjRwz.css` emits `--text-xs:` and `.text-xs` alongside
`--text-micro:` and `.text-micro`. (That artefact is stale — it emits no `.text-[10px]` despite
30 in source — so it evidences the namespace, not current output.)

This is the most consequential blind spot, and it is a gap in Requirement 1 rather than in the
guard. Requirement 1 conflates two properties:

- *does this size scale with the reader?* — Requirements 2.1, 2.2, §1.3. The px ratchet measures
  this exactly.
- *is this size one of the seven steps?* — Requirements 1.1, 1.2. The px ratchet does not
  measure this at all.

`text-xs` is 0.75rem: it scales, it clears the floor, and it is not a step. It passes the
ratchet and fails Requirement 1.2. Requirement 10.4's "the 30 `text-[Npx]` classes SHALL move
onto the `Type_Scale`" therefore addresses 30 of Marketplace's 71 off-scale size declarations.
Recorded as a finding; the remedy is either a `--text-*: initial` reset in `tokens.css` (a
token-layer change, which Requirement 22.3 makes its own commit and which `dead-tailwind.test.js`
would immediately exercise) or a second budget on the built-in classes. Both are bigger than
this spec's remit, and neither should be chosen without the requester.

**3. Files outside the three roots.** **[RE-MEASURED]** `src/App.jsx` carries **6** absolute
sizes and sits in none of `pages`, `components` or `lib`, so neither the colour guard nor this one
reaches it. Requirement 1.2 scopes its rule to `src/pages/` and `src/components/landing/`, so
`App.jsx` is outside the requirement as well as outside the guard. Recorded rather than fixed:
widening `SCAN_ROOTS` to `src/` root files is a change to the shared
`no-colour-literals` scan decision (its header reasons explicitly about which roots it holds and
why `index.css` is excluded), and Requirement 17.5 puts that in the other spec's territory.

**4. Computed sizes.** `style={{ fontSize: someNumber }}` is invisible to any source scan. There
are none in the tree today and no proxy for the future; recorded so the absence is a measurement
rather than an assumption.

---

### 4. Change ordering

Requirement 22.4 fixes the top-level sequence and Requirement 4.5 fixes the order of the eleven
unmigrated pages. Both are adopted. This section argues the first one, decides the four things
neither clause places — the badge, Marketplace's split, the copy and density work, the landing
investigation — and names what must land alone.

### 4.1 The ratchet lands before any page is cleared

**Decision.** The guard pair from §3 is commit one. It touches no source file.

The argument is about the interval, not the endpoint. This pass clears fourteen files for
typography and migrates eleven more for convention. Under Requirement 22.1 that is at least
twenty-five commits, shippable one at a time, over a period in which every commit touches JSX
in a file that could grow a `fontSize`. Without the ratchet the only thing standing between
commit 3 and commit 9 is a reviewer counting by hand — which is exactly the state requirements
§1.2 measures the consequence of: colour had the ratchet and reached zero on eleven pages, font
size had the convention and reached 451.

Seeded at today's counts, the first commit asserts the tree as it is and is green. That makes it
nearly free, which is the second half of the argument: there is no cost to pay for the
protection.

**Alternative considered: land the ratchet last, seeded at zero, as the proof the pass
finished.** Rejected on two grounds.

1. It provides no protection during the only period when protection matters. A guard that is
   green because the work is done has never prevented anything.
2. A guard first written against a clean tree is a guard whose patterns were never exercised.
   `no-colour-literals`' `#features` false positive — `href="#features"` counted as a colour
   because `#fea` is three valid hex digits — was found because the guard ran against a dirty
   tree on the day it landed, and `components/landing/Footer.jsx` scored 1 while containing no
   colour. §3.2's four patterns have the same exposure: `size={9}`, `letterSpacing: 2` and
   `borderRadius: 12` are all device-pixel numbers sitting on lines beside sizes that *are*
   counted. Running the patterns against 451 real call sites on day one is how they get
   validated. Running them against zero proves nothing.

### 4.2 The badge goes first, immediately after the ratchet

`ds/TradingEnvironmentBadge.jsx:230` applies `font-mono font-bold uppercase tracking-wide` to
every variant, over the `text-micro` that `VARIANT_CLASSES` sets on all three (`:179`, `:180`,
`:184`). One className, four stacked treatments, and `ENVIRONMENT UNCONFIRMED` reaching a retail
reader as 23 characters of 10px monospace bold caps with added letter spacing.

**Decision.** First, before any page. Cheap and high leverage beats largest-blast-radius-last
here, and the reason is bisect rather than risk.

**[RE-MEASURED] The blast radius is larger than requirements §1.8 states, which strengthens the
case rather than weakening it.** §1.8 names six pages and four primitives. Measured references:
`Dashboard` 4, `LiveTrading` 9, `PaperTrading` 5, `Portfolio` 3, `Strategies` 2, `StrategyDetail`
1, `StrategyMarketplace` 2, `TradeHistory` 4 — eight pages, not six; and `ds/Alert` 3,
`ds/ConfirmDialog` 3, `ds/PageHeader` 4, `ds/Panel` 2, `ds/SectionHeader` 1, `ds/StrategyStatus`
4 — six primitives, not four. `SignalTrace.jsx`, which §1.8 names, references it nowhere. Via
`ds/Panel` and `ds/PageHeader` the treatment reaches effectively every page in
`In_Scope_Pages`.

Why first:

- **The edit is one line plus three strings.** The treatment is `:230`. The wording lives at
  three declaration sites — `Panel.jsx:126`, `TradingEnvironmentBadge.jsx:113`,
  `StrategyStatus.jsx:201` — which Requirement 9.3 already enumerates and requires to move
  together.
- **The verification exists today.** `tests/unit/ds/` is 26 files and 533 declarations
  (Requirement 18.2), and Requirement 9.1's treatment and Requirement 9.2's distinctions are
  both assertions about a primitive's rendered output. Nothing about that verification improves
  by waiting.
- **Going last would not shrink the blast radius by a single page.** It would only place the
  badge diff *after* eleven page diffs, at which point a badge regression and a page regression
  are indistinguishable during bisect. Going first means any later page commit that looks wrong
  bisects to that page.
- **Going last also invalidates work in flight.** Every page migrated before the badge changed
  would have been read, reviewed and screenshot against the old treatment. The redesign's §14.3
  makes precisely this argument for putting M1–M2 before any page restructure: land the
  app-wide, page-agnostic change first so the app "never looks like three redesigned pages and
  seven old ones". This is the same shape of change and takes the same answer.

**Alternative considered: last, on the largest-blast-radius-last rule.** That rule is sound when
the blast radius is *unverified*. Here it is covered by a complete suite that exists now, so the
rule's premise does not hold. Rejected.

**Precondition, stated so it is checkable rather than discovered.** If
`tests/unit/ds/TradingEnvironmentBadge.test.jsx` asserts the four treatment axes as *class
strings* rather than as rendered behaviour, the edit fails the suite and the assertion must be
corrected first. That correction is its own commit under either ordering, so it is a
precondition, not a reason to reorder. It is the first thing the task reads.

**What the badge commit may not do.** Reword toward a resolved value. Requirement 9.2 is
absolute: `ENVIRONMENT UNCONFIRMED` stays distinguishable from `SIMULATED · SERVER LABEL
UNAVAILABLE`, because `TradingEnvironmentBadge.jsx:62` records that the difference is
information, and neither may be resolved, because `semantic.js:236` records that defaulting to
LIVE is alarmist and defaulting to PAPER is dangerous. The commit changes the *treatment* — four
axes at once on a 23-character string — and may shorten wording only where the shorter wording
draws every distinction the longer one drew.

### 4.3 The order

| # | Commit | Touches | Budget lowered | Alone? |
| --- | --- | --- | --- | --- |
| 1 | The font-size ratchet, seeded (§3) | `tests/unit/guards/absolute-font-sizes.{budget,test}.js` | — (seeds) | **Yes** |
| 2 | Badge treatment + the three label declarations (Req 9) | `ds/TradingEnvironmentBadge.jsx`, `ds/Panel.jsx`, `ds/StrategyStatus.jsx` | — | **Yes** |
| 3 | `ds/Chart.jsx` tick and axis-label sizes (§2.6) | `ds/Chart.jsx` | font-size | **Yes** |
| 4 | Accessibility scope extension (Req 6.1, 6.2) | `a11y-ratchet.test.js`, `eslint-rules/a11y-ratchet.js` | a11y waivers seeded | **Yes** |
| 5–6 | Marketplace visual generation + a11y findings (Req 10, 12) | `StrategyMarketplace.jsx`, `a11y-ratchet.js` | font-size, a11y waiver **deleted** | 12 with 10 |
| 7 | Marketplace read path (Req 11) | `StrategyMarketplace.jsx` | — | **Yes** |
| 8–18 | The eleven unmigrated pages, in Requirement 4.5's order | one page each | font-size + colour, per page | **Yes, each** |
| 19–21 | `PaperTrading`, `StrategyBuilder`, `StrategyDetail` (Req 4.6) | one page each | font-size, per page | **Yes, each** |
| 22–23 | Dead code (Req 14) | 8 landing components, `pages/Landing.jsx` | entries **deleted** with the files | **Yes** (Req 14.3) |
| 24+ | Landing surface token migration (Req 15), gated on Req 13 | 14 landing files | font-size, colour | per file |
| last | Standing prose and fresh-account hierarchy (Req 7, 8) | `LiveTrading`, `SignalTrace`, `Dashboard`, + Req 8.4's four | prose budget seeded, then lowered | **Yes, each** |

Four placements in that table are this design's, not requirements.md's.

**`ds/Chart.jsx` at 3, before Marketplace and before the pages.** Same argument as the badge, one
step weaker: it is one declaration behind every chart on eleven pages, and `tests/unit/ds/` is
its verification. Placing it after the page work would mean `PaperTrading`'s eight axis props
(§2.6) get resolved twice — once on the page, once when the primitive moves.

**The a11y scope extension at 4, before the pages it newly lints.** Requirement 6.1 turns
`jsx-a11y` to error on every file under `src/pages/` plus fourteen landing sections at once.
Landing that inside a page commit puts the other 24 files' findings in that page's PR.
Requirement 6.2 already requires each new page's count to be recorded with the change that will
clear it; commit 4 is that recording, and every page commit after it deletes its own entry.

**Marketplace as 10+12 together, 11 separate.** Requirements 10 and 12 are one edit wearing two
numbers: `:664`'s `onClick` on a `div` is one of the four a11y findings, and Requirement 12.4
names `ds/DataTable`'s row activation as the precedent. You cannot rebuild the card onto a
`ds/*` primitive and leave the div handler behind, so splitting them means writing the same
finding twice and leaving the waiver at a number that no longer describes the file.
Requirement 11 is a different axis — the read path, `usePanelState`, `errorCopy.js`, and the
`42703` case from §1.10 — and it is the only Marketplace change with a behavioural surface.
Requirement 16 makes behaviour-adjacent diffs the ones most worth isolating.
**Alternative considered: all three at once**, since they touch one 1,210-line file. Rejected
under Requirement 22.1: a diff that large is not revertible in any useful sense.

**Requirements 7 and 8 last. Requirement 22.4 places neither.** They are the pass's only
requirements whose target is explicitly a judgement — Requirement 7's 400-character budget is
"chosen, not derived" and Requirement 8's one-next-action target is measured against a proxy,
not derived. Two reasons for last:

- They are the changes most likely to be *judged* a regression. Requirement 7.2 moves prose
  behind a disclosure and Requirement 19.6 requires it to remain reachable and unchanged in
  substance; Requirement 16.5 forbids the same move reaching a confirmation. That is a
  negotiation with the requester, and it goes better against a tree where nothing else is in
  flight and the mechanical value has already shipped.
- A revert of a copy commit must not be able to take a migration with it. Last means it cannot.

Requirement 9 is the deliberate exception and is split from its neighbours: it is nominally copy
work, but the edit is a treatment (one className) plus three strings with a complete suite
already covering them, and it has no placement question to negotiate.

### 4.4 The landing investigation runs in parallel from day one

**Decision.** Requirement 13's reproduction starts alongside commit 1 and produces no diff.
Requirement 15's token migration and any content fix stay where Requirement 22.4 puts them:
after everything else, gated on the recording.

Why parallel rather than sequenced: it is the only **[UNVERIFIED]** item in the document, it has
the longest lead time (Requirement 13.1 wants a URL, a viewport, a browser and a verdict against
the deployed bundle, a local build, or both), and Requirement 13.5 makes "no symptom
reproduces" a legitimate result. An investigation that emits no commit has no ordering
constraint. Sequencing it last treats it as though it were a change, and risks the pass ending
with the one thing the requester actually asked about unanswered.

Requirement 13.4's four already-checked items stay checked and are not re-covered: the legacy
aliases resolve (`tokens.css:150`, `:152`, `:162`), `ScreenshotComingSoon.jsx`'s placeholder is
unreachable, and `DownloadSection`'s installer links were already withdrawn to `ds/Panel`'s
`unavailable` state. And requirements §1.11 records the candidate explanation the investigation
must rule out first: the earlier fixes may have landed in `src/pages/Landing.jsx`, which
`App.jsx:44`/`:590` route nowhere — in which case "still broken" is the expected outcome of a
correct fix applied to a dead file.

### 4.5 What must land alone, and why

| Change | Why alone |
| --- | --- |
| Any `tokens.css` or `design/tokens.js` edit | Requirement 22.3. `tokens.generated.test.js` makes a token edit global; bundled with a page edit, a bisect lands on the page and the cause is app-wide. |
| The ratchet seed (commit 1) | It touches no source. Bundled with a page edit, the seed becomes unverifiable — a reader cannot tell whether a number is today's count or the post-edit count, and the whole value of "seeded from the tree" is that it is checkable by re-running the scan. |
| The badge (commit 2) | One file, eight pages and six primitives (§4.2). Riding with a page makes any regression on the other seven pages bisect to the wrong commit. |
| `ds/Chart.jsx` (commit 3) | Same, for every chart on eleven pages. |
| The a11y scope extension (commit 4) | It changes lint severity for 25+ files simultaneously. |
| The Marketplace waiver deletion (commit 5–6) | `eslint-rules/a11y-ratchet.js` is a shared list and Requirement 12.2 requires deletion rather than lowering. Mixed into a large page diff, a reader cannot tell whether the four findings were fixed or the entry was dropped — and Requirement 12.3 makes the empty-list case a guard behaviour that must be exercised on its own. |
| Requirement 14's deletions | Requirement 14.3 says so: confirm by search that nothing imports the file, and keep it separate from any behavioural change. |
| Each page | Requirement 22.1. |

### 4.6 What the ordering is doing for the P0 constraints

The per-commit discipline is not bookkeeping; it is what makes four of the five P0s checkable at
all in a pass with no build step.

- **No new `ds/*` primitive, no new `usePanelState` state, no new `design/` module**
  (Requirement 17.1–17.3). Checkable per commit precisely because each commit touches one page:
  a new file under `components/ds/` or `src/design/` in a single-page diff is visible at a
  glance, and `no-local-tokens.test.js` (Requirement 17.4) catches the page-local variant. In a
  combined diff it is a needle.
- **No functional change** (Requirement 16). Requirement 16.7 says the diff of every change must
  be confined to presentation, and names `src/api/`, `src/websocketClient.js` and `backend_app/`
  as out of scope regardless of content. That is a per-commit path check, and
  `api-paths.budget.js` must not change at all — a change there means 16.2 was violated.
- **The duplicate frontend is never touched** (Requirement 21). Requirement 21.3 is a per-commit
  `git diff` path check for `aerora_quant_platform/frontend_app/algo22-terminal`. §2's mapping is
  exactly the kind of work that produces a codemod, and a codemod is exactly what lands in both
  trees; the check belongs on every commit, not on the pass.
- **No accessibility regression** (Requirement 20). Commit 4 raises enforcement before the pages
  it covers are edited, so every later page commit is linted at `error` when it lands rather than
  retrospectively. `src/components/ds/**` stays at exactly zero findings (Requirement 20.4),
  which is a standing assertion the badge and chart commits are the first two to test.

The fifth, **zero versus unavailable** (Requirement 19), is not protected by ordering and needs
its own section — §6. §2.6's `:707` conditional is already the case that shows why: the
obvious typography fix there is to keep 13px for the not-available arm, and that is a
Requirement 19.3 violation wearing a font size.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a
system — essentially, a formal statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable correctness guarantees.*

§5 is the argument: per class of change, which file is that class's safety net, what it actually
proves, and where nothing covers a class. The thirteen properties after §5.8 are that argument
consolidated — each one is an invariant §5 already states, restated over all inputs and paired
with the file that holds it. None is new, and the one property this spec inherits rather than
states is Requirement 19.7's Property P5, that a failed read renders no figure and no zero; §6.4
carries it.

Runner: `vitest` + `fast-check`, as `tests/unit/design/errorCopy.property.test.js` and
`tests/unit/ds/columnAlignment.property.test.jsx` already use it. One of the thirteen has no
instrument at all and one has none for half of itself, and §5.1 is why: no test in this repository
can observe a rendered font size or a rendered geometry. The second property below, and the
overflow half of the seventh, stand on §2.5's three-part proxy and are **[JUDGEMENT]** flagged
there, in §5.2 and in §5.8.

### 5. Preservation per change class

Requirement 18 says every existing guard and design suite stays green. That is necessary and it
is not the same thing as being protected: a suite can be green throughout a change it cannot
see. This section names, per class of change, the specific file that is that class's safety net
and the specific thing it proves — and where no file covers a class, says so and specifies the
new one.

### 5.1 Decision D2 — a class with no net gets its test written before the change, not after

**Decision.** Each of the six classes below is preceded in the commit order by whatever test it
is missing. Requirement 22's ordering is adjusted only by insertion, never by reordering.

**Alternative considered: write the tests alongside each change**, in the same commit. Rejected
for the reason §4.1 already gave about the ratchet and §3.2's patterns: a test first written
against the tree it is meant to protect, in the same diff as the change, has never been observed
failing. `no-placeholders.test.js`'s own header names this as the thing its
`finds the placeholder that is really in the tree` fixture exists to prevent, and
`ds/EmptyState.test.jsx`'s `names the panel in the failure, so the call site is findable`
is the same instinct applied to a primitive. A test that cannot be observed failing is not a
guard; `downloadSurface.test.jsx`'s header states it in those words.

**One fact governs the whole section and is stated once.** jsdom applies no Tailwind CSS.
`ds/DataTable.test.jsx`'s header says so directly and explains that it reads `data-align`,
`data-column-key`, `data-row-id` and `data-priority` rather than computed styles for exactly
that reason. So **no test in this repository can observe a rendered font size**, and none of the
919 declarations across `tests/unit/{guards,design,ds}/` (186 + 200 + 533, requirements §1.7)
asserts one. The only font-size
assertion anywhere in the suite is `tests/unit/portfolio-rendering.test.jsx:116` —
`it('declares no inline style, font or fontSize')`, which reads `Portfolio.jsx`'s *source text*
and asserts `expect(CODE).not.toMatch(/fontSize\s*:/)` for that one page. That is a source scan
living in a page test, and it is the nearest thing in the tree to §3's guard. It is also the
proof that §3's guard is the only available shape for this axis: the property is not observable
at render time, so it has to be asserted over source.

### 5.2 Class 1 — typography resolution (Requirements 1, 2, 3)

| Question | The file that answers it | What it proves |
| --- | --- | --- |
| Did a px size survive, or come back? | **`tests/unit/guards/absolute-font-sizes.{budget,test}.js` — NEW (§3)** | Nothing today. This is the class's only net and it does not exist. |
| Did a token class get misspelled into nothing? | `tests/unit/guards/dead-tailwind.test.js` | That every `className` token `src/` asks for resolves in the built CSS. It is the guard for `text-micro` typed as `text-Micro` and for §2's mapping producing `text-small` where no such class is emitted. **Needs a build** (§10). |
| Did the token layer drift from its mirror? | `tests/unit/guards/tokens.generated.test.js` | That `src/design/tokens.js` is not stale relative to `src/styles/tokens.css`. `package.json`'s `prebuild` runs `scripts/gen-tokens.mjs --check`, so this is enforced twice and does not need a build to check once. |
| Did a page start declaring its own scale? | `tests/unit/guards/no-local-tokens.test.js` | That no file outside the token layer declares `const C` / `COLORS` / `THEME`. Structural, matched on the declaration name, so a reviewer does not have to judge whether a new local object is "really" a palette. It is Requirement 17.4's net and it covers the *shape* of a second design system, not its typography. |
| Did a colour move while a size did? | `tests/unit/guards/no-colour-literals.{budget,test}.js` | Per-file counts asserted exactly in both directions. This is the file that catches a colour literal, and §3 is built to mirror it line for line. |

**What is not covered, and what covers it instead.** The overflow question — Requirement 2.5's
"the layout changes and the size does not" — has no test and cannot have one here, because it is
a rendered-geometry question and jsdom has no geometry. §2.5's three-part proxy stands: the
file's ratchet count decreases, the diff adds no `fontSize` / `text-[…px]` / fixed px
`width`/`height` on a text container, and the page's own suite plus `tests/unit/ds/` stay green.
**[JUDGEMENT]** flagged there, flagged again here. The proxy cannot see a clipped chip; it can
see every way of faking the fix.

### 5.3 Class 2 — page convention adoption (Requirements 4, 5, 6)

| Question | The file | What it proves |
| --- | --- | --- |
| Did the page keep its own `loading`/`error` pair? | `tests/unit/hooks/usePanelState.test.js` | The hook's eight states and their resolution order. It proves the hook behaves; it does not prove a page uses it. |
| Does an absent figure still carry its reason? | `tests/unit/design/pageFields.test.js` | `describe('pageFields: Requirement 19.3 — every absence carries a reason')` — every `UNAVAILABLE_FIELDS` entry has a reason longer than 20 characters, every entry whose `absence` is not `never` has one, and every `absence: never` entry has `reason === null` so none is dead copy. **This is the file that fails when a new page's field is added without a reason, and §6 is built on it.** |
| Does a zero still read as a zero? | `tests/unit/design/reported.test.js` | The accessor's totality and "a zero is a reading and is never treated as an absence" — its header's own words. |
| Does the primitive invent a figure? | `tests/unit/ds/Metric.test.jsx` | "It never renders `0` for a value it was not given." |
| Did `err.message` reach the screen? | `tests/unit/design/errorCopy.property.test.js` | `it('returns a headline from an authored table, never one derived from the error')`, over generated errors whose `message` carries V8 stack frames and Python tracebacks, `statusText` names an exception class and `url` is an internal `/api/` path. A future `headline: err.message` fails **even if the message happens to look harmless**. Its sibling `errorCopy.test.js` drives `enforceNoLeak` directly for the throw-in-dev and degrade-in-production branches. |
| Did a keyboard path disappear? | `tests/unit/guards/a11y-ratchet.test.js` | `it('holds every unwaived in-scope page at zero findings')` and, for the primitives, `reports zero accessibility findings across every primitive` plus `holds them at error, not warn`. It runs ESLint in-process over `src/pages/**` and `src/components/ds/**`, so the counts are measured rather than declared. |
| Did the request shape change? | `tests/unit/guards/api-paths.{budget,test}.js` | Requirement 18.3 makes this one absolute: the budget **must not change at all**. A diff to it means Requirement 16.2 was violated. |

**What is not covered.** Requirement 6.5's kill-switch verification. `RiskSettings.jsx` is
outside `IN_SCOPE_PAGES`, so it is not linted at all today, and no test file renders it —
`tests/unit/dashboard-kill-switch.test.jsx` covers the *Dashboard's* halt confirmation, not
Risk Settings' toggles. Requirement 6.5 asks for a test rather than a manual check and it is
right to: a kill switch reachable only by mouse is the one control where the gap is not an
inconvenience.

> **NEW: `tests/unit/pages/riskSettingsKillSwitch.test.jsx`.** Renders `RiskSettings.jsx`,
> reaches every kill-switch control by `getByRole` with an accessible name, operates it by
> `keyDown` rather than `click`, and asserts the request the control issues is the same one the
> pointer path issues. Written **before** the page's Requirement 4 migration, so it is observed
> passing against the unmigrated page and again after. Requirement 6.5's wording — "before and
> after their migration" — asks for exactly that.

### 5.4 Class 3 — primitive edits (Requirements 9, and §4.3's commit 3)

This is the class with the largest gap between assumed and actual coverage.

**There is no `tests/unit/ds/TradingEnvironmentBadge.test.jsx`.** `tests/unit/ds/` holds 26
files and none of them is the badge's. **[RE-MEASURED]** — §4.2 names that file as the badge
commit's precondition, and the precondition as written cannot be read because the file does not
exist. What covers the badge today is indirect and partial:

| File | What it actually proves about the badge |
| --- | --- |
| `tests/unit/dashboard-kill-switch.test.jsx:176` | `ledgerAxes()` reads the dialog header's badge through `data-environment`, `data-environment-border`, `data-environment-icon`, the label span (`span.sr-only + span`) and `title`. **Data attributes and text, never class strings.** So the four *treatment* axes — `font-mono font-bold uppercase tracking-wide` at `:230` — are asserted nowhere, and Requirement 9.1's edit is unblocked. The *label* is asserted, so a rewording breaks this file and must break it. |
| `tests/unit/liveTrading.test.jsx:287` | `it('renders ENVIRONMENT UNCONFIRMED when no position carries an environment')` — `data-environment` is `UNCONFIRMED`, the text contains `ENVIRONMENT UNCONFIRMED`, and it contains neither `PAPER` nor a `LIVE` borrowed from the page strip. This is Requirement 9.2's net for one of its two distinctions. |
| `tests/unit/dashboard_phase2a_ui.test.jsx:287`, `dashboard_phase2d_polish.test.jsx:255` | The `PAPER TRADING` and `LIVE` labels reach the screen. Wording nets, not treatment nets. |

`SIMULATED · SERVER LABEL UNAVAILABLE` — the other half of Requirement 9.2's distinction — is
asserted by **no test**. It is declared at `ds/TradingEnvironmentBadge.jsx:108` and its reason is
recorded at `:61`–`:64`, and nothing renders it in the suite. That is the single most
consequential hole in this class: Requirement 9.2 forbids collapsing the two, and only one of
them would fail if it were collapsed.

> **NEW: `tests/unit/ds/TradingEnvironmentBadge.test.jsx`.** Four blocks. (1) Both null arms
> render, and render *differently*: `environment == null && isSimulated` →
> `SIMULATED · SERVER LABEL UNAVAILABLE`, `environment == null && !isSimulated` →
> `ENVIRONMENT UNCONFIRMED`, with `data-environment` distinct on each, asserted as a pair so a
> collapse fails rather than half-fails. (2) Neither arm resolves to `LIVE` or `PAPER`, which is
> `semantic.js:236`'s rule expressed as an assertion. (3) Every variant carries its
> screen-reader prefix and its `title` long form, so Requirement 20.1's accessible name survives
> a treatment change. (4) The treatment axes are read as classes on the label element and the
> file records that this is the **one** assertion in the suite intended to be edited by
> Requirement 9.1 — seeded at today's four so the edit is observed, not assumed.

**`ds/Chart.jsx` (commit 3) has no size assertion either.** `tests/unit/ds/Chart.test.jsx`
contains no `fontSize`, no `TICK` and no `AXIS_LABEL_STYLE` reference; its strictest assertion
in that region is `it('rejects a kind it does not understand')`. The two declarations are
`Chart.jsx:722` (`TICK`, `fontSize: 10`) and `:729` (`AXIS_LABEL_STYLE`, `fontSize: 10` — note
the name; §3.5 calls it `AXIS_LABEL` and the file does not).

> **NEW, in the existing file:** one `it` asserting `TICK.fontSize` and
> `AXIS_LABEL_STYLE.fontSize` are read from `token.text.micro` rather than written as `10`. An
> assertion over the exported constants, not over rendered SVG, because recharts is stubbed in
> every page test that touches a chart and the value's correctness is a property of the
> declaration.

### 5.5 Class 4 — copy and density (Requirements 7, 8)

| Question | The file | What it proves |
| --- | --- | --- |
| Is the caveat still on the page? | `tests/unit/liveTrading.test.jsx:1041` | `document.querySelectorAll('[data-selector-caveat="in-process-registry"]')` has length exactly 1. `LiveTrading.jsx:2712` and `:2720` carry `data-selector-caveat="in-process-registry"` and `"selection-scope"` on the two caveat `<p>` elements. **This is the net for Requirement 7.3, and it constrains the remedy**: the attribute must move onto the disclosure rather than be dropped, or the assertion has to be rewritten — and rewriting it to `>= 0` is the exact failure Requirement 7.5 forbids. |
| Is a disclosure keyboard-reachable? | `tests/unit/ds/Tooltip.test.jsx` | `opens on focus and closes on blur`; `stays open when the pointer leaves while focus is still on the trigger`; `closes on Escape and reopens on the next focus`; `adds a tab stop to a span, which cannot otherwise take focus`; `leaves a natively focusable trigger alone, so no control gains a second stop`. This is Requirement 20.2's net, already written. |
| Does an expandable note publish its state? | `tests/unit/ds/Accordion.test.jsx` | `defaults to closed, and defaultOpen is the only way out` — `aria-expanded` is `'false'` then `'true'`, read off `getByRole('button', { name: /Advanced settings/ })`. Requirement 20.2's programmatic expanded state, already covered. |
| Does an empty panel name a next action? | `tests/unit/ds/EmptyState.test.jsx` | `renders what is missing, why it matters and the next action`; `throws in development for each missing field`; `rejects copy that is present but blank`; the `no-data` / `no-match` split with `requires a clear-filters action for no-match`. Requirement 8.3's distinction is enforced *at the primitive*, in dev, by a throw. |
| Did the prose count actually fall? | **`tests/unit/guards/standing-prose.{budget,test}.js` — NEW** | Nothing today. Requirement 7.4 asks for a seeded per-page count and 7.5 requires it to count *rendered* text rather than source constants, so it cannot be a `source-scan.js` guard like the other five. It renders each of the eight pages against a fixture and sums the text of the nodes above the first data element. |
| How many empty panels does a fresh account see? | **`tests/unit/pages/freshAccount.test.jsx` — NEW** | Nothing today. `tests/unit/dashboard-tier1.test.jsx` already carries a zero-data fixture — its header says the fixtures hold an empty account "precisely so that tier 1 is the only thing on screen" — so the harness exists and the assertion does not. Requirement 8.5 wants the count of visible empty panels and the count of primary actions, on Dashboard plus Requirement 8.4's four. |

**[JUDGEMENT] on the prose budget's proxy.** "Above the first data element" needs a mechanical
definition or two implementers will count different things. The definition: the first element
carrying a `data-region` attribute, which `pageFields`-declared slots already have on every
migrated page (`liveTrading.test.jsx:245` walks them, and `dashboard-tier2.test.jsx` reads them
through `tierSelector`). Text in document order before that node is standing prose; text after
it is not. That makes 7.1's 400 characters a count rather than an opinion, and it is the reason
7.4's seeded figures can be asserted at all.

### 5.6 Class 5 — Marketplace (Requirements 10, 11, 12)

**No frontend test renders `StrategyMarketplace.jsx`.** Searching `algo22-terminal/tests/` for
the page returns five source-scanning guards that hold an entry for its path
(`no-colour-literals`, `no-placeholders`, `no-native-dialogs`, `dead-tailwind`, `a11y-ratchet`)
and nothing that mounts it. Its behavioural coverage is
`tests/unit/design/subscriptionState.test.js`, which covers the *module* Requirement 10.6
forbids reimplementing — the seven-to-four collapse, `CANCELLED` staying Subscribed, totality
over both server spellings, and `never returns Available from a state word` — and never the page.

| Question | The file | What it proves |
| --- | --- | --- |
| Are the four a11y findings gone? | `tests/unit/guards/a11y-ratchet.test.js` | The waiver's count is the measured count (`${file} holds exactly ${entry.count}`), and every unwaived in-scope page is at zero. It will fail the moment the findings move — in **both** directions, which is what Requirement 12.2 needs. See §7.4 for the assertion that has to change with it. |
| Is the page still at zero colour literals? | `no-colour-literals.budget.js:517` | `'pages/StrategyMarketplace.jsx': 0`, asserted exactly. The rebuild cannot reach for a hex to replace a gradient. |
| Do the arbitrary classes resolve? | `dead-tailwind.test.js` | Its own header records that this page carried seven arbitrary-value classes with the opacity slash missing (`bg-[#00D4FF]5` for `bg-[#00D4FF]/5`), so the hero glow, both notice banners' wash and the subscribe and clone buttons' hover state rendered nothing in the file requirements §1.1 calls "already fully Tailwind". **Needs a build.** |
| Does the failure copy leak? | `errorCopy.property.test.js` | Over the whole input space, as §5.3. It proves `translateError` is safe; it does not prove the page calls it. |

> **NEW: `tests/unit/pages/strategyMarketplace.test.jsx`.** Written **before** commits 5–7 and
> observed failing on the current file, in four blocks matching the four axes: the card is
> keyboard-activatable and carries an accessible name (Requirement 12.4); the four failure
> classes render four different pieces of copy resolved through `errorCopy.js` and none of them
> is `err.message` (Requirement 11.2, 11.3); the `42703` case renders `unavailable` naming what
> cannot be shown rather than an empty catalogue (Requirement 11.4); and `featured` and
> `trending` remain distinguishable after §7.3's replacement. The file is the reason commits 5–7
> can be split: without it, Requirement 11's behavioural change has no assertion of its own and
> Requirement 16's no-functional-change constraint is a reviewer's opinion.

### 5.7 Class 6 — landing (Requirements 13, 14, 15)

| Question | The file | What it proves |
| --- | --- | --- |
| Does the live surface render at all? | `tests/unit/landing_page_pricing_crash_regression.test.jsx` | `describe('Full Landing Page Integration')` mounts `components/landing/LandingPage` with all 13 sections and asserts `Infrastructure Tiers` is present and no `$` appears anywhere in the container. Plus the INR contract: four tiers at ₹0/₹499/₹999/₹2,499, four `.card-surface` cards under `#pricing`, `Recommended` on Pro Quant, no currency selector, `api.billing.getPlans` not called, and the annual arithmetic. **This is the net for Requirement 15.5 and the first instrument §8 reaches for.** |
| Is the installer advertisement still withdrawn? | `tests/unit/pages/downloadSurface.test.jsx` | Requirement 13.4's third already-checked item, asserted rather than remembered: each card states unavailable with its declared reason, no request is issued (proved at the hook *and* at both surfaces, with `fetch`, `XMLHttpRequest` and `HTMLAnchorElement.click` recording), and no size or checksum travels with the marker. Nothing in it is mocked. |
| Would a "coming soon" string on the live surface fail? | `tests/unit/guards/no-placeholders.test.js` | **Requirement 14.4 is already satisfied**: `SCAN_ROOTS` is `['pages', 'components', 'lib']`, so `components/landing/**` is in scope today. The confirmation Requirement 14.4 asks for is this line, and the answer is yes. |

**Three assertions in that end-to-end test cannot fail, and the class's coverage is weaker than
it looks.** `expect(container.querySelector('#pricing')).toBeDefined()` passes when the
selector returns `null`, because `null` is defined. The same is true of the `#architecture` and
`#waitlist` assertions beside it. So of the four claims in
`renders entire LandingPage end-to-end without crashing`, only `getByText('Infrastructure
Tiers')` can throw — and it throws for a missing *section*, not for a missing anchor. The test
does prove the tree mounts without an exception, which is the property §8 needs most; it does
not prove the three anchors exist. Corrected as part of §8's step 1, by replacing
`toBeDefined()` with `not.toBeNull()` on all three. That is a one-line-each fix to an existing
test and it is **not** a landing change: it belongs with the investigation, before any content
edit, because an anchor that is silently absent is one of Requirement 13.3's five candidate
symptoms and the test currently cannot tell us.

**Requirement 14.1's deletion breaks two other files, and one of them is a guard's own fixed
point.** This is the most expensive coupling in the section and it is easy to miss:

- `no-placeholders.test.js`'s `it('finds the placeholder that is really in the tree')` uses
  `components/landing/ScreenshotComingSoon.jsx` as its non-vacuity fixture. It asserts the file
  exists, that the scan reached it, and that `found.found.copy` equals `['Coming Soon']`. Its
  comment calls it "THE FIXED POINT, and this guard's equivalent of `legacy-c-budget`'s
  'actually finds the shim'", and says that if path resolution broke or rule 1 stopped matching,
  every count would be 0 and only the under-budget direction would object. **Deleting the file
  removes the only proof that the placeholder guard is not vacuous.**
- `no-colour-literals.budget.js` holds `'components/landing/PortfolioAnalytics.jsx': 2` and
  `'components/landing/ScreenshotComingSoon.jsx': 2`, both on Requirement 14.1's deletion list,
  plus `'pages/Landing.jsx': 18`. `names only files that still exist` fails unless the entries
  go in the same commit — which §4.3's rows 22–23 already require, and which is now named
  entry by entry.

**Decision D3 — Requirement 14.1 takes its second arm for `ScreenshotComingSoon.jsx` and its
first arm for the other seven.** The file keeps a header stating why an unrendered component is
retained: it is a test fixture for `no-placeholders`, named as such, with the guard's own
`it` quoted. The other seven are deleted with their budget entries.

**Alternative considered: delete all eight and move the fixture into the guard's own test file**
as an inline source string. Rejected. `no-placeholders.test.js` already has that — `measure(...)`
with a literal source is how `would report a cleaned file whose entry was left behind` works —
and the point of the fixed point is precisely that it is *not* synthetic: it proves path
resolution, directory walking and rule matching against a real file on disk. Replacing it with a
string removes the only thing it was measuring. **Alternative considered: retain all eight with
headers**, which is cheaper. Rejected: it leaves seven files a contributor can mistake for live
code, which is the defect Requirement 14 exists to close, and `pages/Landing.jsx`'s own
`DEPRECATED / UNMOUNTED` header is the evidence that a header alone did not stop the earlier
fixes landing in the wrong file (requirements §1.11).

### 5.8 What no file covers, consolidated

Named in one place so the list is auditable rather than scattered.

| Not covered | Consequence | Remedy |
| --- | --- | --- |
| A rendered font size, anywhere | §2's entire mapping is verified by source scan and review, never by render | §3's ratchet, plus §2.5's three-part proxy. Accepted limitation, written down. |
| Layout overflow after a size grows | Requirement 2.5's "the layout yields" is a judgement | §2.5's proxy. Requires a browser (§10). |
| `SIMULATED · SERVER LABEL UNAVAILABLE` rendering | Half of Requirement 9.2's distinction could be collapsed and the suite stay green | NEW `ds/TradingEnvironmentBadge.test.jsx` |
| The badge's four treatment axes | Requirement 9.1's edit is unobserved | Same file |
| `ds/Chart.jsx`'s tick and axis-label sizes | Commit 3 is unobserved | NEW `it` in `ds/Chart.test.jsx` |
| Anything rendered by `StrategyMarketplace.jsx` | Requirements 10, 11 and 12 have no behavioural net | NEW `tests/unit/pages/strategyMarketplace.test.jsx` |
| `RiskSettings.jsx`, at all | Requirement 6.5's kill switch | NEW `tests/unit/pages/riskSettingsKillSwitch.test.jsx` |
| Rendered standing-prose volume | Requirement 7.4 | NEW `standing-prose.{budget,test}.js` |
| Visible empty-panel count on a fresh account | Requirements 8.1, 8.2 | NEW `tests/unit/pages/freshAccount.test.jsx` |
| The three landing anchors | Requirement 13.3's "navigation failure" candidate | Correct three `toBeDefined()` to `not.toBeNull()` in the existing file |
| An empty `A11Y_PAGE_WAIVERS` | Requirement 12.3 | §7.4 — the guard has a hardcoded total and fails the empty case today |

Seven new files or assertions, five of them because a change class had nothing. None of them is
a new module, a new primitive or a new `design/` file, so Requirement 17 is untouched.

### Property 1: Every absolute font size is budgeted, and progress is recorded rather than banked

*For any* file under the guard's three scan roots, measured with §3.2's four patterns over
comment-stripped source: the file's count equals its budget entry exactly, a file with no entry
measures zero, and every file carrying a size is accounted for by the budget. A size removed
without lowering the entry fails; a size added to a cleared file fails as unbudgeted.

**Validates: Requirements 1.3, 1.5**

**Held by:** `tests/unit/guards/absolute-font-sizes.{budget,test}.js` — NEW, specified in §3, with
§3.4's six assertions. §5.2's first row.

### Property 2: A call site's step is a function of its role, not of its previous number

*For any* call site §2.4's procedure resolves, the step is selected by the first of nine questions
the site answers; two sites carrying the same px value resolve to different steps where their roles
differ; and a site carrying more than one role loses its own declaration so that each child takes
its own step.

**Validates: Requirements 1.1, 2.3, 2.4**

**Held by:** Nothing automated. §5.1 establishes that no test in this repository can observe a
rendered font size and §5.8's first row records it; §2.5's three-part proxy and review stand in.
**[JUDGEMENT]**

### Property 3: A reported zero renders as a zero, and an absent figure renders its declared reason

*For any* declared field and *for any* payload for it — including `0`, `0.0`, an empty collection,
`null`, `undefined` and a wrongly-typed value — a genuine zero renders as that value, an
unavailable figure renders the not-available marker together with the reason `pageFields.js`
declares for it, and no primitive renders `0` for a value it was not given.

**Validates: Requirements 19.1, 19.2, 19.5**

**Held by:** `tests/unit/design/reported.test.js`, `tests/unit/design/pageFields.test.js` and
`tests/unit/ds/Metric.test.jsx`, all unchanged by this pass — §5.3's rows 2–4. §6 is the argument
and §6.3 is the canonical case.

### Property 4: Every absence carries a reason, and no never-absent field carries one

*For any* entry in `pageFields.js`: every `UNAVAILABLE_FIELDS` entry has a non-empty reason over 20
characters, every entry whose `absence` is not `ABSENCE.NEVER` has one, and every entry whose
absence *is* `NEVER` has `reason === null`. Shortening a reason to a marker fails the first;
deleting one fails the second; adding one to a never-absent field to make the set uniform fails the
third.

**Validates: Requirements 19.3**

**Held by:** `describe('pageFields: Requirement 19.3 — every absence carries a reason')` in
`tests/unit/design/pageFields.test.js` — §5.3's second row, and §6.2's step 3, which is the step a
reviewer runs rather than reasons about.

### Property 5: Failure copy is authored, never derived from the error

*For any* error — including messages carrying V8 stack frames and Python tracebacks, a `statusText`
naming an exception class, and an internal `/api/` path in `url` — `translateError` returns a
headline from an authored table and never one derived from the error; and the four failure classes
Marketplace can reach resolve to four different pieces of copy, none of them `err.message`.

**Validates: Requirements 11.2, 11.3**

**Held by:** `tests/unit/design/errorCopy.property.test.js` and its sibling `errorCopy.test.js`
(§5.3). That the *page* calls it is the NEW `tests/unit/pages/strategyMarketplace.test.jsx` (§5.6);
§7.5 is the migration.

### Property 6: A schema fault is reported as unavailable, never as an empty catalogue

*For any* read that did not complete — including the `42703` the projection raises with migration
`007` unapplied — the page renders the `unavailable` state naming what cannot be shown, and never a
zero-filled or empty result.

**Validates: Requirements 11.4, 19.5**

**Held by:** NEW `tests/unit/pages/strategyMarketplace.test.jsx` (§5.6). The server side already
holds it: `library.py:720` and `:785` raise `MarketplaceError(MARKETPLACE_READ_FAILED)` rather than
a zero-filled 200 (§7.5).

### Property 7: Moved prose stays reachable and unchanged in substance

*For any* explanation Requirement 7 moves behind a disclosure: the disclosure is a keyboard-operable
control with an accessible name and a programmatic expanded state, the content is reachable through
it, its substance is unchanged, and the caveat's `data-selector-caveat` attribute travels with it
rather than being dropped. In the states where the deployment list is empty or partial, the
operative sentence renders expanded rather than collapsed.

**Validates: Requirements 7.2, 7.3, 7.5, 19.6, 20.2**

**Held by:** `tests/unit/liveTrading.test.jsx:1041`, `tests/unit/ds/Accordion.test.jsx` and
`tests/unit/ds/Tooltip.test.jsx` (§5.5), plus §9.2's two hard constraints. Whether the disclosure
overflows at a viewport is the geometry half and has no instrument (§5.1).

### Property 8: Standing prose falls, measured on rendered text

*For any* of the eight pages Requirement 7 covers, rendered against its fixture: the text of the
nodes standing in document order before the first `data-region` node totals no more than that
page's recorded budget, and the budget is lowered in the same commit as the text.

**Validates: Requirements 7.1, 7.4, 7.5**

**Held by:** NEW `tests/unit/guards/standing-prose.{budget,test}.js` — §5.5's fifth row, with
§5.5's **[JUDGEMENT]** on the `data-region` definition that makes the 400 characters a count rather
than an opinion.

### Property 9: `empty`, `unavailable` and `error` are never collapsed

*For any* panel state: a panel that is empty because the trader has nothing yet is distinguishable
from one that could not be read and from one whose read failed; the no-data and no-match empty
variants cannot be collapsed; and reducing a panel's visual weight leaves its state name, its copy
and its action in place.

**Validates: Requirements 8.3, 19.4**

**Held by:** `tests/unit/ds/EmptyState.test.jsx`'s two-variant `EMPTY_VARIANTS` assertion and
`requires a clear-filters action for no-match` (§5.5); `tests/unit/hooks/usePanelState.test.js` for
the eight states and their resolution order (§5.3). §9.3 is the hierarchy change this bounds.

### Property 10: A fresh account sees exactly one primary action

*For any* of Dashboard plus Requirement 8.4's four pages rendered against a zero-data fixture:
exactly one primary action is visible, and the number of simultaneously visible empty panels does
not exceed three.

**Validates: Requirements 8.1, 8.2, 8.5**

**Held by:** NEW `tests/unit/pages/freshAccount.test.jsx` — §5.5's last row, on the zero-data
fixture `dashboard-tier1.test.jsx` already carries. *Which* action is primary is a **[JUDGEMENT]**
that goes to the requester (§9.3); the property makes "exactly one" checkable.

### Property 11: The two unresolved environment labels stay distinct, and neither resolves

*For any* badge input where `environment == null`: `isSimulated` renders
`SIMULATED · SERVER LABEL UNAVAILABLE`, `!isSimulated` renders `ENVIRONMENT UNCONFIRMED`,
`data-environment` is distinct on each, and neither arm resolves to `LIVE` or `PAPER`. Every variant
keeps its screen-reader prefix and its `title` long form through a treatment change.

**Validates: Requirements 9.1, 9.2, 20.1**

**Held by:** NEW `tests/unit/ds/TradingEnvironmentBadge.test.jsx` — §5.4, which records that
`SIMULATED · SERVER LABEL UNAVAILABLE` is asserted by no test today and that only one of the two
arms would fail if they were collapsed.

### Property 12: Every control stays keyboard-operable and named

*For any* in-scope page carrying no waiver, the measured `jsx-a11y` finding count is zero; *for any*
file under `src/components/ds/**` it is exactly zero, at `error` rather than `warn`; and *for any*
waived file the waiver's count equals the measured count, in both directions. A card rebuilt onto a
`ds/*` primitive is activatable by keyboard and carries an accessible name, and a kill-switch
control operated by `keyDown` issues the same request the pointer path issues.

**Validates: Requirements 6.1, 6.2, 6.5, 12.2, 12.4, 20.1, 20.3, 20.4**

**Held by:** `tests/unit/guards/a11y-ratchet.test.js` (§5.3, §7.4, with §7.4's Decision D7 on the
hardcoded total); NEW `tests/unit/pages/riskSettingsKillSwitch.test.jsx` (§5.3) and NEW
`tests/unit/pages/strategyMarketplace.test.jsx` (§5.6).

### Property 13: The set of requests a page can issue does not change

*For any* commit in this pass: `api-paths.budget.js` is unchanged, the diff contains no path under
`src/api/`, `src/websocketClient.js`, `backend_app/` or
`aerora_quant_platform/frontend_app/algo22-terminal`, and no new file appears under
`src/components/ds/` or `src/design/`, nor a page-local `C` / `COLORS` / `THEME` object.

**Validates: Requirements 16.2, 16.7, 17.1, 17.2, 17.3, 17.4, 18.3, 21.3**

**Held by:** `tests/unit/guards/api-paths.{budget,test}.js` and
`tests/unit/guards/no-local-tokens.test.js` (§5.2, §5.3); the path checks are §4.6's per-commit
`git diff` and §10.2's last row.

---

## Data Models

This is a presentation-layer spec and it introduces no new data model. Requirements 17.1–17.3
forbid a new `ds/*` primitive, a new `usePanelState` state and a new `design/` module, so every
shape this design reads is already declared elsewhere: `src/design/pageFields.js`'s entries, each
with a `path`, an `absence` kind and a `documentedIn` (§6.1), carrying a `reason` wherever the
absence is not `never` (§6.2); `usePanelState`'s eight states and their resolution order (§5.3);
the seven `Type_Scale` steps at `tokens.css:69`–`:75` (§2.1); and `design/reported.js:11`'s
`Reported<T> = { available: true, value: T } | { available: false, reason: string }`. What
follows is the one declared shape this pass can break without editing any of those files — the
distinction between a reported zero and an absent figure — and the rule that stops it.

### 6. The zero-versus-unavailable hazard

`production-launch-hardening` made trader-visible figures explicitly nullable so that an absent
value is distinguishable from a zero, and it did that because the codebase had previously
substituted `100000.0` and `0.0` for failed reads (Requirement 19.3 cites its clauses 2.1–2.6).
Every entry in `src/design/pageFields.js` carries a `reason` as the other half of that work: the
marker says *that* a figure is missing, the reason says *why*, and `pageFields.js` states the
point itself — "not available" with no reason tells a trader exactly as much as a blank cell.

A simplification pass's instinct is to read those reasons as clutter. They are sentences, they
are often the longest strings on a panel, they appear in the state a trader visits least, and
§2.4's own procedure sends every one of them *up* to `body`. So this pass will make them bigger
at the same time as it is looking for text to remove. That is the collision, and it is why
Requirement 19 is a P0 rather than a note.

### 6.1 Decision D4 — the rule is about the sentence's job, not its length

**Decision.** Three categories, and the boundary between them is what the string is *for*.

| May be shortened | May not be shortened | May not exist |
| --- | --- | --- |
| Prose whose job is to *explain the product* — how the list is built, what a tier means, why a figure is computed the way it is. Requirement 7 governs it and Requirement 7.2 requires the substance to survive behind a disclosure. | Prose whose job is to *account for a specific absent value*. Every `reason` in `pageFields.js`; `usePanelState`'s `unavailable` copy; the server's own `not_available_reason` where a field forwards it. | Prose that *replaces* an absent value with a plausible figure. Forbidden outright by Requirement 19.5 and by 16.4's ban on client-side derivation. |

Two properties separate column 1 from column 2, and both are mechanical:

1. **Column 2 renders only in a state the trader did not choose.** It appears because a read
   failed or a field was absent. Column 1 renders unconditionally — that is what makes it
   `Standing_Prose` in the first place, and it is the definition Requirement 7 is written
   against.
2. **Column 2 is declared beside the field it explains.** It lives in `pageFields.js` with a
   `path`, an `absence` kind and a `documentedIn`. Column 1 lives in a page-level `const`.

So the rule reduces to a path check: **a string reachable from `design/pageFields.js` or from
`usePanelState`'s `unavailable` branch is column 2. Everything else is column 1.** Requirement
7's 400-character budget is scoped to column 1 by construction, because §5.5's proxy counts text
above the first `data-region` node and a reason renders *inside* one.

**Alternative considered: a length threshold** — shorten anything over N characters, exempt
anything under. Rejected on the same grounds as §2.2's numeric mapping, and the counterexample is
in the tree: `pageFields.js`'s own test asserts
`expect(field.reason.trim().length).toBeGreaterThan(20)` for every unavailable entry, i.e. the
declaration already treats *short* as the failure mode for a reason and *long* as the failure
mode for a caveat. One threshold cannot point both ways.

**Alternative considered: allow a generic fallback reason** — one sentence covering every
absence, with the specific ones kept in the codebase as documentation. Rejected: Requirement
19.3 names this exact move ("replace a set of reasons with one generic sentence") and forbids it.
It is also the move that looks most like a win, because it deletes the most characters for the
least apparent loss.

### 6.2 The test a reviewer applies

One question, asked of the string in the diff:

> **Remove the string. Can a trader still tell this figure apart from a figure that is genuinely
> zero, and still tell why?**

If the answer is no, the string is column 2 and the edit is a Requirement 19.3 violation. If the
answer is yes, it is column 1 and Requirement 7 governs.

The mechanical form, for a reviewer who wants a check rather than a judgement:

1. Is the string reachable from a `pageFields.js` entry's `reason`, or from a `ds/Panel`
   `unavailable` prop? → column 2. Stop.
2. Does it render in only one of `usePanelState`'s eight states? → column 2. Stop.
3. Does `tests/unit/design/pageFields.test.js` fail when it is emptied? → column 2. Stop.
4. Otherwise → column 1, and Requirement 7's budget applies.

Step 3 is the one worth running rather than reasoning about. `describe('pageFields: Requirement
19.3 — every absence carries a reason')` has three assertions and they are exhaustive over the
declaration: every `UNAVAILABLE_FIELDS` entry has a non-empty reason over 20 characters, every
entry whose `absence` is not `ABSENCE.NEVER` has one, and every entry whose absence *is* `NEVER`
has `reason === null` so no reason is dead copy. A pass that shortens a reason to a marker fails
the first; a pass that deletes one fails the second; a pass that adds a reason to a
never-absent field to make the set uniform fails the third.

### 6.3 `exchange_api_latency_ms` is the canonical case, and it is declared

`pageFields.js`'s `exchangeApiLatencyMs` entry (page `DASHBOARD`, requirement `3.2`, path
`health.exchange_api_latency_ms`, `absence: ABSENCE.UNMEASURABLE`, reason
`Exchange API latency has not been measured.`) carries this note verbatim:

> Genuinely `null` when unmeasured, with `health.exchange_api_latency_status` reading
> "unavailable" beside it. **It must never render as "0 ms"** — a zero-latency exchange call is
> not a thing, so the figure would be read as a claim about a working connection (Requirement
> 14.5).

That is the whole hazard in one field. `0 ms` is not merely wrong, it is *reassuring*: it reads
as a connection so fast it is instant, at the exact moment nothing has been measured. The
figure's plausibility is what makes the collapse dangerous, and it is why Requirement 19.1 and
19.2 are two clauses rather than one.

Two files hold it today, and both survive this pass unchanged:

- `tests/unit/dashboard-tier2.test.jsx`, claim 4 in its header —
  "`health.exchange_api_latency_ms === null` is the marker, never `0 ms`", on the payload
  `pageFields` describes, with `exchange_api_latency_status: "unavailable"` beside it. **The
  measured arm is asserted too**, "so the absence is a distinction and not a page that never
  renders latency". That second half is the part a simplification pass can break without
  touching the first.
- `tests/unit/ds/ExchangeStatus.test.jsx`, whose header calls an unmeasured latency "the
  load-bearing case" and records that `0 ms` on the strength of a `null` "would be a fabricated
  reading a trader would act on". Its sibling constraint is in `pageFields.js`'s
  `exchangeHealth` note: two of the four per-venue fields are constants in the aggregation
  service — `status` is always `"connected"` and `latency_ms` always `35` — so neither is passed
  to the primitive, and `dashboard-tier2.test.jsx`'s claim 6 asserts that too.

**A same-shaped sibling, for the same reason.** `risk.open_positions_count === null` is the
marker, never `0`, "because a count of zero is the safest-looking figure a broken positions read
could publish" — `dashboard-tier2.test.jsx` claim 3, and `dashboard-kill-switch.test.jsx`'s
`renders the not-available marker, never a '0', for a figure the server did not report`, which
asserts the halt confirmation's review grid contains no `0` at all when the count is `null`. A
count and a latency fail the same way and the pass must not treat either as a rounding problem.

### 6.4 The connection to §2.6's `:707`

`PaperTrading.jsx:707` is `fontSize: missing ? 13 : 18`, and §2.6 already argued that it is one
role and one step. What makes it §6's case as well as §2's is *which direction the workaround
went*: the size was shrunk **on the absence arm**, because the not-available marker plus its
reason is longer than a number and 18px overflowed. `wordBreak: 'break-all'` two lines below at
`:709` is the standing evidence that it overflowed anyway.

So the file already contains an instance of the thing Requirement 19.3 forbids, in a form nobody
would recognise as a reason being shortened: the reason was not cut, it was set 28% smaller than
the figure it stands in for. The trader who most needs to read that string reads it at the
smallest size on the panel.

That is the case that closes the loop between the two sections, and it constrains the remedy in
both directions:

- **From §2:** the conditional collapses to the *value's* step, not to 13. Requirement 19.3
  forbids shrinking a reason to reduce clutter, and a conditional that shrinks it to fit is the
  same act with a layout excuse.
- **From §6:** the reason string itself is untouched. The layout yields by §2.5's mechanisms 1
  and 2 — drop `whiteSpace: 'nowrap'`, spend padding from the declared spacing scale — and
  `wordBreak: 'break-all'` goes with the conditional, because a reason that breaks mid-word is a
  sentence a retail reader will not finish.

**[JUDGEMENT] flag, and its proxy.** "Did the reason stay legible at the value's step" is a
rendered-geometry question and §5.1 established there is no instrument for it. The proxy is the
one Requirement 19.7 already names: `design/reported.js` and `design/pageFields.js` suites pass
unchanged, and Property P5 — a failed read renders no figure and no zero — continues to hold
across every declared entry. Add one page-level check for this specific call site: after the
edit, `PaperTrading.jsx` contains no `fontSize` at `:707` and no `wordBreak` at `:709`, and the
panel's absence arm renders the declared reason in full rather than a truncation. The proxy
cannot see a two-line reason where one was intended. It can see every version of the fix that
keeps the workaround.

---

## Components and Interfaces

### 7. Marketplace

Requirements 10, 11 and 12 describe one 1,210-line file on four axes plus a read path.
§4.3 places them as commits 5–6 (visual generation together with the a11y findings) and 7 (the
read path). This section decides what each axis resolves to.

The framing that governs the whole section: **this page is the product's shopfront.** It is where
a retail trader decides whether to pay for someone else's strategy. Requirement 10 asks for the
current visual generation and Requirement 3.4 folds the typography in, and both of those are
subtraction. A page that is only subtracted from is a page that stops selling anything, which is
its own failure and not one Requirement 10 authorises. So every decoration removed below names
what takes its place.

### 7.1 Axis 1 — the 30 `text-[Npx]` classes

**[RE-MEASURED]** The distribution, from the four §3.2 patterns with comments stripped:
`text-[10px]` and `text-[9px]` dominate, with `text-[11px]` on the per-condition metric rows.
Every one resolves through §2.4's procedure exactly as a page-level `fontSize` would — the
syntax differs, the role question does not.

| Cohort | Representative sites | Role | Step |
| --- | --- | --- | --- |
| Environment chip | `:546` (`text-[10px] font-bold font-mono uppercase tracking-widest`) | chip | `micro` |
| Column and figure labels | `:569`, `:576`, `:586`, `:687`, `:693`, `:705`, `:711`, `:750`, `:756`, `:768`, `:964`, `:970`, `:983`, `:993` | label | `micro` |
| Category / difficulty / validation pills | `:742`, `:871`, `:876`, `:881`, `:954` | chip | `micro` |
| The "Featured" ribbon | `:668` | chip | `micro` — and see §7.3 |
| Rating line, subscription eyebrow, billing note | `:777`, `:779`, `:828`, `:902` | label / chip | `micro` |
| Environment description, per-condition metrics, the empty-condition sentence | `:553`, `:607`, `:612` | sentence | **`body`** |
| The historical-results statement | `:628` (`text-[11px] ... leading-relaxed`, `data-testid="historical-results-statement"`) | sentence | **`body`** |

Two of those rows are the ones that matter. `:628` is a safety statement about backtested figures
carrying a `data-testid` precisely so it can be asserted, and it is currently set 2px below the
page default. `:607`'s *No figures recorded for this condition.* is §6's column 2 — an account of
an absence — at `text-[11px]`. Both go to `body`, both grow, and §2.5's mechanisms apply.

### 7.2 Axis 2 — 69 `font-mono` and 25 `uppercase`

Requirement 3.4 routes these here rather than to Requirement 3 because on this page the
typography and the visual generation are the same edit. Requirement 3.1's rule decides each one:
monospace for values whose character alignment or literalness carries meaning, sans for prose.

Reading the call sites, the 69 split three ways:

- **Keep, unconditionally: figures in a grid.** The four-column metric blocks at `:687`–`:713`
  and the three-column block at `:750`–`:771` are numerals a reader compares down a column.
  `--font-mono` is what makes them comparable and `tests/unit/ds/columnAlignment.property.test.jsx`
  is the property that pins the equivalent behaviour in `ds/DataTable`. Keep.
- **Keep, as identifiers:** the price figures (`:713`, `:901`), the rating (`:781`), the
  published date (`:986`), the category/difficulty/validation words as server-supplied literals
  (`:742`, `:871`–`:881`).
- **Convert: every sentence.** `:553`'s environment description, `:607`'s absence account,
  `:628`'s historical-results statement, `:1041`'s hero paragraph, `:1067`'s error line, and the
  hero's three count captions at `:1045`–`:1051`. A `font-mono text-sm` paragraph is the single
  strongest signal on the page that it belongs to the previous generation.

The 25 `uppercase` go against Requirement 3.3's three-word rule. `Sharpe · BACKTEST`,
`Return · BACKTEST`, `Subs`, `Price` and the single-word pills all pass it. `Your subscription`
(`:828`), `Per-condition results` (`:586`) and `Recent reviews` (`:993`) are two- and three-word
*section names* rather than chips, and they lose nothing by dropping the transform while gaining
word-shape recognition — which is the mechanism Requirement 3.3 is about. The one that must be
decided rather than swept: the environment word at `:548` and `:578` is uppercase because it is
`LIVE`/`PAPER`/`BACKTEST` as the server spells it. It stays uppercase because it *is* the
server's literal, not a transform of one, and `ds/TradingEnvironmentBadge` renders it the same
way on every other page.

**Recorded for Requirement 3.2's ledger.** The classification above is the record that clause
asks for. It is recorded here rather than in a table of 94 rows for §2.2's reason: a per-call-site
table is a second design system in table form.

### 7.3 Axis 3 — the decorative styling the token layer does not declare

Four elements, and `tokens.css` declares none of them. Its elevation block says so in its own
comment: *no coloured glows. Calm by default (Req 1.5)*. Its alias block records that
`--animate-pulse-glow` "was not carried forward" because "Requirement 1.5 retires rather than
preserves" a coloured glow, and `dead-tailwind.test.js`'s `KNOWN_DEAD_CLASSES` quarantines the
one class name left behind by that decision. So this axis is not a judgement about taste; it is a
decision the token layer already made, applied to the one page that predates it.

**Decision D5 — every removal names its replacement, and the replacement uses a declared token.**

| Element | Site | Removed | Replaced by |
| --- | --- | --- | --- |
| Featured-card fill | `:665` `bg-gradient-to-br from-surface-panel to-surface-canvas` | the gradient | `--color-surface-panel` flat, with `--color-line-strong` as the border in place of `border-brand/30`. Elevation carries the emphasis the gradient carried: `--shadow-raised` against the ordinary card's `--shadow-panel`. |
| Corner wash | `:667` `bg-gradient-to-l from-brand to-transparent w-32 h-32 opacity-10` | the whole element | Nothing. It is a 128px decorative square with no content and no state; the border and elevation above already distinguish the card. This is the one element that is deleted rather than replaced, and the reason is that it carried no information at any opacity. |
| Card radius | `:665`, `:865`, `:1032` `rounded-2xl` (16px) | 16px | `--radius-xl` (12px). Requirement 10.2's scale is `xs` 2 / `sm` 4 / `md` 6 / `lg` 8 / `xl` 12 / `full` 9999, and `rounded-full` is `--radius-full` and untouched. `ds/Panel`'s own radius is what these cards inherit once they render through it (Requirement 10.6). |
| Hero glow | `:1033` `absolute … w-96 h-96 bg-brand/5 rounded-full blur-3xl` | the whole element | Nothing, and the hero keeps its weight from `ds/PageHeader`'s `--text-page` title plus the three count captions. **[RE-MEASURED]** this sits at `:1033`, not `:1034` as requirements §1.6 and Requirement 10.3 both state. |
| "Featured" ribbon | `:668`–`:670`, `bg-status-warning` + `Sparkles` + `text-[10px] font-bold font-mono uppercase` | the ribbon as a decoration | A `ds/StatusBadge` whose label states the basis. See §7.3.1 — this one is not a styling decision. |

**Alternative considered: keep the gradients behind a named marketing token**, the way
Requirement 15.3 permits for the landing surface. Rejected for this page and only for this page.
Requirement 15.3's argument is that a marketing surface may legitimately differ from a trading
surface and the exception should be declared so it is countable. Marketplace is not a marketing
surface: it is inside the authenticated shell, it is in `IN_SCOPE_PAGES`, and it is where a
trader commits money. The same argument that justifies a gradient on `/` argues against one
here, and Requirement 10.1 is unconditional about the three.

#### 7.3.1 Decision D6 — "featured" and "trending" stay distinguishable by declared means

Requirement 10.5 says the ribbon states what featured means or is removed. The basis is
knowable, and it is not what the ribbon currently implies.

**[MEASURED]** in `backend_app/routers/library.py`:

- `GET /api/library/featured` (`:674`) selects `is_featured = True` and
  `moderation_status in ('approved', 'featured')`, ordered by `published_at` desc. `is_featured`
  is set by an operator through the moderate route, which `:2425` records as
  "RETAINED only for `is_featured`/`moderation_notes`". **Featured is editorial. It is not a
  performance ranking and it is not derived from any figure.**
- `GET /api/library/trending` (`:759`) orders by `clone_count` desc then `avg_rating` desc.
  **Trending has an exact, statable basis.**

The current treatment says the opposite of both. `bg-status-warning` is the token
`design/semantic.js` uses for `warning` and `guidance`, and `Sparkles` plus a gold pill beside a
Sharpe ratio reads as a claim about the strategy's quality. Requirements §1.6 calls it "a claim
without a basis" and it is worse than that: it is a claim the server does not make, rendered in
the hue reserved for a state a trader should act on.

**Decision.** Both sections keep their distinction, and it comes from three declared sources, in
this order:

1. **The section heading, which states the basis.** `ds/SectionHeader` reading
   *Selected by VyomQuant* and *Most cloned*. This is where Requirement 10.5 is satisfied: the
   basis is stated once per section rather than implied once per card, which is also the only
   placement that scales when a card appears in both lists.
2. **Elevation and border, not hue.** Featured cards at `--shadow-raised` + `--color-line-strong`;
   trending and catalogue cards at `--shadow-panel` + `--color-line-default`. Both are declared,
   both survive `no-colour-literals`' zero, and neither is colour-only — Requirement 20.5
   forbids a state carried by colour alone and this replaces a hue with a shape.
3. **A `ds/StatusBadge` on a featured card, with a `ds/Tooltip`**, only if the per-card marker is
   kept at all. Label: *Selected*. Tooltip: that the listing was chosen by VyomQuant and that the
   selection is not a statement about performance. `ds/Tooltip` is already keyboard-reachable
   (§5.5) so this adds no accessibility surface, and `ds/StatusBadge`'s `neutral` token is the
   correct one for an editorial fact.

**Alternative considered: remove the per-card marker entirely and let the section heading carry
it.** This is the cheaper option and it is defensible — Requirement 10.5's second arm explicitly
permits removal. It loses one thing: a card reached from search or from the full catalogue
carries no marker, so a listing that *is* featured looks identical to one that is not. Whether
that matters is a product question, not a design-system one. **Recorded as a judgement the
Marketplace task takes with the requester's sign-off, defaulting to keeping the badge** — because
Requirement 16.3 forbids changing the set of things a trader can see, and silently dropping a
distinction the server draws is closer to that than keeping it in a calmer form.

**Alternative considered: keep `Sparkles` as a neutral-toned icon.** Rejected. Requirement 20.1
requires an accessible name and 20.5 requires the state to survive in text or shape, and both are
satisfied by the word *Selected*; the icon then adds nothing but the gamified register
`vyomquant-ui-redesign` rejected. `ds/EmptyState.test.jsx`'s `renders a static icon with no
animation and no injected keyframes` is the same decision applied one level down.

### 7.4 Axis 4 — the four a11y findings, and the assertion that has to move with them

`eslint-rules/a11y-ratchet.js:227` holds `'src/pages/StrategyMarketplace.jsx': Object.freeze({
count: 4, task: '26.1' })`, and it is the last entry in the file — every other page's line has
been deleted after reaching zero, and the file's comment block above it is a running account of
those deletions.

**[MEASURED]** the four are two findings on each of two elements, and both elements are the same
mistake:

| Site | Element | Rules |
| --- | --- | --- |
| `:664` | featured card — `onClick` on a `div` with `cursor-pointer` (`:665`) and no `role`, no `tabIndex`, no key handler | `click-events-have-key-events`, `no-static-element-interactions` |
| `:729` | catalogue card — the same `onClick={() => loadDetail(strat.listing_id)}` on a `div` with `cursor-pointer` (`:731`) | the same two |

There is no third interactive `div` in the file, and nothing in it sets `role="button"` or
`tabIndex`, so the count of 4 is fully accounted for. Requirement 12.4 names `ds/DataTable`'s row
activation as the precedent; `a11y-ratchet.js`'s own comment block records that `SignalTrace`'s
last two findings were this identical pair on a signal row and that "the rows are
`ds/DataTable`'s now, and its row activation is keyboard-operable".

**This is why Requirements 10 and 12 are one commit.** You cannot rebuild the card onto a `ds/*`
primitive and leave a bare `onClick` `div` behind, and you cannot fix the `div` without deciding
what the card is. §4.3's grouping of 5–6 is the consequence, not a convenience.

**The guard fails on the empty list today, and Requirement 12.3 predicted it.**
`a11y-ratchet.test.js`'s last test, `it('records what M7-M9 is walking into')`, ends:

```js
const waived = Object.values(A11Y_PAGE_WAIVERS).reduce((sum, e) => sum + e.count, 0);
const measured = Object.keys(A11Y_PAGE_WAIVERS).reduce((sum, f) => sum + PAGES[f].count, 0);
expect(measured).toBe(waived);
expect(waived).toBe(4);
```

`expect(measured).toBe(waived)` is the ratchet and it holds in both directions on any list,
empty included. `expect(waived).toBe(4)` is a checked-in total whose stated purpose is "so the
milestone that inherits this debt cannot be surprised by its size" — and deleting Marketplace's
entry makes it `0`, so the test goes red for the right reason expressed the wrong way.

**Decision D7 — the total becomes derived, in the same commit that deletes the entry.** Replace
`expect(waived).toBe(4)` with the two assertions it was standing in for: `expect(measured)
.toBe(waived)` stays, and the historical account moves into the comment block that already holds
the 25 → 23 → 21 → 18 → 8 → 4 sequence, extended with 4 → 0. Requirement 12.3's wording — "IF the
empty case is not handled, THEN that is a defect in the guard and SHALL be fixed rather than
worked around by leaving a `0` entry" — is discharged by exactly this edit.

Note the interaction with Requirement 6.2 and §4.3's commit 4: extending `IN_SCOPE_PAGES` to
every page under `src/pages/` adds waiver entries for the unmigrated pages' existing findings,
which also moves `waived` off 4. So commit 4 hits the same hardcoded assertion before
commit 5–6 does. The edit belongs in commit 4, and commit 5–6 then only deletes a line.
`it('waives only in-scope pages, so every entry has a task that clears it')` is compatible with
the extension by construction: it asserts every waived file is *in* the list, and commit 4
widens the list before adding entries.

### 7.6 OPEN DECISION O1 — Tailwind's 434 built-in size classes

**This is recorded, not resolved. It needs the requester's decision and nothing in §7 proceeds
past axis 1 without it.**

§3.6's second blind spot measured **434** occurrences of `text-xs` / `text-sm` / `text-base` /
`text-lg` / `text-xl` / `text-2xl` and their siblings across `src/`, outside tests — including
**41 in `StrategyMarketplace.jsx`** and 43 in `ScreenshotsSection.jsx`. They resolve:
`tokens.css:14`'s `@theme static` block declares the seven steps but does not reset the namespace
with `--text-*: initial`, so Tailwind v4's defaults are *extended* rather than replaced, and the
built stylesheet emits `--text-xs:` and `.text-xs` alongside `--text-micro:` and `.text-micro`.

The consequence for this page is arithmetic:

| | Count | Scales with the reader (Req 2.1, 2.2) | Is a `Type_Scale` step (Req 1.2) |
| --- | --- | --- | --- |
| `text-[Npx]` | 30 | **No** | No |
| `text-xs` … `text-4xl` | 41 | Yes | **No** |
| **Off-scale total** | **71** | | |

Requirement 10.4 addresses the 30. **Marketplace's real off-scale count is 71.** The 41 include
`text-4xl font-black` on the hero `<h1>` at `:1038` — 36px, larger than `--text-figure`'s 28px
and a step the scale does not have — `text-3xl` on the detail price at `:901`, `text-xl` on the
card title at `:679` and on three detail figures, and `text-lg` on the four featured-card metrics.
Those are the page's entire visual hierarchy, and none of them is a declared step.

Three options, with their costs:

**Option A — reset the namespace.** Add `--text-*: initial;` to `tokens.css`'s `@theme static`
block, so the seven steps are the only text sizes the framework emits.
*For:* it makes Requirement 1.1 literally true — the token layer becomes the only place a font
size is declared — and it makes the guard unnecessary for this class, because a `text-xs` that
emits nothing is caught by `dead-tailwind.test.js` as a dead class.
*Against:* it is a token-layer change, so Requirement 22.3 makes it its own commit and
`tokens.generated.test.js` plus `prebuild`'s `gen-tokens --check` both engage. It breaks all 434
call sites at once, app-wide, on every page including the eleven that are otherwise finished.
That is not a per-page increment and Requirement 22.1 is the clause it collides with. It also
needs a build to see the blast radius (§10), which this pass cannot produce.

**Option B — a second budget on the built-in classes**, seeded at 434, lowered per page
alongside the px budget.
*For:* it is the mechanism this repository already trusts, it is per-page and therefore
Requirement 22.1-shaped, and it needs no token-layer change and no build. Marketplace's entry
would be 71 rather than 30 and would be cleared by the same commit.
*Against:* two budgets on one axis, and a contributor now has to know which of two guards a size
belongs to. It also does not stop the 434th-and-first: a new `text-sm` in a file with headroom
passes.

**Option C — accept them, and narrow Requirement 1.2.** Declare that "scales with the reader" is
the property this pass enforces and "is one of seven steps" is a later pass's.
*For:* honest, cheap, and it is what the tree does today. Requirements 2.1 and 2.2 — the P1s, the
ones a retail reader actually feels — are satisfied by a `text-sm`.
*Against:* it leaves the inconsistency Requirement 1 was written to remove. Nine label sizes
become seven token steps plus six framework steps, and the page-to-page inconsistency
requirements §1.2 measured survives in a form that passes every guard.

**Recommendation, for the record and not as a decision:** B, scoped so that Marketplace's entry
is 71 from the start, with A filed as the follow-up it is. B is the only option that lets
commits 5–7 land at all without either a global token change or a narrowed requirement. But
Requirement 1.4's seeded list, Requirement 1.2's three named syntaxes and Requirement 10.4's
figure of 30 all change under it, and §3.5 already owes the requester one correction to
Requirement 1.4. **Flagged as needing the requester's decision before commit 1 is seeded**,
because the seed's shape depends on the answer.

---

## Error Handling

The failure-path change in this pass is Marketplace's read path, §7.5 below. The eleven pages of
§4.3's commits 8–21 reach the same convention as part of Requirement 4's migration — §1.2's cause
2 — and §5.3 names the files that hold that class. What proves the copy itself is safe is
`errorCopy.property.test.js`: §5.3's row records that it returns a headline from an authored table
and never one derived from the error, and §5.6 records that it proves `translateError` is safe
without proving the page calls it, which is what the new
`tests/unit/pages/strategyMarketplace.test.jsx` (§5.6, §5.8) is for. §6 bounds what a failure path
may reword: a sentence whose job is to account for an absent figure may not be shortened.

### 7.5 The read path (Requirement 11, commit 7)

`StrategyMarketplace.jsx:380`–`:382`:

```js
} catch (err) {
  console.error(err);
  setError('Failed to load marketplace data. Please try again.');
```

One `catch`, one string, every failure mode. The page holds its own `loading`/`error` pair and
renders the string at `:1067` in a hand-styled `bg-status-error/20` box with an `AlertTriangle`
and `font-mono text-sm`. Four distinct facts collapse into it: the network did not answer, the
server faulted, the caller is not authenticated, the caller is not entitled. Requirement 11.2
requires four different answers because the trader's next action differs in each — retry, wait,
sign in, upgrade.

The migration is mechanical and adds nothing:

| Today | After |
| --- | --- |
| `const [loading, setLoading]`, `const [error, setError]` | `usePanelState`, one of its eight states |
| `console.error(err)` | the `Convention`'s reporting path; Requirement 11.3 forbids logging in place of handling |
| one string | `translateError(err, 'marketplace')` from `design/errorCopy.js`, which returns `{headline, detail, retryable, action, supportRef}` — the shape `errorCopy.property.test.js` asserts is complete and authored over the whole input space |
| the hand-styled box at `:1067` | `ds/Alert`, or `ds/Panel`'s `error` state |
| a schema fault presented as an empty catalogue | `unavailable`, naming what cannot be shown |

**The `42703` case is the one worth stating precisely.** Requirements §1.10 records that
`library_strategies.price_minor` is added by migration `007` and that with `007` unapplied the
projection raises PostgreSQL `42703`. The backend already refuses to swallow it:
`library.py:720` and `:785` raise `MarketplaceError(MARKETPLACE_READ_FAILED)` rather than a
zero-filled 200, and the comment beside each says so — "A read that did not complete is the
structured `MARKETPLACE_READ_FAILED`, never a zero-filled 200". So the server sends a code the
client can translate, and the current page discards it. Requirement 11.4's `unavailable` state is
reachable from what is already on the wire; nothing new is needed on either side.

Requirement 11.5 is restated here because it is the clause most likely to be read as done:
**this changes what the trader is told while `007` is unapplied. It does not fix Marketplace.**
Applying the migration belongs to whichever spec owns the deploy, and §1.10 keeps it out of this
one.

---

## Landing investigation

**[UNVERIFIED]** throughout. Requirement 13 asks for a reproduction and this section designs one.
It does not assert a defect, and §4.4 already placed it: it runs alongside commit 1 and produces
no diff.

`src/pages/Landing.jsx` is unmounted dead code — its own header says `DEPRECATED / UNMOUNTED —
LEGACY LANDING PAGE`, and `App.jsx:44` lazy-imports `./components/landing/LandingPage` while
`:590` routes it at `/`. The live surface is `LandingPage.jsx` plus its 13 sections. The symptom
has not been described, and static reading of all 14 rendered files found no render-blocking
fault.

### 8.1 What has already been checked, so the reproduction does not re-cover it

Requirement 13.4 names four items. Two more are added from §5.7's reading, and each carries the
file that holds it — because "checked" and "asserted" are different standings and the difference
matters when the investigation gets handed to someone else.

| Checked | Standing | Held by |
| --- | --- | --- |
| The legacy utility classes `accent-cyan`, `text-muted`, `accent-cyan-dim` resolve | **Asserted.** They are aliases onto `--color-brand-*` / `--color-content-*` in `tokens.css`'s alias block, and `dead-tailwind.test.js` fails on any class that does not resolve in the built CSS | `tokens.css` alias block; `dead-tailwind.test.js` (needs a build) |
| `ScreenshotComingSoon.jsx`'s `Screenshot Coming Soon` is unreachable | **Asserted, twice over.** Nothing imports it, and `no-placeholders.test.js` uses it as its non-vacuity fixture — the guard's own proof that its scan works | `no-placeholders.test.js` |
| `DownloadSection`'s installer links were withdrawn to `ds/Panel`'s `unavailable` state | **Asserted.** Every card states unavailable with its declared reason, no request is issued — proved at the hook and at both surfaces with `fetch`, `XMLHttpRequest` and `HTMLAnchorElement.click` all recording | `tests/unit/pages/downloadSurface.test.jsx` |
| Pricing is INR-only, four tiers, no currency toggle, no `getPlans` call, annual arithmetic cannot throw | **Asserted.** This was a real crash once — the section formatted whatever `GET /api/billing/plans` returned and a null price threw on `toLocaleString` — and the prices are now declared in the component | `tests/unit/landing_page_pricing_crash_regression.test.jsx` |
| The 13-section tree mounts in jsdom without throwing | **Asserted.** `describe('Full Landing Page Integration')` | same file |
| The `#pricing`, `#architecture` and `#waitlist` anchors exist | **NOT asserted.** The three assertions are `expect(container.querySelector(...)).toBeDefined()`, which passes on `null` | nothing — see §5.7 |

That last row is the one to carry into the reproduction. Three of the four claims in the
end-to-end test cannot fail, and a missing in-page anchor is one of Requirement 13.3's five
candidate symptoms. It is corrected as step 1 below, before anything else, because it costs one
line each and it changes what the rest of the investigation can assume.

### 8.2 Decision D8 — the reproduction runs cheapest-first, and each step's outcome is a fork

Five steps, in this order. The order is chosen so that each step is cheaper than the next and so
that a positive result at any step stops the sequence.

**Step 1 — repair and run the existing end-to-end test, scoped.**

```
node node_modules/vitest/vitest.mjs --run tests/unit/landing_page_pricing_crash_regression.test.jsx --fileParallelism=false
```

Before running, change the three `toBeDefined()` assertions on `#pricing`, `#architecture` and
`#waitlist` to `not.toBeNull()`.

| Outcome | What it means | Next |
| --- | --- | --- |
| Passes | The 13-section tree mounts, the three anchors exist, pricing is intact. **A render failure and a missing-anchor navigation failure are both ruled out in jsdom.** Requirement 13.3's five candidates reduce to three: a layout fault at a specific viewport, a missing or 403 asset, incorrect content | step 2 |
| Fails on an anchor | A navigation failure, found for the cost of one line. File it under Requirement 13.6 with this test as the regression test — it fails before the fix and passes after, which is exactly what 13.6 asks for | stop |
| Fails on mount | A render failure, and the stack names the section. File under 13.6 | stop |

**Step 2 — mount each of the 13 sections in isolation.** A throw-free tree does not mean a
throw-free section: `LandingPage.jsx` may render a section inside a boundary, or a section may
render nothing when a prop is absent and nothing is what the visitor reports. One `it` per
section, asserting the section renders at least one element carrying its own text.

| Outcome | What it means | Next |
| --- | --- | --- |
| All 13 render content | The fault is not "a section is missing". Combined with step 1, the symptom is presentational or environmental rather than structural | step 3 |
| One renders empty | That section is the subject. A section that renders nothing is indistinguishable from a broken page to a visitor, and it is invisible to step 1 because an empty `<section>` throws nothing | stop, file under 13.6 |

**Step 3 — ask the requester.** Requirement 13.1 wants a URL, a viewport, a browser and the
observed symptom, and 13.5 makes this a legitimate place to arrive. Steps 1 and 2 exist to make
the question specific: *the tree mounts, all 13 sections render content, the anchors exist and
pricing is intact — so which of a layout fault at a viewport, a missing asset, or wrong content
is it, and at what width?*

Asking third rather than first is deliberate. Steps 1 and 2 cost one scoped test run and one new
test file, and they either find the fault or make the question answerable in one exchange. Asking
first risks a second round of "it looks broken".

**Step 4 — the deployed bundle, if the requester's answer points there.** `HEAD` on the
referenced asset paths; check whether the reported symptom is a 403 like the four installers
were. `.github/workflows/06-frontend-deploy.yml` deploys this surface, and
`downloadSurface.test.jsx`'s header records the mechanism that produced the last asset failure:
`aws s3 sync dist/ --delete` deletes any prefix `dist/` does not carry, so a directory CI never
builds is removed from the bucket on every deploy. That is a *class* of fault this surface has
already had once, and it is invisible to every test in the repository.

**Step 5 — a viewport sweep, if the answer points there.** `DesktopOnlyOverlay` gates the app
below 1000px and the landing surface is outside that gate, so the landing page is the one surface
a visitor can reach on a phone. Requirements' out-of-scope item 6 keeps mobile layout out of this
spec — but *identifying* a mobile layout fault is Requirement 13's job even though fixing it is
not, and the distinction has to be recorded rather than assumed.

### 8.3 The standing hypothesis, and how it is settled

Requirements §1.11 records it: the requester recalls two recent changes — six missing imports plus
an unused `useNavigate`, and the INR-only pricing conversion — and both are recorded against
`src/pages/Landing.jsx`, which is routed nowhere.

**If that is where they landed, they changed nothing a visitor sees**, and "still broken" is the
expected outcome of a correct fix applied to a dead file. This is the first thing to check because
it is the cheapest and because it would explain the report completely:

```
git log --oneline -- algo22-terminal/src/pages/Landing.jsx
git log --oneline -- algo22-terminal/src/components/landing/
```

Two commits touching the first path and none touching the second, in the relevant window, settles
it. The INR conversion is partially settled already: `landing_page_pricing_crash_regression.test.jsx`
asserts the *live* `Pricing.jsx` is INR-only with no toggle, so that half of the work did reach
the live surface. Whether the import fixes did is the open half.

It reads as a diagnosis and it is not one. It explains why a *fix* had no effect; it says nothing
about what the original symptom is. Even fully confirmed, steps 1–3 still have to run.

### 8.4 What a failed reproduction means

Requirement 13.5 is explicit and this design holds to it: **a reproduction that fails is a
result, not a blocker.** If steps 1 and 2 pass and the requester cannot describe a state that
reproduces, the recording says so — the tree mounts, the sections render, the anchors and pricing
are intact, and no symptom was reproduced at the viewports and browsers tried — and Requirements
14 and 15 proceed on their own merits. They do not depend on the outcome:

- Requirement 14 is dead-code removal. It is true that eight components are imported by nothing
  and that `pages/Landing.jsx` is routed nowhere, whatever the landing symptom turns out to be.
- Requirement 15 is a token migration on 14 files, explicitly scoped by 15.4 to the token layer
  only, with content and layout changes filed separately and gated on 13.

What must **not** happen on a failed reproduction is the pass inventing a defect list. §1.2 said
it and it is the reason Requirement 13 was written as a reproduction requirement: static reading
of all 14 rendered sections found no render-blocking fault, and the honest version of that is a
recorded null result rather than a rewrite of a working page.

**[JUDGEMENT] on how much reproduction is enough.** There is no principled stopping point for
"we looked hard enough". The proxy: steps 1 and 2 are executed and their results recorded, the
`git log` check in §8.3 is recorded, and the requester has been asked once with the specific
question step 3 produces. Three recorded outcomes and one specific question is the bar. It cannot
prove no fault exists. It can prove the investigation was not skipped, which is what Requirement
13.1's recording is for.

---

## Interactivity, honestly

The request that opened this spec asked for the product to be "interactive and minimalist". Those
words point in two directions and one of them this codebase has already refused. This section
separates the improvements that are real from the decoration that would be a regression, and it
starts from what the design system permits rather than from what the words suggest.

### 9.1 Decision D9 — the motion policy already exists, and this pass adds nothing to it

Checked before proposing anything that moves, as it must be:

- `src/styles/tokens.css` ends with a global `@media (prefers-reduced-motion: reduce)` block
  collapsing `animation-duration` and `transition-duration` to `0.01ms !important` and pinning
  `animation-iteration-count` to `1`. `src/index.css` records that the block "used to sit here"
  and now lives in `tokens.css`, declared identically.
- `src/styles/ds.css` carries a second, narrower block under a header section titled
  `REDUCED MOTION (Requirement 18.5)`, which states that the global block "works by collapsing
  `animation-duration` to 0.01ms" and that this is "the right blunt instrument" for one class of
  animation but not for `.ds-skeleton`, whose shimmer it replaces with `background-image: none`.
- The only declared motion tokens are `--transition-fast: 120ms` and `--transition-base: 180ms`,
  both `cubic-bezier(0.4, 0, 0.2, 1)`. There is no declared `--animate-*` of any kind:
  `tokens.css`'s alias block records that `--animate-gradient-sweep` and `--animate-shimmer` were
  not carried forward because they have zero call sites, and that `--animate-pulse-glow` was not
  carried forward because "a coloured glow … Requirement 1.5 retires rather than preserves".
- `ds/EmptyState.test.jsx` asserts it at the component level:
  `renders a static icon with no animation and no injected keyframes`, whose comment records that
  "the legacy EmptyState bobbed its icon forever via an inline `<style>@keyframes float` and
  halved its opacity". Its sibling assertion,
  `is not a live region — an empty panel is a resting state, not an event`, is the same decision
  applied to announcements.

**Decision.** This pass proposes no animation, declares no `--animate-*` token, and adds no
transition beyond `--transition-fast` / `--transition-base` on properties already transitioning.
A disclosure opens and closes; it does not slide. **Alternative considered: a height transition on
the §9.2 disclosure**, which is the single most conventional piece of motion in the entire idiom.
Rejected: it needs a declared token that does not exist, `tokens.css` is the only place one could
be declared, and Requirement 22.3 makes that its own app-wide commit — for a 180ms ease on one
element. The cost is out of proportion and the reduced-motion block would collapse it anyway for
the readers most likely to need the disclosure.

### 9.2 Real improvement 1 — progressive disclosure on Live Trading

`LiveTrading.jsx:1324`'s `REGISTRY_CAVEAT` is **384 characters** across four sentences and
`:1332`'s `SELECTION_CAVEAT` is **242**, and both render unconditionally as `<p>` elements above
the deployment list at `:2712` and `:2720`. Together they are 626 characters standing between a
trader and the answer to "what is running".

The content is correct and it is safety-relevant. `LiveTrading.jsx:114` records why the first one
exists: the endpoint reports the deployments the backend process currently holds in memory, so one
started by an earlier process — before a restart, or on another worker — can be absent, and the
file's own words are that the response is "to stop the list from being read as exhaustive". A
trader who reads the list as complete can double-deploy. Requirement 7.3 is precise about the
remedy and this design adopts it without extending it:

| Stays visible, always | Moves behind a disclosure |
| --- | --- |
| The operative sentence: **a deployment that is absent from this list is UNKNOWN, not stopped.** | Why the list can be incomplete — the in-process registry, the restart, the other worker. |
| The operative half of `SELECTION_CAVEAT`: selecting a deployment re-scopes some things below and not others. | Which four things it re-scopes and which three it does not. |

Mechanism: `ds/Accordion`, or `ds/Tooltip` on the operative sentence. Both exist, both are
keyboard-operable, and §5.5 names the assertions that already prove it —
`Accordion.test.jsx`'s `defaults to closed, and defaultOpen is the only way out` reading
`aria-expanded`, and `Tooltip.test.jsx`'s five focus and Escape cases. Requirement 17.3 forbids a
new primitive and none is needed.

**Two hard constraints on the edit, both already asserted.** `liveTrading.test.jsx:1041` asserts
`[data-selector-caveat="in-process-registry"]` appears exactly once, so the attribute moves onto
whatever renders the caveat rather than being dropped — and rewriting that assertion to tolerate
zero is the failure Requirement 7.5 names. And Requirement 19.6: the explanation stays reachable
and unchanged in substance. Moving is permitted; paraphrasing it shorter is not.

**Where this may not go.** `LiveTrading.jsx:2574` records that the empty rendering deliberately
uses `ds/EmptyState` as a *child* rather than `ds/Panel`'s `empty` state, because `empty` renders
no children and the panel would then say "nothing is running" with the sentence explaining why
that may be false suppressed. That is the same trap one level up, already avoided once in this
file. A disclosure that is closed in the `empty` state reproduces it. So: **in the states where
the list is empty or partial, the operative sentence renders expanded.** The disclosure collapses
only where there are rows and the list is complete.

### 9.3 Real improvement 2 — one next action on an empty account

`Dashboard.jsx` renders 7 `ds/Panel` instances, 6 of which carry an empty branch. On a fresh
account all 6 resolve to `empty` simultaneously, at equal weight, with no element marked as the
next thing to do. Requirement 8.1 asks for exactly one primary action and 8.2 caps simultaneous
empty panels at 3.

The primitive already enforces the copy half. `ds/EmptyState.test.jsx`'s
`renders what is missing, why it matters and the next action` and
`throws in development for each missing field, naming the one that is missing` mean every empty
panel on the page already names an action — the problem is that six of them do, equally.
Requirement 8.3's distinction is enforced too, by `requires a clear-filters action for no-match`
and the two-variant `EMPTY_VARIANTS` assertion: "you have none of these" and "a filter is hiding
them" cannot be collapsed at the primitive level.

So the work is hierarchy, not copy:

- One panel's action is primary. The other five keep their action and lose the emphasis —
  `ds/CommandButton`'s non-primary treatment, or a text link, both declared.
- Panels beyond the first three collapse. `ds/Accordion` again, or `ds/Panel`'s own collapsed
  presentation if it has one; if it does not, the panels are ordered so the three that matter on a
  fresh account are the three rendered, which is a `pageHierarchy` question and not a new
  mechanism.
- Requirement 8.3's `empty`-versus-`unavailable` distinction survives verbatim. A panel that is
  empty because the trader has nothing yet is not a panel that could not be read, and Requirement
  19.4 forbids collapsing them "in pursuit of fewer states on screen". Reduced weight is not
  collapse: the state name, its copy and its action all stay.

**Which panel is primary is a [JUDGEMENT] and its proxy is a count, not an opinion.** The proxy
is Requirement 8.5's: `tests/unit/pages/freshAccount.test.jsx` renders each page against a
zero-data fixture and asserts the number of visible empty panels and the number of primary
actions. `dashboard-tier1.test.jsx` already carries the zero-data fixture (§5.5), so the harness
exists. The test does not decide which action is right; it makes "exactly one" checkable. Which
one it is goes to the requester.

### 9.4 Real improvement 3 — direct manipulation where a page only reports

The third improvement is the one most likely to become a Requirement 16 violation, so its boundary
is drawn before any candidate is named.

**Permitted:** operating an existing control from where its effect is visible. A `ds/DataTable`
row that already navigates on click, reached by keyboard — which is precisely §7.4's Marketplace
fix, and the one instance of this improvement this pass actually ships. A `ds/FilterBar` control
that already exists moved above the table it filters. An existing sort made reachable from the
column header.

**Forbidden:** anything that issues a request the page does not issue today, changes which
endpoint it calls, or adds an affordance for an action that does not exist. Requirement 16.2 and
16.4 are unconditional, and 16.5 puts every `ds/ConfirmDialog` out of reach: a confirmation is
friction on purpose and "remove complexity" does not reach it. Inline editing of a risk limit,
a drag to resize a position, a swipe to halt — all three are new functionality wearing an
interaction-design word.

**The test that separates them:** does the change alter the set of requests the page can issue?
`api-paths.budget.js` answers it mechanically, and Requirement 18.3 makes the answer absolute —
that budget must not change at all, so a diff to it means 16.2 was violated. A direct-manipulation
change that leaves it untouched is presentational. One that touches it is out of scope regardless
of how good it is.

### 9.5 What this pass declines to build

Named so the omissions are decisions rather than oversights, and each with the file that already
refused it.

| Declined | Refused by |
| --- | --- |
| Coloured glows, on any surface | `tokens.css`'s elevation block — *no coloured glows. Calm by default (Req 1.5)* — and `vyomquant-ui-redesign` §3.2. §7.3 removes the one that survived. |
| Gradients on a trading surface | Requirement 10.1, and `tokens.css` declares no gradient of any kind |
| An animated empty state | `ds/EmptyState.test.jsx`'s `renders a static icon with no animation and no injected keyframes` |
| A live region on a resting state | `ds/EmptyState.test.jsx`'s `is not a live region — an empty panel is a resting state, not an event` |
| Any new `--animate-*` token | `tokens.css`'s alias block, which declined three of them on the record |
| A skeleton shimmer anywhere new | `ds.css`'s `REDUCED MOTION (Requirement 18.5)` section, which had to special-case the one that exists |
| Hover-only affordances | `ds/Tooltip.test.jsx`'s `opens on focus and closes on blur` and its header: "A hover-only tooltip passes every 'does it appear' test written with a mouse and is unreachable for a trader who does not use one" |

**The honest summary of this section.** Of the three improvements, one ships as part of work
already scheduled (§7.4's keyboard-activatable cards), one is a placement change to existing copy
(§9.2), and one is a hierarchy change to existing panels (§9.3). None of them is interactivity in
the sense the word usually carries. That is the finding: the product's problem is not that it does
too little when touched, it is that it says too much before being touched. "Minimalist" is the
half of the request with work behind it, and the interactive half is satisfied by making the
controls that already exist reachable from where their effect shows.

---

## Testing Strategy

The per-class coverage analysis — which file is each change class's safety net, what it proves,
and the seven new files or assertions five of those classes need — is §5. The one guard this pass
writes is specified in §3. This section is how each of them is run, given that no production
bundle can be produced.

### 10. Verification without a build

Disk is constrained and no production bundle can be produced during this pass. Requirement 18.5
forbids an unscoped run in any case — `production-launch-hardening` records the frontend suite
exceeding 25 minutes — so every check below is scoped to a named file.

### 10.1 The command form

```
node node_modules/vitest/vitest.mjs --run <path> --fileParallelism=false
```

Run from `algo22-terminal/`, in PowerShell. Three parts, each with a recorded failure behind it:

- **`node node_modules/vitest/vitest.mjs`, never `npx`.** `npx` swallows stdout, so a count read
  through it is not a count. `production-launch-hardening`'s tooling block records it in those
  words and applies the same rule to `node node_modules/eslint/bin/eslint.js`.
- **`--run <path>`, never the bare suite.** Always one named file. The bare suite exceeds 25
  minutes, which is why even that spec's own duration measurement is taken in scoped batches and
  summed.
- **`--fileParallelism=false`, explicitly.** `algo22-terminal/vitest.config.js` already sets
  `fileParallelism: false` and `maxWorkers: 1`, because parallel fork workers fail their startup
  handshake on constrained hosts and whole files then silently never run. The render-heavy
  `tests/unit/ds` files depend on it. It is passed explicitly rather than trusted, so that no CLI
  override reintroduces the contention.

For lint counts: `node node_modules/eslint/bin/eslint.js src`. Note that
`tests/unit/guards/a11y-ratchet.test.js` runs ESLint in-process over `src/pages/**` and
`src/components/ds/**` at import time, so it is the one guard with a real execution cost rather
than a file-read cost. It is also the only one that measures rather than declares its numbers,
which is why it is worth the cost.

### 10.2 Per change class, what verifies it scoped

| Class | Scoped command | What it establishes |
| --- | --- | --- |
| The ratchet (commit 1) | `--run tests/unit/guards/absolute-font-sizes.test.js` | The seed matches the tree, and §3.3's self-tests prove the four patterns fire and the exclusions hold. Green on the first commit by construction (Requirement 22.4) |
| Typography, per page | `--run tests/unit/guards/absolute-font-sizes.test.js` plus that page's own test file | The count fell, and the page still renders. **Not** that anything looks right (§5.1) |
| The badge (commit 2) | `--run tests/unit/ds/TradingEnvironmentBadge.test.jsx`, then `tests/unit/liveTrading.test.jsx`, `tests/unit/dashboard-kill-switch.test.jsx` | Both null arms still differ, neither resolved, accessible names intact. Three files because the badge reaches eight pages and six primitives (§4.2) |
| `ds/Chart.jsx` (commit 3) | `--run tests/unit/ds/Chart.test.jsx` | The tick and axis-label sizes read from the token |
| a11y scope (commit 4) | `--run tests/unit/guards/a11y-ratchet.test.js` | Every waiver's count is the measured count; `ds/**` at zero, at `error` |
| Marketplace (5–7) | `--run tests/unit/pages/strategyMarketplace.test.jsx`, then `a11y-ratchet.test.js`, then `tests/unit/design/subscriptionState.test.js` | Cards keyboard-activatable, four failure classes distinguished, the waiver deleted, `subscriptionState` not reimplemented |
| Page migrations (8–21) | `--run` that page's test file, then `no-colour-literals.test.js` and `absolute-font-sizes.test.js` | Both budgets lowered in the same commit (Requirement 22.2) |
| Dead code (22–23) | `--run tests/unit/guards/no-placeholders.test.js`, then `no-colour-literals.test.js` | §5.7's two couplings: the retained fixture, and the three deleted budget entries |
| Landing (24+) | `--run tests/unit/landing_page_pricing_crash_regression.test.jsx`, then `tests/unit/pages/downloadSurface.test.jsx` | The tree mounts, pricing is INR-only, the installer withdrawal holds |
| Prose and hierarchy (last) | `--run tests/unit/guards/standing-prose.test.js`, then `tests/unit/pages/freshAccount.test.jsx`, then `tests/unit/liveTrading.test.jsx` | The rendered count fell, one primary action, the caveat's `data-selector-caveat` attribute survived |
| Every commit | `git diff --name-only` | No path under `src/api/`, `src/websocketClient.js`, `backend_app/` (Requirement 16.7) or `aerora_quant_platform/frontend_app/algo22-terminal` (Requirement 21.3); no change to `api-paths.budget.js` (Requirement 18.3) |

### 10.3 `dead-tailwind.test.js` reports green having checked nothing

This is the one guard that cannot run from a clean checkout, and the way it fails is worth stating
precisely because it is the failure mode most likely to be mistaken for a pass.

It compares the class names `src/` asks for against the classes the built stylesheet actually
contains, reading every `.css` under `dist/assets/`. With no build, `STYLESHEETS` is empty,
`HAS_BUILD` is false, and its `needsBuild()` helper calls `ctx.skip(SKIP_NOTE)` on each of its
tests with the one-line reason `no dist/assets/*.css — run \`npm run build\` first`. Its header is
explicit about why it skips rather than fails: a missing artefact is not a Requirement 1.1
violation, and `npm test` must not silently require `npm run build`. It also says why it does not
pass quietly — "which would let a CI job that forgot to build report green on a guard that checked
nothing".

**So running it unbuilt produces a green file and `6 skipped`.** The skip notice travels with each
test so the number is never unexplained, but a reader scanning for red sees green.

`.github/workflows/01-pr-check.yml`'s `frontend-tests` job runs `npm run build` immediately before
the unit tests, so CI always gets the real check. During this pass, locally, it does not.

**No other guard shares that property.** Every other file under `tests/unit/guards/` reads `src/`
or the repository root and never `dist/`:

| Guard | Reads | Can it be vacuously green? |
| --- | --- | --- |
| `dead-tailwind.test.js` | `dist/assets/*.css` + `src/` | **Yes — skips with a notice** |
| `no-colour-literals.test.js` | `src/{pages,components,lib}` | No. Asserts every scanned file is inside a root and that the budget names only files that exist |
| `no-placeholders.test.js` | same | No. `finds the placeholder that is really in the tree` is its fixed point, plus `SCANNED.length > 100` and one-file-per-root |
| `no-native-dialogs.test.js` | same | No. Same shape of structural assertions |
| `no-local-tokens.test.js` | `src/` | No. Asserts the deleted shim is really absent |
| `a11y-ratchet.test.js` | runs ESLint over `src/` | No. `lints at least the primitives this spec builds` — `> 20` files and two named ones — exists because, in its own comment, "a guard that measured nothing would pass vacuously" |
| `tokens.generated.test.js` | `src/styles/tokens.css`, `src/design/tokens.js`, the generator | No. Asserts all three files exist before comparing |
| `api-paths.test.js` | `src/api/` | No |
| `nav-contract.test.js` | `src/` shell files | No. Asserts each named file exists before reading it |

`absolute-font-sizes.test.js` must carry the same non-vacuity assertion, and §3.5 already supplies
it: running the four patterns over the three roots reproduces every one of requirements §1.1's
per-page numbers exactly. That reproduction is the guard's fixed point and it belongs in the file
as an assertion rather than in this document as a claim.

### 10.4 What genuinely requires a build, and is therefore deferred

Stated as a list so nothing on it is later mistaken for verified.

1. **Class resolution.** `dead-tailwind.test.js`. This pass rewrites class names on 14 files plus
   Marketplace plus the landing surface, which is the largest class-name churn since the redesign,
   and it is exactly the churn the guard exists for. Its own header records nine live dead classes
   found the day it landed — seven of them on `StrategyMarketplace.jsx`, arbitrary-value classes
   with the opacity slash missing, so the hero glow, both notice banners' wash and the subscribe
   and clone buttons' hover state rendered nothing. **CI is where this pass gets checked.** Every
   commit must reach `01-pr-check.yml` before it is called verified.
2. **Requirement 2.1's actual behaviour.** Whether text scales when the browser default moves from
   16px to 20px. jsdom applies no CSS, so this needs a real browser. The ratchet proves no px value
   remains; it cannot prove the rem values render proportionally.
3. **Requirement 2.5's overflow question.** A rendered-geometry question at a viewport. §2.5's
   three-part proxy is what stands in for it and §5.8 records the limitation.
4. **§7.6's option A.** Whether `--text-*: initial` breaks 434 call sites is only visible in built
   CSS. This is a substantive reason to prefer option B, independent of the requirement-shape
   arguments.
5. **Requirement 13's steps 4 and 5.** The deployed bundle and the viewport sweep. Both need
   something this pass cannot produce, which is why §8.2 puts them after the requester's answer.

**What does *not* need a build, and is sometimes assumed to.** Token staleness:
`tokens.generated.test.js` compares `src/design/tokens.js` against `src/styles/tokens.css` through
the generator, all three in `src/`, and `package.json`'s `prebuild` runs
`scripts/gen-tokens.mjs --check` as a second gate. A token edit that forgets `npm run tokens`
fails scoped, immediately, with a message naming the staleness.

---

## Corrections to requirements.md

Consolidated so the seven corrections sections 1–4 reported sit in one place. Each is a
**[RE-MEASURED]** finding against an approved document, recorded rather than applied silently, and
each needs the requester's acknowledgement before the change it affects lands.

| # | Clause | What it says | What the tree says | Affects |
| --- | --- | --- | --- | --- |
| 1 | §1.5, Req 2.3 | `:477` is one of four 9px label styles (with `:433`, `:459`, `:502`) | `:477` is `tdStyle` — an **11px table cell**, not a 9px label. §2.6 classifies it at `:469` as a table cell at `--text-small`, and the 9px style at `:478` is `tableCaptionStyle` | Req 2.3's label set is three, not four. The fourth 9px label is the `Explainer` eyebrow at `:751`, which no clause names |
| 2 | §1.1, §1.5, Req 1.4 | `PaperTrading.jsx` carries 43 absolute sizes | **51.** A fourth syntax exists — `fontSize={9}` as a JSX prop, on recharts axes at `:3197`, `:3198`, `:3263`, `:3264`, `:3308`, `:3309`, `:3346`, `:3347`. It is the only file in `src/` with any. Req 1.2's three named syntaxes do not reach it | §3.2 adds a fourth pattern; the seed entry is 8 short. Req 2.4's "41 of 43 at or below 11px" is right about the 43 and short of the file's real below-floor total, which is 49 |
| 3 | Req 1.3 vs Req 1.4 | The guard shares `no-colour-literals`' helper and construction (which scans `pages`, `components`, `lib`), and is seeded at 18 entries | Both cannot hold. Eleven files under `components/` carry **155** sizes with no seed entry: `SupportCenter` 69, `DeploymentConsole` 19, `NotificationCenter` 17, `ResearchConsole` 17, `common/primitives` 12, `FirstTradeWizard` 7, `DeployPreflightPanel` 5, `waitlist/WaitlistForm` 5, **`ds/Chart` 2**, `download/DownloadPage` 1, `admin/AdminDashboard` 1 | §3.5. `ds/Chart.jsx:722`/`:729` is one declaration behind every chart on eleven pages; narrowing the roots to match the seed would leave it permanently invisible. Req 1.4's seed extends by eleven entries |
| 4 | Req 1, §1.3 | The 451 absolute sizes are the typography debt | `src/` also carries **434** occurrences of Tailwind's built-in `text-xs`/`text-sm`/`text-base`/`text-lg`/`text-xl`/`text-2xl`, outside tests — 41 in `StrategyMarketplace.jsx`, 43 in `ScreenshotsSection.jsx`. `tokens.css:14`'s `@theme static` does not reset the `--text-*` namespace, so v4's defaults survive beside the seven steps. They **do** scale with the reader (Req 2.1, 2.2) and are **not** `Type_Scale` steps (Req 1.2) | §3.6 blind spot 2, §7.6's OPEN DECISION O1. Req 10.4's 30 addresses 30 of Marketplace's **71** off-scale size declarations |
| 5 | Req 1.1 vs Req 2.3/2.4 | 1.1: a call site not in the scale moves to the **nearest step**. 2.3/2.4: resolution is by **role** | They disagree on real call sites. `:2355`'s `'1.25rem'` page `<h1>` goes *down* to `section` under nearest-step and *up* to `page` under role. `:2882`'s `fontSize: 16` is equidistant between `title` and `section` and nearest-step has no answer. `:434` and `:2885` are both 9px and three steps apart under role | §2.2's Decision D1: **role governs**; nearest-step is a tie-break of last resort for an undeterminable `value` tier, and ties resolve up |
| 6 | §1.8 | The badge treatment reaches 6 pages and 4 primitives, including `SignalTrace` | **8 pages** — `Dashboard` 4, `LiveTrading` 9, `PaperTrading` 5, `Portfolio` 3, `Strategies` 2, `StrategyDetail` 1, `StrategyMarketplace` 2, `TradeHistory` 4 — and **6 primitives**: `ds/Alert` 3, `ds/ConfirmDialog` 3, `ds/PageHeader` 4, `ds/Panel` 2, `ds/SectionHeader` 1, `ds/StrategyStatus` 4. **`SignalTrace.jsx` references it nowhere.** Via `ds/Panel` and `ds/PageHeader` it reaches effectively every page in `In_Scope_Pages` | §4.2. Strengthens the case for the badge going first rather than weakening it |
| 7 | §1.5, Req 2.3 | The five multi-sentence explanations are at `:2887`, `:3285`, `:3409`, `:3436`, `:3490` | All five are **two lines earlier**: `:2885`, `:3283`, `:3407`, `:3434`, `:3488`. The strings and the argument are right; the citations are off by two | §2.6's cohort table uses the measured lines. Req 2.3 names call sites by number, so the clause needs correcting or it points at the wrong lines |

**One further off-by-one, found while designing §7 and recorded here for completeness.** §1.6 and
Requirement 10.3 place `StrategyMarketplace.jsx`'s `blur-3xl` glow at `:1034`. It is at
**`:1033`** — `:1034` is the `relative z-10` wrapper below it. The three gradients at `:665`,
`:667` and `:1032`, the `onClick` div at `:664`, `console.error(err)` at `:381` and the single
error string at `:382` are all cited correctly.

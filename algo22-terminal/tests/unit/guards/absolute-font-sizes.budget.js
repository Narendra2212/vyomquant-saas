/**
 * The `absolute-font-sizes` decreasing budget — retail-ui-simplification task 1.2.
 * Requirements 1.3, 1.4, 1.5, 22.2, 22.4. design.md §3.1, §3.4, §3.5, §3.6.
 *
 * ===========================================================================
 * WHAT THIS FILE IS
 * ===========================================================================
 * A checked-in count, per file, of how many absolute **pixel** font sizes that
 * file is still allowed to declare. `absolute-font-sizes.test.js` measures the
 * real counts with task 1.1's four patterns and asserts each file matches its
 * entry **exactly**:
 *
 *   * over budget  -> a size was added to a file that is supposed to be
 *                     shrinking. Express it as a `Type_Scale` step instead.
 *   * under budget -> sizes were removed and this file was not updated. Lower
 *                     the number here in the same commit, or delete the entry
 *                     if the file reached zero (see THE INVERSION below).
 *
 * The second half is the point, and `no-colour-literals.test.js`'s header states
 * why: under a `<=` assertion a contributor could clear forty sizes, leave the
 * budget high, and the next contributor could put forty back without CI
 * noticing. Progress has to be *recorded* to count, not banked.
 *
 * ===========================================================================
 * OPEN DECISION O1 — RESOLVED: option B, px-only
 * ===========================================================================
 * design.md §7.6 flagged O1 as needing the requester's decision before this
 * budget was seeded, because the seed's *shape* depends on the answer. The
 * decision is **option B: this budget counts absolute pixel sizes only.**
 *
 * The rationale, and it is a distinction between two different faults rather
 * than a convenience:
 *
 *   * The **630 absolute pixel sizes** this file governs break accessibility.
 *     A device-pixel value ignores the reader's browser font-size preference
 *     outright — a trader who raised their default for presbyopia sees no
 *     change on any of the 29 files below. That is Requirement 2.1's clause,
 *     and §1.3 is the measurement behind it.
 *   * Tailwind's **434 built-in `text-xs` / `text-sm` / `text-base` / …**
 *     occurrences (§3.6 blind spot 2) DO scale: `text-xs` is `0.75rem`, it
 *     honours the reader's preference, and it clears the 11px floor. What it is
 *     not is one of the seven `--text-*` steps. That makes it an
 *     **inconsistency** — a second size vocabulary — and not an accessibility
 *     defect. It fails Requirement 1.2 and passes Requirement 2.1.
 *
 * Requirement 1 conflates those two properties; §3.6 says so. This ratchet
 * measures the first one exactly and does not measure the second one at all.
 *
 * **Scope is px-only, and that is a scope decision rather than a verdict on the
 * built-ins.** A second budget over the built-in classes can be added later
 * without redoing this one: it would be a different map, keyed the same way,
 * measured by a different pattern, and every number below would stand
 * unchanged. **The decision is therefore reversible** — reversing it widens the
 * guard, it does not invalidate the seed.
 *
 * ===========================================================================
 * HOW TO CHANGE IT
 * ===========================================================================
 * Lowering a number is routine and belongs to the task that touched the file —
 * the failure message tells you the new value and prints the distribution that
 * is left. Raising a number, or adding an entry to a file that had none, means a
 * new absolute size entered the tree; that reverses Requirement 1.1 and needs a
 * reason in the PR description.
 *
 * ===========================================================================
 * THE INVERSION: AN ENTRY THAT REACHES ZERO IS DELETED — Requirement 1.5
 * ===========================================================================
 * This is the one place this budget does NOT mirror `no-colour-literals`. That
 * budget keeps `pages/Dashboard.jsx: 0` and deletes an entry only when the file
 * is deleted. Here, **an entry that reaches zero is removed from this map in the
 * same commit that clears the file.** Requirement 1.5's argument is that a file
 * with no entry is a file held at zero by default, and it is correct.
 *
 * It works mechanically, and it is worth spelling out *which assertion* holds a
 * cleared file, because the inversion moves it:
 *
 *   * Colour: the entry stays at `0`, and `holds every file at or below its
 *     budget` fails a reintroduction.
 *   * Font size: the entry is gone, and `accounts for every file that still
 *     carries an absolute size` fails a reintroduction — as an *unbudgeted*
 *     file.
 *
 * Both hold the line. The inversion buys one real thing: the in-scope /
 * out-of-scope split that `no-colour-literals` needed at its task 27.3 is
 * unnecessary here, because a deleted entry already means "held at zero" and a
 * surviving entry already means "still carries debt". No second map, no
 * disjointness assertion, no `it.skip`.
 *
 * It costs one thing, and the cost is paid in the unbudgeted failure message.
 * For a file that was previously cleared, **re-adding an entry here is the thing
 * being prevented** — so that message distinguishes the two cases it now covers
 * (a size came back into a cleared file; versus a genuinely new file that
 * arrived carrying debt) and gives a different remedy for each. See
 * `absolute-font-sizes.test.js`.
 *
 * ===========================================================================
 * SEEDED FROM THE TREE ON THE DAY TASK 1.2 LANDED — measured, not estimated
 * ===========================================================================
 * Requirement 22.4 requires this guard to be green on its first commit, seeded
 * at today's counts. It is, and these are the counts, produced by *running* task
 * 1.1's four patterns over the three roots rather than by transcribing a table:
 *
 *   136 files scanned. **29 carry at least one absolute pixel size. 630 total.**
 *
 *     src/pages/       14 files, 459 sizes
 *     src/components/  15 files, 171 sizes   (4 landing + 11 others)
 *     src/lib/          0 files,   0 sizes
 *
 * 459 + 171 = 630, and 14 + 15 = 29 entries. Every file that carries a size has
 * an entry; no file without one does.
 *
 * ===========================================================================
 * WHAT HAS BEEN CLEARED SINCE — the running account Requirement 1.5 asks for
 * ===========================================================================
 * The seed above is the tree on the day task 1.2 landed and is left as written,
 * so it stays checkable by re-running the scan against that commit. Each entry
 * that has since reached zero is DELETED from the map and recorded here instead —
 * which is where a deleted line's history has to live, because the map no longer
 * has a line to carry it.
 *
 *   29 entries / 630 sizes  — seeded, task 1.2
 *   28 entries / 628 sizes  — `components/ds/Chart.jsx` (2) cleared by task 3.2:
 *                             `:722`'s `TICK` and `:729`'s `AXIS_LABEL_STYLE` now
 *                             read `token.text.micro`. The first entry to leave
 *                             this map, and the first to leave by clearing rather
 *                             than by file deletion.
 *   27 entries / 598 sizes  — `pages/StrategyMarketplace.jsx` (30) cleared by task
 *                             5.2. The largest single clearance so far and the
 *                             first whole PAGE to leave the map. All 30 were
 *                             `text-[Npx]`; see the deleted entry's note below for
 *                             where each cohort went, and for the 41 built-in
 *                             `text-*` classes that left with them without being
 *                             in scope.
 *   26 entries / 582 sizes  — `pages/RiskSettings.jsx` (16) cleared by task 7.2,
 *                             the first of Requirement 4.5's ten page migrations
 *                             and the first entry to leave this map in the same
 *                             commit as a colour entry reaching zero. Nine of the
 *                             sixteen left by moving their call site onto a
 *                             primitive that already reads a declared step
 *                             (`ds/PageHeader`, `ds/CommandButton`, `ds/Alert`,
 *                             `ds/Panel`'s `empty` arm); seven resolved in place.
 *   25 entries / 570 sizes  — `pages/TwoFA.jsx` (12) cleared by task 7.3. Five of
 *                             the twelve left with the block that became `ds/Alert`
 *                             or `ds/LoadingState` or `ds/CommandButton`; seven
 *                             resolved in place, and TWO of those seven are
 *                             recorded findings about §2.3's role set rather than
 *                             clean matches — a TOTP secret is an IDENTIFIER and
 *                             there is no identifier role, and `control text`'s
 *                             step had to be read as a floor to stop a 6-digit
 *                             one-time code shrinking 41%. Both arguments are in
 *                             the page's header.
 *   24 entries / 563 sizes  — `pages/SecurityLogs.jsx` (7) cleared by task 7.4. Six
 *                             of the seven left by moving onto `ds/Metric`,
 *                             `ds/DataTable` or `ds/Panel`'s error arm; one resolved
 *                             in place. It carried the **8px `<th>`** — the smallest
 *                             text in the tree outside `PaperTrading.jsx`, two pixels
 *                             below the floor, naming the columns of a security audit
 *                             log — which is the call site §2.4 orders Q4 ahead of Q6
 *                             for: a `<th>` is a label, and `ds/DataTable` already
 *                             renders one at `--text-micro`.
 *   23 entries / 560 sizes  — `pages/UpdatePasswordPage.jsx` (3) cleared by task
 *                             7.5. The smallest file in the tree and the first
 *                             clearance in this spec with NO colour entry beside it:
 *                             the page was already at zero literals, so task 7's step
 *                             5 was satisfied by the *absence* of a new entry rather
 *                             than by a number coming down. Two of the three sizes
 *                             were the same construct twice — a sentence in a
 *                             hand-built coloured strip — and both became `ds/Alert`.
 *
 * **The "25 entries" figure in circulation is wrong; the number is 29.** It is
 * the arithmetic of 14 pages + §3.5's eleven `components/` additions, which
 * silently drops the four `components/landing/` files Requirement 1.4 already
 * seeded — those four are under `components/`, which is why `components/` reads
 * 15 files and not 11. Resolved by measurement: **29 entries totalling 630.**
 *
 * ===========================================================================
 * TWO CORRECTIONS TO REQUIREMENT 1.4'S SEEDED LIST — design.md §3.5
 * ===========================================================================
 * Requirement 1.4 seeds 18 entries: the 14 `src/pages/` files plus
 * `ScreenshotsSection` 12, `Hero` 2, `DownloadSection` 1, `HowItWorks` 1. Both
 * corrections below are carried here rather than applied silently, because they
 * change a seeded list in an approved requirements document.
 *
 * **1. `pages/PaperTrading.jsx` measures 51, not 43.** The difference is eight
 * `fontSize={9}` recharts axis props (`:3197`, `:3198`, `:3263`, `:3264`,
 * `:3308`, `:3309`, `:3346`, `:3347`) — a fourth syntax none of Requirement
 * 1.2's three named patterns reaches. `FONT_SIZE_PROP` exists for it and this is
 * the only file in `src/` with any.
 *
 * **2. Eleven files under `components/` carry 155 sizes with no seeded entry.**
 * They are seeded below. Requirement 1.3 mandates the colour guard's three roots
 * and Requirement 1.4's list covers two of them, so the two clauses cannot both
 * be satisfied by the list as written. Left unseeded, `accounts for every file
 * that still carries an absolute size` would fail on this guard's own first
 * commit, which Requirement 22.4 forbids. Narrowing `SCAN_ROOTS` to match the
 * list instead was considered and rejected in §3.5: it would leave
 * `ds/Chart.jsx:722` permanently invisible, and that one declaration sets the
 * axis tick size on every chart on all eleven migrated pages.
 *
 * Nothing in this spec is scheduled to lower most of the eleven, and that is
 * fine — **a ratchet has a resting position.**
 * `no-colour-literals.budget.js`'s header says the same about
 * `pages/ExchangeManager.jsx: 128`. Each entry below records whether a task owns
 * it or whether it is resting.
 *
 * ===========================================================================
 * ONE FILE THAT MEASURES SIZES AND GETS NO ENTRY
 * ===========================================================================
 * `components/ds/SectionHeader.jsx` measures **2 before comment stripping and 0
 * after**, because `:17`'s docblock records the `fontSize: 13` / `fontSize: 10`
 * values `PanelTitle` used to hardcode. It gets no entry, and it is §3.3's
 * argument already standing in the tree: Requirement 2.4 requires every migrated
 * call site to carry a note naming the size it came from, so without comment
 * blanking a file that documented its own migration exactly as required would
 * measure the count it started with, and the only way to pass would be to delete
 * the record. `ds/Chart.jsx`'s prose docblocks (`:291`, `:294`, `:719`, `:726`)
 * were the same case — its entry of `2` was the two live declarations only, and
 * task 3.2 has since moved both onto `token.text.micro` and added exactly the
 * Requirement 2.4 notes this section is about, so that file now measures 4 before
 * stripping and 0 after and its entry is gone.
 *
 * ===========================================================================
 * WHAT THIS RATCHET CANNOT SEE — design.md §3.6, quoted so it is not trusted
 * past its limits
 * ===========================================================================
 * Four blind spots. A guard whose limits are undocumented gets trusted past
 * them. The second is the one that matters.
 *
 * **1. Off-scale relative sizes.** `fontSize: '1.25rem'`
 * (`pages/PaperTrading.jsx:2355`, the page `<h1>`) is 20px, is not one of the
 * seven steps, and carries no `px` — so no pattern matches it. It satisfies
 * Requirement 2.1 (it scales with the reader) and violates Requirement 1.1 (a
 * size declared outside the token layer). Counting px only is the mirror
 * Requirement 1.3 asks for; widening to "any unit not resolving to a declared
 * step" means this guard reading and parsing `tokens.css`, which is a materially
 * different guard. The proxy is narrower and honest: **that one site is the only
 * off-scale rem in the tree**, it is named here, task 8.1 clears it, and
 * `no-local-tokens.test.js` is the guard that owns "a page declared its own
 * value". If a second off-scale rem appears, this blind spot is the reason and it
 * is written down here.
 *
 * **2. Tailwind's built-in size classes — 434 of them, and explicitly out of
 * scope per O1 above.** `src/` carries 434 occurrences of `text-xs` / `text-sm`
 * / `text-base` / `text-lg` / `text-xl` / `text-2xl` and their siblings outside
 * tests, across 30 files — including 52 in `components/admin/AdminDashboard.jsx`,
 * 43 in `components/landing/ScreenshotsSection.jsx` and **41 in
 * `pages/StrategyMarketplace.jsx`**. They resolve because `tokens.css:14`'s
 * `@theme static` block declares the seven steps without resetting the namespace
 * (`--text-*: initial`), so Tailwind v4's defaults are extended rather than
 * replaced. This is a gap in Requirement 1 rather than in the guard:
 * `text-xs` scales, clears the floor, and is not a step, so it passes this
 * ratchet and fails Requirement 1.2. Requirement 10.4's "the 30 `text-[Npx]`
 * classes SHALL move onto the `Type_Scale`" therefore addresses 30 of
 * Marketplace's **71** off-scale size declarations. The remedy is either a
 * `--text-*: initial` reset in `tokens.css` (a token-layer change, its own commit
 * per Requirement 22.3, immediately exercised by `dead-tailwind.test.js`) or a
 * second budget over the built-in classes. Both are bigger than this spec's
 * remit.
 *
 * **3. Files outside the three roots.** `src/App.jsx` carries **6** absolute
 * sizes (`:229` 22, `:233` 13, `:251` 12, `:319` 12, `:327` 16, `:330` 11) and
 * sits in none of `pages`, `components` or `lib`, so neither the colour guard nor
 * this one reaches it. Requirement 1.2 scopes its rule to `src/pages/` and
 * `src/components/landing/`, so `App.jsx` is outside the requirement as well as
 * outside the guard. Recorded rather than fixed: widening `SCAN_ROOTS` to `src/`
 * root files is a change to the shared `no-colour-literals` scan decision, and
 * Requirement 17.5 puts that in the other spec's territory.
 *
 * **4. Computed sizes.** `style={{ fontSize: someNumber }}` is invisible to any
 * source scan. There are none in the tree today and there is no proxy for the
 * future; recorded so the absence is a measurement rather than an assumption.
 *
 * ===========================================================================
 * SCOPE
 * ===========================================================================
 * `src/pages/**`, `src/components/**`, `src/lib/**` — the colour guard's three
 * roots, per Requirement 1.3 — over `.js`, `.jsx`, `.ts`, `.tsx`, `.css`,
 * excluding tests and excluding `TOKEN_LAYER_FILES`. A file whose job is to
 * declare font sizes cannot be policed by a rule that forbids them. The scan and
 * its reasoning live in `absolute-font-sizes.test.js`.
 */

/**
 * How many absolute pixel font sizes each file may still declare.
 *
 * Paths are relative to `src/`, forward-slashed, matching the spec's notation.
 * Each entry carries its measured distribution — §2's migration procedure reads
 * the number as evidence about the role a size is playing, so the distribution is
 * the part that shortens the work — and the task that clears it, or `RESTING` if
 * no task in this spec is scheduled to lower it.
 *
 * **An entry that reaches zero is deleted, not set to `0`.** See THE INVERSION.
 */
export const ABSOLUTE_FONT_SIZE_BUDGET = Object.freeze({
  // -- src/pages/ — 14 files, 459 sizes. Requirement 1.4's seed, with
  //    PaperTrading corrected from 43 to 51 per §3.5. -------------------------

  // 10×27, 11×13, 9×10, 12×7, 13×7, 8×4, 14×4, 16×4, 18×1, 20×1, 24×1.
  // The largest single-file count in the tree, and 54 of the 79 are at or below
  // 11px. Task 7.8 (commit 14) clears it, alongside 51 inline `monospace`
  // declarations — the second-largest such block.
  'pages/Profile.jsx': 79,
  // 10×17, 12×15, 11×11, 14×9, 24×3, 9×2, 16×2, 8×1, 13×1. Second largest.
  // The page is already at zero colour literals, so task 8.3 (commit 21) is
  // almost entirely §2.4's size procedure.
  'pages/StrategyDetail.jsx': 61,
  // 12×14, 13×14, 11×10, 24×5, 14×3, 16×3, 10×2, 18×1. Task 7.10 (commit 16),
  // which also carries this page's 128 colour literals — 48% of that budget's
  // remaining total and the entry its header names as a resting position.
  'pages/ExchangeManager.jsx': 52,
  // 9×31, 11×10, 10×8, 8×1, 16×1. **51, not Requirement 1.4's 43** — §3.5's
  // first correction. The eight extra are `fontSize={9}` recharts axis props and
  // this is the only file in `src/` carrying that syntax. 49 of the 51 are at or
  // below 11px and 31 are at 9px, which is Requirement 2.3/2.4's subject: the
  // page renders multi-sentence explanatory prose below the 11px floor. Task 8.1
  // (commit 19) clears it, including the `'1.25rem'` at `:2355` that blind spot
  // 1 cannot see.
  'pages/PaperTrading.jsx': 51,
  // 12×9, 11×6, 9×4, 13×4, 14×4, 32×4, 10×3, 15×2, 20×2, 18×1, 22×1, 36×1.
  // **This entry is deleted, not lowered, by task 9.2 (commit 23), which deletes
  // the file.** `Landing.jsx` carries its own `DEPRECATED / UNMOUNTED` header and
  // is routed nowhere — `App.jsx:44` lazy-imports `components/landing/LandingPage`
  // and `:590` routes that at `/`. Counting 41 sizes against a file nothing
  // renders spends the ratchet on nothing (Requirement 14.2). Until then the
  // entry holds, because the file is in the tree and the guard reports what is
  // there. `names only files that still exist` is the assertion that fails if the
  // file goes and this line stays.
  'pages/Landing.jsx': 41,
  // 11×15, 12×10, 10×5, 9×2, 13×2, 18×1, 20×1, 22×1, 24×1, 36×1. Task 7.9
  // (commit 15). Money figures, so every one also gets a `pageFields.js` entry.
  'pages/Billing.jsx': 39,
  // `pages/StrategyMarketplace.jsx` was here at 30 — 10×16, 9×9, 11×5, all of
  // them `text-[Npx]` Tailwind arbitrary values, which was the whole of
  // Requirement 10.4. **Task 5.2 (commit 5) took it to 0 and the entry is DELETED
  // rather than set to 0** (Requirement 1.5): the page is held at zero from here
  // by `accounts for every file that still carries an absolute size`, which fires
  // on it as unbudgeted if a size comes back. Where the 30 went, by role: chips
  // and figure labels to `--text-micro`, and the four SENTENCES up to
  // `--text-body` — the environment description, the per-condition rows, the
  // empty-condition account and the historical-results statement, which was set
  // 2px BELOW the page default while saying that past figures do not predict
  // future ones. Blind spot 2's further 41 built-in `text-*` classes on this page
  // — the 41 of 71 O1 left out of scope — went with them, because the figures now
  // render through `ds/Metric` and the hero through `ds/PageHeader`, and both
  // read declared steps. This page is the first evidence that O1's option C
  // leaves less behind than it looks: migrating a page to the primitives clears
  // the built-ins as a side effect of clearing the arbitrary values.
  // 9×9, 11×8, 10×7, 12×3, 20×1, 22×1. Task 7.11 (commit 17) — the largest page
  // in Requirement 4.5's group at 1,181 lines, which is why it goes last.
  'pages/AuthPage.jsx': 29,
  // 11×7, 10×4, 18×4, 9×3, 16×2, 8×1, 12×1, 14×1, 24×1. Task 7.6 (commit 12).
  // A first-run surface, so §2.4's Q1 carries more here than anywhere: a wizard
  // is mostly sentences and sentences go to `--text-body` or larger.
  'pages/Wizard.jsx': 24,
  // `pages/RiskSettings.jsx` was here at 16 — 12×10, 13×4, 10×1, 24×1 —
  // Requirement 4.5's first page, smallest first, so the convention's fit was
  // tested on 438 lines before it was tested on 1,181. **Task 7.2 (commit 8) took
  // it to 0 and the entry is DELETED rather than set to 0** (Requirement 1.5): the
  // page is held at zero from here by `accounts for every file that still carries
  // an absolute size`, which fires on it as unbudgeted if a size comes back, and
  // re-adding this line is not the remedy. Where the 16 went, by §2.4's questions:
  // the page `<h1>` (24) to `--text-page` and the subtitle (13) to `--text-body`
  // through `ds/PageHeader`; the two command labels (12×2) and the toast (12) and
  // the empty-allocations sentence (12) to the primitives that own their step
  // (`ds/CommandButton`, `ds/Alert`, `ds/Panel`'s `empty` arm), so those four
  // declarations are gone rather than mapped; the three limit NAMES (12×3) to
  // `--text-micro` by Q4 and their three FIGURES (13×3) to `--text-title` by Q7,
  // which is `ds/Metric`'s own label/figure pair; the kill-switch name (12) to
  // `--text-title` by Q8 and its explanation (10) to `--text-body` by Q1, +30% on
  // the one sentence that says what a guard does before a trader arms it; the
  // strategy card's name (12) to `--text-title` and its allocation (12) to
  // `--text-body`. Two of the sixteen are shrinks and both are Q4 answered
  // literally rather than a layout yield — §2.5's tell is a shrink standing beside
  // the overflow it avoids, and there is none. The same commit took this page's
  // colour entry from 53 to 0; see `no-colour-literals.budget.js`.
  // 14×12, 10×2, 16×1. The only file in the tree whose sizes are **all** the
  // quoted `fontSize: 'Npx'` syntax, so it is the one page that exercises
  // `FONT_SIZE_QUOTED` end to end. Task 7.7 (commit 13).
  'pages/LegalPage.jsx': 15,
  // `pages/TwoFA.jsx` was here at 12 — 10×5, 11×4, 12×1, 20×1, 22×1. **Task 7.3
  // (commit 9) took it to 0 and the entry is DELETED rather than set to 0**
  // (Requirement 1.5). Where the 12 went: the `<h1>` (20) to `--text-page` by Q8;
  // four sentences (11, 10×2 and the step instruction) to `--text-body` by Q1, one
  // of which — *QR code unavailable. Please use the secret key below.* — was the
  // smallest text on the screen at the moment it mattered most; *Manual Setup Key:*
  // (10) to `--text-micro` by Q4; the error line, the success line, the loading line
  // and both footer commands (11×2, 11, 10×2) onto `ds/Alert`, `ds/LoadingState` and
  // `ds/CommandButton`, so five declarations are gone rather than mapped. The last
  // two are the ones worth reading the page's header for: the TOTP secret (12) went
  // to `--text-title` as a `value` because §2.3 HAS NO IDENTIFIER ROLE and Q9 says
  // to record the gap rather than guess, and the six code inputs (22) went to
  // `--text-page` because `control text`'s `body` is the floor the role was added to
  // establish, not a cap — read as a cap it would have shrunk a one-time code 41%
  // inside a 56px box. This page's 10 inline `monospace` declarations also went to 2
  // (the secret and the six inputs keep it; eight prose and label sites lose it),
  // and its colour entry went 12 -> 0 in the same commit.
  // `pages/SecurityLogs.jsx` was here at 7 — 9×2, 11×2, 8×1, 10×1, 18×1. **Task 7.4
  // (commit 10) took it to 0 and the entry is DELETED rather than set to 0**
  // (Requirement 1.5). Six of the seven left with their call site: the summary label
  // (9) and figure (18) onto `ds/Metric`, the error banner (11) onto `ds/Panel`'s
  // error arm, and the `<table>` (10), the `<th>`s (8) and the device cell (9) onto
  // `ds/DataTable`. The seventh, the search input (11), resolved in place to
  // `--text-body` by Q3. The 8px `<th>` is the one worth naming: two pixels below the
  // floor, on the headers of a security audit log, and it is exactly why §2.4 asks Q4
  // before Q6 — a `<th>` is a label, not a cell. This page's colour entry went 3 -> 0
  // in the same commit, and two off-palette constructs the colour guard cannot see
  // went with them: `${token.line.default}15`, a token with two hex digits
  // concatenated on, and `placeholder:text-slate-600`.
  // `pages/UpdatePasswordPage.jsx` was here at 3 — 12×1, 13×1, 22×1 — the smallest
  // file in the tree at 79 lines and the cheapest test of the convention's fit.
  // **Task 7.5 (commit 11) took it to 0 and the entry is DELETED rather than set to
  // 0** (Requirement 1.5). The `<h1>` (22) went to `--text-page` by Q8; the success
  // (13) and failure (12) messages were the same construct twice — a sentence in a
  // hand-built coloured strip — and both went to `ds/Alert`, so two of the three
  // declarations are gone rather than mapped.
  //
  // THIS IS THE ONE CLEARANCE IN THIS SPEC WITH NO COLOUR ENTRY BESIDE IT. The file
  // was already at zero literals, so task 7's step 5 is satisfied by the *absence* of
  // a new entry in `no-colour-literals.budget.js` rather than by a number coming down
  // — which is worth recording, because "the colour half did nothing" and "the colour
  // half was skipped" look identical in a diff. Two off-palette constructs neither
  // guard can see did go: `${token.status.profit.fg}12`, a token with two hex digits
  // concatenated on (a hand-mixed 7% alpha carrying no `#`, so HEX_LITERAL never
  // matched it), and `borderRadius: 18`, which is off the declared radius scale
  // entirely — `--radius-xl` stops at 12.

  // -- src/components/landing/ — 4 files, 16 sizes. Requirement 1.4's seed,
  //    unchanged. All 16 are `text-[Npx]`. Task 11.1 takes them one commit per
  //    file and **deletes** each entry at zero. Requirement 15 is P2, so these
  //    four may rest for a while; the entry is what holds them meanwhile. ------

  // 10×6, 11×6. Blind spot 2 also counts 43 built-in `text-*` classes here —
  // the second-largest concentration in `src/` — and those are out of scope.
  'components/landing/ScreenshotsSection.jsx': 12,
  // 9×1, 10×1. Plus 33 built-ins, out of scope per O1.
  'components/landing/Hero.jsx': 2,
  // 11×1. Its unavailable-download cards keep their declared reasons.
  'components/landing/DownloadSection.jsx': 1,
  // 11×1.
  'components/landing/HowItWorks.jsx': 1,

  // -- src/components/ — seeded at 11 files / 155 sizes, now **10 files / 153**:
  //    `ds/Chart.jsx` was the only one of the eleven a task owned and task 3.2
  //    cleared it, so its entry is deleted per THE INVERSION. §3.5's second
  //    correction: these carry debt today and Requirement 1.4 seeds none of them.
  //    The ten that remain are a ratchet's resting position — budgeted so they
  //    cannot grow, with no promise in this spec that they shrink. ------------

  // 11×31, 10×17, 12×7, 9×6, 13×3, 16×2, 8×1, 14×1, 18×1. The largest count
  // under `components/` and the fourth largest in the tree. Trader-reachable,
  // and one of the two page-local `C` objects the previous redesign recorded as
  // G4. RESTING — no task in this spec lowers it.
  'components/SupportCenter.jsx': 69,
  // 9×6, 12×6, 11×3, 14×2, 10×1, 18×1. RESTING.
  'components/DeploymentConsole.jsx': 19,
  // 12×6, 11×5, 13×3, 10×1, 15×1, 22×1. The other G4 file. RESTING.
  'components/NotificationCenter.jsx': 17,
  // 9×6, 18×5, 11×3, 14×2, 10×1. RESTING.
  'components/ResearchConsole.jsx': 17,
  // 9×4, 10×4, 12×2, 13×1, 18×1. A shared primitive module, so each size here is
  // rendered by every caller. RESTING.
  'components/common/primitives.jsx': 12,
  // 13×3, 11×2, 16×1, 18×1. The first-run surface a new retail account sees
  // first, which is the reason §3.5 rejected narrowing the roots. RESTING.
  'components/FirstTradeWizard.jsx': 7,
  // 9×4, 10×1. Four of the five are at 9px, below Requirement 2.2's floor.
  // RESTING.
  'components/DeployPreflightPanel.jsx': 5,
  // 10×5, all `text-[Npx]`. RESTING.
  'components/waitlist/WaitlistForm.jsx': 5,
  // `components/ds/Chart.jsx` was here at 2 — `:722`'s `TICK` and `:729`'s
  // `AXIS_LABEL_STYLE` (§3.5 calls the second one `AXIS_LABEL`; the file does
  // not), two declarations behind the axis labels of every chart on all eleven
  // migrated pages, which is why this file was in scope at all and why the roots
  // were not narrowed. Task 3.2 moved both to `token.text.micro` and **deleted
  // the entry** rather than setting it to 0 (Requirement 1.5). The file is now
  // held at zero by `accounts for every file that still carries an absolute
  // size`: if a px size returns here, it fails as UNBUDGETED, and re-adding this
  // line is not the remedy.
  // 10×1, `text-[Npx]`. RESTING.
  'components/download/DownloadPage.jsx': 1,
  // 10×1, `text-[Npx]`. Blind spot 2 also counts 52 built-in `text-*` classes
  // here, the largest concentration in `src/`, and those are out of scope per O1.
  // RESTING.
  'components/admin/AdminDashboard.jsx': 1,
});

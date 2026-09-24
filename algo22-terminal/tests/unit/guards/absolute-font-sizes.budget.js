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
 *   22 entries / 536 sizes  — `pages/Wizard.jsx` (24) cleared by task 7.6. The
 *                             first-run surface, and the page where §2.4's Q1 did
 *                             the most work: SEVEN of the twenty-four were
 *                             sentences at or below the 11px floor and all seven
 *                             went UP to `--text-body`. Six more left with their
 *                             call site onto `ds/Metric`, `ds/LoadingState` and
 *                             `ds/CommandButton`, and one — the `/mo ($$…)` suffix
 *                             — was dead code hanging off a condition that was
 *                             structurally never true. It is also the clearance
 *                             that found the most INVENTED FIGURES of any page in
 *                             this spec: two hardcoded percentages (`+12.4%` and
 *                             `68.2%`) rendered over a response the page fetched
 *                             and threw away, a price read off `p.inr` and `p.usd`
 *                             — two keys `GET /api/billing/plans` does not carry,
 *                             so every plan advertised itself as "Free" with a
 *                             "Start Free" button while the paid ones went on to
 *                             open a paid checkout — and a green tick declared
 *                             `ok: true` against a notification setting nothing
 *                             reports. All three are now declared in
 *                             `design/pageFields.js` under a `wizard` page and
 *                             render through `design/reported.js`.
 *   21 entries / 521 sizes  — `pages/LegalPage.jsx` (15) cleared by task 7.7, and
 *                             the clearance that proves pattern 2 on real bytes:
 *                             **every one of the 15 was the quoted
 *                             `fontSize: 'Npx'` syntax**, which is true of no other
 *                             file in the tree, so a broken `FONT_SIZE_QUOTED`
 *                             would have seeded this entry at 0 and left the commit
 *                             measuring no change. Three left with their call site
 *                             onto `ds/PageHeader`, `ds/Tabs` and
 *                             `ds/CommandButton`; the other TWELVE were one cohort
 *                             — the `<h2>` section headings — and they went UP to
 *                             `--text-section` because the measurement is that
 *                             **they were SMALLER than the prose they headed**:
 *                             14px headings at preflight's inherited weight over
 *                             paragraphs that declare no size and so render at the
 *                             browser default 16px. `--text-title` is the exact
 *                             number and was refused for that reason. The prose
 *                             itself keeps NO declaration, which is the first time
 *                             in this spec that the right answer to §2.4 is to
 *                             declare nothing: Q1's `--text-body` is a 13px FLOOR,
 *                             the sentences are already reader-scaled above it, and
 *                             the step would have shrunk a legal document 19%.
 *   20 entries / 442 sizes  — `pages/Profile.jsx` (79) cleared by task 7.8. **The
 *                             largest single-file clearance in this spec and the
 *                             largest single-file count this budget was ever seeded
 *                             with**, on a 999-line page carrying 54 of its 79 sizes
 *                             at or below the 11px floor. Roughly half left with their
 *                             call site onto `ds/PageHeader`, `ds/Panel`, `ds/Metric`,
 *                             `ds/StatusBadge`, `ds/CommandButton` and `ds/Field`; the
 *                             rest resolved in place. The deleted entry below records
 *                             the three resolutions that carry an argument — the two
 *                             16px ties, the 24px that sized no text at all, and the
 *                             four 8px money labels whose +25% is paid for by a grid
 *                             reflow. It is also the page with the MOST substituted
 *                             figures in this spec: ELEVEN kinds, past `Wizard.jsx`'s
 *                             three, including a green *Configured* under MFA
 *                             AUTHENTICATION with no read behind it and a referral
 *                             code manufactured from the account's own UUID.
 *   19 entries / 403 sizes  — `pages/Billing.jsx` (39) cleared by task 7.9, and **the
 *                             first clearance in this spec to carry a SHRINK that is not
 *                             a mistake**: three call sites were outranking their own
 *                             container (a 24px `<h2>` equal to the page `<h1>`, a 22px
 *                             card name above its panel's 14px heading, and a 36px price
 *                             at a number the seven steps do not contain). 20 of the 39
 *                             left with their call site onto `ds/Alert`,
 *                             `ds/CommandButton`, `ds/Metric`, `ds/Panel` and
 *                             `ds/DataTable`; four of the 19 that stayed are §2.4's
 *                             one-call-site-two-roles case. It is the money surface
 *                             design.md §6 is written about and it had substituted zeros
 *                             in TEN places — including eight `|| 0`s over quota figures
 *                             whose genuine value on the free plan really is `0`, and an
 *                             invoice amount that put a dollar sign on every non-INR
 *                             invoice. The deleted entry below records all of it.
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

  // `pages/Profile.jsx` was here at 79 — 10×27, 11×13, 9×10, 12×7, 13×7, 8×4,
  // 14×4, 16×4, 18×1, 20×1, 24×1 — **the largest single-file count in the tree**,
  // with 54 of the 79 at or below 11px. **Task 7.8 (commit 14) took it to 0 and the
  // entry is DELETED rather than set to 0** (Requirement 1.5): the page is held at
  // zero from here by `accounts for every file that still carries an absolute size`,
  // which fires on it as unbudgeted if a size comes back, and re-adding this line is
  // not the remedy.
  //
  // Roughly half of the 79 left with their call site rather than being mapped,
  // because the step belongs to the primitive: the `<h1>` (20) and its subtitle (11)
  // onto `ds/PageHeader`; the hand-built loading block (13, 11) and the whole
  // seven-branch error screen (16, 13, 11) onto `ds/Panel`'s own arms; the four card
  // headings (14×4) onto `ds/Panel`'s `<h2>`; the platform and referral labels and
  // figures (18×1, 16×1, 13×4, 10×4, 8×4) onto `ds/Metric`; the verified and
  // subscription chips onto `ds/StatusBadge`; the command labels onto
  // `ds/CommandButton`; the four editor labels (10×4) onto `ds/Field`. Of the rest,
  // the Q4 eyebrow labels and Q5 chips went to `--text-micro`, the Q1 sentences and
  // Q3 control text to `--text-body`, and the two switch names to `--text-title` by
  // Q8 — the same Q8-over-Q4 ordering task 7.2 recorded for `RiskSettings`'s
  // identical label/description construct.
  //
  // Three resolutions carry an argument rather than a lookup. **Both 16px values are
  // §2.2's undefined case**: 16 is equidistant from `--text-title` (14) and
  // `--text-section` (18), so "nearest step" has no answer, and both are Q7 values
  // that are their panel's hero, so both resolve UP. **The 24px was not a font size
  // at all** — it sat on the 56px avatar circle, whose content is an `<img>` or a
  // 26px glyph carrying its own `size` prop, so the declaration is removed rather
  // than mapped. And **the four 8px referral labels are the layout-yield case**: they
  // were the smallest text on the page, two pixels below the floor, labelling two
  // counts and two money figures in a `repeat(4, 1fr)` grid inside the narrower
  // column, so at `--text-micro` (+25%) the grid becomes 2-up below `sm` and 4-up
  // above — §2.5's third mechanism, chosen over shrinking the figures, which is
  // §2.5's named tell for an illegal shrink.
  //
  // This is also the page that carried the MOST substituted figures in this spec —
  // ELEVEN kinds, past `pages/Wizard.jsx`'s three — and the worst single one found
  // anywhere in it: the *MFA AUTHENTICATION* tile rendered a green dot and the word
  // *Configured* as static JSX with no read behind it, so an account with no second
  // factor was told its second factor was on. All eleven are declared in
  // `design/pageFields.js` under a new `profile` page (24 entries) and render through
  // `design/reported.js`. The same commit took this page's colour entry from 7 to 0
  // and moved it out of the deferred block; see `no-colour-literals.budget.js`. Three
  // off-palette constructs neither guard can see went with them: `${token.brand.base}33`,
  // `${token.brand.base}15` and `${token.brand.base}40`, three tokens with two hex
  // digits concatenated on, which carry no `#` and so were never counted.
  // 10×17, 12×15, 11×11, 14×9, 24×3, 9×2, 16×2, 8×1, 13×1. Second largest.
  // The page is already at zero colour literals, so task 8.3 (commit 21) is
  // almost entirely §2.4's size procedure.
  'pages/StrategyDetail.jsx': 61,
  // `pages/ExchangeManager.jsx` was here at 52 — 12×14, 13×14, 11×10, 24×5, 14×3,
  // 16×3, 10×2, 18×1, with 50 of the 52 at or below 16px. **Task 7.10 (commit 16)
  // took it to 0 and the entry is DELETED rather than set to 0** (Requirement 1.5):
  // the page is held at zero from here by `accounts for every file that still
  // carries an absolute size`, which fires on it as unbudgeted if a size comes back.
  //
  // The same commit took this page's colour entry from **128 to 0** — the largest
  // single entry in `no-colour-literals.budget.js`, 48% of its remaining 268, and
  // the one its own header named as a ratchet's resting position. The full hue map
  // is on that entry.
  //
  // FORTY of the 52 left with their call site, because the step belongs to the
  // primitive: the `<h1>` and its subtitle (24, 14) onto `ds/PageHeader`; four
  // command labels (13, 12, 13, 13) onto `ds/CommandButton`; the four summary tiles
  // and the four per-card tiles, labels and figures alike (12×4, 24×4, 11×4, 12×4),
  // onto `ds/Metric`; four region headings (18, 16, 12, 12) onto `ds/Panel`'s `<h2>`;
  // six loading, empty and placeholder blocks (14, 16, 13×4) onto `ds/Panel`'s own
  // arms; the card's venue name and its status word (16, 12) onto
  // `ds/ExchangeStatus`; the credential label, four control texts and the field
  // description (11, 13×4, 10) onto `ds/Field`; and the toast (13) onto `ds/Alert`.
  // Of the 12 that resolved in place, six went to `--text-micro` as a label or a
  // decorative initial, four to `--text-body` as a Q1 sentence or a Q7 secondary
  // value, one to `--text-title`, and ONE DECLARATION WAS REMOVED rather than
  // resolved — the 200px centred spinner row that was the whole of the connections
  // loading state, now `ds/Panel`'s `skeleton-cards` arm.
  //
  // **The five 24px figures resolve UP to `--text-figure` (28), not down.** 24 is not
  // one of the seven steps, `tokens.css:74` reserves `--text-page` for the page
  // `<h1>` and `:75` reserves `--text-figure` for a tier-1 metric; on a page whose
  // subject is exchange connections the count of connections IS the headline figure,
  // so all four summary tiles are tier 1 and share the step. §2.2 forbids inventing
  // an eighth step to keep 24. **The three 16px values split by role**, which is why
  // the 14-versus-18 tie is not resolved once: two go to `ds/Panel`'s heading and
  // empty arm, and the card's venue name goes to `ds/ExchangeStatus`, which sets
  // `--text-body` on it — a shrink, legal under §2.5 because it is hierarchy, a card
  // heading inside a panel not outranking the panel's own `--text-title`.
  //
  // This is also the page that carried the MOST substituted figures in this spec —
  // THIRTEEN kinds, past `pages/Profile.jsx`'s eleven and `pages/Billing.jsx`'s ten —
  // and the worst of them is the one this spec keeps finding: a *Health* tile reading
  // "Healthy" in green whenever a row existed, i.e. an account-wide health grade
  // derived from WHETHER A ROW EXISTS, which is `can_trade`'s defect verbatim on the
  // client. FOUR of the columns `GET /api/exchanges/` returns are constants in the
  // router — `status` always "CONNECTED", `health` always "healthy", `account_type`
  // always "Spot", `subscription_tier` always "free" — and the page put a `||`
  // fallback on three of them, so an absent field and a hardcoded word rendered
  // identically. A **balance** was defaulted to `0` on both probe paths and a
  // clock-synchronisation result was invented for a field one of the two endpoints
  // does not send at all. All thirteen are declared in `design/pageFields.js` under a
  // new `exchange-manager` page (16 entries) and render through `design/reported.js`.
  // Thirty-nine off-scale constructs neither guard can see went with them, listed on
  // the colour entry; the fifteen hand-mixed alphas, unusually, WERE counted, because
  // this page spells them as eight-digit hex rather than as a token with two digits
  // concatenated on.
  // Seeded at 51 — 9×31, 11×10, 10×8, 8×1, 16×1 — **51, not Requirement 1.4's
  // 43**, §3.5's first correction. The eight extra are `fontSize={9}` recharts
  // axis props and this is the only file in `src/` carrying that syntax. 49 of
  // the 51 were at or below 11px and 31 were at 9px, which is Requirement
  // 2.3/2.4's subject: the page renders multi-sentence explanatory prose below
  // the 11px floor.
  //
  // **Now 41 — 9×26, 10×7, 11×7, 16×1. Task 8.1 slice 1 of 3 took the TEN
  // module-level style objects, and only those.** `labelStyle`, `thStyle`,
  // `tableCaptionStyle`, `stackedLabelStyle` and `PanelNotice`'s eyebrow (9×5)
  // are Q4 labels at `--text-micro`; `fieldStyle` (11) is Q3 control text at
  // `--text-body`; `tdStyle` and `stackedValueStyle` (11×2) are Q6 cells at
  // `--text-small`; `StatusPill` (10) and `SimulatedTag` (8) are Q5 chips at
  // `--text-micro`. Three of the ten were already at their step's value and did
  // not move. `SimulatedTag` is the layout-yield case — the only text on the page
  // two pixels below the floor, at 19 call sites — and it paid for +25% by losing
  // `whiteSpace: 'nowrap'` and moving `padding: '1px 5px'` onto the declared 4px
  // grid, with its `FlaskConical` glyph following to 10. Nothing shrank.
  //
  // **Now 18 — 9×8, 11×7, 10×2, 16×1. Task 8.1 slice 2 of 3 took the TWENTY-THREE
  // sentence call sites in the JSX body, and only those**: the seventeen 9px hints,
  // notes and footnotes, the five 10px report lists / tooltip body / prose blocks
  // including the page's one `role="alert"` line, and `:3367`'s mixed-role container.
  // Every one is Q1 → `--text-body`, a +30% to +44% growth on multi-sentence
  // explanations of safety-relevant behaviour — that a negative latency means the
  // clocks disagree and is shown as recorded, that no price is carried forward and
  // none is synthesised, that three series stay distinguishable without relying on
  // colour. These are Requirement 2.3's subject and the reason this spec exists; not
  // one word of any of them changed.
  //
  // `:3367` is §2.4's one-declaration-three-roles case and is why this budget counts
  // occurrences rather than elements: one `fontSize: 9` sat over two chart legend
  // labels and one sentence, so the CONTAINER lost its declaration and the children
  // took `--text-micro` (Q4) and `--text-body` (Q1) separately. One violation, three
  // resolutions. §2.5's yields: nothing here carried `whiteSpace: 'nowrap'`, so yield
  // 1 was already available and absorbs the growth; the two report lists additionally
  // took yield 2, moving `gap: 6` onto `space['2']` now that their rows wrap inside a
  // 220px track. Columns were NOT reduced and nothing shrank.
  //
  // **The entry stays**, because 18 is not zero. Slice 3 holds the rest: the eight
  // `fontSize={9}` recharts axis props (all 8 of the remaining 9s), the two 10px and
  // seven 11px call sites, `:2882`'s 16px latency figure, plus the two constructs the
  // patterns cannot see — `:707`'s `missing ? 13 : 18` conditional and the
  // `'1.25rem'` page `<h1>`, which blind spot 1 records.
  'pages/PaperTrading.jsx': 18,
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
  // `pages/Billing.jsx` was here at 39 — 11×15, 12×10, 10×5, 9×2, 13×2, 18×1,
  // 20×1, 22×1, 24×1, 36×1, with 32 of the 39 at or below 12px. **Task 7.9 (commit
  // 15) took it to 0 and the entry is DELETED rather than set to 0** (Requirement
  // 1.5): the page is held at zero from here by `accounts for every file that still
  // carries an absolute size`, which fires on it as unbudgeted if a size comes back.
  //
  // TWENTY of the 39 left with their call site, because the step belongs to the
  // primitive: the two notice strips and the payment-failure block including its
  // button (12, 12, 13, 12, 11) onto `ds/Alert`; the six remaining command labels
  // (11×5, 12) onto `ds/CommandButton`; an allowance label, its figure, the price and
  // the price's unit (11, 20, 36, 12) onto `ds/Metric`; the *Available Plans* heading,
  // *No payment methods on file.* and *Loading plans…* (18, 12, 12) onto `ds/Panel`'s
  // heading and its `empty` / `loading` arms; the `<table>` and its `<th>`s (11, 9)
  // onto `ds/DataTable`. Of the 19 that resolved in place, five went to
  // `--text-micro` as a label or a chip, eight to `--text-body` as a Q1 sentence or a
  // Q7 secondary value, and FOUR are §2.4's one-call-site-two-roles case — *Renews*,
  // *Cancels*, the allowance denominator and the card expiry each sat on a container
  // holding an eyebrow word and a figure, so the container lost its declaration and
  // the label took `--text-micro` while the figure took `--text-body`.
  //
  // **This page carries the only three SHRINKS in the group so far, and all three are
  // hierarchy rather than §2.5's illegal "shrink to avoid a layout change".** Each one
  // was outranking its own container. The current plan's name went 24 →
  // `--text-section`, because at 24 it equalled the page `<h1>` and `tokens.css:74`
  // reserves `--text-page` for that; each catalogue card's name went 22 →
  // `--text-title`, because a card heading inside a panel may not outrank the panel's
  // own heading, and the card's dominant element is its PRICE — the relationship the
  // old 22-over-36 pair already had, preserved at 14-over-28; and the price itself
  // went 36 → `--text-figure` (28), because 36 is not one of the seven steps, §2.2
  // forbids an eighth, and `tokens.css:75` reserves `--text-figure` for a tier-1
  // figure, which a plan price on a billing page is. The *Available Plans* heading
  // shrinks 18 → `--text-title` as a consequence of adopting `ds/Panel` rather than as
  // a mapping: three sibling regions on this page are panels, and one of the three at
  // `--text-section` would read as a section containing the other two.
  //
  // One declaration is REMOVED rather than resolved: *Loading plans…* (12) was this
  // read's entire loading AND failure state, so a 500 left it on screen forever.
  // `ds/Panel`'s `loading` arm is a skeleton and its `error` arm carries a retry; a
  // sentence that meant two different things does not survive as either.
  //
  // It is also the MONEY SURFACE design.md §6 is written about, and it had substituted
  // zeros in TEN places — second only to `Profile.jsx`'s eleven, and worse per figure
  // because every one of them is a price, a period or an entitlement. The clearest:
  // eight `|| 0`s over `usage.*`/`quotas.*` where the free plan's real bot, ML and
  // marketplace quotas are all literally `0`, so the fabricated zero and the genuine
  // zero were the same glyph; `formatPlanPrice` substituting `0` so an unreadable price
  // advertised itself as FREE (the same defect `Wizard.jsx` carried at task 7.6, on the
  // same catalogue); a hardcoded `$` in front of an amount the same line labelled as
  // another currency (`$2499 INR`); and an invoice amount whose else-arm put a dollar
  // sign on every non-INR invoice whatever it was denominated in, both arms `|| 0`, so
  // an unreadable amount rendered as a settled invoice for nothing. All ten are declared
  // in `design/pageFields.js` under a new `billing` page (27 entries) and render through
  // `design/reported.js`. The same commit took this page's colour entry from 9 to 0 and
  // moved it out of the deferred block; see `no-colour-literals.budget.js`. Fourteen
  // off-palette `${token.x.y}NN` constructs went with them — a token with two hex digits
  // concatenated on, carrying no `#`, so no guard ever counted one.
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
  // `pages/AuthPage.jsx` was here at 29 — 9×9, 11×8, 10×7, 12×3, 20×1, 22×1 — the
  // largest page in Requirement 4.5's group and therefore the last of it.
  // **Task 7.11 (commit 17) took it to 0 and the entry is DELETED rather than set
  // to 0** (Requirement 1.5): the page is held at zero from here by `accounts for
  // every file that still carries an absolute size`, which fires on it as
  // unbudgeted if a size comes back, and re-adding this line is not the remedy.
  //
  // Where the 29 went, by §2.4's questions. 24 of them were at or below 12px, so
  // almost every resolution is upward, which §2.5 calls the normal case. Eleven
  // resolved by moving onto a primitive that already reads a declared step —
  // `ds/Alert` ×2 (the failure and confirmation bands), `ds/CommandButton` ×4 (the
  // submit, the Google command, resend and back), `ds/StatusBadge` ×1 (the password
  // strength verdict), `ds/Field` ×1 (the inline email message), plus two that left
  // with a DUPLICATED Google command and its now-pointless divider, and one
  // container that held a sentence and two commands and so lost its declaration
  // while each child took its own step (§2.4's own worked example, on this page).
  // Eight sentences answered Q1 and went UP to `--text-body`: the header subtitle
  // (11), the terms-and-risk line (11), the code-entry label (10), *Passwords
  // match* (10), the confirm-mismatch message (10), the mode-toggle footer (10) and
  // the two password inputs (12, Q3 read as a floor). The `<h1>` (22) and the six
  // code boxes (20) both take `--text-page`, the second on task 7.3's finding that
  // `control text`'s step is a floor and not a cap — Q3 would have shrunk a
  // one-time code 35% inside a 54px box.
  //
  // FOUR RESOLUTIONS ARE FINDINGS ABOUT THE ROLE SET, and this is the page that
  // reached Q9 twice: **§2.3 has no role for connective text and none for a
  // navigation link.** The *OR* divider (10) and the four legal footer links (9×4)
  // answer none of the nine questions, so both are taken at the label step
  // (`--text-micro`) with the gap recorded rather than guessed. The two password
  // labels (9×2) take `--text-small` rather than Q4's `--text-micro` because they
  // sit beside a third label `ds/Field` renders at `--text-small` and a page commit
  // cannot move a shared primitive's step. And an IMPERATIVE label satisfies Q1's
  // finite-verb test, so *Enter 6-Digit Email Code* — a `<label htmlFor>`, the
  // canonical Q4 element — resolves as a sentence because Q1 is asked first and the
  // order is load-bearing.
  //
  // The same commit took this page's colour entry from 30 to 0 and moved it out of
  // the deferred block; see `no-colour-literals.budget.js`, where the three
  // gradients and Google's four brand colours are on the entry. TWELVE off-scale
  // constructs neither guard can see went with them: `borderRadius: 18` (the third
  // page in this group to carry the one number past `--radius-xl`) and
  // `borderRadius: 10`, four `transition: all` durations at 150ms ×3 and 200ms ×1,
  // and six `letterSpacing` values — including a NEGATIVE one on the `<h1>`.
  // `pages/Wizard.jsx` was here at 24 — 11×7, 10×4, 18×4, 9×3, 16×2, 8×1, 12×1,
  // 14×1, 24×1 — the first-run surface, so §2.4's Q1 carried more here than
  // anywhere: a wizard is mostly sentences and this one had every one of them at
  // or below the 11px floor. **Task 7.6 (commit 12) took it to 0 and the entry is
  // DELETED rather than set to 0** (Requirement 1.5): the page is held at zero
  // from here by `accounts for every file that still carries an absolute size`,
  // which fires on it as unbudgeted if a size comes back, and re-adding this line
  // is not the remedy.
  //
  // Where the 24 went, by §2.4's questions. Seven answered Q1 and went UP to
  // `--text-body`: the four step subtitles (11×4), *Backtest Complete!* (11), the
  // security row explanation (10, +30%) and the plan feature lines (10, +30%).
  // Six answered Q8: the four step headings (18×4) to `--text-section`, which is
  // 18px exactly, the plan name (14) to `--text-title`, also exact, and the
  // security row name (12) to `--text-title`. Three answered Q4 or Q5 at the
  // floor: the progress ordinal (11) and the RECOMMENDED ribbon (8, +25%) are
  // chips by Q5, the progress step label (9) is a label by Q4. Six left with
  // their call site rather than being mapped, because the step belongs to the
  // primitive: the two backtest figures (16×2) and their two labels (9×2) onto
  // `ds/Metric`, the plan price (24) onto `ds/Metric` at tier 1, *Processing
  // historical ticks…* (11) onto `ds/LoadingState`, and the six exchange buttons
  // (10) onto `ds/CommandButton`. The last one, the `/mo ($$…)` suffix (10), was
  // DEAD CODE — it hung off `priceINR > 0`, which was structurally never true —
  // so it is removed as dead code rather than as copy.
  //
  // Three resolutions carry an argument rather than a lookup. The progress ordinal
  // is a CHIP and not a label: it holds `1` or `✓` and does not name the step, so
  // Q4 misses it and Q5 catches it, and 11 → 10 is Q5 answered literally inside a
  // 32px circle rather than a layout yield. The step labels lose
  // `whiteSpace: nowrap`, which is §2.5's first and cheapest yield, so the +1px
  // has somewhere to go. And the plan price goes UP — 24 → `--text-figure` (28) —
  // with the four-up `repeat(4,1fr)` grid becoming 1 / 2 / 4 by breakpoint, which
  // is §2.5's third mechanism; reading it as tier 2 instead would have shrunk a
  // price 42% to avoid the reflow, which is §2.5's named tell for an illegal
  // shrink.
  //
  // The same commit took this page's colour entry from 5 to 0 and moved it out of
  // the deferred block; see `no-colour-literals.budget.js`. Two off-palette
  // constructs neither guard can see went with them: `borderRadius: 18` and
  // `borderRadius: 20`, both off the declared radius scale entirely
  // (`--radius-xl` stops at 12) — the same construct
  // `pages/UpdatePasswordPage.jsx` recorded at task 7.5.
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
  // `pages/LegalPage.jsx` was here at 15 — 14×12, 10×2, 16×1 — the only file in
  // the tree whose sizes are **all** the quoted `fontSize: 'Npx'` syntax, so it
  // is the one page that exercises `FONT_SIZE_QUOTED` end to end. **Task 7.7
  // (commit 13) took it to 0 and the entry is DELETED rather than set to 0**
  // (Requirement 1.5): the page is held at zero from here by `accounts for every
  // file that still carries an absolute size`, which fires on it as unbudgeted if
  // a size comes back, and re-adding this line is not the remedy. It is also this
  // guard's end-to-end proof of pattern 2: had `FONT_SIZE_QUOTED` been broken,
  // this entry would have seeded at 0 and that commit would have measured no
  // change at all.
  //
  // Where the 15 went. Three left with their call site — the page `<h1>` (16) onto
  // `ds/PageHeader`'s `--text-page`, the four tab buttons (10) onto `ds/Tabs`, and
  // the Back control (10) onto `ds/CommandButton`. The other TWELVE are one
  // cohort: the `<h2>` section headings inside the four documents, Q8 headings one
  // level below the page `<h1>`, resolved to `--text-section` (+29%).
  //
  // That cohort carries the one resolution on the page worth arguing, and the
  // argument is a measurement: **the headings were SMALLER than the text they
  // headed.** The twelve were declared at 14px while the paragraphs and list items
  // beneath them declare no size at all, so the prose renders at the browser
  // default — 16px — and Tailwind's preflight had reset the headings' weight to
  // `inherit` as well. What made those lines read as headings was the ALL-CAPS and
  // the brand hue, and Requirement 3.3 removes the first. `--text-title` (14px)
  // was therefore refused despite being numerically exact: it would leave every
  // section heading in a legal document below its own body text with the capitals
  // gone too. `--text-section` is the first step above the prose and is what
  // `pages/Wizard.jsx` gave its own `<h2>`s one commit earlier.
  //
  // The corollary is the reason this page clears 15 rather than 15-plus-the-prose:
  // **the sentences keep no size declaration.** Q1's `--text-body` is 13px and a
  // FLOOR, and this page's prose is already at the reader's own default and
  // already scales with their browser setting, so declaring the step would be a
  // 19% shrink of a legal document with no overflow to yield to. Text with no
  // declaration satisfies Requirement 2.1 completely; there was nothing here for
  // this guard to count and nothing for that commit to add.
  //
  // The same commit took this page's colour entry from 3 to 0 and moved it out of
  // the deferred block; see `no-colour-literals.budget.js`. Four off-palette
  // constructs neither guard can see went with them — a `backdropFilter:
  // blur(20px)` glassmorphic wash the token layer does not declare, and TWO tokens
  // with hex-alpha digits concatenated onto them (`${token.brand.base}15` and
  // `${token.line.default}80`), which carry no `#` and so were never counted, plus
  // 12 `textTransform: 'uppercase'` declarations resolved against Requirement 3.3.
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

# PHASE 10 — PHASE F: UI / DESKTOP READINESS CLOSURE

**Revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`, tracking `origin/main`)
**Remote:** `https://github.com/Narendra2212/vyomquant-saas.git`
**Date:** 2026-10-10
**Continues:** [`staging/PHASE10_UI_DESKTOP_READINESS.md`](PHASE10_UI_DESKTOP_READINESS.md)

**Live trading OFF.** No execution, exchange, billing, financial, authorization, database or
certification code was changed. No order was placed.
**Nothing committed, pushed, reset, cleaned, stashed or broadly reverted. The git index was
never touched.**

---

## 0. How to read this report

Claims are separated by the strength of their evidence:

| Marker | Means |
|---|---|
| **MEASURED** | Executed here; command and result recorded in §8. |
| **MUTATION-VERIFIED** | A test was shown to FAIL when the code it guards was broken, then restored. |
| **INFERRED** | Reasoned from source that was read. Not executed. |
| **NOT VERIFIED** | No evidence obtained. The blocker is named. |

This report is not itself evidence. Every verdict below points at a command in §8.

### 0.1 Two corrections to the previous report

Both were found by execution in this phase, and both ran against my own prior conclusions.

1. **The AccountMenu "defect" was mis-diagnosed.** The previous report stated that collapsing
   the sidebar moves the account trigger from 216px to 56px, requiring re-measurement.
   **MEASURED: false.** The sidebar is grid column 1, so its left edge is the viewport's at
   every width; collapsing changes the trigger's `width`, and `popoverPosition` reads only
   `rect.left` and `rect.top`. Neither moves. §3.3.
2. **The two dashboard failures were reported as "pre-existing". That was not established,
   and is not correct as stated.** At pristine HEAD they pass 3/3; with the working tree they
   fail 1/3. They are **load-sensitive flakes**, not a stable pre-existing failure and not a
   stable regression. §7.

---

## 1. Objective and scope

Close the four items left open by Phase D/E: browser-level AccountMenu coverage, workflow
validation, native build evidence, and the logo. Plus establish real ownership for the two
dashboard failures.

Out of scope and untouched: the 38-route polish, backend, database, billing, trading.

---

## 2. Baseline and ownership boundaries

**MEASURED.** Identical at entry and exit.

| Category | Count | Ownership |
|---|---|---|
| Staged | **22** | Other workstreams — untouched |
| Unstaged, frontend + workflows | 8 | 7 mine, **1 not mine** |
| Untracked, attributable to this task | 9 | listed in §9 |

### 2.1 Staged work preserved

All 22 staged paths are byte-identical at exit. `algo22-terminal/src/pages/ExchangeManager.jsx`
— named explicitly in the brief — still shows **55 insertions, 13 deletions**. Never opened
for modification.

### 2.2 The one unstaged file that is NOT this workstream's

`.github/workflows/01-pr-check.yml` carries **+143 unstaged lines from another workstream**.
It was already ` M` before Phase D began. **Not modified in this phase or any previous one.**
Recorded again because it appears in any `.github`-scoped diff and must not be attributed here.

`05-security.yml` is **staged** by another workstream and likewise untouched. Together these
are why the desktop workflow is a new file: both candidate hosts are occupied, and the brief
forbids modifying either without ownership confirmation, which was not obtained.

### 2.3 Infrastructure found

- **Playwright: present but unprovisioned.** `@playwright/test@^1.59.1` in `devDependencies`,
  `playwright.config.ts` with chromium/firefox/webkit projects, `testDir: ./tests/e2e`,
  `webServer: npm run dev` on port 1420. `tests/e2e/` existed and was **empty**.
  `~/AppData/Local/ms-playwright` did not exist — no browser binaries. Provisioned chromium
  only (§3.1).
- **AccountMenu:** `popoverPosition` (pure, line 262) and `measure` (line 296); constants
  `ACCOUNT_MENU_WIDTH_PX=232`, `VIEWPORT_GUTTER_PX=8`, `TRIGGER_GAP_PX=6`,
  `MIN_PANEL_HEIGHT_PX=160`. Existing unit coverage: `tests/unit/shell/accountMenu.test.jsx`,
  33 tests.
- **CI:** 7 workflows registered on the remote — 01–06 and Dependabot. **No desktop build
  workflow exists remotely.** Every job in 01–06 is `ubuntu-latest`.

---

## 3. AccountMenu browser regression

New file: `algo22-terminal/tests/e2e/accountMenuAnchoring.spec.ts` — 5 specs, real Chromium.

### 3.1 Provisioning

`npx playwright install chromium` — exit 0. Installs `chromium-1217`,
`chromium_headless_shell-1217`, `ffmpeg-1011`, `winldd-1007` into the **user cache**, not the
repository. No framework added: Playwright and its config were already committed; only its
browser binary was missing. Firefox and WebKit were deliberately not installed — one engine is
sufficient to read box geometry, and the download is ~400MB.

### 3.2 The intended observable behaviour

**INFERRED** from source, then **MEASURED**. `popoverPosition(rect, viewport)` reads exactly
four inputs and produces three inline `position: fixed` values:

```
left      = clamp(GUTTER, rect.left, viewportWidth - 232 - GUTTER)
bottom    = max(GUTTER, viewportHeight - rect.top + GAP)
maxHeight = rect.top - GUTTER - GAP
```

`rect.width` is **not** an input. That single fact drives §3.3.

### 3.3 Design: which output can a test actually hold?

This required two attempts, and the first one is worth recording because it was a test that
could not fail.

**Attempt 1 — rejected by mutation.** The spec shrank the window with the panel open and
asserted the gap between the panel's bottom edge and the trigger's top edge was preserved.
With `window.addEventListener('resize', sync)` deleted, **it still passed.** The arithmetic
says why: the trigger is the sidebar footer, pinned at `viewportHeight - 56`, so
`bottom = viewportHeight - (viewportHeight - 56) + 6 = 62` — **a constant at every viewport
height.** `left` is likewise invariant: `rect.left` is 0, so the clamp always yields the 8px
gutter. An assertion over either is vacuous.

**Attempt 2 — mutation-verified.** `maxHeight = viewportHeight - 70` is the **only** output
that varies with the viewport. A stale clamp is a real visual defect: the panel keeps its
taller allowance, renders past it, and runs off the top of the window. The spec opens the
panel at 1440×960, asserts the precondition that the panel is taller than the clamp it will
face, shrinks to 1440×300, then asserts `panel.y >= 7`.

| Run | Code state | Result |
|---|---|---|
| Baseline | intact | **5 passed**, exit 0 |
| **Mutation** | `resize` listener deleted | **FAILED**: `expect(received).toBeGreaterThanOrEqual(7)` / `Received: -86` — exit 1 |
| Restore | listener restored | **5 passed**, exit 0 |

The panel's top edge went **86px above the viewport**. The spec is a genuine guard.

### 3.4 The collapse premise, measured and disproven

Spec *"collapsing the sidebar changes its width but not the trigger anchor point"* — **MEASURED,
passing**:

- sidebar width `216 → 56` — the collapse really happens;
- `trigger.x` **unchanged**; `trigger.y` **unchanged**;
- the popover anchors identically in the collapsed state.

So the collapse **cannot** move this popover. The `rail` entry in the measuring effect's
dependency list is **defensive, not load-bearing.**

Spec *"a click aimed at the collapse toggle while the panel is open hits the scrim and
dismisses"* — **MEASURED, passing**: `document.elementFromPoint` at the toggle's centre returns
`account-menu-scrim`. The portal is `fixed; inset: 0` at `--z-dropdown`; the click dismisses the
panel and the sidebar stays `expanded`. With the focus trap holding Tab inside the panel,
**collapse-while-open is unreachable by any user.**

**Action taken:** the `rail` dependency was **kept** — it costs one `getBoundingClientRect` per
collapse and becomes correct the moment a layout change gives the trigger a left edge that is
not the viewport's. Its **comment was corrected**, because it asserted the disproven premise as
fact. A wrong comment is worse than none.

### 3.5 Verdict

| Item | Verdict |
|---|---|
| Browser-level coverage exists and executes | **MEASURED** — 5 specs, Chromium, exit 0 |
| Fails if the measuring effect breaks | **MUTATION-VERIFIED** — `-86px`, exit 1 |
| Collapse moves the anchor | **DISPROVEN by measurement** |
| Collapse-while-open is reachable | **DISPROVEN by measurement** |
| Firefox / WebKit | **NOT RUN** — chromium only installed |
| Phase D/E's "popover re-anchoring" open item | **CLOSED**, with a corrected diagnosis |

Sidebar persistence tests and `rehydrateSidebarCollapsed()` were preserved — no defect found.

---

## 4. Workflow and artifact-gate verification

`07-desktop-build.yml` was **preserved**; one defect was found and fixed, in the checker rather
than the workflow (§4.2).

### 4.1 Invariants — MEASURED

`python staging/verify_desktop_workflow.py` → **7 held, 0 violated, exit 0**:

```
OK  native runners: macos-latest, ubuntu-22.04, windows-latest
OK  permissions: contents: read (cannot publish)
OK  no continue-on-error (build failures cannot be hidden)
OK  upload: gated on success, if-no-files-found=error
OK  lockfiles honoured: npm ci + cargo --locked
OK  no bash-4-only construct (macOS /bin/bash 3.2 safe)
OK  no signing, notarization, publishing or deployment step
```

### 4.2 The checker was wrong three times; recording it

Each iteration produced a false result, and each was the scanner's fault:

1. **Grepping the raw file** flagged `continue-on-error`, `mapfile` and "notariz" — all present
   only in the comments *stating their absence*. A scanner must not read comments.
2. **Scanning the re-dumped parse** still flagged `mapfile` (a **shell** comment inside a
   `run:` block, which survives YAML parsing) and "notariz" (the report job's honest
   `Not notarized.` echo). Fixed by scanning executable lines only, and by narrowing the
   notarization pattern to real CLIs and credential names — a pattern that flagged the
   disclaimer would punish honesty.
3. **After that narrowing, the `continue-on-error` check became vacuous** — it was reading the
   executable-lines string, where a YAML *key* can never appear. Repointed at the structural
   dump, and it now emits a note when it passes, so a future reader can see it ran.

That is the same failure mode as the vacuous sidebar test in Phase D and attempt 1 in §3.3: a
check that cannot fail. Three for three in this workstream.

### 4.3 Artifact gate — MEASURED against real placeholders

`bash staging/verify_bundle_gate_probe.sh algo22-terminal/releases` → **exit 1**:

```
FAIL: …/linux/VyomQuant-0.1.0.AppImage is 64 bytes, below the 1048576-byte floor.
FAIL: …/linux/vyomquant_0.1.0_amd64.deb is 67 bytes, below the 1048576-byte floor.
FAIL: …/mac/VyomQuant-0.1.0-universal.dmg is 69 bytes, below the 1048576-byte floor.
OK:   …/windows/VyomQuant-Setup-0.1.0.exe (112117309 bytes)
```

Rejects all three text placeholders; accepts the one real installer.

### 4.4 What this does NOT establish

**A green YAML parse, a green invariant check and a green gate probe do not mean the native
build works.** None of them compiles Rust or runs the Tauri bundler. The only evidence that
would is §5, and there is none.

---

## 5. Native platform build evidence

### 5.1 Per-platform status

| Platform | Status | Evidence / blocker |
|---|---|---|
| **Windows** | **NOT VERIFIED** | Local `npx tauri build` fails: `linker 'link.exe' not found`. **MEASURED:** no `Microsoft Visual Studio` directory, no `Windows Kits`, no `link.exe` on PATH, while `rustc -vV` reports host `x86_64-pc-windows-msvc`. Remediation is a multi-GB elevated Visual Studio Build Tools install — a privileged system change the brief forbids. |
| **macOS** | **NOT VERIFIED** | No macOS host. Not cross-compilable from Windows. |
| **Linux** | **NOT VERIFIED** | WSL2 is **Ubuntu 24.04.5** with no `cargo`/`rustc` and no native `node` (only Windows `npm` via `/mnt/c`); `sudo -n` requires a password, so the §5.2 packages cannot be installed. 24.04 would also raise the glibc floor the workflow avoids by pinning 22.04. |

### 5.2 Prerequisites (unchanged, restated)

Linux: `libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev
libayatana-appindicator3-dev librsvg2-dev` plus `patchelf`. Windows: MSVC C++ build tools +
Windows SDK + WebView2 — preinstalled on `windows-latest`. macOS: Xcode CLT plus both
`aarch64-apple-darwin` and `x86_64-apple-darwin` targets for a universal binary.
Sources: [Tauri prerequisites](https://tauri.app/start/prerequisites/),
[Tauri Debian guide](https://tauri.app/it/distribute/debian/). *Content rephrased for
compliance with licensing restrictions.*

### 5.3 Why CI could not be triggered — MEASURED

| Probe | Result |
|---|---|
| `gh --version` | `2.100.0` — installed |
| `gh auth status` | Logged in as `Narendra2212`, scopes include **`workflow`** |
| `git ls-tree origin/main -- .github/workflows` | **01–06 only. No `07-desktop-build.yml`.** |
| `gh workflow list --all` | 7 registered: 01–06 + Dependabot. **No desktop build workflow.** |

The credentials to dispatch a workflow are present. The workflow is not: `workflow_dispatch`
can only run a file that exists in the remote repository, and `07-desktop-build.yml` is
**untracked locally**. Putting it on the remote requires a commit and a push, both forbidden.

**This is a permission/sequencing blocker, not a technical one.** The single action that
converts all three platforms from NOT VERIFIED to evidence-bearing is: commit this file to a
branch, push it, dispatch the workflow, attach the three job results.

### 5.4 Cross-platform desktop readiness

**UNVERIFIED on all three operating systems.** No runner has compiled this application. No
artifact has been produced or gate-checked by CI. Nothing here may be reported as desktop
support.

---

## 6. Product branding

### 6.1 The asset was located — MEASURED

`node staging/find_logo_asset.mjs` searched 6 roots and 162 image files. One matches the
supplied artwork, confirmed by visual inspection:

```
C:\Users\aa\Downloads\cnlg_l1u_400x400.jpg
```

It is the blue-and-cyan **VQ** monogram with a bar chart and ascending trend line on black.
**It is not in the repository**, and nothing was copied, converted or committed.

### 6.2 Technical sufficiency — MEASURED

`node staging/inspect_logo_asset.mjs` against the file:

| Property | Value |
|---|---|
| Format | **JPEG** |
| Dimensions | **400 × 400** |
| Size | 10,401 bytes |
| Alpha channel | **NONE** — JPEG cannot carry transparency; the black field is baked in |

**14 of 17 required targets are reachable without upscaling. 3 are blocked:**

| Target | Needs | Status |
|---|---|---|
| `src-tauri/icons/icon.png` | 512px | **BLOCKED** |
| `src-tauri/icons/icon.icns` (largest frame) | 1024px | **BLOCKED** |
| `public/og-image.png` | 1200px wide | **BLOCKED** |

A fourth problem is not about size: with **no alpha**, the mark cannot be placed on the
sidebar's cyan brand tile or any light surface without a black box around it. Every current
brand surface composites the glyph onto a coloured ground.

### 6.3 Provenance

The filename pattern `…_400x400.jpg` is the shape of a **social-media profile image export**,
not a design-system handoff. No licence, source file or approval record accompanies it.

### 6.4 Decision — no files changed

**Branding remains an explicit release blocker.** Nothing was substituted, generated,
upscaled or recreated, because:

1. three required formats are unreachable from a 400px source without upscaling, which is
   approximating the logo — forbidden;
2. the absence of an alpha channel makes it unusable on the surfaces that need it;
3. provenance is a social export, not an approval record.

**To unblock:** the master asset — **SVG preferred**, or PNG ≥1024×1024 **with transparency**.
From that, all 17 targets derive cleanly in one pass. The 16 files in `src-tauri/icons/` are
**still the default Tauri scaffolding mark** and must be replaced before any installer is
promoted.

No installer was promoted or published.

---

## 7. Dashboard failures — reproduction and ownership

### 7.1 Exact failures — MEASURED

`tests/unit/dashboard_phase2d_polish.test.jsx`, 5 tests:

1. `2D.3: Spot Liquidation Safety Invariants > renders dash for spot liquidation price and distance without fabrication`
   → `TestingLibraryElementError: Unable to find an element with the text: 21.9%`
2. `2D.4: WebSocket Reconciliation & Reconnect Behavior > rejects stale WebSocket events from the past`
   → `TestingLibraryElementError: Unable to find an element with the text: BTC Momentum Grid`

### 7.2 The fixture is correct

**INFERRED** from reading both: the mock supplies `positions[1]` with `market_type: "future"`,
`mark_price: 3200`, `liquidation_price: 2500` → `(3200−2500)/3200 = 21.875% → 21.9%`, and
`strategies.items[0].name: "BTC Momentum Grid"`. Both match the shapes `Dashboard.jsx` reads
(`payload.strategies?.items`, `positions[]`). The fixture is not the fault.

### 7.3 They are flakes — MEASURED by repetition

A pristine `HEAD` checkout was created with `git worktree add --detach` (**additive**; no
reset, stash or index change) with `node_modules` junctioned, and removed afterwards.

| Configuration | Runs | Failures |
|---|---|---|
| **Pristine HEAD** | 3 | **0, 0, 0** |
| Pristine + my `ResponsiveGate.jsx` + `useSidebarStore.js` | 2 | **1, 2** |
| **Working tree (all changes)** | 3 | **1, 0, 0** |

Two facts settle it:

- **The same configuration gives different results.** The worktree+ResponsiveGate runs failed
  with **1** failure and then **2**. Identical code. Non-deterministic.
- **The working tree passes 2 of 3.** Whatever these failures are, they are not a
  deterministic consequence of the changes.

### 7.4 Mechanism

**INFERRED, consistent with all 8 runs.** Both assertions sit inside `waitFor(...)` and
**no `waitFor` in the file passes an explicit `timeout`**, so all use testing-library's
**1000 ms** default. `vitest.config.js`'s `testTimeout: 15000` does not apply to a
`waitFor` budget. Free physical memory here measured **1.17–1.67 GB**, with
`fileParallelism: false` / `maxWorkers: 1` already set for that reason. Under load the
Dashboard's async projection intermittently needs more than 1 s.

Corroboration: `Dashboard.jsx` has **no import path to any file changed by this workstream**.
The only importers of `ResponsiveGate` are `App.jsx`, `Sidebar.jsx`, `AccountMenu.jsx` and
`StrategyBuilder.jsx` — none reachable from `Dashboard.jsx`. `ds/*` imports it nowhere.

### 7.5 Ownership

**Not this workstream's.** The tests are load-sensitive by construction, and the construction
predates this work — the file is `dashboard_phase2d_polish.test.jsx` and the assertions are
unmodified at HEAD.

**Likely owner:** the spec that authored the Phase 2D dashboard tests and the `task 19.3`
tier-2 projection — `.kiro/specs/vyomquant-ui-redesign` / `retail-ui-simplification`.
**Recommended fix (for that owner, not applied here):** pass an explicit `timeout` to the two
`waitFor` calls, or await the projection deterministically.

**Nothing was skipped, weakened or modified.** `dashboard_phase2d_polish.test.jsx` is
unchanged.

**Caveat stated plainly:** pristine passed 3/3 and the working tree passed 2/3 — not enough
samples to put a confidence interval on the flake rate. The claim is "non-deterministic and
not attributable", not "harmless". CI (`ubuntu-latest`, more memory) is where the real rate
should be measured.

---

## 8. Commands and outcomes

Exit codes captured with `cmd /v:on /c "… & echo EXITCODE=!ERRORLEVEL!"`. **Delayed expansion
(`/v:on`, `!ERRORLEVEL!`) is required** — Phase D/E used `%ERRORLEVEL%`, which cmd expands at
parse time, before the command runs. Those earlier "EXITCODE=0" readings were unreliable; the
pass/fail counts printed by the runners were not.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `npx playwright install chromium` | **0** | chromium-1217 + headless shell |
| 2 | `npx playwright test …accountMenuAnchoring… --project=chromium --workers=1` | **0** | **5 passed** |
| 3 | same, **`resize` listener deleted** | **1** | **1 failed** — `Received: -86` |
| 4 | same, after restore | **0** | **5 passed** |
| 5 | `npx vitest --run tests/unit/shell` | **1** | **BLOCKED** — worker-start timeout on `accountMenu.test.jsx`; 6 files / **171 passed**, 1 file not run |
| 6 | `npx vitest --run tests/unit/shell/accountMenu.test.jsx` | **0** | **33 passed** (shell total 204) |
| 7 | `python staging/verify_desktop_workflow.py` | **0** | **7 invariants held, 0 violated** |
| 8 | `bash staging/verify_bundle_gate_probe.sh algo22-terminal/releases` | **1** (expected) | 3 placeholders rejected, 1 real accepted |
| 9 | `node staging/find_logo_asset.mjs` | 0 | 162 images; asset located |
| 10 | `node staging/inspect_logo_asset.mjs <logo>` | 0 | 400×400 JPEG, no alpha, **3/17 blocked** |
| 11 | `npx vitest --run …dashboard_phase2d_polish…` (working tree ×3) | 1, 0, 0 | **1, 0, 0** failures |
| 12 | same, pristine worktree ×3 | 0, 0, 0 | **0** failures |
| 13 | same, worktree + ResponsiveGate ×2 | 1, 1 | **1 then 2** failures |
| 14 | `gh auth status` | 0 | authenticated, `workflow` scope |
| 15 | `gh workflow list --all` | 0 | 01–06 + Dependabot; **no desktop workflow** |
| 16 | `git worktree remove --force …` / `prune` | 0 | only the main worktree remains |

### 8.1 Blocked, not failed

Run 5 is **BLOCKED/NOT RUN** for one file, per the brief's rule: `[vitest-pool]: Failed to
start forks worker … Timeout waiting for worker to respond`. The file never executed. Re-run
in isolation (run 6) it passed 33/33. **A vitest run that reports "no tests" still exits 0 and
must never be read as a pass.**

### 8.2 Not run in this phase

The full repository suite (prohibited). Firefox and WebKit e2e projects. `npm run build` — no
production-build-affecting source changed beyond the corrected `AccountMenu` comment; the last
green build is recorded in Phase D/E §8 run 8.

---

## 9. Final git status and attributable diff

**MEASURED at exit.** Staged: **22**, unchanged. `ExchangeManager.jsx`: 55 insertions, 13
deletions, unchanged.

### Modified by this workstream (unstaged)

```
algo22-terminal/index.html
algo22-terminal/src/components/Sidebar.jsx
algo22-terminal/src/components/landing/DownloadSection.jsx
algo22-terminal/src/components/landing/FAQ.jsx
algo22-terminal/src/components/shell/AccountMenu.jsx      <-- comment corrected this phase
algo22-terminal/src/components/shell/ResponsiveGate.jsx
algo22-terminal/tests/unit/landing/landingSections.test.jsx
```

### Added by this workstream (untracked)

```
.github/workflows/07-desktop-build.yml
algo22-terminal/src/store/useSidebarStore.js
algo22-terminal/tests/e2e/accountMenuAnchoring.spec.ts     <-- new this phase
algo22-terminal/tests/unit/shell/sidebarCollapse.test.jsx
staging/PHASE10_UI_DESKTOP_READINESS.md
staging/PHASE10_UI_DESKTOP_CLOSURE.md                      <-- this file
staging/verify_bundle_gate_probe.sh
staging/verify_desktop_workflow.py                         <-- new this phase
staging/find_logo_asset.mjs                                <-- new this phase
staging/inspect_logo_asset.mjs                             <-- new this phase
```

### Changed in this phase only

| File | Change |
|---|---|
| `tests/e2e/accountMenuAnchoring.spec.ts` | **new** — 5 browser specs |
| `src/components/shell/AccountMenu.jsx` | **comment only** — corrected the disproven `rail` rationale. The `[active, rail]` dependency is unchanged; the mutation probe was fully reverted and verified |
| `staging/verify_desktop_workflow.py` | **new** — workflow invariant checker |
| `staging/find_logo_asset.mjs`, `inspect_logo_asset.mjs` | **new** — read-only asset probes |

**NOT modified:** `01-pr-check.yml`, `05-security.yml`, `07-desktop-build.yml` (preserved — no
defect found in it), `dashboard_phase2d_polish.test.jsx`, `useSidebarStore.js`,
`sidebarCollapse.test.jsx`, `package.json`, `package-lock.json`, `Cargo.toml`, `Cargo.lock`.
**No dependency added or upgraded.** Playwright's chromium binary went to the user cache.

**Side effects outside the repo:** chromium in `~/AppData/Local/ms-playwright`; the temporary
worktree and its junction, both removed (`git worktree list` shows only the main worktree).

---

## 10. Remaining blockers and follow-up

| # | Blocker | Owner | Unblocks when |
|---|---|---|---|
| 1 | **Native builds unverified on all 3 OSes.** `07-desktop-build.yml` is untracked, so `workflow_dispatch` has nothing to run; committing/pushing is forbidden here | release owner | the file is on a remote branch and the workflow is dispatched; attach 3 job results |
| 2 | **Branding.** 400×400 opaque JPEG; 3/17 targets unreachable; Tauri icons still default Tauri | design / brand owner | master SVG, or PNG ≥1024² with alpha |
| 3 | **Signing + notarization** absent on every platform | release owner | certificates and secrets provisioned — deliberately out of scope |
| 4 | **Dashboard `waitFor` flakes** | `vyomquant-ui-redesign` / `retail-ui-simplification` | explicit `timeout` on the two `waitFor` calls, then measure the rate in CI |
| 5 | **e2e covers chromium only** | frontend | firefox + webkit installed and run |
| 6 | **`useWorkspaceStore`'s undeclared `zustand`** — pre-existing, untouched | frontend | declare `zustand`, or migrate as `useSidebarStore` was |
| 7 | **Host memory ~1.2–1.7 GB** causes worker-start timeouts that exit 0 | infra | more headroom, or a CI-only full-suite policy |
| 8 | **`og-image.png`** absent; `og:image` / `twitter:image` removed as broken | design | a 1200×630 asset lands, then restore the three tags together |

---

## 11. Acceptance recommendation

### ACCEPT the AccountMenu browser coverage and the workflow verification. KEEP desktop readiness and branding BLOCKED.

**Accept — evidenced:**

1. **AccountMenu browser regression.** 5 specs in real Chromium, exit 0, and
   **mutation-verified**: breaking the measuring effect produces a `-86px` off-screen panel
   and exit 1. The Phase D/E open item is closed, and closed with a *corrected* diagnosis
   rather than the one it was opened with.
2. **Workflow verification.** 7 structural invariants hold; the artifact gate demonstrably
   rejects the three real placeholder files and accepts the one real installer.
3. **Dashboard ownership.** Reproduced, shown non-deterministic across 8 runs in 3
   configurations, mechanism identified, owner named, nothing skipped or weakened.
4. **Baseline integrity.** 22 staged files and `ExchangeManager.jsx` byte-identical; no
   commit, push, reset, stash or clean; `01-pr-check.yml` and `05-security.yml` untouched.

**Block — unevidenced:**

5. **Windows / macOS / Linux desktop: NOT VERIFIED.** No runner has compiled this application.
   A green YAML parse and a green gate probe are not a build. **Do not state desktop support
   in any form** until §10.1 is satisfied.
6. **Branding: BLOCKED.** The supplied asset is a 400×400 opaque JPEG of social-export
   provenance; 3 of 17 required formats are unreachable without upscaling, and the missing
   alpha channel rules out the surfaces that need it. Nothing was substituted. §10.2.

**Conditions on accepting 1–4:**

- Treat workflow 07's output as **unsigned build verification**, never as an installer.
- Replace the default Tauri icons before any artifact is promoted.
- Route §10.4 to the dashboard owner; the frontend suite is not green until it is fixed.
- Re-measure the flake rate on CI rather than on this host.

**Two prior claims are withdrawn and corrected in this report:** the AccountMenu collapse
defect (§3.4) and the "pre-existing" characterisation of the dashboard failures (§7.3).

No live-trading, execution, exchange, billing, financial, authorization or database code was
modified. **Live trading remains OFF.** This report makes no trading-readiness and no
production-certification claim.

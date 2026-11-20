# PHASE 10 — UI CHANGE-CONTROL REVIEW AND DESKTOP BUILD READINESS

**Revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`, tracking `origin/main`)
**Date:** 2026-10-10
**Scope:** review of the sidebar/landing changes, plus a native desktop build pipeline.
**Live trading during this work:** OFF. No execution, exchange, billing, financial, database
or certification code was read for modification or changed. No order was placed.

**Nothing was committed, pushed, reset, stashed or cleaned. The git index was not touched.**

---

## 1. Baseline

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Upstream | `origin/main` |
| Staged files | **22** (all other workstreams) |
| Unstaged modified | **97** at entry (7 frontend, 90 backend/infra) |
| Untracked | ~2906 (dominated by `staging/.ccxt_upgrade_probe/` venv and `.hypothesis/`) |

### 1.1 Pre-existing staged work — PRESERVED, UNTOUCHED

Verified identical at entry and exit (`git diff --cached --name-only` → 22 both times):

```
M  .github/workflows/05-security.yml
A  .kiro/security-template.md
M  algo22-terminal/src/pages/ExchangeManager.jsx      <-- 55 insertions, 13 deletions
M  backend_app/core/dependencies.py
A  backend_app/core/tenant_db_context.py
M  backend_app/routers/auth.py
M  backend_app/routers/exchange.py
A  docs/adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md
A  docs/adr/ADR-0007-per-tenant-credential-encryption-keys.md
A  docs/saas-architecture/tenant-isolation-matrix.md
A  docs/security-reviews/ALGO22_Quant_Platform_Review_10032026.md
M  scripts/verify_rls.py
A  scripts/verify_rls_enforcement.py
M  terraform/alb.tf
A  terraform/cloudtrail.tf
M  terraform/providers.tf
M  terraform/variables.tf
A  tests/test_sc3_aal_claim_propagation.py
A  tests/test_tenant_db_context.py
A  tests/test_tenant_db_context_postgres.py
A  tests/test_verify_rls_script.py
A  tests/test_worker_deployment_coverage.py
```

`algo22-terminal/src/pages/ExchangeManager.jsx` is the file the brief named explicitly. Its
staged diff is byte-identical at exit: **55 insertions, 13 deletions**. This task did not
open it for modification.

### 1.2 Ownership — and one correction to the handoff

The handoff attributed 7 modified + 2 new frontend files to the prior task. Confirmed exactly,
with no ambiguity: `git diff --name-status -- algo22-terminal` returns those 7 and no others,
and the 2 untracked files are the only untracked entries under `algo22-terminal/`.

**Ambiguity found and resolved — one file is NOT this task's and must not be attributed to it:**

- `.github/workflows/01-pr-check.yml` carries **+143 unstaged lines** from another
  workstream. It was already ` M` at entry. This task did not edit it. It appears in a
  frontend-scoped `git diff --stat` only because that command was scoped to `.github` as
  well, and it is called out here so a reviewer does not read those 143 lines as part of
  this change.

That file, plus the staged `05-security.yml`, is why the new workflow in §4 is a **new file**
rather than an edit: both candidate hosts for a build job are owned by other in-flight work.

### 1.3 Configuration read (not modified)

- `algo22-terminal/package.json` — scripts: `dev`, `tokens`, `prebuild`
  (`gen-tokens.mjs --check`), `build`, `preview`, `tauri`, `test`, `test:ui`, `lint`.
- `algo22-terminal/vitest.config.js` — jsdom, `setupFiles: []`, `testTimeout: 15000`,
  `pool: 'forks'`, `fileParallelism: false`, `maxWorkers: 1`.
- `algo22-terminal/src-tauri/tauri.conf.json` — `productName` VyomQuant, identifier
  `com.algo22.terminal`, `bundle.active: true`, `bundle.targets: "all"`, 5 icon paths.
- `algo22-terminal/src-tauri/Cargo.toml` + **`Cargo.lock` present**; no `rust-toolchain.toml`.
- Workflows at entry: `01-pr-check`, `02-build`, `03-deploy`, `04-nightly-audit`,
  `05-security`, `06-frontend-deploy` (root) and `algo22-terminal/.github/workflows/playwright.yml`.

**Platform runners available in CI at entry: `ubuntu-latest` only.** Every job in all seven
workflows. No `windows-*` or `macos-*` runner existed anywhere. The sole Tauri reference is
the `cargo audit` step in `04-nightly-audit.yml` (`continue-on-error: true`) — a dependency
audit that reads `Cargo.lock` and never invokes a linker. **No CI job has ever compiled the
desktop application on any operating system.**

---

## 2. Files changed by THIS task

| File | Status | Purpose |
|---|---|---|
| `algo22-terminal/src/store/useSidebarStore.js` | modified (was new in prior task) | added `rehydrateSidebarCollapsed()` test seam — §3.2 |
| `algo22-terminal/tests/unit/shell/sidebarCollapse.test.jsx` | modified (was new in prior task) | replaced a vacuous test; +11 tests — §3.2 |
| `.github/workflows/07-desktop-build.yml` | **new** | native desktop build matrix — §4 |
| `staging/verify_bundle_gate_probe.sh` | **new** | reproducible probe for the artifact gate — §4.3 |
| `staging/PHASE10_UI_DESKTOP_READINESS.md` | **new** | this report |

Carried over from the prior task and reviewed here, not re-edited: `index.html`,
`src/components/Sidebar.jsx`, `src/components/shell/ResponsiveGate.jsx`,
`src/components/shell/AccountMenu.jsx`, `src/components/landing/DownloadSection.jsx`,
`src/components/landing/FAQ.jsx`, `tests/unit/landing/landingSections.test.jsx`.

**No dependency was added, removed or upgraded.** `package.json`, `package-lock.json`,
`Cargo.toml` and `Cargo.lock` are unmodified.

---

## 3. Phase B — sidebar review

### 3.1 Verification matrix

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Persisted preference survives reloads | **PASS** (after fix) | Was asserted vacuously — see §3.2. Now covered by `rehydrateSidebarCollapsed`, which re-runs the module's read path. |
| 2 | Viewport overrides stored preference at narrow widths | **PASS** | `sidebarModeFor('TABLET', true)` and `('TABLET', false)` both `'rail'`; `('BELOW', …)` likewise. Render at 800px yields `data-sidebar-mode="rail"` and `width: 56px` with a stored *expanded* preference. |
| 3 | Toggle state and accessible labels correct | **PASS** | `aria-label` states the **action** (`Collapse sidebar` / `Expand sidebar`); `aria-expanded` states `!collapsed`; `aria-controls` resolves to the `<nav>`'s real `id`. Name present at 56px where the label is `sr-only`. |
| 4 | Keyboard interaction | **PASS** | Native `<button type="button">`. Enter and Space both toggle; focus is retained across the mode change (the button is relabelled, not remounted). |
| 5 | Storage failures do not crash the shell | **PASS** (newly covered) | New tests replace `window.localStorage` with a throwing getter — the Safari-private-mode shape, where *access* throws, not just `getItem`. Read returns the default, write does not propagate, and the shell still renders with a working toggle. |
| 6 | Account menu stays anchored after mode change | **PASS by inspection — NOT test-covered** | See §3.3. |
| 7 | No unnecessary dependencies | **PASS** | `package.json` untouched. `zustand` deliberately avoided: `npm ls zustand` resolves it at 4.5.7 **only** as a transitive dependency of `reactflow@11.11.4`; it is not in `dependencies`. The store uses React 18's `useSyncExternalStore`. |
| 8 | Existing layout / top-bar / design-system contracts intact | **PASS** | 204 shell tests green, including `topBar`, `responsiveGate`, `navigation`, `accountMenu`, `sidebar` and the `nav-contract` guard (nav still exactly 10 entries, no buttons inside the `Main` landmark). 228 guard/ratchet tests green. |

Design review notes, no change required:

- `sidebarModeFor(viewport, collapsed = false)` keeps its single-argument behaviour, so the
  pre-existing `responsiveGate.test.jsx` calls remain valid. The preference is a **floor,
  not a vote** — it can only narrow. This is the one way the feature could have regressed
  §11.6's viewport judgement, and it is pinned by four assertions.
- The toggle sits **outside** `<nav aria-label="Main">`. `topBar.test.jsx` pins the top bar
  to "no buttons", so placing it there — the more conventional position — would have broken
  an existing contract. The sidebar's own reserved 36px row is identical in both states, so
  toggling shifts the nav list by zero pixels.
- No width transition. Animating the grid column would re-fire `ds/Chart`'s `ResizeObserver`
  and every recharts container once per frame, and `DESIGN_SYSTEM_V2.md` limits motion to
  colour and opacity.

### 3.2 Defect found and corrected (minimal, in owned files)

**A test asserted persistence and verified nothing.**

```js
window.localStorage.setItem(SIDEBAR_STORAGE_KEY, 'yes-please');
act(() => resetSidebarCollapsed());
expect(getSidebarCollapsed()).toBe(false);   // could not fail
```

`resetSidebarCollapsed()` **assigns** `collapsed = false` and never consults storage, so the
assertion passed regardless of what the parser did. `readPersisted()` — which holds the
`=== '1'` comparison, and is the code path that makes the preference survive a real page
reload — runs once at module load and was therefore **never exercised by any test**. A
truthiness check or a `String.includes` in its place would have shipped undetected.

Correction, confined to the two files this task owns:

- `useSidebarStore.js`: added `rehydrateSidebarCollapsed()`, which re-runs `readPersisted()`
  and notifies subscribers. A remount re-runs the component, not the module, so this seam is
  the closest a jsdom test gets to a page reload. `resetSidebarCollapsed()`'s docblock now
  warns that it cannot be used to assert parsing.
- `sidebarCollapse.test.jsx`: replaced the vacuous test with 11 real ones — sentinel
  round-trip, absent key, seven parametrised hand-edited values (`'true'` and `'11'` are the
  rows that catch a truthiness or substring check), a write→read round trip, and the two
  storage-failure cases in row 5 above.

Test count for this file: **22 → 33**.

No other defect was found. Nothing required changing another workstream's files.

### 3.3 Honest gap — item 6 is reviewed, not tested

The `AccountMenu` fix is correct by inspection: the measuring `useLayoutEffect` depended on
`[active]` only, so collapsing the sidebar moved the trigger ~160px without firing a window
`resize`, leaving an open popover anchored to the trigger's former position. Adding `rail` to
the dependency list re-measures on the mode change.

**It is not covered by a test, and I did not write one.** `measure()` reads
`getBoundingClientRect()`, which is all zeros under jsdom, so any positional assertion would
pass whether or not the effect re-ran — a second vacuous test, which is the defect §3.2 was
about. Honest coverage needs a real browser; the existing Playwright setup
(`algo22-terminal/playwright.config.ts`) is the right home. **Recorded as an open item.**

### 3.4 Commands and results

| # | Command (cwd `algo22-terminal`) | Exit | Result |
|---|---|---|---|
| B1 | `npx vitest --run tests/unit/shell/sidebarCollapse.test.jsx` | **0** | 1 file, **33 passed** |
| B2 | `npx vitest --run tests/unit/shell` | **0** | 7 files, **204 passed** |
| B3 | `npx vitest --run tests/unit/guards` | **0** | 12 files, **228 passed** |

Exit codes captured via `cmd /c "… & echo EXITCODE=%ERRORLEVEL%"`; piping to PowerShell
`Select-Object` masks the child exit status and must not be used to claim a pass.

**Environment note affecting reproducibility.** One run of B1 aborted before executing with
`[vitest-pool]: Failed to start forks worker … Timeout waiting for worker to respond`,
reporting `Test Files no tests`. Free physical memory was **1.17 GB**. This is the condition
`vitest.config.js` documents, and it is why `fileParallelism: false` / `maxWorkers: 1` are
set. **A "no tests" run exits 0 and must not be read as a pass** — the counts above are from
runs that reported real totals.

---

## 4. Phase C — landing-page accuracy and metadata

Reviewed: `DownloadSection.jsx`, `FAQ.jsx`, `landingSections.test.jsx`, `index.html`.

| Check | Verdict | Evidence |
|---|---|---|
| Platform claims match actual artifacts | **PASS** | Measured `algo22-terminal/releases/`: Windows `.exe` **112,117,309 B**; macOS `.dmg` **69 B**; AppImage **64 B**; `.deb` **67 B**. Copy now reads "in development" / "none is published for download", not "in final testing". |
| Text placeholders never presented as installers | **PASS** | All four `PlatformArtifact` panels render `VERDICT.UNAVAILABLE` from `design/pageFields.js` with reasons such as "No macOS build has been produced for this release". No anchor, size or checksum renders inside a card (`downloadSurface.test.jsx`). |
| Structured metadata claims no unsupported native OS | **PASS** | JSON-LD `operatingSystem` was `"Web, Windows, macOS"` — a machine-readable native-app claim with no published build, which also omitted the Linux the page itself names. Now `"Web"`, with `browserRequirements` carrying the real constraint. |
| INR pricing metadata matches catalogue | **PASS** | JSON-LD offer now `price "0"`, `priceCurrency "INR"`. Matches `subscription_engine.py`: `PRICE_BASE_CURRENCY = "INR"`, Free plan `pricing={"INR": 0}`. Previously USD, naming a price list that does not exist. |
| Referenced icons/images exist | **PASS** | The only asset reference left in `index.html` is `/vq-favicon.svg`, which exists in `public/` and is emitted to `dist/`. |
| Social card does not point at a missing image | **PASS** | `og:image`/`twitter:image` pointed at `og-image.png`, absent from both `public/` and `dist/`. Both removed; `twitter:card` downgraded to `summary`, which renders without an image. A `summary_large_image` card with no image yields no card at all. |
| Accurate security / exchange / ML claims intact | **PASS — not weakened** | `SecuritySection` still states "We hold no third-party security certification yet". `FAQ` still states Binance is the only production-certified venue — consistent with `staging/EXCHANGE_ADAPTER_INVENTORY.md`. `TrustSection` still claims ML (XGBoost) and **not** AI; no AI/copilot claim exists anywhere on the landing surface, which is correct because `routers/copilot.py` is a 7-entry keyword matcher with no model. |

Two further notes:

- `landingSections.test.jsx` was **pinning the false claim** (`'In final testing'`,
  `/Native terminals for Windows, macOS and Linux/`). Updated, with the byte measurements
  recorded in the test, plus negative assertions that the retired strings are genuinely gone
  rather than merely unasserted.
- **No logo, social image or PWA manifest was added.** No approved asset exists — §6.

### 4.1 Verified independently

Token utilities on `<body>` (replacing four hex literals that matched no token) were confirmed
to survive into the emitted stylesheet and to still be referenced by the emitted `<body>`;
`bg-surface-canvas`, `text-content-primary`, `selection:bg-brand/20` and `selection:text-brand`
all present in `dist/assets/index-*.css`. The corrected metadata was read back out of
`dist/index.html`: `theme-color #080A0E`, no `og:image` element, `twitter:card summary`,
`operatingSystem "Web"`, `priceCurrency "INR"`.

---

## 5. Phase D — desktop build readiness

### 5.1 The reported failure, confirmed and diagnosed

```
error: linker `link.exe` not found
note: the msvc targets depend on the msvc linker but `link.exe` was not found
note: please ensure that Visual Studio 2017 or later, or Build Tools for Visual Studio
      were installed with the Visual C++ option
```

Measured on this host:

| Probe | Result |
|---|---|
| `cargo --version` | `cargo 1.98.1` — present |
| `rustc -vV` host | `x86_64-pc-windows-msvc` — **requires the MSVC linker** |
| `npx tauri --version` | `tauri-cli 2.10.1` — present |
| `C:\Program Files*\Microsoft Visual Studio` | **absent** |
| `C:\Program Files (x86)\Windows Kits` | **absent** |
| `Get-Command link.exe` | **absent** |

**This is a host provisioning gap, not a code defect.** The Rust toolchain targets MSVC and
no MSVC toolchain or Windows SDK is installed.

### 5.2 Prerequisites required by this repository's configuration

| Platform | Requirement | On the matching GitHub runner |
|---|---|---|
| Windows | MSVC C++ build tools (`link.exe`) + Windows SDK; WebView2 runtime | **Preinstalled** on `windows-latest` (VS 2022 + Windows SDK; WebView2 is evergreen on Server 2022+) |
| macOS | Xcode command line tools; both `aarch64-apple-darwin` and `x86_64-apple-darwin` Rust targets for a universal binary | CLT preinstalled on `macos-latest`; targets added by the toolchain step |
| Linux | `libwebkit2gtk-4.1-dev`, `build-essential`, `curl`, `wget`, `file`, `libxdo-dev`, `libssl-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`, plus `patchelf` for AppImage | Installed by an explicit `apt-get` step |

Sources: [Tauri prerequisites](https://tauri.app/start/prerequisites/) and the
[Tauri Debian distribution guide](https://tauri.app/it/distribute/debian/). *Content was
rephrased for compliance with licensing restrictions.* The Debian guidance is also the reason
the Linux job pins **`ubuntu-22.04`** rather than `ubuntu-latest`: 22.04 carries
`libwebkit2gtk-4.1-dev` in its standard repositories, and building on a newer base raises the
minimum glibc the application demands at runtime, which breaks the AppImage on older desktops.

### 5.3 CI change — `.github/workflows/07-desktop-build.yml` (new)

A **new** file, because `01-pr-check.yml` is unstaged-modified and `05-security.yml` is staged,
both by other workstreams; and because the brief requires native builds to stay distinct from
dependency audits, which `04-nightly-audit.yml` owns. **No existing workflow was modified.**
Nothing in the deploy path (`03-deploy.yml`, `06-frontend-deploy.yml`) was touched.

| Property | Value |
|---|---|
| Triggers | `workflow_dispatch`, plus `push`/`pull_request` filtered to `src-tauri/**`, `package.json`, `package-lock.json`, the workflow itself |
| Runners | `windows-latest`, `macos-latest` (`--target universal-apple-darwin`), `ubuntu-22.04` |
| `fail-fast` | `false` — each OS is an independent question |
| `permissions` | `contents: read` only — it structurally cannot publish or release |
| Lockfiles | `npm ci` (uses `package-lock.json`); `tauri build … -- --locked` enforces `Cargo.lock` |
| Dependencies | none added or upgraded; the pinned `@tauri-apps/cli` from `package-lock.json` is used via `npx` |
| Audits | none — kept in `04-nightly-audit.yml` |
| Token guard | `gen-tokens.mjs --check` as its own step, so a stale token file reports as itself |
| Failure behaviour | no `continue-on-error` anywhere; the `report` job fails if any matrix leg fails |
| Upload | `actions/upload-artifact@v4`, **no `if: always()`** — a failed or unverified build uploads nothing; `if-no-files-found: error`; name `desktop-unsigned-<os>-<sha>` |

The `Verify bundle artifacts` step is the substantive gate. It fails the job unless at least
one installer was produced **and every produced installer clears 1 MiB**. That floor is far
below a real bundle (~107 MiB for the genuine Windows installer) and far above a text
placeholder (64–69 B), so it targets exactly the defect class found in `releases/`.

The step's script is written for **bash 3.2**. An earlier draft used `mapfile`, a bash 4
builtin; `macos-latest` runs `/bin/bash` 3.2, so that draft would have failed on exactly one
of three runners with `mapfile: command not found`. Caught before the workflow was finalised,
not in CI.

### 5.4 Artifact gate — verified by execution

`staging/verify_bundle_gate_probe.sh` runs the step's script verbatim against the real
`releases/` tree:

```
$ bash staging/verify_bundle_gate_probe.sh algo22-terminal/releases
Bundle directory: algo22-terminal/releases
FAIL: …/linux/VyomQuant-0.1.0.AppImage is 64 bytes, below the 1048576-byte floor.
FAIL: …/linux/vyomquant_0.1.0_amd64.deb is 67 bytes, below the 1048576-byte floor.
FAIL: …/mac/VyomQuant-0.1.0-universal.dmg is 69 bytes, below the 1048576-byte floor.
OK:   …/windows/VyomQuant-Setup-0.1.0.exe (112117309 bytes)
Verified 4 artifact(s).
EXITCODE=1
```

The gate rejects all three placeholders and accepts the one real installer.

### 5.5 Per-platform verification status

| Platform | Status | Blocker / required evidence |
|---|---|---|
| **Windows** | **BLOCKED** | MSVC build tools and Windows SDK absent (§5.1). Remediation is a multi-gigabyte elevated install of Visual Studio Build Tools with the Visual C++ workload — a host change outside this task's remit and not performed. Evidence required: a green `07-desktop-build` / `windows-latest` leg. |
| **macOS** | **NOT RUN** | No macOS host, and macOS is not cross-compilable from Windows (needs the macOS SDK and Apple toolchain). Evidence required: a green `macos-latest` leg producing a non-empty universal `.dmg`. |
| **Linux** | **BLOCKED** | WSL2 Ubuntu **24.04.5** is present but has no `cargo`/`rustc` and no native `node` (only the Windows `npm` via `/mnt/c`), and `sudo -n` requires a password, so `libwebkit2gtk-4.1-dev` and the rest of §5.2 cannot be installed. 24.04 would also raise the glibc floor the workflow avoids by pinning 22.04. Evidence required: a green `ubuntu-22.04` leg. |

**No desktop platform is verified. The pipeline that can verify them is in place but has not
executed — it runs on GitHub-hosted runners, which this environment is not.** First
`workflow_dispatch` on a branch carrying this file will produce the evidence.

Explicitly **not** claimed: code signing, macOS notarization, Windows Authenticode, installer
execution, application launch, or any end-to-end desktop functionality. None was attempted and
no signing secret is configured. Artifacts from this workflow are unsigned,
unnotarized build-verification bundles and are **not release-ready installers**.

---

## 6. Logo dependency — still open

No authoritative asset exists. Searched the repository, `C:\mnt`, `C:\tmp`, and the user's
Downloads, Desktop and Pictures. **Nothing was created, approximated or substituted.**

Current marks, for the handoff:

| Surface | Current state |
|---|---|
| Sidebar brand block, login, 2FA | lucide `Zap` glyph |
| Landing navbar + footer, legal pages | `"VQ"` text in a cyan square |
| Favicon | `public/vq-favicon.svg` — `"VQ"` text |
| **Desktop app icons** | **still the 16 default Tauri scaffolding icons** (confirmed visually: `src-tauri/icons/128x128.png` is the stock Tauri mark) |
| Social share image | none — references removed as broken (§4) |
| PWA manifest | **does not exist**; the app is not an installable PWA |

The Tauri icon set matters for §5: `tauri.conf.json` references five icon paths and the
workflow will happily bundle installers carrying Tauri's logo. Replacing them is a
one-pass change once the asset lands, and it should happen **before** any artifact is
promoted to a release.

---

## 7. Items requiring separate owner approval

1. **Windows host provisioning** — installing Visual Studio Build Tools (Visual C++ workload
   + Windows SDK) on this machine, if local desktop builds are wanted alongside CI.
2. **Signing and notarization** — Authenticode certificate, Apple Developer ID, notarization
   credentials, and the secret plumbing. Required before any installer reaches a user.
3. **`useWorkspaceStore.js`'s undeclared `zustand` dependency** — pre-existing, untouched,
   and a latent break if `reactflow` ever drops it. Fix is either adding `zustand` to
   `dependencies` or migrating that store as this task's store was. Not this task's file.
4. **The 2 pre-existing dashboard test failures** — `tests/unit/dashboard_phase2d_polish.test.jsx`
   ("spot liquidation price … without fabrication", "rejects stale WebSocket events from the
   past"); `TestingLibraryElementError: Unable to find an element with the text: BTC Momentum
   Grid`. Proven unrelated: that file imports only `Dashboard`, `api/modules/*` and
   `websocketClient`, and `Dashboard.jsx` contains zero references to `ResponsiveGate`,
   `Sidebar`, `AccountMenu`, `useSidebarStore` or `useViewportAccess`. Needs the dashboard owner.
5. **A real browser test for the popover re-anchoring** (§3.3) — belongs in Playwright.
6. **`og-image.png`** — a 1200×630 design asset. Once it exists, restore `og:image`,
   `twitter:image` and `twitter:card: summary_large_image` together.

---

## 8. Full verification log

| # | Command | cwd | Exit | Result |
|---|---|---|---|---|
| 1 | `git diff --cached --name-only` (entry and exit) | repo root | 0 | 22 files, unchanged |
| 2 | `git diff --cached --stat -- …/ExchangeManager.jsx` | repo root | 0 | 55 insertions, 13 deletions — unchanged |
| 3 | `node scripts/gen-tokens.mjs --check` | `algo22-terminal` | **0** | up to date, 107 tokens |
| 4 | `npx vitest --run tests/unit/shell/sidebarCollapse.test.jsx` | `algo22-terminal` | **0** | 1 file, **33 passed** |
| 5 | `npx vitest --run tests/unit/shell` | `algo22-terminal` | **0** | 7 files, **204 passed** |
| 6 | `npx vitest --run tests/unit/landing …downloadSurface… …pricing_crash_regression…` | `algo22-terminal` | **0** | 3 files, **97 passed** |
| 7 | `npx vitest --run tests/unit/guards` | `algo22-terminal` | **0** | 12 files, **228 passed** |
| 8 | `npm run build` | `algo22-terminal` | **0** | built in 1m45s |
| 9 | `bash staging/verify_bundle_gate_probe.sh algo22-terminal/releases` | repo root | **1** (expected) | 3 placeholders rejected, 1 real accepted |
| 10 | `python -c "import yaml; …"` on the new workflow | repo root | 0 | parses; 2 jobs, 3 runners, `contents: read`, 10 steps |
| 11 | `npx tauri build` | `algo22-terminal` | **non-zero** | **`linker 'link.exe' not found`** — §5.1 |
| 12 | Full suite `npx vitest --run` (prior task, same tree) | `algo22-terminal` | 1 | 156 files, **3723 passed / 2 failed** — both pre-existing, §7.4 |

Full-suite wall clock: 1644 s. It was not re-run in this phase; changes since are confined to
`useSidebarStore.js` and its own test file, both fully covered by runs 4, 5 and 7. Recorded
rather than implied.

Temporary logs and probe scripts created during verification were removed, except
`staging/verify_bundle_gate_probe.sh`, which is retained deliberately as reproducible
evidence for §5.4 and matches the existing `staging/*` probe convention.
`algo22-terminal/bundle-analysis.html` is a tracked build artifact regenerated by `npm run
build`; it was restored to HEAD so it does not pollute the review diff.

---

## 9. Recommendation

### ACCEPT WITH CONDITIONS

The sidebar change is sound, scoped, dependency-free and well covered. The landing-page and
metadata corrections replace claims that were measurably false with claims the tree supports,
and the test that pinned the false claim was corrected rather than deleted. One genuine defect
in the prior task's own tests was found and fixed, which also closed an untested persistence
path. The staged index and all 22 staged files are untouched, as is every other workstream's
unstaged work.

Conditions, all tracked above:

1. **Do not describe desktop support as verified.** All three platforms are BLOCKED or NOT RUN
   (§5.5). Run `07-desktop-build` and attach the three green legs before any such statement.
2. **Replace the default Tauri icons before promoting any artifact**, and supply the logo asset
   to close §6.
3. **Treat workflow 07's artifacts as unsigned build verification only** — not installers.
4. **Route the 2 dashboard failures to their owner** (§7.4). They are pre-existing and
   unrelated, but the suite is not green until they are resolved.
5. **Add the Playwright coverage for popover re-anchoring** (§3.3) — the one behaviour here
   accepted on inspection rather than on a test.

No live-trading, execution, exchange, billing, financial, authorization or database code was
modified. **Live trading remains OFF.** This report makes no trading-readiness and no
production-certification claim.

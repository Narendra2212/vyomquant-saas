/**
 * FE-03 regression: the deploy dialog must open on PAPER, and must not even offer Live
 * unless the server reports the platform can run it.
 *
 * Source-level contract tests (the page is a ~3k-line component whose full render needs the
 * whole app shell); they pin the three facts that matter and fail if any is reverted.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const src = fs.readFileSync(path.resolve(__dirname, "../../src/pages/Strategies.jsx"), "utf8");

describe("FE-03 deploy dialog defaults", () => {
  it("opens on paper, not live", () => {
    expect(src).toMatch(/const ROW_DEPLOY_ENVIRONMENT = "paper";/);
    expect(src).not.toMatch(/const ROW_DEPLOY_ENVIRONMENT = "live";/);
  });

  it("offers Live only after the server says the platform is live (fail closed)", () => {
    expect(src).toMatch(/useState\(false\)[\s\S]{0,40}|const \[liveAvailable, setLiveAvailable\] = useState\(false\)/);
    expect(src).toContain('publicGet("/system/trading-capabilities")');
    expect(src).toMatch(/live_trading_available === true/);
    expect(src).toMatch(/options=\{liveAvailable \? EXECUTION_MODE_OPTIONS : PAPER_ONLY_MODE_OPTIONS\}/);
    // an unreachable endpoint must also leave Live un-offered
    expect(src).toMatch(/catch \{\s*if \(!cancelled\) setLiveAvailable\(false\);/);
  });

  it("forces the request state back to paper when Live is not available", () => {
    expect(src).toMatch(/if \(!liveAvailable\) \{\s*setDeployConfig\(\(prev\) => \(prev\.environment === "paper"/);
  });

  it("the paper-only option list contains no live entry", () => {
    expect(src).toMatch(/PAPER_ONLY_MODE_OPTIONS = Object\.freeze\(\s*EXECUTION_MODE_OPTIONS\.filter\(\(option\) => option\.value === "paper"\)/);
  });
});

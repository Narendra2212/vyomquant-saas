#!/usr/bin/env python3
"""Measure the REAL log-return z-score distribution of BTC/USDT 1m against live Binance data.

WHY
---
`market_data_validation.Z_SCORE_THRESHOLD_DEFAULT` is 5.0, justified in its own comment by
a NORMAL model: "per-point P(|Z|>5) ~ 5.7e-7 ... so even a 10,000-bar window's overall
false-positive probability stays under 1%". The same comment concedes the number is "a
principled default, not a result of fitting real market data, which this sandbox has none
of".

Crypto log-returns are famously fat-tailed, so the normal model understates tail frequency
by orders of magnitude. And `OutlierDetector._z_score_filter` rejects the ENTIRE window if
ANY single point exceeds the threshold -- so the probability that matters is not per-point,
it is per-window.

Measured consequence, already observed: a single 500-bar fetch of real BTC/USDT 1m produced
one >5-sigma outlier and the whole dataset was refused with DATA_QUALITY, which is why
RT-04's Candidate B could not deliver one closed bar.

This fetches real bars and reports, per window size, what fraction of windows would be
rejected at each candidate threshold. Read-only; no platform code is modified.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request

SYMBOL = "BTCUSDT"
INTERVAL = "1m"
BATCH = 1000           # Binance max per request is 1000
BATCHES = 12           # ~12,000 bars, past the 10,000-bar floor RT-04 needs
THRESHOLDS = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]
WINDOWS = [200, 500, 900, 2000, 10000]

# ── DATA-03 (A+C) calibration mode: `--robust` ──────────────────────────────────
#: The instruments the shipped constants were fitted across. One instrument would fit the
#: threshold to one asset's volatility; three of materially different character (a major, a
#: large alt, a high-beta meme) is what makes the chosen pair defensible as a default.
ROBUST_SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
ROBUST_THRESHOLDS = [5.0, 8.0, 10.0, 12.0, 15.0, 20.0]
MAD_TO_SIGMA = 1.4826
FLOOR_WINDOW = 10_000


def fetch_klines(end_time_ms: int | None = None, symbol: str = SYMBOL) -> list:
    url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
           f"&interval={INTERVAL}&limit={BATCH}")
    if end_time_ms is not None:
        url += f"&endTime={end_time_ms}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise
            print(f"   retry {attempt + 1} after {type(exc).__name__}")
            time.sleep(2 ** attempt)
    return []


def _closes_for(symbol: str) -> list[float]:
    """Oldest-first closes, paginated backwards from now, de-duplicated by open time."""
    closes: list[float] = []
    end = None
    seen: set[int] = set()
    for _ in range(BATCHES):
        rows = fetch_klines(end, symbol=symbol)
        if not rows:
            break
        fresh = [r for r in rows if int(r[0]) not in seen]
        for r in fresh:
            seen.add(int(r[0]))
        closes = [float(r[4]) for r in fresh] + closes
        end = int(rows[0][0]) - 1
        time.sleep(0.25)
    return closes


def _log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]


def _robust_scores(rets: list[float]) -> list[float]:
    """``|x - median| / (1.4826 * MAD)`` -- what OutlierDetector._robust_z_scores computes."""
    ordered = sorted(rets)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    devs = sorted(abs(r - median) for r in rets)
    dmid = len(devs) // 2
    mad = devs[dmid] if len(devs) % 2 else (devs[dmid - 1] + devs[dmid]) / 2
    if mad <= 0:
        return [0.0] * len(rets)
    return [abs(r - median) / (MAD_TO_SIGMA * mad) for r in rets]


def _worst_window_fraction(flags: list[bool], w: int) -> float:
    """Max contamination share over any length-w window -- what a budget must cover."""
    if len(flags) < w:
        return float("nan")
    running = sum(flags[:w])
    worst = running
    for i in range(w, len(flags)):
        running += flags[i] - flags[i - w]
        worst = max(worst, running)
    return worst / w


def measure_robust() -> int:
    """DATA-03 (A+C): reproduce the calibration behind the shipped constants.

    Prints the two tables the defaults were read off:
    ``ROBUST_Z_SCORE_THRESHOLD_DEFAULT`` (per-point rate, so "outlier" means something real)
    and ``MAX_OUTLIER_FRACTION_DEFAULT`` (worst-case contamination of a floor-sized window,
    so the budget has headroom over measured reality). Also prints the estimator comparison
    that justifies using median/MAD at all, and the largest real single-bar move, which is
    what ``MAX_BAR_LOG_MOVE_DEFAULT`` has to sit well above.
    """
    series: dict[str, list[float]] = {}
    for sym in ROBUST_SYMBOLS:
        closes = _closes_for(sym)
        if len(closes) < FLOOR_WINDOW:
            print(f"{sym}: only {len(closes)} bars, need >={FLOOR_WINDOW}; skipping")
            continue
        series[sym] = _log_returns(closes)
        print(f"{sym}: {len(closes)} bars, {len(series[sym])} log-returns")

    if not series:
        print("no instrument returned enough bars")
        return 2

    print("\n" + "=" * 78)
    print("(C) ESTIMATOR: the classic scale is inflated by the points it judges")
    print("=" * 78)
    print(f"{'symbol':<10}{'std':>12}{'1.4826*MAD':>14}{'ratio':>8}"
          f"{'max|z|':>9}{'max|rz|':>10}")
    for sym, rets in series.items():
        n = len(rets)
        mean = sum(rets) / n
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / n)
        rz = _robust_scores(rets)
        scale = max(abs(r - mean) / sd for r in rets)
        ordered = sorted(rets)
        mid = len(ordered) // 2
        median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        devs = sorted(abs(r - median) for r in rets)
        dmid = len(devs) // 2
        mad = devs[dmid] if n % 2 else (devs[dmid - 1] + devs[dmid]) / 2
        robust_scale = MAD_TO_SIGMA * mad
        print(f"{sym:<10}{sd:>12.3e}{robust_scale:>14.3e}"
              f"{sd / robust_scale:>8.2f}{scale:>9.2f}{max(rz):>10.2f}")
    print("\nA ratio > 1 means a few extreme returns inflate std and therefore DEFLATE every")
    print("ordinary point's classic z-score -- the masking effect. max|rz| exceeding max|z|")
    print("is the same fact seen from the other side: the classic rule hid its own extremes.")

    print("\n" + "=" * 78)
    print(f"(A)+(C) ADMISSIBILITY over {FLOOR_WINDOW}-bar windows")
    print("=" * 78)
    print(f"{'robust-z':>9}", end="")
    for sym in series:
        print(f"{sym[:4] + ' rate':>14}{sym[:4] + ' worst%':>14}", end="")
    print(f"{'worst of all':>14}")
    for t in ROBUST_THRESHOLDS:
        print(f"{t:>9.1f}", end="")
        worst_all = 0.0
        for rets in series.values():
            flags = [s > t for s in _robust_scores(rets)]
            rate = sum(flags) / len(flags)
            worst = _worst_window_fraction(flags, FLOOR_WINDOW)
            worst_all = max(worst_all, worst)
            print(f"{rate:>14.3e}{worst:>14.4%}", end="")
        print(f"{worst_all:>14.4%}")

    print("\n" + "=" * 78)
    print("MAGNITUDE CEILING: the largest move real data actually produces")
    print("=" * 78)
    for sym, rets in series.items():
        biggest = max(abs(r) for r in rets)
        print(f"{sym:<10} max |log-return| = {biggest:.5f}  "
              f"({math.exp(biggest):.4f}x in one bar)")
    print(f"\nMAX_BAR_LOG_MOVE_DEFAULT is log(2) = {math.log(2):.4f} (2.00x), which must sit")
    print("far ABOVE every figure here -- a real flash crash must still pass -- and far")
    print("BELOW a fabricated spike (DATA-02's fixtures are 3.91 and 7.05).")

    print("\nREADING THIS")
    print("   Pick the first threshold whose 'worst of all' column fits comfortably inside")
    print("   MAX_OUTLIER_FRACTION_DEFAULT while its per-point rate is still non-zero. A")
    print("   threshold with a zero rate detects nothing and is not a safety control --")
    print("   which was the classic rule's own failure mode at 12.0.")
    return 0


def main() -> int:
    if "--robust" in sys.argv:
        return measure_robust()

    print(f"fetching ~{BATCH * BATCHES} real {SYMBOL} {INTERVAL} bars from Binance...")
    closes: list[float] = []
    end = None
    seen_open_times: set[int] = set()
    for i in range(BATCHES):
        rows = fetch_klines(end)
        if not rows:
            break
        fresh = [r for r in rows if int(r[0]) not in seen_open_times]
        for r in fresh:
            seen_open_times.add(int(r[0]))
        # Binance returns oldest-first; prepend older batches.
        closes = [float(r[4]) for r in fresh] + closes
        end = int(rows[0][0]) - 1
        print(f"   batch {i + 1}: +{len(fresh)} bars (total {len(closes)})")
        time.sleep(0.25)

    if len(closes) < 200:
        print(f"only {len(closes)} bars; too few to characterise")
        return 2

    # The platform computes z-scores over bar-to-bar log-returns, per _log_returns.
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    sd = math.sqrt(var)
    zs = [abs(r - mean) / sd for r in rets] if sd > 0 else [0.0] * n

    zs_sorted = sorted(zs, reverse=True)
    print(f"\n{n} log-returns measured.  sd={sd:.6g}")
    print(f"max |z| = {zs_sorted[0]:.2f}")
    print(f"top 12 |z|: {', '.join(f'{z:.2f}' for z in zs_sorted[:12])}")

    print("\nper-point exceedance (real data vs the normal model the default assumes):")
    print(f"   {'threshold':>9} {'real count':>11} {'real rate':>12} {'normal rate':>13} "
          f"{'understated by':>15}")
    for t in THRESHOLDS:
        c = sum(1 for z in zs if z > t)
        real = c / n
        # two-sided normal tail
        normal = math.erfc(t / math.sqrt(2))
        ratio = (real / normal) if normal > 0 and real > 0 else float("inf") if real > 0 else 0
        ratio_s = "-" if real == 0 else (f"{ratio:,.0f}x" if ratio != float("inf") else "inf")
        print(f"   {t:>9.1f} {c:>11} {real:>12.3e} {normal:>13.3e} {ratio_s:>15}")

    print("\nWHAT ACTUALLY MATTERS: the whole window is rejected if ANY point exceeds.")
    print("probability a window is REJECTED, measured by sliding over the real series:")
    header = f"   {'window':>7} " + " ".join(f"{t:>7.1f}" for t in THRESHOLDS)
    print(header)
    print("   " + "-" * (len(header) - 3))
    for w in WINDOWS:
        if w > n:
            print(f"   {w:>7} {'(series too short)':>20}")
            continue
        cells = []
        step = max(1, w // 10)
        starts = list(range(0, n - w + 1, step)) or [0]
        for t in THRESHOLDS:
            rejected = sum(1 for s in starts if any(z > t for z in zs[s:s + w]))
            cells.append(f"{rejected / len(starts):>7.1%}")
        print(f"   {w:>7} " + " ".join(cells))

    print("\nREADING THIS")
    print("   The platform's default is 5.0 and it rejects the whole window on any single")
    print("   exceedance. A 100% figure in the 5.0 column means every window of that size")
    print("   is refused -- i.e. real market data of that length can never be admitted,")
    print("   which is exactly what RT-04's Candidate B hit.")
    print("   RT-04's correctness floor requires >=10,000 bars, so the 10000 row is the")
    print("   one that decides whether the measurement is possible at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

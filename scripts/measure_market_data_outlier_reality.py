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
import time
import urllib.error
import urllib.request

SYMBOL = "BTCUSDT"
INTERVAL = "1m"
BATCH = 1000           # Binance max per request is 1000
BATCHES = 12           # ~12,000 bars, past the 10,000-bar floor RT-04 needs
THRESHOLDS = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]
WINDOWS = [200, 500, 900, 2000, 10000]


def fetch_klines(end_time_ms: int | None = None) -> list:
    url = (f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}"
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


def main() -> int:
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

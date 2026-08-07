"""
Aerora compatibility indicator library.

This module is intentionally valid and self-contained so the trading backend
can boot reliably. The implementations are lightweight NumPy versions that
return array-shaped outputs compatible with the rest of the codebase.
"""

from __future__ import annotations

import numpy as np

EPS = 1e-12

# Available indicators for Strategy Builder
AVAILABLE_INDICATORS = [
    'sma', 'ema', 'wma', 'hma',
    'rsi', 'macd', 'atr', 'bollinger_bands',
    'stochastic', 'cci', 'williams_r', 'obv',
    'mfi', 'adx', 'supertrend', 'trix',
    'vortex_indicator', 'choppiness_index',
    'awesome_oscillator', 'fisher_transform',
    'rolling_z_score', 'historical_volatility',
    'rolling_vwap', 'momentum', 'roc',
    'donchian_channel', 'keltner_channels',
    'ichimoku_cloud', 'cmf', 'psar',
    'fibonacci_rolling', 'pivot_standard', 'pivot_camarilla'
]


def _as_float_array(x):
    return np.asarray(x, dtype=np.float64)


def _nan_array(n):
    return np.full(int(n), np.nan, dtype=np.float64)


def _rolling_mean(a, window):
    a = _as_float_array(a)
    n = len(a)
    out = _nan_array(n)
    if window < 1 or n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.mean(a[i - window + 1 : i + 1])
    return out


def _rolling_std(a, window, ddof=0):
    a = _as_float_array(a)
    n = len(a)
    out = _nan_array(n)
    if window < 2 or n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.std(a[i - window + 1 : i + 1], ddof=ddof)
    return out


def shift_array(arr, periods, fill_value=np.nan):
    arr = _as_float_array(arr)
    n = len(arr)
    out = np.full(n, fill_value, dtype=np.float64)
    periods = int(periods)
    if periods > 0:
        out[periods:] = arr[:-periods]
    elif periods < 0:
        out[:periods] = arr[-periods:]
    else:
        out[:] = arr.copy()
    return out


def sma(close, window=20):
    return _rolling_mean(close, int(window))


def ema(close, window=14):
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    window = int(window)
    if window < 1 or n < window:
        return out
    out[window - 1] = np.mean(close[:window])
    k = 2.0 / (window + 1.0)
    for i in range(window, n):
        out[i] = (close[i] - out[i - 1]) * k + out[i - 1]
    return out


def wma(close, window=9):
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    window = int(window)
    if window < 1 or n < window:
        return out
    weights = np.arange(1.0, window + 1.0)
    weight_sum = np.sum(weights)
    for i in range(window - 1, n):
        out[i] = np.sum(close[i - window + 1 : i + 1] * weights) / weight_sum
    return out


def hma(close, window=14):
    close = _as_float_array(close)
    window = int(window)
    half = max(1, window // 2)
    sqrt_w = max(1, int(np.sqrt(window)))
    return wma(2 * wma(close, half) - wma(close, window), sqrt_w)


def rsi(close, window=14):
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    window = int(window)
    if window < 1 or n <= window:
        return out
    diff = np.diff(close)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.mean(gains[:window])
    avg_loss = np.mean(losses[:window])
    rs = avg_gain / (avg_loss + EPS)
    out[window] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(window + 1, n):
        avg_gain = (avg_gain * (window - 1) + gains[i - 1]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i - 1]) / window
        rs = avg_gain / (avg_loss + EPS)
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(close, fast=12, slow=26, signal=9):
    close = _as_float_array(close)
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(np.where(np.isnan(macd_line), 0.0, macd_line), signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    tr = _nan_array(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    return ema(tr, int(window))


def bollinger_bands(close, window=20, num_std=2.0):
    close = _as_float_array(close)
    mid = sma(close, int(window))
    std = _rolling_std(close, int(window), ddof=0)
    upper = mid + float(num_std) * std
    lower = mid - float(num_std) * std
    denom = np.where(np.abs(upper - lower) < EPS, np.nan, upper - lower)
    pb = (close - lower) / denom
    bw = (upper - lower) / (mid + EPS)
    return mid, lower, upper, bw, pb


def stochastic(high, low, close, k_window=14, d_window=3):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    k = _nan_array(n)
    k_window = int(k_window)
    for i in range(k_window - 1, n):
        hh = np.max(high[i - k_window + 1 : i + 1])
        ll = np.min(low[i - k_window + 1 : i + 1])
        k[i] = 100.0 * (close[i] - ll) / (hh - ll + EPS)
    d = sma(k, int(d_window))
    return k, d


def cci(high, low, close, window=20):
    tp = (_as_float_array(high) + _as_float_array(low) + _as_float_array(close)) / 3.0
    sma_tp = sma(tp, int(window))
    md = _nan_array(len(tp))
    for i in range(int(window) - 1, len(tp)):
        md[i] = np.mean(np.abs(tp[i - int(window) + 1 : i + 1] - sma_tp[i]))
    return (tp - sma_tp) / (0.015 * md + EPS)


def williams_r(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    for i in range(int(window) - 1, n):
        hh = np.max(high[i - int(window) + 1 : i + 1])
        ll = np.min(low[i - int(window) + 1 : i + 1])
        out[i] = -100.0 * (hh - close[i]) / (hh - ll + EPS)
    return out


def obv(close, volume):
    close = _as_float_array(close)
    volume = _as_float_array(volume)
    n = len(close)
    out = _nan_array(n)
    if n == 0:
        return out
    out[0] = volume[0]
    for i in range(1, n):
        if close[i] > close[i - 1]:
            out[i] = out[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            out[i] = out[i - 1] - volume[i]
        else:
            out[i] = out[i - 1]
    return out


def mfi(high, low, close, volume, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    volume = _as_float_array(volume)
    tp = (high + low + close) / 3.0
    mf = tp * volume
    pos = np.where(tp[1:] > tp[:-1], mf[1:], 0.0)
    neg = np.where(tp[1:] < tp[:-1], mf[1:], 0.0)
    out = _nan_array(len(close))
    w = int(window)
    for i in range(w, len(close)):
        p = np.sum(pos[i - w : i])
        n_ = np.sum(neg[i - w : i])
        out[i] = 100.0 - (100.0 / (1.0 + (p / (n_ + EPS))))
    return out


def adx(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    plus_dm = _nan_array(n)
    minus_dm = _nan_array(n)
    tr = _nan_array(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    atr_v = ema(tr, int(window))
    pdi = 100.0 * ema(plus_dm, int(window)) / (atr_v + EPS)
    mdi = 100.0 * ema(minus_dm, int(window)) / (atr_v + EPS)
    adx_v = ema(100.0 * np.abs(pdi - mdi) / (pdi + mdi + EPS), int(window))
    return adx_v, pdi, mdi


def supertrend(high, low, close, window=10, multiplier=3.0):
    atr_v = atr(high, low, close, int(window))
    hl2 = (_as_float_array(high) + _as_float_array(low)) / 2.0
    upper = hl2 + float(multiplier) * atr_v
    lower = hl2 - float(multiplier) * atr_v
    direction = _nan_array(len(close))
    trend = _nan_array(len(close))
    direction[:] = 1.0
    for i in range(1, len(close)):
        if close[i] > upper[i - 1]:
            direction[i] = 1.0
        elif close[i] < lower[i - 1]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]
        trend[i] = lower[i] if direction[i] > 0 else upper[i]
    return trend, direction


def trix(close, window=15):
    close = _as_float_array(close)
    e1 = ema(close, int(window))
    e2 = ema(np.where(np.isnan(e1), 0.0, e1), int(window))
    e3 = ema(np.where(np.isnan(e2), 0.0, e2), int(window))
    out = _nan_array(len(close))
    for i in range(1, len(close)):
        if abs(e3[i - 1]) > EPS:
            out[i] = 100.0 * (e3[i] - e3[i - 1]) / e3[i - 1]
    return out


def vortex_indicator(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    vm_plus = _nan_array(n)
    vm_minus = _nan_array(n)
    tr = _nan_array(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        vm_plus[i] = abs(high[i] - low[i - 1])
        vm_minus[i] = abs(low[i] - high[i - 1])
        tr[i] = max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    out_p = _nan_array(n)
    out_m = _nan_array(n)
    w = int(window)
    for i in range(w - 1, n):
        tr_sum = np.sum(tr[i - w + 1 : i + 1]) + EPS
        out_p[i] = np.sum(vm_plus[i - w + 1 : i + 1]) / tr_sum
        out_m[i] = np.sum(vm_minus[i - w + 1 : i + 1]) / tr_sum
    return out_p, out_m


def choppiness_index(high, low, close, window=14):
    atr_v = atr(high, low, close, int(window))
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(close)
    out = _nan_array(n)
    for i in range(int(window) - 1, n):
        tr_sum = np.sum(atr_v[i - int(window) + 1 : i + 1])
        hh = np.max(high[i - int(window) + 1 : i + 1])
        ll = np.min(low[i - int(window) + 1 : i + 1])
        out[i] = (
            100.0 * np.log10((tr_sum + EPS) / (hh - ll + EPS)) / np.log10(float(window))
        )
    return out


def awesome_oscillator(high, low, fast_w=5, slow_w=34):
    median = (_as_float_array(high) + _as_float_array(low)) / 2.0
    return sma(median, int(fast_w)) - sma(median, int(slow_w))


def fisher_transform(high, low, window=10):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(high)
    out = _nan_array(n)
    hl2 = (high + low) / 2.0
    for i in range(int(window) - 1, n):
        h = np.max(hl2[i - int(window) + 1 : i + 1])
        low = np.min(hl2[i - int(window) + 1 : i + 1])
        value = 2.0 * ((hl2[i] - low) / (h - low + EPS) - 0.5)
        value = np.clip(value, -0.999, 0.999)
        out[i] = 0.5 * np.log((1 + value) / (1 - value))
    return out


def rolling_z_score(close, window=20):
    close = _as_float_array(close)
    mean = sma(close, int(window))
    std = _rolling_std(close, int(window), ddof=0)
    return (close - mean) / (std + EPS)


def historical_volatility(close, window=20, periods_per_year=252):
    close = _as_float_array(close)
    ret = np.diff(np.log(close + EPS), prepend=np.nan)
    return _rolling_std(ret, int(window), ddof=1) * np.sqrt(float(periods_per_year))


def rolling_vwap(high, low, close, volume, window=20):
    tp = (_as_float_array(high) + _as_float_array(low) + _as_float_array(close)) / 3.0
    volume = _as_float_array(volume)
    n = len(close)
    out = _nan_array(n)
    w = int(window)
    for i in range(w - 1, n):
        vol = np.sum(volume[i - w + 1 : i + 1]) + EPS
        out[i] = np.sum(tp[i - w + 1 : i + 1] * volume[i - w + 1 : i + 1]) / vol
    return out


def momentum(close, window=10):
    close = _as_float_array(close)
    out = _nan_array(len(close))
    w = int(window)
    for i in range(w, len(close)):
        out[i] = close[i] - close[i - w]
    return out


def roc(close, window=10):
    close = _as_float_array(close)
    out = _nan_array(len(close))
    w = int(window)
    for i in range(w, len(close)):
        out[i] = 100.0 * (close[i] - close[i - w]) / (close[i - w] + EPS)
    return out


def donchian_channel(high, low, window=20):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(high)
    upper = _nan_array(n)
    lower = _nan_array(n)
    mid = _nan_array(n)
    w = int(window)
    for i in range(w - 1, n):
        upper[i] = np.max(high[i - w + 1 : i + 1])
        lower[i] = np.min(low[i - w + 1 : i + 1])
        mid[i] = (upper[i] + lower[i]) / 2.0
    return upper, lower, mid


def keltner_channels(high, low, close, window=20, multiplier=2.0):
    mid = ema(close, int(window))
    atr_v = atr(high, low, close, int(window))
    upper = mid + float(multiplier) * atr_v
    lower = mid - float(multiplier) * atr_v
    return mid, upper, lower


def ichimoku_cloud(high, low, close, tenkan=9, kijun=26, senkou_b=52):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(close)
    tenkan_line = _nan_array(n)
    kijun_line = _nan_array(n)
    span_a = _nan_array(n)
    span_b = _nan_array(n)
    chikou = shift_array(close, -int(kijun))
    for i in range(int(tenkan) - 1, n):
        tenkan_line[i] = (
            np.max(high[i - int(tenkan) + 1 : i + 1])
            + np.min(low[i - int(tenkan) + 1 : i + 1])
        ) / 2.0
    for i in range(int(kijun) - 1, n):
        kijun_line[i] = (
            np.max(high[i - int(kijun) + 1 : i + 1])
            + np.min(low[i - int(kijun) + 1 : i + 1])
        ) / 2.0
    for i in range(n):
        if not np.isnan(tenkan_line[i]) and not np.isnan(kijun_line[i]):
            j = i + int(kijun)
            if j < n:
                span_a[j] = (tenkan_line[i] + kijun_line[i]) / 2.0
    for i in range(int(senkou_b) - 1, n):
        j = i + int(kijun)
        if j < n:
            span_b[j] = (
                np.max(high[i - int(senkou_b) + 1 : i + 1])
                + np.min(low[i - int(senkou_b) + 1 : i + 1])
            ) / 2.0
    return tenkan_line, kijun_line, span_a, span_b, chikou


def cmf(high, low, close, volume, window=20):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    volume = _as_float_array(volume)
    n = len(close)
    out = _nan_array(n)
    mf = ((close - low) - (high - close)) / (high - low + EPS) * volume
    w = int(window)
    for i in range(w - 1, n):
        out[i] = np.sum(mf[i - w + 1 : i + 1]) / (
            np.sum(volume[i - w + 1 : i + 1]) + EPS
        )
    return out


def psar(high, low, close, step=0.02, max_step=0.2):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(close)
    out = _nan_array(n)
    if n == 0:
        return out
    bull = True
    af = float(step)
    ep = high[0]
    sar = low[0]
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            if low[i] < sar:
                bull = False
                sar = ep
                ep = low[i]
                af = float(step)
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + float(step), float(max_step))
        else:
            if high[i] > sar:
                bull = True
                sar = ep
                ep = high[i]
                af = float(step)
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + float(step), float(max_step))
        out[i] = sar
    return out


def fibonacci_rolling(high, low, window=20):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(high)
    w = int(window)
    l_0 = _nan_array(n)
    l_236 = _nan_array(n)
    l_382 = _nan_array(n)
    l_500 = _nan_array(n)
    l_618 = _nan_array(n)
    l_786 = _nan_array(n)
    l_100 = _nan_array(n)
    for i in range(w - 1, n):
        swing_high = np.max(high[i - w + 1 : i + 1])
        swing_low = np.min(low[i - w + 1 : i + 1])
        diff = swing_high - swing_low
        l_0[i] = swing_low
        l_236[i] = swing_low + 0.236 * diff
        l_382[i] = swing_low + 0.382 * diff
        l_500[i] = swing_low + 0.500 * diff
        l_618[i] = swing_low + 0.618 * diff
        l_786[i] = swing_low + 0.786 * diff
        l_100[i] = swing_high
    return l_0, l_236, l_382, l_500, l_618, l_786, l_100


def pivot_standard(high, low, close):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    pp = _nan_array(n)
    r1 = _nan_array(n)
    r2 = _nan_array(n)
    r3 = _nan_array(n)
    s1 = _nan_array(n)
    s2 = _nan_array(n)
    s3 = _nan_array(n)
    for i in range(1, n):
        prev_h = high[i - 1]
        prev_l = low[i - 1]
        prev_c = close[i - 1]
        pp[i] = (prev_h + prev_l + prev_c) / 3.0
        r1[i] = 2 * pp[i] - prev_l
        s1[i] = 2 * pp[i] - prev_h
        r2[i] = pp[i] + (prev_h - prev_l)
        s2[i] = pp[i] - (prev_h - prev_l)
        r3[i] = prev_h + 2 * (pp[i] - prev_l)
        s3[i] = prev_l - 2 * (prev_h - pp[i])
    return pp, r1, r2, r3, s1, s2, s3


def pivot_camarilla(high, low, close):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    pp = _nan_array(n)
    r1 = _nan_array(n)
    r2 = _nan_array(n)
    r3 = _nan_array(n)
    r4 = _nan_array(n)
    s1 = _nan_array(n)
    s2 = _nan_array(n)
    s3 = _nan_array(n)
    s4 = _nan_array(n)
    for i in range(1, n):
        rng = high[i - 1] - low[i - 1]
        c = close[i - 1]
        pp[i] = (high[i - 1] + low[i - 1] + c) / 3.0
        r1[i] = c + (rng * 1.1 / 12.0)
        r2[i] = c + (rng * 1.1 / 6.0)
        r3[i] = c + (rng * 1.1 / 4.0)
        r4[i] = c + (rng * 1.1 / 2.0)
        s1[i] = c - (rng * 1.1 / 12.0)
        s2[i] = c - (rng * 1.1 / 6.0)
        s3[i] = c - (rng * 1.1 / 4.0)
        s4[i] = c - (rng * 1.1 / 2.0)
    return pp, r1, r2, r3, r4, s1, s2, s3, s4


# aliases used by the rest of the codebase
bb = bollinger_bands
stochastic_oscillator = stochastic
vortex = vortex_indicator
chop = choppiness_index
ao = awesome_oscillator
fisher = fisher_transform
hv = historical_volatility

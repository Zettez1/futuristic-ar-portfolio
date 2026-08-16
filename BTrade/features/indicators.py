import numpy as np
import pandas as pd


def series(values):
    return pd.Series(np.asarray(values, dtype=float))


def ema(values, n):
    return series(values).ewm(span=n, adjust=False).mean()


def sma(values, n):
    return series(values).rolling(n).mean()


def rsi(values, n=14):
    c = series(values)
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    up_v = np.asarray(up, dtype=float)
    dn_v = np.asarray(dn, dtype=float)
    rsi_v = np.full(len(c), np.nan)
    valid = dn_v > 0
    rsi_v[valid] = 100.0 - 100.0 / (1.0 + up_v[valid] / dn_v[valid])
    rsi_v[(dn_v == 0) & (up_v > 0)] = 100.0
    return pd.Series(rsi_v, index=c.index)


def macd(values, fast=12, slow=26, signal=9):
    c = series(values)
    line = ema(c, fast) - ema(c, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def bollinger(values, n=20, k=2.0):
    c = series(values)
    mid = sma(c, n)
    std = c.rolling(n).std()
    return mid, mid + k * std, mid - k * std


def atr(high, low, close, n=14):
    h, l, c = series(high), series(low), series(close)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(high, low, close, n=14):
    h, l, c = series(high), series(low), series(close)
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def stoch(high, low, close, n=14):
    h, l, c = series(high), series(low), series(close)
    ll = l.rolling(n).min()
    hh = h.rolling(n).max()
    rng = (hh - ll).replace(0, np.nan)
    k = 100 * (c - ll) / rng
    d = k.rolling(3).mean()
    return k, d


def obv(close, volume):
    c = series(close)
    v = series(volume)
    signed = v * np.sign(c.diff()).fillna(0)
    return signed.cumsum()


def mfi(high, low, close, volume, n=14):
    h, l, c, v = series(high), series(low), series(close), series(volume)
    tp = (h + l + c) / 3
    mf = tp * v
    diff = tp.diff()
    pos = mf.where(diff > 0, 0).rolling(n).sum()
    neg = mf.where(diff < 0, 0).rolling(n).sum()
    ratio = (pos / neg.replace(0, np.nan))
    return 100 - 100 / (1 + ratio)


def cci(high, low, close, n=20):
    h, l, c = series(high), series(low), series(close)
    tp = (h + l + c) / 3
    ma = tp.rolling(n).mean()
    md = (tp - ma).abs().rolling(n).mean().replace(0, np.nan)
    return (tp - ma) / (0.015 * md)


def roc(values, n=10):
    c = series(values)
    return (c / c.shift(n) - 1) * 100


def vwap(high, low, close, volume):
    h, l, c, v = series(high), series(low), series(close), series(volume)
    tp = (h + l + c) / 3
    cum_vp = (tp * v).cumsum()
    cum_v = v.cumsum().replace(0, np.nan)
    return cum_vp / cum_v


def last(series_obj, default=0.0):
    try:
        v = series_obj.dropna()
        return float(v.iloc[-1]) if len(v) else default
    except Exception:
        return default

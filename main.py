"""OKX USDT perpetual technical signal notifier.

Signals: RSI(14), MACD(12,26,9), Stochastic(KDJ 14,3,3),
Engulfing, EMA(8/21) cross, and VWAP cross.

Stochastic (KDJ) assumptions (documented here and in code):
- We use KDJ/Stochastic parameterization: K length = 14, D = 3 (i.e. 14,3,3).
- pandas_ta.stoch is used to compute %K and %D. Only %K and %D are considered; J is ignored.

These parameters mirror OKX KDJ implementation on their platform.
"""

import logging
import os
import time
from typing import Optional

import ccxt
import numpy as np
import pandas as pd
import pandas_ta as ta
import requests

# Change this list to add or remove USDT-margined perpetual futures.
WATCHLIST = [
    "BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP",
    "LIT", "SUI", "BNB",
]
TIMEFRAMES = ("1h", "4h", "1d")
MACD_TIMEFRAMES = ("1h", "4h", "1d")
STOCH_TIMEFRAMES = ("1h", "4h", "1d")
ENGULFING_TIMEFRAMES = ("1h", "4h", "1d")
EMA_TIMEFRAMES = ("1h", "4h", "1d")
VWAP_TIMEFRAMES = ("1h", "4h", "1d")
VWAP_COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")
CORE_INDICATORS = ("RSI", "MACD", "Engulfing", "EMA Cross", "VWAP Cross")
SECONDARY_INDICATORS = ("Stochastic", "Supertrend", "BB Squeeze Breakout")
ENGULFING_LOW_TOLERANCE = 1.005
TIMEFRAME_IMPORTANCE = {
    "1d": "🏆",
    "4h": "🔥",
    "1h": "⚡️",
}
RSI_LENGTH = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
STOCH_K = 14
STOCH_D = 3
STOCH_SMOOTH = 1
EMA_FAST = 8
EMA_SLOW = 21
EMA_TREND = 20
ATR_LENGTH = 14
SECONDARY_N = 20
MULT = 1.5
VOLUME_AVG_LENGTH = SECONDARY_N
HIGHER_TF_MAP = {"1h": "4h"}
CORE_VOLUME_RATIOS = {
    "RSI": 0.9,
    "MACD": 0.9,
    "Engulfing": 1.2,
    "EMA Cross": 1.0,
    "VWAP Cross": 1.0,
}
HIGHER_CONFIRM_CACHE_SECONDS = 10
_higher_tf_direction_cache: dict[tuple[str, str], tuple[float, Optional[str]]] = {}
OHLCV_LIMIT = 100
REQUEST_DELAY_SECONDS = 0.5
CANDLE_CLOSE_GRACE_SECONDS = 30
SCAN_INTERVAL_SECONDS = 15 * 60
TIMEFRAME_MILLISECONDS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,  # UTC 00:00 close (ccxt okx appends "utc" for >=6h bars)
}
ENGULFING_BODY_RATIO = {"1h": 1.1, "4h": 1.0, "1d": 1.0}
ENGULFING_LOOKBACK = {"1h": 8, "4h": 5, "1d": 5}
VWAP_WINDOW = {"1h": 24, "4h": 6, "1d": 7}
VWAP_BREAKOUT_STRENGTH = {"1h": 1.003, "4h": 1.0015, "1d": 1.0015}
VWAP_BREAKDOWN_STRENGTH = {"1h": 0.997, "4h": 0.9985, "1d": 0.9985}
OKX_BAR = {"1h": "1H", "4h": "4H", "1d": "1Dutc"}
PIVOT_LEFT = 5
PIVOT_RIGHT = 3
DIV_RANGE_MIN = 5    # TV rangeLower
DIV_RANGE_MAX = 60   # TV rangeUpper
FIB_ZONE_TOP = 0.5
FIB_ZONE_BOTTOM = 0.618
FIB_INVALID = 0.786
FIB_MIN_RANGE_ATR = 3.0
DERIV_COINS = VWAP_COINS  # funding/OI info alerts: major coins only
FUNDING_HISTORY_LIMIT = 90   # 8h settlements (~30 days)
FUNDING_MIN_ABS = 0.0002     # ignore |rate| < 0.02% per 8h even if it is a percentile extreme
OI_HISTORY_LIMIT = 768       # hourly bars = 744 24h-changes (~31 days); OKX serves 100 per call, ~60 days deep
OI_PAGE_LIMIT = 100
OI_CHANGE_HOURS = 24
OI_EXTREME_TAIL = 0.01       # OI changes are autocorrelated, so a tighter tail than funding
OI_QUIET_HOURS = 24          # no alert if any of the last 24 hourly values was already extreme (stops re-firing)
EXTREME_TAIL = 0.05          # top/bottom 5% of own history
EXTREME_MIN_SAMPLES = 30
FIB_LABELS = {"LONG": "피보나치 되돌림 지지 반등(0.5~0.618)", "SHORT": "피보나치 되돌림 저항 반락(0.5~0.618)"}


def create_exchange() -> ccxt.okx:
    """Create a public-only OKX client for market-data requests."""
    exchange = ccxt.okx(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    exchange.has["fetchCurrencies"] = False
    return exchange


def _prepare_ohlcv_frame(candles: list) -> pd.DataFrame:
    """Normalize raw OHLCV rows into a sorted numeric frame."""
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["timestamp", *numeric_columns]).copy()
    return frame.sort_values("timestamp").drop_duplicates(subset="timestamp")


def completed_candles(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Return only fully closed candles for a timeframe."""
    now_ms = int(time.time() * 1000)
    close_cutoff_ms = now_ms - (CANDLE_CLOSE_GRACE_SECONDS * 1000)
    timeframe_ms = TIMEFRAME_MILLISECONDS[timeframe]
    completed = frame[frame["timestamp"] + timeframe_ms <= close_cutoff_ms]
    if "confirm" in completed:
        completed = completed[completed["confirm"].astype(str) == "1"]
    return completed.copy()


def fetch_confirmed_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OKX history candles, retaining its authoritative ``confirm`` field."""
    market = exchange.market(symbol)
    response = exchange.publicGetMarketHistoryCandles(
        {"instId": market["id"], "bar": OKX_BAR[timeframe], "limit": str(OHLCV_LIMIT)}
    )
    if response.get("code") not in (None, "0", 0):
        raise RuntimeError(f"OKX history-candles failed: {response.get('msg', response)}")
    rows = response.get("data", [])
    frame = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume", "vol_ccy", "vol_quote", "confirm"]
    )
    if frame.empty:
        return frame
    frame = _prepare_ohlcv_frame(frame[["timestamp", "open", "high", "low", "close", "volume"]].values.tolist())
    # The endpoint is reverse chronological; reattach confirmation by timestamp.
    confirms = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "vol_ccy", "vol_quote", "confirm"])[["timestamp", "confirm"]]
    confirms["timestamp"] = pd.to_numeric(confirms["timestamp"], errors="coerce")
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    return frame.merge(confirms, on="timestamp", how="left").sort_values("timestamp").reset_index(drop=True)


def fetch_rsi_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV, remove incomplete/invalid data, and calculate RSI(14)."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = _prepare_ohlcv_frame(candles)
    frame["rsi"] = ta.rsi(frame["close"], length=RSI_LENGTH)
    return frame.dropna(subset=["rsi"]).reset_index(drop=True)


def fetch_macd_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV, remove incomplete/invalid data, and calculate MACD(12, 26, 9)."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = _prepare_ohlcv_frame(candles)
    macd = ta.macd(
        frame["close"],
        fast=MACD_FAST,
        slow=MACD_SLOW,
        signal=MACD_SIGNAL,
    )
    if macd is None:
        return frame.iloc[0:0].copy()

    dif_column = f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    dea_column = f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    frame["dif"] = macd[dif_column]
    frame["dea"] = macd[dea_column]
    return frame.dropna(subset=["dif", "dea"]).reset_index(drop=True)


def fetch_stoch_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV and calculate Stochastic %K and %D (OKX KDJ 14,3,3)."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = _prepare_ohlcv_frame(candles)
    stoch = ta.stoch(frame["high"], frame["low"], frame["close"], k=STOCH_K, d=STOCH_D, smooth_k=STOCH_SMOOTH)
    if stoch is None or len(stoch.columns) < 2:
        return frame.iloc[0:0].copy()

    k_col, d_col = stoch.columns[:2]
    frame["k"] = stoch[k_col]
    frame["d"] = stoch[d_col]
    return frame.dropna(subset=["k", "d"]).reset_index(drop=True)


def fetch_engulfing_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV and calculate EMA(20) for Engulfing trend filtering."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = _prepare_ohlcv_frame(candles)
    frame["ema20"] = ta.ema(frame["close"], length=EMA_TREND)
    return frame.dropna(subset=["ema20"]).reset_index(drop=True)


def fetch_ema_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV and calculate EMA(8/21) plus ATR(14) for cross filtering."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = _prepare_ohlcv_frame(candles)
    frame["ema8"] = ta.ema(frame["close"], length=EMA_FAST)
    frame["ema21"] = ta.ema(frame["close"], length=EMA_SLOW)
    frame["atr14"] = ta.atr(frame["high"], frame["low"], frame["close"], length=ATR_LENGTH)
    return frame.dropna(subset=["ema8", "ema21", "atr14"]).reset_index(drop=True)


def fetch_vwap_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV and calculate rolling VWAP for the configured window."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = _prepare_ohlcv_frame(candles)
    window = VWAP_WINDOW[timeframe]
    typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    volume_sum = frame["volume"].rolling(window).sum()
    frame["vwap"] = (typical_price * frame["volume"]).rolling(window).sum() / volume_sum
    return frame.dropna(subset=["vwap"]).reset_index(drop=True)


def latest_completed_candles(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[pd.Series, pd.Series]]:
    """Return the two latest confirmed, closed candles for a timeframe."""
    completed = completed_candles(frame, timeframe)
    if len(completed) < 2:
        return None
    return completed.iloc[-2], completed.iloc[-1]


def is_freshly_closed(current: pd.Series, timeframe: str) -> bool:
    """Return True only during the first scan after ``current`` closed."""
    now_ms = int(time.time() * 1000)
    close_ms = int(current["timestamp"]) + TIMEFRAME_MILLISECONDS[timeframe]
    return now_ms - close_ms < SCAN_INTERVAL_SECONDS * 1000


def find_pivots(series: pd.Series, left: int, right: int) -> tuple[list[int], list[int]]:
    """Return positional (low, high) pivot indexes; NaN in a window or short data gives none."""
    values = series.to_numpy(dtype=float)
    lows: list[int] = []
    highs: list[int] = []
    for i in range(left, len(values) - right):
        window = values[i - left:i + right + 1]
        if np.isnan(window).any():
            continue
        v = values[i]
        before, after = values[i - left:i], values[i + 1:i + right + 1]
        if v < before.min() and v <= after.min():
            lows.append(i)
        if v > before.max() and v >= after.max():
            highs.append(i)
    return lows, highs


def find_rsi_divergences(frame: pd.DataFrame, timeframe: str) -> list[tuple[str, str]]:
    """Return [(side, label)] for regular/hidden RSI divergences confirmed on the latest closed candle."""
    completed = completed_candles(frame, timeframe).reset_index(drop=True)
    n = len(completed)
    if n < PIVOT_LEFT + PIVOT_RIGHT + 2 or not is_freshly_closed(completed.iloc[-1], timeframe):
        return []
    rsi = completed["rsi"]
    lows, highs = find_pivots(rsi, PIVOT_LEFT, PIVOT_RIGHT)
    last = n - 1 - PIVOT_RIGHT
    found: list[tuple[str, str]] = []
    if len(lows) >= 2 and lows[-1] == last and DIV_RANGE_MIN + 1 <= lows[-1] - lows[-2] <= DIV_RANGE_MAX + 1:
        p1, p2 = lows[-2], lows[-1]
        if rsi[p2] > rsi[p1] and completed["low"][p2] < completed["low"][p1]:
            found.append(("LONG", "RSI 일반 Div"))
        elif rsi[p2] < rsi[p1] and completed["low"][p2] > completed["low"][p1]:
            found.append(("LONG", "RSI 히든 Div"))
    if len(highs) >= 2 and highs[-1] == last and DIV_RANGE_MIN + 1 <= highs[-1] - highs[-2] <= DIV_RANGE_MAX + 1:
        h1, h2 = highs[-2], highs[-1]
        if rsi[h2] < rsi[h1] and completed["high"][h2] > completed["high"][h1]:
            found.append(("SHORT", "RSI 일반 Div"))
        elif rsi[h2] > rsi[h1] and completed["high"][h2] < completed["high"][h1]:
            found.append(("SHORT", "RSI 히든 Div"))
    return found


def find_fib_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, str]]:
    """Return (side, label) when the latest closed candle first bounces inside the 0.5-0.618 Fib zone."""
    completed = completed_candles(frame, timeframe).reset_index(drop=True)
    n = len(completed)
    if n < PIVOT_LEFT + PIVOT_RIGHT + 2 or not is_freshly_closed(completed.iloc[-1], timeframe):
        return None
    atr = ta.atr(completed["high"], completed["low"], completed["close"], length=14)
    atr_value = None if atr is None or atr.empty else atr.iloc[-1]
    if atr_value is None or pd.isna(atr_value) or atr_value <= 0:
        return None
    high, low, close, open_ = completed["high"], completed["low"], completed["close"], completed["open"]
    lo_idx = find_pivots(low, PIVOT_LEFT, PIVOT_RIGHT)[0]
    hi_idx = find_pivots(high, PIVOT_LEFT, PIVOT_RIGHT)[1]
    if not lo_idx or not hi_idx or lo_idx[-1] == hi_idx[-1]:
        return None
    c = n - 1
    if hi_idx[-1] > lo_idx[-1]:
        side, ph = "LONG", hi_idx[-1]
        pl = max((i for i in lo_idx if i < ph), default=None)
    else:
        side, pl = "SHORT", lo_idx[-1]
        ph = max((i for i in hi_idx if i < pl), default=None)
    if ph is None or pl is None:
        return None
    top_price, bottom_price = high[ph], low[pl]
    rng = top_price - bottom_price
    if rng < atr_value * FIB_MIN_RANGE_ATR or ph - pl > DIV_RANGE_MAX or pl - ph > DIV_RANGE_MAX:
        return None
    if side == "LONG":
        # empty close slice -> NaN comparison is False -> passes
        if high[ph + 1:c + 1].max() > top_price or close[ph + 1:c].min() < top_price - FIB_INVALID * rng:
            return None
        zone_top, zone_bottom = top_price - FIB_ZONE_TOP * rng, top_price - FIB_ZONE_BOTTOM * rng
        cond = lambda k: low[k] <= zone_top and close[k] > zone_bottom and close[k] > open_[k]
        start = ph
    else:
        if low[pl + 1:c + 1].min() < bottom_price or close[pl + 1:c].max() > bottom_price + FIB_INVALID * rng:
            return None
        zone_bottom, zone_top = bottom_price + FIB_ZONE_TOP * rng, bottom_price + FIB_ZONE_BOTTOM * rng
        cond = lambda k: high[k] >= zone_bottom and close[k] < zone_top and close[k] < open_[k]
        start = pl
    if cond(c) and not any(cond(k) for k in range(start + PIVOT_RIGHT, c)):
        return side, FIB_LABELS[side]
    return None


def find_extreme(
    values: list[float], min_abs: float = 0.0, tail: float = EXTREME_TAIL, quiet: int = 1
) -> Optional[tuple[str, float]]:
    """Return ("HIGH"|"LOW", tail share) when the last value has just entered the tail of its own history.

    Suppressed if any of the previous ``quiet`` values was already extreme on the same side.
    """
    values = [v for v in values if v is not None and not pd.isna(v)]
    if len(values) < EXTREME_MIN_SAMPLES:
        return None
    base = values[:-1]

    def side_of(x: float) -> Optional[tuple[str, float]]:
        if abs(x) < min_abs:
            return None
        top = sum(v >= x for v in base) / len(base)
        bottom = sum(v <= x for v in base) / len(base)
        if top <= tail:
            return "HIGH", top
        if bottom <= tail:
            return "LOW", bottom
        return None

    current = side_of(values[-1])
    if current is None or any(
        (earlier := side_of(v)) is not None and earlier[0] == current[0] for v in values[-1 - quiet:-1]
    ):
        return None
    return current


def find_funding_info(exchange: ccxt.okx, symbol: str) -> Optional[str]:
    """Info line when the latest settled funding rate just entered an extreme of its recent history."""
    rows = exchange.fetch_funding_rate_history(symbol, limit=FUNDING_HISTORY_LIMIT)
    rows = sorted((r for r in rows if r.get("fundingRate") is not None and r.get("timestamp")), key=lambda r: r["timestamp"])
    if not rows or int(time.time() * 1000) - rows[-1]["timestamp"] >= SCAN_INTERVAL_SECONDS * 1000:
        return None  # same one-shot rule as is_freshly_closed: only the first scan after settlement
    rates = [r["fundingRate"] for r in rows]
    hit = find_extreme(rates, FUNDING_MIN_ABS)
    if hit is None or (hit[0] == "HIGH") != (rates[-1] > 0):
        return None
    side, share = hit
    return (
        f"펀딩비 {rates[-1] * 100:+.3f}% (직전 {len(rates) - 1}회 중 {'상위' if side == 'HIGH' else '하위'} {share:.0%})"
        f" — {'롱' if rates[-1] > 0 else '숏'} 쏠림"
    )


def find_oi_info(exchange: ccxt.okx, symbol: str, price_change: Optional[float]) -> Optional[str]:
    """Info line when the 24h open-interest change just entered an extreme of its recent history."""
    hour_ms = TIMEFRAME_MILLISECONDS["1h"]
    now_ms = int(time.time() * 1000)
    rows_by_ts: dict[int, dict] = {}
    until = None
    while len(rows_by_ts) < OI_HISTORY_LIMIT:  # OKX returns at most 100 rows per call, newest first
        page = exchange.fetch_open_interest_history(
            symbol, "1h", limit=OI_PAGE_LIMIT, params={"until": until} if until else {}
        )
        stamps = [r["timestamp"] for r in page if r.get("timestamp")]
        if until is None:  # same one-shot rule as funding: skip the extra pages unless an hour just closed
            closed = [t for t in stamps if t + hour_ms <= now_ms]
            if not closed or now_ms - (max(closed) + hour_ms) >= SCAN_INTERVAL_SECONDS * 1000:
                return None
        rows_by_ts.update({r["timestamp"]: r for r in page if r.get("timestamp")})
        if not stamps or min(stamps) == until:
            break
        until = min(stamps)
    rows = list(rows_by_ts.values())
    # row timestamp = bucket start (confirmed on OKX); drop the still-forming hour
    rows = sorted(
        (r for r in rows if (r.get("openInterestValue") or r.get("openInterestAmount"))
         and r["timestamp"] + hour_ms <= now_ms),
        key=lambda r: r["timestamp"],
    )
    if len(rows) <= OI_CHANGE_HOURS:
        return None
    oi = [r.get("openInterestValue") or r.get("openInterestAmount") for r in rows]
    changes = [oi[i] / oi[i - OI_CHANGE_HOURS] - 1 for i in range(OI_CHANGE_HOURS, len(oi))]
    hit = find_extreme(changes, tail=OI_EXTREME_TAIL, quiet=OI_QUIET_HOURS)
    if hit is None:
        return None
    side, share = hit
    line = (
        f"OI 24h {changes[-1]:+.1%} (직전 {len(changes) - 1}시간 중 {'상위' if side == 'HIGH' else '하위'} {share:.0%})"
        f" — {'급증' if side == 'HIGH' else '급감'}"
    )
    return line + (f" | 가격 24h {price_change:+.1%}" if price_change is not None else "")


def price_change_24h(frame: Optional[pd.DataFrame]) -> Optional[float]:
    """24h close-to-close change from completed 1h candles, None when unavailable."""
    if frame is None:
        return None
    close = completed_candles(frame, "1h")["close"]
    return close.iloc[-1] / close.iloc[-25] - 1 if len(close) >= 25 else None


def passes_volume_filter(
    frame: pd.DataFrame,
    timeframe: str,
    signal_candle: pd.Series,
    min_ratio: float = 1.0,
) -> bool:
    """Return True when the signal candle volume is >= recent average * min_ratio."""
    completed = completed_candles(frame, timeframe)
    if len(completed) < VOLUME_AVG_LENGTH + 1:
        return False

    if int(completed.iloc[-1]["timestamp"]) != int(signal_candle["timestamp"]):
        return False

    prior_volumes = completed.iloc[-(VOLUME_AVG_LENGTH + 1):-1]["volume"]
    avg_volume = float(prior_volumes.mean())
    if avg_volume <= 0:
        return False

    signal_volume = float(signal_candle["volume"])
    return signal_volume >= avg_volume * min_ratio


def _derive_higher_tf_side(frame: pd.DataFrame, higher_tf: str) -> Optional[str]:
    completed = completed_candles(frame, higher_tf)
    if completed.empty:
        return None
    current = completed.iloc[-1]
    close = float(current["close"])
    ema8 = float(current["ema8"])
    ema21 = float(current["ema21"])
    if close > ema21 and ema8 >= ema21:
        return "LONG"
    if close < ema21 and ema8 <= ema21:
        return "SHORT"
    return None


def _fetch_higher_trend_frame(exchange: ccxt.okx, symbol: str, higher_tf: str) -> pd.DataFrame:
    frame = fetch_confirmed_frame(exchange, symbol, higher_tf)
    frame["ema8"] = ta.ema(frame["close"], length=EMA_FAST)
    frame["ema21"] = ta.ema(frame["close"], length=EMA_SLOW)
    return frame.dropna(subset=["ema8", "ema21"]).reset_index(drop=True)


def _get_higher_tf_side(exchange: ccxt.okx, coin: str, symbol: str, higher_tf: str) -> Optional[str]:
    cache_key = (coin, higher_tf)
    now = time.time()
    cached = _higher_tf_direction_cache.get(cache_key)
    if cached and (now - cached[0]) < HIGHER_CONFIRM_CACHE_SECONDS:
        return cached[1]
    frame = _fetch_higher_trend_frame(exchange, symbol, higher_tf)
    side = _derive_higher_tf_side(frame, higher_tf)
    _higher_tf_direction_cache[cache_key] = (now, side)
    return side


def _confirm_higher(side: str, coin: str, timeframe: str, exchange: ccxt.okx, symbol: str) -> bool:
    # ponytail: 4h/1d secondary is trusted standalone and bypasses higher timeframe checks.
    if timeframe in ("4h", "1d"):
        logging.info("Higher-TF bypass for %s %s secondary signal (%s)", coin, side, timeframe)
        return True
    higher_tf = HIGHER_TF_MAP.get(timeframe)
    if not higher_tf:
        logging.warning("Higher-TF fail-closed for %s %s: no mapping for %s", coin, side, timeframe)
        return False
    try:
        higher_side = _get_higher_tf_side(exchange, coin, symbol, higher_tf)
    except (ccxt.BaseError, ValueError, RuntimeError, KeyError) as error:
        logging.warning("Higher-TF fail-closed for %s %s (%s): %s", coin, side, timeframe, error)
        return False
    if higher_side is None:
        logging.info("Higher-TF fail-closed for %s %s (%s): neutral/insufficient", coin, side, timeframe)
        return False
    if higher_side != side:
        logging.info(
            "Higher-TF mismatch for %s (%s): signal=%s higher(%s)=%s",
            coin,
            timeframe,
            side,
            higher_tf,
            higher_side,
        )
        return False
    return True


def _candle_body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _upper_wick(row: pd.Series) -> float:
    return float(row["high"]) - max(float(row["open"]), float(row["close"]))


def _lower_wick(row: pd.Series) -> float:
    return min(float(row["open"]), float(row["close"])) - float(row["low"])


def _is_doji_blocked(previous: pd.Series, current: pd.Series) -> bool:
    previous_body = _candle_body(previous)
    reference_price = float(current["close"])
    if reference_price <= 0:
        return True
    return previous_body < reference_price * 0.004


def _is_excessive_wick_blocked(row: pd.Series) -> bool:
    body = _candle_body(row)
    if body <= 0:
        return True
    return (_upper_wick(row) + _lower_wick(row)) > body * 1.8


def _basic_bullish_engulfing(previous: pd.Series, current: pd.Series, body_ratio: float) -> bool:
    prev_open = float(previous["open"])
    prev_close = float(previous["close"])
    curr_open = float(current["open"])
    curr_close = float(current["close"])
    if not (prev_close < prev_open and curr_close > curr_open):
        return False
    if not (curr_open <= prev_close and curr_close > prev_open):
        return False
    previous_body = prev_open - prev_close
    current_body = curr_close - curr_open
    return current_body >= previous_body * body_ratio


def _basic_bearish_engulfing(previous: pd.Series, current: pd.Series, body_ratio: float) -> bool:
    prev_open = float(previous["open"])
    prev_close = float(previous["close"])
    curr_open = float(current["open"])
    curr_close = float(current["close"])
    if not (prev_close > prev_open and curr_close < curr_open):
        return False
    if not (curr_open >= prev_close and curr_close < prev_open):
        return False
    previous_body = prev_close - prev_open
    current_body = curr_open - curr_close
    return current_body >= previous_body * body_ratio


def _has_recent_same_direction_engulfing(
    completed: pd.DataFrame,
    current_index: int,
    direction: str,
    timeframe: str,
) -> bool:
    """Return True when the same Engulfing direction appeared within the prior 3 candles."""
    body_ratio = ENGULFING_BODY_RATIO[timeframe]
    for end_index in (current_index - 3, current_index - 2, current_index - 1):
        prev_index = end_index - 1
        if prev_index < 0 or end_index >= current_index:
            continue
        previous = completed.iloc[prev_index]
        current = completed.iloc[end_index]
        if direction == "LONG" and _basic_bullish_engulfing(previous, current, body_ratio):
            return True
        if direction == "SHORT" and _basic_bearish_engulfing(previous, current, body_ratio):
            return True
    return False


def find_rsi_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return an RSI reversal signal from the two latest completed candles."""
    candles = latest_completed_candles(frame, timeframe)
    if candles is None:
        return None

    previous, current = candles
    if not is_freshly_closed(current, timeframe):
        return None
    if previous["rsi"] < 30 and current["rsi"] >= 30:
        return "LONG", previous, current
    if previous["rsi"] > 70 and current["rsi"] <= 70:
        return "SHORT", previous, current
    return None


def find_macd_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a MACD golden/dead cross from the two latest completed candles."""
    candles = latest_completed_candles(frame, timeframe)
    if candles is None:
        return None

    previous, current = candles
    if not is_freshly_closed(current, timeframe):
        return None

    if (
        previous["dif"] < previous["dea"]
        and current["dif"] >= current["dea"]
        and current["dif"] < 0
        and current["dea"] < 0
    ):
        return "LONG", previous, current

    if (
        previous["dif"] > previous["dea"]
        and current["dif"] <= current["dea"]
        and current["dif"] > 0
        and current["dea"] > 0
    ):
        return "SHORT", previous, current

    return None


def find_stoch_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a Stochastic (KDJ) signal from the two latest completed candles."""
    candles = latest_completed_candles(frame, timeframe)
    if candles is None:
        return None

    previous, current = candles
    if not is_freshly_closed(current, timeframe):
        return None

    prev_k = float(previous["k"])
    prev_d = float(previous["d"])
    curr_k = float(current["k"])
    curr_d = float(current["d"])

    if (
        prev_k < prev_d
        and curr_k >= curr_d
        and curr_k <= 20
        and curr_d <= 20
    ):
        return "LONG", previous, current

    if (
        prev_k > prev_d
        and curr_k <= curr_d
        and curr_k >= 80
        and curr_d >= 80
    ):
        return "SHORT", previous, current

    return None


def find_engulfing_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a quality-filtered Engulfing signal from the two latest completed candles."""
    completed = completed_candles(frame, timeframe)
    if len(completed) < max(VOLUME_AVG_LENGTH + 1, ENGULFING_LOOKBACK[timeframe], EMA_TREND) + 1:
        return None

    previous = completed.iloc[-2]
    current = completed.iloc[-1]
    if not is_freshly_closed(current, timeframe):
        return None
    if _is_doji_blocked(previous, current):
        return None
    if _is_excessive_wick_blocked(current):
        return None

    body_ratio = ENGULFING_BODY_RATIO[timeframe]
    lookback = ENGULFING_LOOKBACK[timeframe]
    current_index = len(completed) - 1
    prior_window = completed.iloc[-(lookback + 1):-1]

    curr_open = float(current["open"])
    curr_close = float(current["close"])
    curr_low = float(current["low"])
    curr_high = float(current["high"])

    if _basic_bullish_engulfing(previous, current, body_ratio):
        if _has_recent_same_direction_engulfing(completed, current_index, "LONG", timeframe):
            return None
        if curr_low > float(prior_window["low"].min()) * ENGULFING_LOW_TOLERANCE:
            return None
        if curr_close <= float(current["ema20"]):
            return None
        bullish_body = curr_close - curr_open
        if curr_close < curr_open + bullish_body * 0.5:
            return None
        return "LONG", previous, current

    if _basic_bearish_engulfing(previous, current, body_ratio):
        if _has_recent_same_direction_engulfing(completed, current_index, "SHORT", timeframe):
            return None
        if curr_high < float(prior_window["high"].max()) / ENGULFING_LOW_TOLERANCE:
            return None
        if curr_close >= float(current["ema20"]):
            return None
        bearish_body = curr_open - curr_close
        if curr_close > curr_open - bearish_body * 0.5:
            return None
        return "SHORT", previous, current

    return None


def find_ema_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return an EMA(8/21) golden/dead cross from the two latest completed candles."""
    completed = completed_candles(frame, timeframe)
    if len(completed) < 5:
        return None

    previous = completed.iloc[-2]
    current = completed.iloc[-1]
    if not is_freshly_closed(current, timeframe):
        return None

    prev_ema8 = float(previous["ema8"])
    prev_ema21 = float(previous["ema21"])
    curr_ema8 = float(current["ema8"])
    curr_ema21 = float(current["ema21"])
    curr_close = float(current["close"])
    curr_atr = float(current["atr14"])
    ema_gap = abs(curr_ema8 - curr_ema21)
    if curr_atr <= 0 or ema_gap < curr_atr * 0.15:
        return None

    ema8_three_ago = float(completed.iloc[-4]["ema8"])

    if (
        prev_ema8 < prev_ema21
        and curr_ema8 > curr_ema21
        and curr_close > curr_ema8
        and all(float(completed.iloc[index]["ema8"]) < float(completed.iloc[index]["ema21"]) for index in (-5, -4, -3))
        and curr_ema8 - ema8_three_ago > 0
    ):
        return "LONG", previous, current

    if (
        prev_ema8 > prev_ema21
        and curr_ema8 < curr_ema21
        and curr_close < curr_ema8
        and all(float(completed.iloc[index]["ema8"]) > float(completed.iloc[index]["ema21"]) for index in (-5, -4, -3))
        and curr_ema8 - ema8_three_ago < 0
    ):
        return "SHORT", previous, current

    return None


def find_vwap_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a VWAP cross signal from the two latest completed candles."""
    completed = completed_candles(frame, timeframe)
    if len(completed) < max(VWAP_WINDOW[timeframe], 5):
        return None

    previous = completed.iloc[-2]
    current = completed.iloc[-1]
    if not is_freshly_closed(current, timeframe):
        return None

    prev_close = float(previous["close"])
    curr_close = float(current["close"])
    curr_open = float(current["open"])
    curr_high = float(current["high"])
    curr_low = float(current["low"])
    prev_vwap = float(previous["vwap"])
    curr_vwap = float(current["vwap"])
    candle_range = curr_high - curr_low

    if (
        prev_close < prev_vwap
        and curr_close > curr_vwap
        and curr_close > curr_vwap * VWAP_BREAKOUT_STRENGTH[timeframe]
        and all(float(completed.iloc[index]["close"]) < float(completed.iloc[index]["vwap"]) for index in (-5, -4, -3, -2))
        and curr_close > curr_open
        and candle_range > 0
    ):
        return "LONG", previous, current

    if (
        prev_close > prev_vwap
        and curr_close < curr_vwap
        and curr_close < curr_vwap * VWAP_BREAKDOWN_STRENGTH[timeframe]
        and all(float(completed.iloc[index]["close"]) > float(completed.iloc[index]["vwap"]) for index in (-5, -4, -3, -2))
        and curr_close < curr_open
        and candle_range > 0
    ):
        return "SHORT", previous, current

    return None


def fetch_supertrend_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    frame = fetch_confirmed_frame(exchange, symbol, timeframe)
    frame["atr10"] = ta.atr(frame["high"], frame["low"], frame["close"], length=10)
    midpoint = (frame["high"] + frame["low"]) / 2
    upper = midpoint + frame["atr10"] * 3.0
    lower = midpoint - frame["atr10"] * 3.0
    supertrend = pd.Series(index=frame.index, dtype=float)
    trend_up = pd.Series(index=frame.index, dtype=bool)
    for index in range(len(frame)):
        if pd.isna(frame.at[index, "atr10"]):
            continue
        if index == 0 or pd.isna(supertrend.iloc[index - 1]):
            supertrend.iloc[index] = lower.iloc[index]
            trend_up.iloc[index] = True
            continue
        previous_trend_up = bool(trend_up.iloc[index - 1])
        previous_supertrend = float(supertrend.iloc[index - 1])
        close = float(frame.at[index, "close"])
        if previous_trend_up:
            trend_up.iloc[index] = close >= previous_supertrend
            supertrend.iloc[index] = max(float(lower.iloc[index]), previous_supertrend) if trend_up.iloc[index] else float(upper.iloc[index])
        else:
            trend_up.iloc[index] = close > previous_supertrend
            supertrend.iloc[index] = float(lower.iloc[index]) if trend_up.iloc[index] else min(float(upper.iloc[index]), previous_supertrend)
    frame["supertrend"] = supertrend
    return frame.dropna(subset=["atr10", "supertrend"]).reset_index(drop=True)


def fetch_bb_squeeze_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    frame = fetch_confirmed_frame(exchange, symbol, timeframe)
    sma = frame["close"].rolling(20).mean()
    std = frame["close"].rolling(20).std(ddof=0)
    frame["bb_upper"] = sma + std * 2
    frame["bb_lower"] = sma - std * 2
    frame["bb_width"] = frame["bb_upper"] - frame["bb_lower"]
    frame["atr14"] = ta.atr(frame["high"], frame["low"], frame["close"], length=14)
    return frame.dropna(subset=["bb_upper", "bb_lower", "bb_width", "atr14"]).reset_index(drop=True)


def _new_indicator_pair(frame: pd.DataFrame, timeframe: str, minimum: int) -> Optional[tuple[pd.DataFrame, pd.Series, pd.Series]]:
    completed = completed_candles(frame, timeframe)
    if len(completed) < minimum:
        return None
    previous, current = completed.iloc[-2], completed.iloc[-1]
    return (completed, previous, current) if is_freshly_closed(current, timeframe) else None


def check_supertrend(df: pd.DataFrame, timeframe: str, coin: str):
    pair = _new_indicator_pair(df, timeframe, 21)
    if not pair:
        return None
    completed, previous, current = pair
    prior_three = completed.iloc[-4:-1]
    st = float(current["supertrend"])
    bullish = float(current["close"]) > float(current["open"])
    bearish = float(current["close"]) < float(current["open"])
    strength = abs(float(current["close"]) - st) >= float(current["atr10"]) * .2
    if float(previous["close"]) <= float(previous["supertrend"]) and float(current["close"]) > st and bullish and strength and all(prior_three["close"] <= prior_three["supertrend"]):
        return "Supertrend", "LONG", {"previous": previous, "current": current}
    if float(previous["close"]) >= float(previous["supertrend"]) and float(current["close"]) < st and bearish and strength and all(prior_three["close"] >= prior_three["supertrend"]):
        return "Supertrend", "SHORT", {"previous": previous, "current": current}
    return None


def check_bb_squeeze(df: pd.DataFrame, timeframe: str, coin: str):
    pair = _new_indicator_pair(df, timeframe, 31)
    if not pair:
        return None
    completed, previous, current = pair
    squeeze = completed.iloc[-11:-1]
    if (squeeze["bb_width"] <= squeeze["atr14"] * 1.5).sum() < 8:
        return None
    prior_four = completed.iloc[-5:-1]
    close, open_ = float(current["close"]), float(current["open"])
    if all(prior_four["close"] <= prior_four["bb_upper"]) and close > float(current["bb_upper"]) and close > open_ and close - float(current["bb_upper"]) >= float(current["atr14"]) * .15:
        return "BB Squeeze", "LONG", {"previous": previous, "current": current}
    if all(prior_four["close"] >= prior_four["bb_lower"]) and close < float(current["bb_lower"]) and close < open_ and float(current["bb_lower"]) - close >= float(current["atr14"]) * .15:
        return "BB Squeeze", "SHORT", {"previous": previous, "current": current}
    return None


def send_telegram_message(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured.")

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
    except requests.RequestException as error:
        # The request URL embeds the bot token; drop the original exception so it never reaches logs.
        raise RuntimeError(f"Telegram sendMessage request failed: {type(error).__name__}") from None
    if not response.ok:
        description = response.text
        try:
            description = response.json().get("description", description)
        except ValueError:
            pass
        raise RuntimeError(
            f"Telegram sendMessage failed ({response.status_code}): {description}"
        )


def format_rsi_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] RSI 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}"
    )


def format_macd_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] MACD 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}"
    )


def format_stoch_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] Stochastic 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}"
    )


def format_engulfing_message(coin: str, timeframe: str, position: str) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position_label = "📈 LONG" if position == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] Engulfing 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position_label}"
    )


def format_ema_message(coin: str, timeframe: str, position: str) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position_label = "📈 LONG" if position == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] EMA Cross 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position_label}"
    )


def format_vwap_message(coin: str, timeframe: str, position: str) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position_label = "📈 LONG" if position == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] VWAP Cross 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position_label}"
    )


def format_indicator_message(label: str, coin: str, timeframe: str, side: str) -> str:
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return f"🚨 [{timeframe}] {label} 신호 발생{importance}\n- 코인: {coin}\n- 포지션: {position}"


_pending_signals: list[tuple[str, str, str, str]] = []  # (coin, side, timeframe, label)


def format_signal_digest(signals: list[tuple[str, str, str, str]]) -> str:
    """Group signals by coin+side, timeframes highest first."""
    grouped: dict[tuple[str, str], dict[str, list[str]]] = {}
    for coin, side, timeframe, label in signals:
        grouped.setdefault((coin, side), {}).setdefault(timeframe, []).append(label)
    lines = [f"🚨 신호 요약 (UTC {pd.Timestamp.now(tz='UTC'):%H:%M})"]
    for (coin, side), by_timeframe in grouped.items():
        lines.append(f"{'📈' if side == 'LONG' else '📉'} {coin} {side}")
        for timeframe in ("1d", "4h", "1h"):
            if timeframe in by_timeframe:
                lines.append(f" {TIMEFRAME_IMPORTANCE[timeframe]} {timeframe}: {', '.join(by_timeframe[timeframe])}")
    return "\n".join(lines)


_pending_info: list[tuple[str, list[str]]] = []  # (coin, info lines); informational, no side


def format_info_digest(info: list[tuple[str, list[str]]]) -> str:
    lines = [f"📊 파생 지표 극단 (UTC {pd.Timestamp.now(tz='UTC'):%H:%M})"]
    for coin, items in info:
        lines.append(coin)
        lines.extend(f" · {item}" for item in items)
    return "\n".join(lines)


def flush_signals() -> None:
    """Send queued signals and, separately, queued funding/OI info as Telegram messages."""
    if _pending_signals:
        send_telegram_message(format_signal_digest(_pending_signals))
        _pending_signals.clear()
    if _pending_info:
        send_telegram_message(format_info_digest(_pending_info))
        _pending_info.clear()


def write_workflow_summary(
    checked_count: int,
    signal_count: int,
    error_count: int,
    signal_breakdown: dict[str, int],
    btc_rsi_samples: list[tuple[str, pd.Series, pd.Series]],
    btc_macd_samples: list[tuple[str, pd.Series, pd.Series]],
    btc_stoch_samples: list[tuple[str, pd.Series, pd.Series]],
) -> None:
    """Show an operational summary in the GitHub Actions run page."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    status = "✅ 정상" if error_count == 0 else "⚠️ 일부 오류"
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write(
            "## Signal Bot 실행 결과\n\n"
            f"- 상태: **{status}**\n"
            f"- 검사 완료: **{checked_count}건**\n"
            f"- 발생 신호: **{signal_count}건**\n"
            f"- 오류: **{error_count}건**\n"
            f"- 실행 시각(UTC): {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        summary.write("\n### 신호별 검사 / 발생 건수\n\n")
        summary.write("| 신호 | 검사 | 발생 |\n")
        summary.write("| --- | ---: | ---: |\n")
        for name, counts in signal_breakdown.items():
            summary.write(f"| {name} | {counts['checked']} | {counts['signals']} |\n")

        if btc_rsi_samples:
            summary.write("\n### BTC 최근 확정 캔들 RSI(14)\n\n")
            summary.write("| 시간봉 | 이전 완료 캔들 (UTC) | 이전 RSI | 최근 완료 캔들 (UTC) | 최근 RSI |\n")
            summary.write("| --- | --- | ---: | --- | ---: |\n")
            for timeframe, previous, current in btc_rsi_samples:
                previous_time = pd.to_datetime(previous["timestamp"], unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")
                current_time = pd.to_datetime(current["timestamp"], unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")
                summary.write(
                    f"| {timeframe} | {previous_time} | {previous['rsi']:.2f} | "
                    f"{current_time} | {current['rsi']:.2f} |\n"
                )
        else:
            summary.write(
                "\n### BTC 최근 확정 캔들 RSI(14)\n\n"
                "BTC 완료 캔들 샘플을 만들지 못했습니다. 실행 로그의 `BTC RSI sample` 또는 "
                "`Failed to check BTC` 항목을 확인하세요.\n"
            )
        if btc_macd_samples:
            summary.write("\n### BTC 최근 확정 캔들 MACD(12, 26, 9)\n\n")
            summary.write("| 시간봉 | 이전 완료 캔들 (UTC) | 이전 DIF / DEA | 최근 완료 캔들 (UTC) | 최근 DIF / DEA |\n")
            summary.write("| --- | --- | --- | --- | --- |\n")
            for timeframe, previous, current in btc_macd_samples:
                previous_time = pd.to_datetime(previous["timestamp"], unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")
                current_time = pd.to_datetime(current["timestamp"], unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")
                summary.write(
                    f"| {timeframe} | {previous_time} | {previous['dif']:.4f} / {previous['dea']:.4f} | "
                    f"{current_time} | {current['dif']:.4f} / {current['dea']:.4f} |\n"
                )
        else:
            summary.write(
                "\n### BTC 최근 확정 캔들 MACD(12, 26, 9)\n\n"
                "BTC 완료 캔들 샘플을 만들지 못했습니다. 실행 로그의 `BTC MACD sample` 또는 "
                "`Failed to check BTC` 항목을 확인하세요.\n"
            )
        if btc_stoch_samples:
            summary.write("\n### BTC 최근 확정 캔들 Stochastic (K,D) - OKX KDJ(14,3,3)\n\n")
            summary.write("| 시간봉 | 이전 완료 캔들 (UTC) | 이전 K / D | 최근 완료 캔들 (UTC) | 최근 K / D |\n")
            summary.write("| --- | --- | --- | --- | --- |\n")
            for timeframe, previous, current in btc_stoch_samples:
                previous_time = pd.to_datetime(previous["timestamp"], unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")
                current_time = pd.to_datetime(current["timestamp"], unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")
                summary.write(
                    f"| {timeframe} | {previous_time} | {previous['k']:.2f} / {previous['d']:.2f} | "
                    f"{current_time} | {current['k']:.2f} / {current['d']:.2f} |\n"
                )
        else:
            summary.write(
                "\n### BTC 최근 확정 캔들 Stochastic (K,D) - OKX KDJ(14,3,3)\n\n"
                "BTC 완료 캔들 샘플을 만들지 못했습니다. 실행 로그의 `BTC Stochastic sample` 또는 "
                "`Failed to check BTC` 항목을 확인하세요.\n"
            )


def _process_signal_check(
    label: str,
    coin: str,
    symbol: str,
    timeframe: str,
    frame: pd.DataFrame,
    find_signal,
    build_message,
    volume_ratio: float,
    use_volume_filter: bool = True,
    require_higher_tf_confirmation: bool = False,
    exchange: Optional[ccxt.okx] = None,
) -> bool:
    """Run one signal check and send Telegram alerts when filters pass."""
    signal = find_signal(frame, timeframe)
    if not signal:
        return False

    side, previous, current = signal
    if use_volume_filter and not passes_volume_filter(frame, timeframe, current, min_ratio=volume_ratio):
        logging.info(
            "Skipped %s %s signal for %s (%s): volume %.4f below %.1fx avg (last %d candles)",
            label,
            side,
            symbol,
            timeframe,
            float(current["volume"]),
            volume_ratio,
            VOLUME_AVG_LENGTH,
        )
        return False
    if require_higher_tf_confirmation:
        if exchange is None:
            return False
        if not _confirm_higher(side, coin, timeframe, exchange, symbol):
            return False

    _pending_signals.append((coin, side, timeframe, label))
    logging.info("Queued %s %s signal for %s (%s)", label, side, symbol, timeframe)
    return True


def _process_new_indicator_check(
    label: str,
    coin: str,
    symbol: str,
    timeframe: str,
    frame: pd.DataFrame,
    checker,
    exchange: ccxt.okx,
) -> bool:
    """Run a new indicator checker returning (type, direction, details)."""
    signal = checker(frame, timeframe, coin)
    if not signal:
        return False
    _, side, details = signal
    current = details["current"]
    if not passes_volume_filter(frame, timeframe, current, min_ratio=MULT):
        return False
    if not _confirm_higher(side, coin, timeframe, exchange, symbol):
        return False
    _pending_signals.append((coin, side, timeframe, label))
    logging.info("Queued %s %s signal for %s (%s)", label, side, coin, timeframe)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.info("Signal scan started (UTC %s)", pd.Timestamp.now(tz="UTC").isoformat())
    exchange = create_exchange()
    exchange.load_markets()
    logging.info("Loaded %d OKX markets", len(exchange.markets))

    checked_count = 0
    signal_count = 0
    error_count = 0
    signal_breakdown = {
        "RSI": {"checked": 0, "signals": 0},
        "RSI Divergence": {"checked": 0, "signals": 0},
        "Fibonacci": {"checked": 0, "signals": 0},
        "MACD": {"checked": 0, "signals": 0},
        "Stochastic": {"checked": 0, "signals": 0},
        "Engulfing": {"checked": 0, "signals": 0},
        "EMA Cross": {"checked": 0, "signals": 0},
        "VWAP Cross": {"checked": 0, "signals": 0},
        "Funding": {"checked": 0, "signals": 0},
        "OI": {"checked": 0, "signals": 0},
        "Supertrend": {"checked": 0, "signals": 0},
        "BB Squeeze": {"checked": 0, "signals": 0},
    }
    btc_rsi_samples: list[tuple[str, pd.Series, pd.Series]] = []
    btc_macd_samples: list[tuple[str, pd.Series, pd.Series]] = []
    btc_stoch_samples: list[tuple[str, pd.Series, pd.Series]] = []

    for coin in WATCHLIST:
        symbol = f"{coin}/USDT:USDT"
        if symbol not in exchange.markets:
            logging.warning("OKX market not found: %s", symbol)
            continue

        frame_1h = None
        for timeframe in TIMEFRAMES:
            checked_count += 1
            signal_breakdown["RSI"]["checked"] += 1
            try:
                frame = fetch_rsi_frame(exchange, symbol, timeframe)
                if timeframe == "1h":
                    frame_1h = frame
                completed_candle_pair = latest_completed_candles(frame, timeframe)
                if coin == "BTC" and completed_candle_pair:
                    previous, current = completed_candle_pair
                    btc_rsi_samples.append((timeframe, previous, current))
                    logging.info(
                        "BTC RSI sample (%s): %.2f -> %.2f",
                        timeframe,
                        previous["rsi"],
                        current["rsi"],
                    )
                if _process_signal_check(
                    "RSI",
                    coin,
                    symbol,
                    timeframe,
                    frame,
                    find_rsi_signal,
                    format_rsi_message,
                    CORE_VOLUME_RATIOS["RSI"],
                ):
                    signal_count += 1
                    signal_breakdown["RSI"]["signals"] += 1
                checked_count += 1
                signal_breakdown["RSI Divergence"]["checked"] += 1
                try:
                    for side, label in find_rsi_divergences(frame, timeframe):
                        _pending_signals.append((coin, side, timeframe, label))
                        signal_count += 1
                        signal_breakdown["RSI Divergence"]["signals"] += 1
                        logging.info("Queued %s %s signal for %s (%s)", label, side, symbol, timeframe)
                except (ValueError, KeyError, IndexError) as error:
                    error_count += 1
                    logging.exception("Failed to check RSI Divergence %s (%s): %s", symbol, timeframe, error)
                checked_count += 1
                signal_breakdown["Fibonacci"]["checked"] += 1
                try:
                    fib_signal = find_fib_signal(frame, timeframe)
                    if fib_signal is not None:
                        side, label = fib_signal
                        _pending_signals.append((coin, side, timeframe, label))
                        signal_count += 1
                        signal_breakdown["Fibonacci"]["signals"] += 1
                        logging.info("Queued %s %s signal for %s (%s)", label, side, symbol, timeframe)
                except (ValueError, KeyError, IndexError) as error:
                    error_count += 1
                    logging.exception("Failed to check Fibonacci %s (%s): %s", symbol, timeframe, error)
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                error_count += 1
                logging.exception("Failed to check RSI %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in MACD_TIMEFRAMES:
            checked_count += 1
            signal_breakdown["MACD"]["checked"] += 1
            try:
                frame = fetch_macd_frame(exchange, symbol, timeframe)
                completed_candle_pair = latest_completed_candles(frame, timeframe)
                if coin == "BTC" and completed_candle_pair:
                    previous, current = completed_candle_pair
                    btc_macd_samples.append((timeframe, previous, current))
                    logging.info(
                        "BTC MACD sample (%s): DIF %.4f / DEA %.4f -> DIF %.4f / DEA %.4f",
                        timeframe,
                        previous["dif"],
                        previous["dea"],
                        current["dif"],
                        current["dea"],
                    )
                if _process_signal_check(
                    "MACD",
                    coin,
                    symbol,
                    timeframe,
                    frame,
                    find_macd_signal,
                    format_macd_message,
                    CORE_VOLUME_RATIOS["MACD"],
                ):
                    signal_count += 1
                    signal_breakdown["MACD"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                error_count += 1
                logging.exception("Failed to check MACD %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in STOCH_TIMEFRAMES:
            checked_count += 1
            signal_breakdown["Stochastic"]["checked"] += 1
            try:
                frame = fetch_stoch_frame(exchange, symbol, timeframe)
                completed_candle_pair = latest_completed_candles(frame, timeframe)
                if coin == "BTC" and completed_candle_pair:
                    previous, current = completed_candle_pair
                    btc_stoch_samples.append((timeframe, previous, current))
                    logging.info(
                        "BTC Stochastic sample (%s): K %.2f / D %.2f -> K %.2f / D %.2f",
                        timeframe,
                        previous["k"],
                        previous["d"],
                        current["k"],
                        current["d"],
                    )
                if _process_signal_check(
                    "Stochastic",
                    coin,
                    symbol,
                    timeframe,
                    frame,
                    find_stoch_signal,
                    format_stoch_message,
                    MULT,
                    require_higher_tf_confirmation=True,
                    exchange=exchange,
                ):
                    signal_count += 1
                    signal_breakdown["Stochastic"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                error_count += 1
                logging.exception("Failed to check Stochastic %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in ENGULFING_TIMEFRAMES:
            checked_count += 1
            signal_breakdown["Engulfing"]["checked"] += 1
            try:
                frame = fetch_engulfing_frame(exchange, symbol, timeframe)
                if _process_signal_check(
                    "Engulfing",
                    coin,
                    symbol,
                    timeframe,
                    frame,
                    find_engulfing_signal,
                    lambda coin, timeframe, side, previous, current: format_engulfing_message(coin, timeframe, side),
                    CORE_VOLUME_RATIOS["Engulfing"],
                ):
                    signal_count += 1
                    signal_breakdown["Engulfing"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                error_count += 1
                logging.exception("Failed to check Engulfing %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in EMA_TIMEFRAMES:
            checked_count += 1
            signal_breakdown["EMA Cross"]["checked"] += 1
            try:
                frame = fetch_ema_frame(exchange, symbol, timeframe)
                if _process_signal_check(
                    "EMA Cross",
                    coin,
                    symbol,
                    timeframe,
                    frame,
                    find_ema_signal,
                    lambda coin, timeframe, side, previous, current: format_ema_message(coin, timeframe, side),
                    CORE_VOLUME_RATIOS["EMA Cross"],
                ):
                    signal_count += 1
                    signal_breakdown["EMA Cross"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                error_count += 1
                logging.exception("Failed to check EMA Cross %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        if coin in VWAP_COINS:
            for timeframe in VWAP_TIMEFRAMES:
                checked_count += 1
                signal_breakdown["VWAP Cross"]["checked"] += 1
                try:
                    frame = fetch_vwap_frame(exchange, symbol, timeframe)
                    if _process_signal_check(
                        "VWAP Cross",
                        coin,
                        symbol,
                        timeframe,
                        frame,
                        find_vwap_signal,
                        lambda coin, timeframe, side, previous, current: format_vwap_message(coin, timeframe, side),
                        CORE_VOLUME_RATIOS["VWAP Cross"],
                    ):
                        signal_count += 1
                        signal_breakdown["VWAP Cross"]["signals"] += 1
                except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                    error_count += 1
                    logging.exception("Failed to check VWAP Cross %s (%s): %s", symbol, timeframe, error)
                finally:
                    time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in ("1h", "4h", "1d"):
            checked_count += 1
            signal_breakdown["Supertrend"]["checked"] += 1
            try:
                frame = fetch_supertrend_frame(exchange, symbol, timeframe)
                if _process_new_indicator_check("Supertrend", coin, symbol, timeframe, frame, check_supertrend, exchange):
                    signal_count += 1
                    signal_breakdown["Supertrend"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError, KeyError) as error:
                error_count += 1
                logging.exception("Failed to check Supertrend %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in ("1h", "4h", "1d"):
            checked_count += 1
            signal_breakdown["BB Squeeze"]["checked"] += 1
            try:
                frame = fetch_bb_squeeze_frame(exchange, symbol, timeframe)
                if _process_new_indicator_check("BB Squeeze Breakout", coin, symbol, timeframe, frame, check_bb_squeeze, exchange):
                    signal_count += 1
                    signal_breakdown["BB Squeeze"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError, KeyError) as error:
                error_count += 1
                logging.exception("Failed to check BB Squeeze %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        if coin in DERIV_COINS:
            change = price_change_24h(frame_1h)
            info_lines = []
            for name, finder in (
                ("Funding", lambda: find_funding_info(exchange, symbol)),
                ("OI", lambda: find_oi_info(exchange, symbol, change)),
            ):
                checked_count += 1
                signal_breakdown[name]["checked"] += 1
                try:
                    line = finder()
                    if line:
                        info_lines.append(line)
                        signal_breakdown[name]["signals"] += 1
                except (ccxt.BaseError, requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
                    error_count += 1
                    logging.exception("Failed to check %s %s: %s", name, symbol, error)
                finally:
                    time.sleep(REQUEST_DELAY_SECONDS)
            if info_lines:
                _pending_info.append((coin, info_lines))
                signal_count += 1
                logging.info("Queued funding/OI info for %s: %s", symbol, info_lines)

    logging.info(
        "Signal scan finished: %d checks, %d alerts sent, %d errors",
        checked_count,
        signal_count,
        error_count,
    )
    all_checks_failed = bool(checked_count and error_count == checked_count)
    write_workflow_summary(
        checked_count,
        signal_count,
        error_count,
        signal_breakdown,
        btc_rsi_samples,
        btc_macd_samples,
        btc_stoch_samples,
    )
    if all_checks_failed:
        raise RuntimeError("Every market check failed; see the errors above.")


if __name__ == "__main__":
    try:
        main()
    finally:
        flush_signals()  # still deliver signals found before a mid-scan crash

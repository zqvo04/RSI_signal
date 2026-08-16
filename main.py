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
import pandas as pd
import pandas_ta as ta
import requests

# Change this list to add or remove USDT-margined perpetual futures.
WATCHLIST = [
    "BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP",
    "LIT", "SUI", "BNB",
]
TIMEFRAMES = ("15m", "1h", "4h")
RSI_15M_COINS = ("BTC", "ETH")
MFI_15M_COINS = ("BTC", "ETH")
PSAR_COINS = ("BTC", "ETH", "SOL", "BNB")
MACD_TIMEFRAMES = ("1h", "4h")
STOCH_TIMEFRAMES = ("1h", "4h")
ENGULFING_TIMEFRAMES = ("1h", "4h")
EMA_TIMEFRAMES = ("1h", "4h")
VWAP_TIMEFRAMES = ("1h", "4h")
VWAP_COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")
TIMEFRAME_IMPORTANCE = {
    "4h": "🔥",
    "1h": "⚡️",
    "15m": "👀",
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
VOLUME_AVG_LENGTH = 20
VOLUME_MIN_RATIO = 1.1
ENGULFING_VOLUME_RATIO = 1.4
EMA_VOLUME_RATIO = 1.2
VWAP_VOLUME_RATIO = 1.3
SUPERTREND_VOLUME_RATIO = 1.2
BB_SQUEEZE_VOLUME_RATIO = 1.3
MFI_VOLUME_RATIO = 1.1
PSAR_VOLUME_RATIO = 1.2
OHLCV_LIMIT = 100
REQUEST_DELAY_SECONDS = 0.5
CANDLE_CLOSE_GRACE_SECONDS = 30
SCAN_INTERVAL_SECONDS = 15 * 60
TIMEFRAME_MILLISECONDS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
ENGULFING_BODY_RATIO = {"1h": 1.1, "4h": 1.0}
ENGULFING_LOOKBACK = {"1h": 8, "4h": 5}
VWAP_WINDOW = {"1h": 24, "4h": 6}
VWAP_BREAKOUT_STRENGTH = {"1h": 1.003, "4h": 1.0015}
VWAP_BREAKDOWN_STRENGTH = {"1h": 0.997, "4h": 0.9985}
OKX_BAR = {"15m": "15m", "1h": "1H", "4h": "4H"}


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


def passes_volume_filter(
    frame: pd.DataFrame,
    timeframe: str,
    signal_candle: pd.Series,
    min_ratio: float = VOLUME_MIN_RATIO,
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
        if curr_low > float(prior_window["low"].min()):
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
        if curr_high < float(prior_window["high"].max()):
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


def _passes_vwap_volume_filter(completed: pd.DataFrame, current: pd.Series) -> bool:
    """VWAP signals require both the 20-candle average gate and a 3-candle spike."""
    if len(completed) < VOLUME_AVG_LENGTH + 1:
        return False

    prior_volumes = completed.iloc[-(VOLUME_AVG_LENGTH + 1):-1]["volume"]
    avg_volume = float(prior_volumes.mean())
    if avg_volume <= 0:
        return False

    recent_avg_volume = float(completed.iloc[-4:-1]["volume"].mean())
    signal_volume = float(current["volume"])
    return signal_volume >= avg_volume * VWAP_VOLUME_RATIO and signal_volume > recent_avg_volume


def find_vwap_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a VWAP cross signal from the two latest completed candles."""
    completed = completed_candles(frame, timeframe)
    if len(completed) < max(VWAP_WINDOW[timeframe], VOLUME_AVG_LENGTH + 1, 5):
        return None

    previous = completed.iloc[-2]
    current = completed.iloc[-1]
    if not is_freshly_closed(current, timeframe):
        return None
    if not _passes_vwap_volume_filter(completed, current):
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
        and curr_close >= curr_low + candle_range * 0.6
    ):
        return "LONG", previous, current

    if (
        prev_close > prev_vwap
        and curr_close < curr_vwap
        and curr_close < curr_vwap * VWAP_BREAKDOWN_STRENGTH[timeframe]
        and all(float(completed.iloc[index]["close"]) > float(completed.iloc[index]["vwap"]) for index in (-5, -4, -3, -2))
        and curr_close < curr_open
        and candle_range > 0
        and curr_close <= curr_high - candle_range * 0.6
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


def fetch_mfi_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    frame = fetch_confirmed_frame(exchange, symbol, timeframe)
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    flow = typical * frame["volume"]
    positive = flow.where(typical > typical.shift(1), 0.0)
    negative = flow.where(typical < typical.shift(1), 0.0)
    positive_sum = positive.rolling(14).sum()
    negative_sum = negative.rolling(14).sum()
    frame["mfi"] = 100 - (100 / (1 + positive_sum / negative_sum))
    frame.loc[negative_sum == 0, "mfi"] = 100.0
    return frame.dropna(subset=["mfi"]).reset_index(drop=True)


def fetch_parabolic_sar_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    frame = fetch_confirmed_frame(exchange, symbol, timeframe)
    if len(frame) < 2:
        return frame
    sar = pd.Series(index=frame.index, dtype=float)
    uptrend = bool(frame.at[1, "close"] >= frame.at[0, "close"])
    sar.iloc[0] = float(frame.at[0, "low"] if uptrend else frame.at[0, "high"])
    ep = float(frame.at[0, "high"] if uptrend else frame.at[0, "low"])
    af = 0.02
    for index in range(1, len(frame)):
        candidate = float(sar.iloc[index - 1]) + af * (ep - float(sar.iloc[index - 1]))
        if uptrend:
            candidate = min(candidate, float(frame.iloc[index - 1]["low"]), float(frame.iloc[max(0, index - 2)]["low"]))
            if float(frame.iloc[index]["low"]) < candidate:
                uptrend, sar.iloc[index], ep, af = False, ep, float(frame.iloc[index]["low"]), 0.02
            else:
                sar.iloc[index] = candidate
                if float(frame.iloc[index]["high"]) > ep:
                    ep, af = float(frame.iloc[index]["high"]), min(af + 0.02, 0.2)
        else:
            candidate = max(candidate, float(frame.iloc[index - 1]["high"]), float(frame.iloc[max(0, index - 2)]["high"]))
            if float(frame.iloc[index]["high"]) > candidate:
                uptrend, sar.iloc[index], ep, af = True, ep, float(frame.iloc[index]["high"]), 0.02
            else:
                sar.iloc[index] = candidate
                if float(frame.iloc[index]["low"]) < ep:
                    ep, af = float(frame.iloc[index]["low"]), min(af + 0.02, 0.2)
    frame["psar"] = sar
    frame["atr14"] = ta.atr(frame["high"], frame["low"], frame["close"], length=14)
    return frame.dropna(subset=["psar", "atr14"]).reset_index(drop=True)


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


def check_mfi(df: pd.DataFrame, timeframe: str, coin: str):
    pair = _new_indicator_pair(df, timeframe, 21)
    if not pair:
        return None
    _, previous, current = pair
    mfi, prev_mfi = float(current["mfi"]), float(previous["mfi"])
    if prev_mfi < 20 <= mfi and mfi - 20 >= 2 and float(current["close"]) > float(current["open"]):
        return "MFI", "LONG", {"previous": previous, "current": current}
    if prev_mfi > 80 >= mfi and 80 - mfi >= 2 and float(current["close"]) < float(current["open"]):
        return "MFI", "SHORT", {"previous": previous, "current": current}
    return None


def check_parabolic_sar(df: pd.DataFrame, timeframe: str, coin: str):
    pair = _new_indicator_pair(df, timeframe, 21)
    if not pair:
        return None
    completed, previous, current = pair
    prior_three = completed.iloc[-4:-1]
    psar, close, open_ = float(current["psar"]), float(current["close"]), float(current["open"])
    if float(previous["close"]) < float(previous["psar"]) and close > psar and close > open_ and close - psar >= float(current["atr14"]) * .15 and all(prior_three["close"] < prior_three["psar"]):
        return "Parabolic SAR", "LONG", {"previous": previous, "current": current}
    if float(previous["close"]) > float(previous["psar"]) and close < psar and close < open_ and psar - close >= float(current["atr14"]) * .15 and all(prior_three["close"] > prior_three["psar"]):
        return "Parabolic SAR", "SHORT", {"previous": previous, "current": current}
    return None


def send_telegram_message(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured.")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=15,
    )
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

    send_telegram_message(build_message(coin, timeframe, side, previous, current))
    logging.info("Sent %s %s signal for %s (%s)", label, side, symbol, timeframe)
    return True


def _process_new_indicator_check(label: str, coin: str, timeframe: str, frame: pd.DataFrame, checker, volume_ratio: float) -> bool:
    """Run a new indicator checker returning (type, direction, details)."""
    signal = checker(frame, timeframe, coin)
    if not signal:
        return False
    _, side, details = signal
    current = details["current"]
    if not passes_volume_filter(frame, timeframe, current, min_ratio=volume_ratio):
        return False
    send_telegram_message(format_indicator_message(label, coin, timeframe, side))
    logging.info("Sent %s %s signal for %s (%s)", label, side, coin, timeframe)
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
        "MACD": {"checked": 0, "signals": 0},
        "Stochastic": {"checked": 0, "signals": 0},
        "Engulfing": {"checked": 0, "signals": 0},
        "EMA Cross": {"checked": 0, "signals": 0},
        "VWAP Cross": {"checked": 0, "signals": 0},
        "Supertrend": {"checked": 0, "signals": 0},
        "BB Squeeze": {"checked": 0, "signals": 0},
        "MFI": {"checked": 0, "signals": 0},
        "Parabolic SAR": {"checked": 0, "signals": 0},
    }
    btc_rsi_samples: list[tuple[str, pd.Series, pd.Series]] = []
    btc_macd_samples: list[tuple[str, pd.Series, pd.Series]] = []
    btc_stoch_samples: list[tuple[str, pd.Series, pd.Series]] = []

    for coin in WATCHLIST:
        symbol = f"{coin}/USDT:USDT"
        if symbol not in exchange.markets:
            logging.warning("OKX market not found: %s", symbol)
            continue

        for timeframe in TIMEFRAMES:
            if timeframe == "15m" and coin not in RSI_15M_COINS:
                continue
            checked_count += 1
            signal_breakdown["RSI"]["checked"] += 1
            try:
                frame = fetch_rsi_frame(exchange, symbol, timeframe)
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
                    VOLUME_MIN_RATIO,
                ):
                    signal_count += 1
                    signal_breakdown["RSI"]["signals"] += 1
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
                    VOLUME_MIN_RATIO,
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
                    VOLUME_MIN_RATIO,
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
                    ENGULFING_VOLUME_RATIO,
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
                    EMA_VOLUME_RATIO,
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
                        VWAP_VOLUME_RATIO,
                        use_volume_filter=False,
                    ):
                        signal_count += 1
                        signal_breakdown["VWAP Cross"]["signals"] += 1
                except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                    error_count += 1
                    logging.exception("Failed to check VWAP Cross %s (%s): %s", symbol, timeframe, error)
                finally:
                    time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in ("1h", "4h"):
            checked_count += 1
            signal_breakdown["Supertrend"]["checked"] += 1
            try:
                frame = fetch_supertrend_frame(exchange, symbol, timeframe)
                if _process_new_indicator_check("Supertrend", coin, timeframe, frame, check_supertrend, SUPERTREND_VOLUME_RATIO):
                    signal_count += 1
                    signal_breakdown["Supertrend"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError, KeyError) as error:
                error_count += 1
                logging.exception("Failed to check Supertrend %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in ("1h", "4h"):
            checked_count += 1
            signal_breakdown["BB Squeeze"]["checked"] += 1
            try:
                frame = fetch_bb_squeeze_frame(exchange, symbol, timeframe)
                if _process_new_indicator_check("BB Squeeze Breakout", coin, timeframe, frame, check_bb_squeeze, BB_SQUEEZE_VOLUME_RATIO):
                    signal_count += 1
                    signal_breakdown["BB Squeeze"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError, KeyError) as error:
                error_count += 1
                logging.exception("Failed to check BB Squeeze %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        for timeframe in TIMEFRAMES:
            if timeframe == "15m" and coin not in MFI_15M_COINS:
                continue
            checked_count += 1
            signal_breakdown["MFI"]["checked"] += 1
            try:
                frame = fetch_mfi_frame(exchange, symbol, timeframe)
                if _process_new_indicator_check("MFI", coin, timeframe, frame, check_mfi, MFI_VOLUME_RATIO):
                    signal_count += 1
                    signal_breakdown["MFI"]["signals"] += 1
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError, KeyError) as error:
                error_count += 1
                logging.exception("Failed to check MFI %s (%s): %s", symbol, timeframe, error)
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)

        if coin in PSAR_COINS:
            for timeframe in ("1h", "4h"):
                checked_count += 1
                signal_breakdown["Parabolic SAR"]["checked"] += 1
                try:
                    frame = fetch_parabolic_sar_frame(exchange, symbol, timeframe)
                    if _process_new_indicator_check("Parabolic SAR", coin, timeframe, frame, check_parabolic_sar, PSAR_VOLUME_RATIO):
                        signal_count += 1
                        signal_breakdown["Parabolic SAR"]["signals"] += 1
                except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError, KeyError) as error:
                    error_count += 1
                    logging.exception("Failed to check Parabolic SAR %s (%s): %s", symbol, timeframe, error)
                finally:
                    time.sleep(REQUEST_DELAY_SECONDS)

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
    main()

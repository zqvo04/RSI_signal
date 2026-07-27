"""OKX USDT perpetual RSI, MACD and Stochastic(KDJ) signal notifier.

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
    "BTC", "ETH", "SOL", "HYPE", "DOGE", "WLD", "XRP", "PEPE",
    "LIT", "SUI", "BNB", "LINK", "AVAX", "PENGU", "ONDO",
]
TIMEFRAMES = ("15m", "1h", "4h")
MACD_TIMEFRAMES = ("1h", "4h")
STOCH_TIMEFRAMES = ("1h", "4h")  # KDJ signals only for 1h and 4h as requested
TIMEFRAME_IMPORTANCE = {
    "4h": "🔥",
    "1h": "⚡️",
    "15m": "👀",
}
RSI_LENGTH = 14
EMA_LENGTH = 20
RSI_TREND_FILTER_TIMEFRAME = "1h"
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
# Stochastic (KDJ) parameters: OKX KDJ (14,3,3)
STOCH_K = 14
STOCH_D = 3
STOCH_SMOOTH = 1  # No smoothing for K (direct calculation)
OHLCV_LIMIT = 100
REQUEST_DELAY_SECONDS = 0.5
# The workflow runs one minute after each 15-minute boundary.  This short
# additional buffer avoids using a candle whose final exchange value is late.
CANDLE_CLOSE_GRACE_SECONDS = 30
# The scheduler dispatches the workflow every 15 minutes.  An alert fires only
# on the first scan after a crossing candle closes (the :01 run following the
# candle boundary).  For 15m this means every completed candle can alert, while
# a 1h/4h crossing candle -- which stays the "latest completed" candle for many
# scans -- alerts once instead of being re-sent on every scan.
SCAN_INTERVAL_SECONDS = 15 * 60
TIMEFRAME_MILLISECONDS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


def create_exchange() -> ccxt.okx:
    """Create a public-only OKX client for market-data requests."""
    exchange = ccxt.okx(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    # OKX's currency endpoint is private in CCXT.  The bot needs only public
    # swap-market metadata and OHLCV, so never authenticate or request it.
    exchange.has["fetchCurrencies"] = False
    return exchange


def fetch_rsi_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV, remove incomplete/invalid data, and calculate RSI(14)."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    # APIs may return N/A / null candle fields. Never calculate an indicator on them.
    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["timestamp", *numeric_columns]).copy()
    frame = frame.sort_values("timestamp").drop_duplicates(subset="timestamp")
    frame["rsi"] = ta.rsi(frame["close"], length=RSI_LENGTH)
    return frame.dropna(subset=["rsi"]).reset_index(drop=True)


def fetch_ema_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV and calculate EMA(20)."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["timestamp", *numeric_columns]).copy()
    frame = frame.sort_values("timestamp").drop_duplicates(subset="timestamp")
    frame["ema20"] = ta.ema(frame["close"], length=EMA_LENGTH)
    return frame.dropna(subset=["ema20"]).reset_index(drop=True)


def fetch_macd_frame(exchange: ccxt.okx, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV, remove incomplete/invalid data, and calculate MACD(12, 26, 9)."""
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["timestamp", *numeric_columns]).copy()
    frame = frame.sort_values("timestamp").drop_duplicates(subset="timestamp")
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
    """Fetch OHLCV and calculate Stochastic %K and %D (OKX KDJ 14,3,3).

    OKX KDJ parameters: K length = 14, D = 3 (SMA of K)
    """
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=OHLCV_LIMIT)
    frame = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["timestamp", *numeric_columns]).copy()
    frame = frame.sort_values("timestamp").drop_duplicates(subset="timestamp")

    stoch = ta.stoch(frame["high"], frame["low"], frame["close"], k=STOCH_K, d=STOCH_D, smooth_k=STOCH_SMOOTH)
    # pandas_ta returns two columns (k and d) with names like STOCHk_14_3_1 and STOCHd_14_3_1.
    if stoch is None or len(stoch.columns) < 2:
        return frame.iloc[0:0].copy()

    k_col, d_col = stoch.columns[:2]
    frame["k"] = stoch[k_col]
    frame["d"] = stoch[d_col]
    return frame.dropna(subset=["k", "d"]).reset_index(drop=True)


def latest_completed_candles(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[pd.Series, pd.Series]]:
    """Return the two latest confirmed, closed candles for a timeframe."""
    now_ms = int(time.time() * 1000)
    close_cutoff_ms = now_ms - (CANDLE_CLOSE_GRACE_SECONDS * 1000)
    timeframe_ms = TIMEFRAME_MILLISECONDS[timeframe]

    # A candle timestamp marks its opening time.  Only use it after its closing
    # time has passed; this prevents intrabar 1h/4h RSI crosses from alerting.
    completed = frame[frame["timestamp"] + timeframe_ms <= close_cutoff_ms]
    if len(completed) < 2:
        return None

    return completed.iloc[-2], completed.iloc[-1]


def latest_completed_candle(frame: pd.DataFrame, timeframe: str) -> Optional[pd.Series]:
    """Return the latest confirmed, closed candle for a timeframe."""
    now_ms = int(time.time() * 1000)
    close_cutoff_ms = now_ms - (CANDLE_CLOSE_GRACE_SECONDS * 1000)
    timeframe_ms = TIMEFRAME_MILLISECONDS[timeframe]
    completed = frame[frame["timestamp"] + timeframe_ms <= close_cutoff_ms]
    if completed.empty:
        return None
    return completed.iloc[-1]


def is_freshly_closed(current: pd.Series, timeframe: str) -> bool:
    """Return True only during the first scan after ``current`` closed.

    A 1h/4h crossing candle remains the latest completed candle across many
    15-minute scans.  Without this guard the same crossing would be re-sent on
    every scan until the next candle closes.  A candle is "fresh" while its
    close time is within the most recent scan interval.
    """
    now_ms = int(time.time() * 1000)
    close_ms = int(current["timestamp"]) + TIMEFRAME_MILLISECONDS[timeframe]
    return now_ms - close_ms < SCAN_INTERVAL_SECONDS * 1000


def find_rsi_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return an RSI reversal signal from the two latest completed candles."""
    candles = latest_completed_candles(frame, timeframe)
    if candles is None:
        return None

    previous, current = candles
    # Alert only on the first scan after the crossing candle closes so a 1h/4h
    # crossing is not re-sent on every 15-minute scan.
    if not is_freshly_closed(current, timeframe):
        return None
    if previous["rsi"] < 30 and current["rsi"] >= 30:
        return "LONG", previous, current
    if previous["rsi"] > 70 and current["rsi"] <= 70:
        return "SHORT", previous, current
    return None


def passes_rsi_trend_filter(
    side: str,
    timeframe: str,
    rsi_current: pd.Series,
    ema_current: pd.Series,
) -> bool:
    """Apply 1h EMA20 filter only to 15m RSI signals."""
    if timeframe != "15m":
        return True
    price = float(rsi_current["close"])
    ema20 = float(ema_current["ema20"])
    if side == "LONG":
        return price >= ema20
    return price <= ema20


def find_macd_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a MACD golden/dead cross from the two latest completed candles."""
    candles = latest_completed_candles(frame, timeframe)
    if candles is None:
        return None

    previous, current = candles
    if not is_freshly_closed(current, timeframe):
        return None

    # Golden cross: DIF crosses above DEA while both lines stay below zero.
    if (
        previous["dif"] < previous["dea"]
        and current["dif"] >= current["dea"]
        and current["dif"] < 0
        and current["dea"] < 0
    ):
        return "LONG", previous, current

    # Dead cross: DIF crosses below DEA while both lines stay above zero.
    if (
        previous["dif"] > previous["dea"]
        and current["dif"] <= current["dea"]
        and current["dif"] > 0
        and current["dea"] > 0
    ):
        return "SHORT", previous, current

    return None


def find_stoch_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a Stochastic (KDJ) signal from the two latest completed candles.

    Exact rules implemented (OKX KDJ 14,3,3):
    - LONG (Golden Cross):
      - %K and %D are each 20 or below (oversold)
      - %K crosses above %D from below (previous %K < %D and current %K >= current %D)
      - Previous candle: %K < %D (condition for cross)
    - SHORT (Dead Cross):
      - %K and %D are each 80 or above (overbought)
      - %K crosses below %D from above (previous %K > %D and current %K <= current %D)
      - Previous candle: %K > %D (condition for cross)
    """
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

    # LONG condition: Golden Cross (K crosses D from below in oversold zone)
    if (
        prev_k < prev_d
        and curr_k >= curr_d
        and curr_k <= 20
        and curr_d <= 20
    ):
        return "LONG", previous, current

    # SHORT condition: Dead Cross (K crosses D from above in overbought zone)
    if (
        prev_k > prev_d
        and curr_k <= curr_d
        and curr_k >= 80
        and curr_d >= 80
    ):
        return "SHORT", previous, current

    return None


def send_telegram_message(message: str) -> None:
    # Secrets pasted into GitHub can carry a trailing newline or spaces.  An
    # untrimmed chat_id makes Telegram reject sendMessage with a 400
    # "chat not found", so always strip before use.
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
        # raise_for_status() drops Telegram's explanation and only reports the
        # (token-masked) URL.  Surface the API body so failures like
        # "chat not found" are actually diagnosable from the workflow log.
        description = response.text
        try:
            description = response.json().get("description", description)
        except ValueError:
            pass
        raise RuntimeError(
            f"Telegram sendMessage failed ({response.status_code}): {description}"
        )


def format_rsi_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    """Minimal RSI alert message."""
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] RSI 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}"
    )


def format_macd_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    """Minimal MACD alert message."""
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] MACD 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}"
    )


def format_stoch_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    """Minimal Stochastic alert message as requested."""
    importance = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] Stochastic 신호 발생{importance}\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}"
    )


def write_workflow_summary(
    checked_count: int,
    signal_count: int,
    error_count: int,
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
                previous_time = pd.to_datetime(previous["timestamp"], unit="ms
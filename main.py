"""OKX USDT perpetual RSI reversal signal notifier."""

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
TIMEFRAME_IMPORTANCE = {
    "4h": ("🔥", "높은 신뢰도"),
    "1h": ("⚡️", "중간 신뢰도"),
    "15m": ("👀", "단기 진입 타점"),
}
RSI_LENGTH = 14
OHLCV_LIMIT = 100
REQUEST_DELAY_SECONDS = 0.5
# The workflow runs one minute after each 15-minute boundary.  This short
# additional buffer avoids using a candle whose final exchange value is late.
CANDLE_CLOSE_GRACE_SECONDS = 30
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


def find_signal(frame: pd.DataFrame, timeframe: str) -> Optional[tuple[str, pd.Series, pd.Series]]:
    """Return a reversal signal using the two latest confirmed, closed candles."""
    now_ms = int(time.time() * 1000)
    close_cutoff_ms = now_ms - (CANDLE_CLOSE_GRACE_SECONDS * 1000)
    timeframe_ms = TIMEFRAME_MILLISECONDS[timeframe]

    # A candle timestamp marks its opening time.  Only use it after its closing
    # time has passed; this prevents intrabar 1h/4h RSI crosses from alerting.
    completed = frame[frame["timestamp"] + timeframe_ms <= close_cutoff_ms]
    if len(completed) < 2:
        return None

    previous = completed.iloc[-2]
    current = completed.iloc[-1]
    if previous["rsi"] < 30 and current["rsi"] >= 30:
        return "LONG", previous, current
    if previous["rsi"] > 70 and current["rsi"] <= 70:
        return "SHORT", previous, current
    return None


def send_telegram_message(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured.")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=15,
    )
    response.raise_for_status()


def format_message(coin: str, timeframe: str, side: str, previous: pd.Series, current: pd.Series) -> str:
    importance, description = TIMEFRAME_IMPORTANCE[timeframe]
    position = "📈 LONG" if side == "LONG" else "📉 SHORT"
    return (
        f"🚨 [{timeframe}] 신호 발생 (중요도: {importance} {description})\n"
        f"- 코인: {coin}\n"
        f"- 포지션: {position}\n"
        f"- 현재가: {current['close']:,.8g} USDT\n"
        f"- RSI 지표: {previous['rsi']:.2f} -> {current['rsi']:.2f} (돌파 완료)"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    exchange = create_exchange()
    exchange.load_markets()

    for coin in WATCHLIST:
        symbol = f"{coin}/USDT:USDT"
        if symbol not in exchange.markets:
            logging.warning("OKX market not found: %s", symbol)
            continue

        for timeframe in TIMEFRAMES:
            try:
                frame = fetch_rsi_frame(exchange, symbol, timeframe)
                signal = find_signal(frame, timeframe)
                if signal:
                    side, previous, current = signal
                    send_telegram_message(format_message(coin, timeframe, side, previous, current))
                    logging.info("Sent %s signal for %s (%s)", side, symbol, timeframe)
            except (ccxt.BaseError, requests.RequestException, ValueError, RuntimeError) as error:
                logging.exception("Failed to check %s (%s): %s", symbol, timeframe, error)
            finally:
                # Additional pacing protects both OHLCV and Telegram endpoints.
                time.sleep(REQUEST_DELAY_SECONDS)


if __name__ == "__main__":
    main()

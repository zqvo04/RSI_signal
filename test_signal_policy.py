import unittest
from unittest.mock import Mock, patch

import pandas as pd

import main


class SignalPolicyTests(unittest.TestCase):
    def test_core_secondary_volume_constants(self):
        assert main.SECONDARY_N == 20
        assert main.MULT == 1.5
        assert main.HIGHER_TF_MAP == {"1h": "4h"}
        assert main.CORE_VOLUME_RATIOS == {
            "RSI": 0.9,
            "MACD": 0.9,
            "Engulfing": 1.2,
            "EMA Cross": 1.0,
            "VWAP Cross": 1.0,
        }

    def test_confirm_higher_match_and_mismatch(self):
        with patch("main._get_higher_tf_side", return_value="LONG"):
            assert main._confirm_higher("LONG", "BTC", "1h", Mock(), "BTC/USDT:USDT") is True
            assert main._confirm_higher("SHORT", "BTC", "1h", Mock(), "BTC/USDT:USDT") is False

    def test_confirm_higher_fail_closed_cases(self):
        with patch("main._get_higher_tf_side", return_value=None):
            assert main._confirm_higher("LONG", "BTC", "1h", Mock(), "BTC/USDT:USDT") is False
        with patch("main._get_higher_tf_side", side_effect=RuntimeError("boom")):
            assert main._confirm_higher("LONG", "BTC", "1h", Mock(), "BTC/USDT:USDT") is False
        assert main._confirm_higher("LONG", "BTC", "3m", Mock(), "BTC/USDT:USDT") is False

    def test_confirm_higher_4h_1d_bypass(self):
        with patch("main._get_higher_tf_side") as mocked:
            assert main._confirm_higher("LONG", "BTC", "4h", Mock(), "BTC/USDT:USDT") is True
            assert main._confirm_higher("LONG", "BTC", "1d", Mock(), "BTC/USDT:USDT") is True
            mocked.assert_not_called()

    def test_1d_fires_only_in_first_scan_after_close(self):
        day = main.TIMEFRAME_MILLISECONDS["1d"]
        close_ms = 1_700_000_000_000
        candle = pd.Series({"timestamp": close_ms - day})
        with patch("main.time.time", return_value=(close_ms + 60_000) / 1000):
            assert main.is_freshly_closed(candle, "1d") is True
        with patch("main.time.time", return_value=(close_ms + main.SCAN_INTERVAL_SECONDS * 1000 + 1) / 1000):
            assert main.is_freshly_closed(candle, "1d") is False

    def test_higher_tf_cache(self):
        frame = pd.DataFrame(
            [{"timestamp": 1, "close": 110.0, "ema8": 105.0, "ema21": 100.0, "confirm": "1"}]
        )
        with patch("main.time.time", side_effect=[5000.0, 5000.0, 5005.0]), patch(
            "main._fetch_higher_trend_frame", return_value=frame
        ) as fetcher:
            main._higher_tf_direction_cache.clear()
            side1 = main._get_higher_tf_side(Mock(), "BTC", "BTC/USDT:USDT", "1h")
            side2 = main._get_higher_tf_side(Mock(), "BTC", "BTC/USDT:USDT", "1h")
            assert side1 == "LONG"
            assert side2 == "LONG"
            assert fetcher.call_count == 1

    def test_secondary_flow_uses_mult_and_higher_confirm(self):
        frame = pd.DataFrame()

        def checker(_frame, _timeframe, _coin):
            return "MFI", "LONG", {"current": pd.Series({"timestamp": 1, "volume": 10})}

        main._pending_signals.clear()
        with patch("main.passes_volume_filter", return_value=True) as volume_filter, patch(
            "main._confirm_higher", return_value=True
        ) as confirm_higher, patch("main.send_telegram_message") as send:
            sent = main._process_new_indicator_check(
                "MFI", "BTC", "BTC/USDT:USDT", "1h", frame, checker, Mock()
            )
            assert sent is True
            assert volume_filter.call_args.kwargs["min_ratio"] == main.MULT
            confirm_higher.assert_called_once()
            send.assert_not_called()
            assert main._pending_signals == [("BTC", "LONG", "1h", "MFI")]
        main._pending_signals.clear()

    def test_digest_groups_by_coin_side_and_sorts_timeframes(self):
        digest = main.format_signal_digest(
            [
                ("BTC", "LONG", "1h", "EMA Cross"),
                ("BTC", "LONG", "4h", "RSI"),
                ("BTC", "LONG", "4h", "MACD"),
                ("SOL", "SHORT", "1h", "Engulfing"),
            ]
        )
        assert digest.splitlines()[1:] == [
            "📈 BTC LONG",
            " 🔥 4h: RSI, MACD",
            " ⚡️ 1h: EMA Cross",
            "📉 SOL SHORT",
            " ⚡️ 1h: Engulfing",
        ]

    def test_flush_sends_once_and_clears(self):
        main._pending_signals[:] = [("BTC", "LONG", "1h", "RSI"), ("ETH", "LONG", "1h", "RSI")]
        with patch("main.send_telegram_message") as send:
            main.flush_signals()
            main.flush_signals()
        assert send.call_count == 1
        assert main._pending_signals == []

    def test_p1_find_pivots_single_low_and_high(self):
        low, high = main.find_pivots(pd.Series([5, 5, 5, 1, 5, 5, 5, 5, 9, 5, 5, 5]), 3, 3)
        assert low == [3]
        assert high == [8]

    def test_p2_find_pivots_needs_right_bars(self):
        low, high = main.find_pivots(pd.Series([5, 5, 5, 5, 5, 1, 5, 5]), 3, 3)
        assert low == [] and high == []
        assert main.find_pivots(pd.Series([1.0, 2.0]), 3, 3) == ([], [])

    def test_p3_find_pivots_equal_values_single(self):
        low, _ = main.find_pivots(pd.Series([5, 5, 5, 1, 1, 5, 5, 5, 5]), 3, 3)
        assert low == [3]
        nan_low, _ = main.find_pivots(pd.Series([5, 5, float("nan"), 1, 5, 5, 5, 5]), 3, 3)
        assert nan_low == []

    @staticmethod
    def _div_frame(n, rsi_at, low_at=None, high_at=None):
        """Flat rsi=50/price=100 frame with overrides {position: value}."""
        rsi, low, high = [50.0] * n, [100.0] * n, [100.0] * n
        for target, overrides in ((rsi, rsi_at), (low, low_at or {}), (high, high_at or {})):
            for i, v in overrides.items():
                target[i] = v
        return pd.DataFrame({
            "timestamp": [1_000_000_000_000 + i * 3_600_000 for i in range(n)],
            "open": 100.0, "high": high, "low": low, "close": 100.0, "volume": 1.0, "rsi": rsi,
        })

    def _divs(self, frame):
        with patch("main.is_freshly_closed", return_value=True):
            return main.find_rsi_divergences(frame, "1h")

    def test_v1_regular_bullish(self):
        # p1=5, p2=15, n-1-R=15 -> n=19; rsi higher low, price lower low
        frame = self._div_frame(19, {5: 30.0, 15: 35.0}, low_at={5: 90.0, 15: 80.0})
        assert self._divs(frame) == [("LONG", "RSI 일반 Div")]

    def test_v2_hidden_bullish(self):
        frame = self._div_frame(19, {5: 35.0, 15: 30.0}, low_at={5: 80.0, 15: 90.0})
        assert self._divs(frame) == [("LONG", "RSI 히든 Div")]

    def test_v3_regular_bearish(self):
        frame = self._div_frame(19, {5: 70.0, 15: 65.0}, high_at={5: 110.0, 15: 120.0})
        assert self._divs(frame) == [("SHORT", "RSI 일반 Div")]

    def test_v4_hidden_bearish(self):
        frame = self._div_frame(19, {5: 65.0, 15: 70.0}, high_at={5: 120.0, 15: 110.0})
        assert self._divs(frame) == [("SHORT", "RSI 히든 Div")]

    def test_v5_distance_boundaries(self):
        for gap, expected in ((5, []), (6, [("LONG", "RSI 일반 Div")]), (61, [("LONG", "RSI 일반 Div")]), (62, [])):
            p2 = 5 + gap
            frame = self._div_frame(p2 + 4, {5: 30.0, p2: 35.0}, low_at={5: 90.0, p2: 80.0})
            assert self._divs(frame) == expected, gap

    def test_v6_pivot_not_on_latest_confirmation_bar(self):
        frame = self._div_frame(20, {5: 30.0, 15: 35.0}, low_at={5: 90.0, 15: 80.0})
        assert self._divs(frame) == []

    def test_v7_insufficient_data(self):
        assert self._divs(self._div_frame(9, {})) == []
        assert self._divs(self._div_frame(0, {})) == []


if __name__ == "__main__":
    unittest.main()

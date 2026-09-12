import unittest
from unittest.mock import Mock, patch

import pandas as pd

import main


class SignalPolicyTests(unittest.TestCase):
    def test_core_secondary_volume_constants(self):
        assert main.SECONDARY_N == 20
        assert main.MULT == 1.5
        assert main.HIGHER_TF_MAP == {"15m": "1h", "1h": "4h"}
        assert main.CORE_VOLUME_RATIOS == {
            "RSI": 0.9,
            "MACD": 0.9,
            "Engulfing": 1.2,
            "EMA Cross": 1.0,
            "VWAP Cross": 1.0,
        }

    def test_confirm_higher_match_and_mismatch(self):
        with patch("main._get_higher_tf_side", return_value="LONG"):
            assert main._confirm_higher("LONG", "BTC", "15m", Mock(), "BTC/USDT:USDT") is True
            assert main._confirm_higher("SHORT", "BTC", "15m", Mock(), "BTC/USDT:USDT") is False

    def test_confirm_higher_fail_closed_cases(self):
        with patch("main._get_higher_tf_side", return_value=None):
            assert main._confirm_higher("LONG", "BTC", "1h", Mock(), "BTC/USDT:USDT") is False
        with patch("main._get_higher_tf_side", side_effect=RuntimeError("boom")):
            assert main._confirm_higher("LONG", "BTC", "1h", Mock(), "BTC/USDT:USDT") is False
        assert main._confirm_higher("LONG", "BTC", "3m", Mock(), "BTC/USDT:USDT") is False

    def test_confirm_higher_4h_bypass(self):
        with patch("main._get_higher_tf_side") as mocked:
            assert main._confirm_higher("LONG", "BTC", "4h", Mock(), "BTC/USDT:USDT") is True
            mocked.assert_not_called()

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

        with patch("main.passes_volume_filter", return_value=True) as volume_filter, patch(
            "main._confirm_higher", return_value=True
        ) as confirm_higher, patch("main.send_telegram_message"):
            sent = main._process_new_indicator_check(
                "MFI", "BTC", "BTC/USDT:USDT", "1h", frame, checker, Mock()
            )
            assert sent is True
            assert volume_filter.call_args.kwargs["min_ratio"] == main.MULT
            confirm_higher.assert_called_once()


if __name__ == "__main__":
    unittest.main()

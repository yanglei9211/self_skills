"""Compatibility tests for the legacy ``stock_core.price_history`` import path."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
SHARED = SKILL_DIR.parent / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

from stock_core import price_history  # noqa: E402


class PriceHistoryCompatTests(unittest.TestCase):
    def test_get_price_history_wraps_kline_summary(self):
        fake_kline = [
            {"date": "2026-05-26", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1000},
            {"date": "2026-05-27", "open": 10.2, "high": 10.9, "low": 10.1, "close": 10.8, "volume": 1500},
        ]
        fake_summary = {"regime": "IN_RANGE", "coverage": {"total_days": 2}}
        with mock.patch.object(price_history, "fetch_daily_kline", return_value=fake_kline) as fetcher:
            with mock.patch.object(price_history, "summarize_price_history", return_value=fake_summary) as summarizer:
                out = price_history.get_price_history("HK01810", count=365)

        fetcher.assert_called_once_with("01810", "hk", count=365)
        summarizer.assert_called_once_with(fake_kline, current_price=10.8)
        self.assertEqual(out, fake_summary)

    def test_get_price_history_returns_structured_error_when_no_kline(self):
        with mock.patch.object(price_history, "fetch_daily_kline", return_value=[]):
            out = price_history.get_price_history("SZ300750")

        self.assertEqual(out["error"], "no_kline_data")
        self.assertEqual(out["market"], "a")
        self.assertEqual(out["code"], "300750")
        self.assertEqual(out["coverage"]["total_days"], 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SHARED = SKILL_DIR.parent / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

from stock_core.swing_screen import (  # noqa: E402
    ScreenFeatures,
    build_features,
    classify_candidate,
    screen_candidates,
)


def features(
    *,
    symbol: str = "SH600001",
    name: str = "样例股份",
    percent: float = 2.0,
    amount_yi: float = 12.0,
    turnover_rate: float = 2.0,
    rsi: float = 58.0,
    pos_60: float = 0.92,
    ma20_ratio: float = 1.04,
    ma60_ratio: float = 1.02,
    chg_20d: float = 12.0,
    f5: float = 1.2,
    f10: float = 2.4,
    f20: float = 5.0,
    verdict: str = "RESONANCE_INFLOW",
    acceleration: str = "accelerating_inflow",
    conflict: bool = False,
    is_resonance: bool = True,
) -> ScreenFeatures:
    return ScreenFeatures(
        symbol=symbol,
        code=symbol[-6:],
        name=name,
        current=10.0,
        percent=percent,
        amount_yi=amount_yi,
        turnover_rate=turnover_rate,
        rsi=rsi,
        pos_60=pos_60,
        ma20_ratio=ma20_ratio,
        ma60_ratio=ma60_ratio,
        chg_20d=chg_20d,
        flow_5d=f5,
        flow_10d=f10,
        flow_20d=f20,
        fund_verdict=verdict,
        fund_acceleration=acceleration,
        fund_conflict=conflict,
        fund_is_resonance=is_resonance,
    )


class SwingScreenClassificationTests(unittest.TestCase):
    def test_build_features_combines_quote_fund_flow_and_kline(self):
        quote = {
            "symbol": "SH600001",
            "name": "趋势样例",
            "current": 12.0,
            "percent": 2.5,
            "amount": 1_200_000_000,
            "turnover_rate": 2.4,
            "market_capital": 50_000_000_000,
            "pe_ttm": 18.0,
        }
        fund = {
            "rolling": {
                "5d": {"main_yi": 1.1},
                "10d": {"main_yi": 2.5},
                "20d": {"main_yi": 4.8},
            },
            "cross_validation": {
                "verdict": "RESONANCE_INFLOW",
                "acceleration": "accelerating_inflow",
                "short_long_conflict": False,
                "is_resonance": True,
            },
        }
        kline = []
        for i in range(80):
            close = 10.0 + i * 0.02
            kline.append({"date": f"2026-01-{i+1:02d}", "close": close})
        kline[-1]["close"] = 12.0

        out = build_features(quote, fund, kline)

        self.assertEqual(out.symbol, "SH600001")
        self.assertEqual(out.code, "600001")
        self.assertEqual(out.amount_yi, 12.0)
        self.assertEqual(out.flow_20d, 4.8)
        self.assertEqual(out.fund_verdict, "RESONANCE_INFLOW")
        self.assertGreater(out.ma20_ratio, 1.0)
        self.assertIsNotNone(out.rsi)

    def test_build_features_rejects_non_a_share_symbol(self):
        with self.assertRaises(ValueError):
            build_features({"symbol": "BABA"}, {}, [])

    def test_classifies_trend_continuation_candidate(self):
        candidate = classify_candidate(features())

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.candidate_type, "trend_continuation")
        self.assertEqual(candidate.action, "trend_entry_candidate")
        self.assertGreaterEqual(candidate.score, 70)
        self.assertTrue(any("资金共振流入" in r for r in candidate.reasons))

    def test_classifies_bottom_reversal_candidate(self):
        candidate = classify_candidate(features(
            symbol="SZ300001",
            name="筑底科技",
            rsi=44.0,
            pos_60=0.76,
            ma20_ratio=1.01,
            ma60_ratio=0.94,
            chg_20d=-14.0,
            f5=0.8,
            f10=-0.2,
            f20=-3.0,
            verdict="REVERSAL_INFLOW_CONFIRMED",
            acceleration="decelerating_outflow",
            is_resonance=False,
        ))

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.candidate_type, "bottom_reversal")
        self.assertEqual(candidate.action, "reversal_probe_candidate")
        self.assertGreaterEqual(candidate.score, 60)
        self.assertTrue(any("反转流入确认" in r for r in candidate.reasons))

    def test_hard_filters_exclude_risk_and_overheated_names(self):
        cases = [
            features(name="*ST 风险", verdict="RESONANCE_INFLOW"),
            features(amount_yi=0.2),
            features(rsi=82.0),
            features(conflict=True),
            features(pos_60=1.02, chg_20d=46.0),
        ]

        for item in cases:
            with self.subTest(item=item):
                self.assertIsNone(classify_candidate(item))

    def test_screen_candidates_keeps_separate_ranked_buckets(self):
        trend = features(symbol="SH600010", name="趋势一号", f20=8.0)
        reversal = features(
            symbol="SZ300010",
            name="反转一号",
            rsi=42.0,
            pos_60=0.72,
            ma20_ratio=1.02,
            ma60_ratio=0.93,
            chg_20d=-12.0,
            f5=1.0,
            f10=-0.1,
            f20=-2.0,
            verdict="REVERSAL_INFLOW_CONFIRMED",
            acceleration="decelerating_outflow",
            is_resonance=False,
        )
        ignored = features(symbol="SH600011", name="过热票", rsi=82.0)

        result = screen_candidates([reversal, ignored, trend], top=5)

        self.assertEqual([c.symbol for c in result["trend_continuation"]], ["SH600010"])
        self.assertEqual([c.symbol for c in result["bottom_reversal"]], ["SZ300010"])


if __name__ == "__main__":
    unittest.main()

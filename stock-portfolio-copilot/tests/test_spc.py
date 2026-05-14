from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
REPO_ROOT = SKILL_DIR.parent
SHARED_DIR = REPO_ROOT / "shared"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHARED_DIR))

from spc_core.db import connect  # noqa: E402
from spc_core.decision import analyze_now, render_analysis_text as render_spc_analysis_text  # noqa: E402
from spc_core.ledger import (  # noqa: E402
    add_position_seed,
    add_trade,
    add_watch,
    delete_trade,
    latest_analysis_run,
    latest_snapshots,
    list_trades,
    list_watch,
)
from spc_core.portfolio import pnl_summary, sync_portfolio  # noqa: E402
from spc_core.settings import capital_settings, set_capital  # noqa: E402
from spc_core.market_bridge import StockMarketHubProvider  # noqa: E402
from spc_core.utils import utc_now_iso  # noqa: E402
from stock_core.fund_flow import _ttl_for_call, _ttl_for_moment  # noqa: E402
from stock_core.stock_market_hub import analyze_symbol, render_analysis_text  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402


class FakeProvider:
    def __init__(
        self,
        fund_flow_overrides: dict | None = None,
        market_regime_overrides: dict | None = None,
    ):
        self.quote_map = {
            ("a", "300750"): {"current": "260.00", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("a", "300308"): {"current": "935.00", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("hk", "01810"): {"current": "19.24", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("hk", "00700"): {"current": "410.20", "fetched_at": "2026-05-08T14:31:00+08:00"},
        }
        self.fund_flow_overrides = fund_flow_overrides or {}
        # 默认所有市场 NEUTRAL；测试可注入 {"a": "RISK_OFF", "hk": "RISK_ON"} 等
        self.market_regime_overrides = market_regime_overrides or {}

    def fetch_quote(self, market, code):
        return self.quote_map.get((market, code), {"current": "10.00", "fetched_at": "2026-05-08T14:31:00+08:00"})

    def _attach_fund_flow(self, market: str, code: str, payload: dict) -> dict:
        ff = self.fund_flow_overrides.get((market, code))
        if ff is not None:
            payload["fund_flow"] = ff
        return payload

    def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
        quote = self.fetch_quote(market, code)
        if (market, code) == ("a", "300750"):
            return self._attach_fund_flow(market, code, {
                "fetched_at": quote["fetched_at"],
                "info": {"name": "宁德时代"},
                "quote": {"current": quote["current"], "percent": 1.23},
                "price_history": {"regime": "IN_RANGE"},
                "concepts": ["光模块", "算力"],
                "peers": [
                    {"symbol": "SZ300308", "name": "中际旭创", "percent": 3.2, "ytd_pct": 18.0, "main_inflow_yi": 1.2},
                ] if with_peers else [],
                "announcements": [
                    {"date": "2026-05-01", "title": "签订合作协议", "pdf_url": "https://example.com/a1.pdf"},
                ],
            })
        if (market, code) == ("hk", "01810"):
            return self._attach_fund_flow(market, code, {
                "fetched_at": quote["fetched_at"],
                "info": {"name": "小米集团-W"},
                "quote": {"current": quote["current"], "percent": -3.5},
                "price_history": {"regime": "NEW_YTD_LOW"},
                "announcements": [
                    {"date": "2026-05-02", "title": "收到监管问询函", "pdf_url": "https://example.com/h1.pdf"},
                    {"date": "2026-05-03", "title": "主要股东减持公告", "pdf_url": "https://example.com/h2.pdf"},
                ],
            })
        if (market, code) == ("a", "300308"):
            return self._attach_fund_flow(market, code, {
                "fetched_at": quote["fetched_at"],
                "info": {"name": "中际旭创"},
                "quote": {"current": quote["current"], "percent": 3.2},
                "price_history": {"regime": "NEW_ALL_TIME_HIGH"},
                "concepts": ["通信", "光模块"],
                "peers": [],
                "announcements": [
                    {"date": "2026-05-03", "title": "新品合作公告", "pdf_url": "https://example.com/b1.pdf"},
                ],
            })
        return self._attach_fund_flow(market, code, {
            "fetched_at": quote["fetched_at"],
            "info": {"name": f"{market.upper()}-{code}"},
            "quote": {"current": quote["current"], "percent": 0.8},
            "price_history": {"regime": "IN_RANGE"},
            "concepts": ["半导体"],
            "peers": [],
            "announcements": [
                {"date": "2026-05-03", "title": "回购股份公告", "pdf_url": "https://example.com/w1.pdf"},
            ],
        })

    def market_board(self, market="all_a", board="gainers", top=10):
        if market == "all_a" and board == "gainers":
            items = [{"symbol": "SZ300762", "name": "上海瀚讯", "percent": 5.2, "main_yi": 0.6}]
        elif market == "all_a" and board == "main_inflow":
            items = [{"symbol": "SZ000034", "name": "神州数码", "percent": 2.1, "main_yi": 1.4}]
        elif market == "hk" and board == "gainers":
            items = [{"symbol": "00700", "name": "腾讯控股", "percent": 1.8, "main_yi": 0.8}]
        else:
            items = []
        return {"market": market, "board": board, "items": items}

    def get_market_regime(self, market: str) -> dict:
        regime = self.market_regime_overrides.get(market, "NEUTRAL")
        # 返回结构与 stock_core.market_regime.classify_market_regime 对齐，
        # 但只填 decision.py / render_analysis_text 实际依赖的字段。
        return {
            "market": market,
            "regime": regime,
            "reasons": [f"FakeProvider 注入：{market} = {regime}"],
            "indices": [
                {
                    "name": "测试指数1",
                    "close": 4000.0,
                    "from_52w_high_pct": -0.5,
                    "ytd_pct": 5.0,
                    "above_ma200": regime != "RISK_OFF",
                },
            ],
        }


def _ff(regime: str, *, reversal: str | None = None, m3: float = 0.0, m5: float = 0.0, m20: float = 0.0,
        main_today_yi: float = 0.0, super_big_yi: float = 0.0, big_yi: float = 0.0) -> dict:
    """构造 fund_flow 摘要的小工具（仅含 decision.py 实际用到的字段）。"""
    return {
        "as_of": "2026-05-08",
        "today": {
            "main_yi": main_today_yi,
            "super_big_yi": super_big_yi,
            "big_yi": big_yi,
            "mid_yi": 0.0, "small_yi": 0.0,
            "main_pct": 0.0, "super_big_pct": 0.0, "big_pct": 0.0,
            "mid_pct": 0.0, "small_pct": 0.0,
            "close": 100.0, "change_pct": 0.0,
        },
        "rolling": {
            "1d": {"main_yi": main_today_yi, "inflow_days": 0, "outflow_days": 0, "days": 1},
            "3d": {"main_yi": m3, "inflow_days": 0, "outflow_days": 0, "days": 3},
            "5d": {"main_yi": m5, "inflow_days": 0, "outflow_days": 0, "days": 5},
            "10d": {"main_yi": m5, "inflow_days": 0, "outflow_days": 0, "days": 10},
            "20d": {"main_yi": m20, "inflow_days": 0, "outflow_days": 0, "days": 20},
        },
        "regime": regime,
        "reversal": reversal,
    }


class FakeFXProvider:
    def get_rate(self, _conn, from_currency, to_currency):
        if from_currency == to_currency:
            return Decimal("1")
        if from_currency == "HKD" and to_currency == "CNY":
            return Decimal("0.92")
        if from_currency == "CNY" and to_currency == "HKD":
            return Decimal("1.086956")
        raise ValueError("unsupported")


class SPCTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SPC_DATA_DIR"] = self.tmp.name
        os.environ["SPC_DISABLE_FX_HTTP"] = "1"
        self.conn = connect()
        self.provider = FakeProvider()
        self.fx_provider = FakeFXProvider()
        # Create default test account
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO accounts(slug, display_name, broker, base_currency, is_default, is_archived, created_at, updated_at) VALUES(?, ?, ?, ?, 1, 0, ?, ?)",
            ("default", "默认账户", "", "CNY", now, now),
        )
        self.conn.commit()
        self.acct_id = self.conn.execute("SELECT id FROM accounts WHERE slug='default'").fetchone()[0]
        self.acct_slug = "default"
        self.acct_name = "默认账户"
        # Set capital for the test account
        set_capital(self.conn, self.acct_id, "500000", "20")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        os.environ.pop("SPC_DATA_DIR", None)
        os.environ.pop("SPC_DISABLE_FX_HTTP", None)

    def test_position_trade_sync_and_delete_flow(self):
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "sell",
            "200",
            "251.80",
            "2026-05-08 14:15:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
        )
        sync_portfolio(self.conn, self.acct_id, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        snap = latest_snapshots(self.conn, self.acct_id)[0]
        self.assertEqual(snap["qty"], "800.0000")
        self.assertEqual(snap["avg_cost_price"], "245.3000")
        self.assertEqual(snap["last_price"], "260.0000")
        self.assertEqual(snap["realized_pnl_ccy"], "1274.82")
        self.assertEqual(snap["unrealized_pnl_ccy"], "11760.00")

        delete_trade(self.conn, self.acct_id, trade_id)
        self.assertEqual(len(list_trades(self.conn, self.acct_id)), 0)
        self.assertEqual(len(list_trades(self.conn, self.acct_id, include_deleted=True)), 1)

    def test_hk_sync_with_buy_fees_and_fx(self):
        add_position_seed(self.conn, self.acct_id, "hk", "1810", "2000", "18.62", None, "2026-05-01 09:30:00", "")
        add_trade(
            self.conn,
            self.acct_id,
            "hk",
            "01810",
            "buy",
            "500",
            "19.10",
            "2026-05-08 10:32:00",
            None,
            "0.92",
            "9.55",
            "0",
            "0",
            "10",
            "",
        )
        sync_portfolio(self.conn, self.acct_id, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        snap = latest_snapshots(self.conn, self.acct_id)[0]
        self.assertEqual(snap["qty"], "2500.0000")
        self.assertEqual(snap["avg_cost_price"], "18.7241")
        self.assertEqual(snap["total_fees_ccy"], "20.36")
        self.assertEqual(snap["position_value_cny"], "44252.00")

    def test_watch_capital_and_analysis(self):
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        add_position_seed(self.conn, self.acct_id, "hk", "1810", "2000", "18.62", None, "2026-05-01 09:30:00", "")
        add_watch(self.conn, self.acct_id, "hk", "00700", "")

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "all", analysis_provider=self.provider)
        self.assertEqual(payload["scope"], "all")
        self.assertEqual(len(payload["results"]), 3)
        actions = {(item["market"], item["code"]): item["decision"]["action"] for item in payload["results"]}
        self.assertEqual(actions[("a", "300750")], "trim")
        self.assertEqual(actions[("hk", "01810")], "trim")
        self.assertEqual(actions[("hk", "00700")], "focus")
        opp_codes = {(item["market"], item["code"]) for item in payload["opportunities"]}
        self.assertIn(("a", "300308"), opp_codes)
        self.assertIn(("a", "300762"), opp_codes)
        last = latest_analysis_run(self.conn, self.acct_id)
        self.assertIsNotNone(last)
        self.assertEqual(last["payload"]["scope"], "all")
        caps = capital_settings(self.conn, self.acct_id)
        self.assertEqual(caps["total_cny"], "500000")
        self.assertEqual(len(list_watch(self.conn, self.acct_id)), 1)

    def test_analysis_payload_and_text_include_security_name(self):
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "holdings", analysis_provider=self.provider)
        self.assertEqual(payload["results"][0]["name"], "宁德时代")

        text = render_spc_analysis_text(payload)
        self.assertIn("标的：A 300750 宁德时代", text)

    def test_strict_buy_requires_strong_setup(self):
        add_watch(self.conn, self.acct_id, "a", "300308", "")

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "watchlist", analysis_provider=self.provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        self.assertEqual(decision["action_label"], "买入候选")
        self.assertTrue(any("强趋势" in reason for reason in decision["reasoning"]))

    def test_fund_flow_blocks_buy_on_recent_outflow(self):
        """自选 + 强趋势 + 正向公告，但近 5 日主力反向流出 → buy 候选被否，回退到 focus。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_INFLOW", reversal="INFLOW_TO_OUTFLOW",
                m3=-1.5, m5=-3.0, m20=15.0, main_today_yi=-1.2,
            ),
        })

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "watchlist", analysis_provider=provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "focus")
        self.assertTrue(
            any("近 5 日主力资金由流入转为流出" in r for r in decision["risks"]),
            f"reasoning={decision['reasoning']}, risks={decision['risks']}",
        )
        self.assertTrue(
            any("fund_flow.regime=PERSISTENT_INFLOW" in s for s in decision["sources"]),
            f"sources={decision['sources']}",
        )

    def test_fund_flow_persistent_outflow_forces_avoid(self):
        """自选 + 强趋势 + 正向公告，但主力 20 日持续净流出 → 直接 avoid，不能 buy/focus。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_OUTFLOW",
                m3=-4.0, m5=-8.0, m20=-22.0, main_today_yi=-2.5, super_big_yi=-1.8, big_yi=-0.7,
            ),
        })

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "watchlist", analysis_provider=provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "avoid")
        self.assertTrue(
            any("主力 20 日持续净流出" in r for r in decision["risks"]),
            f"risks={decision['risks']}",
        )

    def test_fund_flow_background_weak_but_3d_5d_repair_keeps_focus_not_avoid(self):
        """20 日背景偏弱，但近 3/5 日修复时，不应直接按 avoid 处理。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_OUTFLOW",
                reversal="OUTFLOW_TO_INFLOW",
                m3=2.0, m5=6.0, m20=-18.0, main_today_yi=1.1,
            ),
        })

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "watchlist", analysis_provider=provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "focus")
        self.assertTrue(
            any("近 5 日主力资金由流出转为流入" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )

    def test_market_regime_risk_off_demotes_buy_to_focus(self):
        """A 股大盘 RISK_OFF 时，原本严格满足 buy 条件的 300308 应降为 focus，
        但港股标的不受影响（市场隔离）。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        add_watch(self.conn, self.acct_id, "hk", "00700", "")
        provider = FakeProvider(market_regime_overrides={"a": "RISK_OFF", "hk": "RISK_ON"})

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decisions = {(r["market"], r["code"]): r["decision"] for r in payload["results"]}

        a_decision = decisions[("a", "300308")]
        self.assertEqual(a_decision["action"], "focus")
        self.assertTrue(
            any("大盘 RISK_OFF" in reason for reason in a_decision["reasoning"]),
            f"reasoning={a_decision['reasoning']}",
        )
        self.assertTrue(
            any("market_regime=RISK_OFF" in s for s in a_decision["sources"]),
            f"sources={a_decision['sources']}",
        )
        self.assertTrue(
            any("大盘 RISK_OFF" in r for r in a_decision["risks"]),
            f"risks={a_decision['risks']}",
        )

        hk_decision = decisions[("hk", "00700")]
        self.assertTrue(
            any("market_regime=RISK_ON" in s for s in hk_decision["sources"]),
            f"sources={hk_decision['sources']}",
        )
        # 港股 RISK_ON：肯定不会有 RISK_OFF 提示混进去
        self.assertFalse(
            any("RISK_OFF" in r for r in hk_decision["risks"]),
            f"risks={hk_decision['risks']}",
        )

        self.assertEqual(payload["market_regime"]["a"]["regime"], "RISK_OFF")
        self.assertEqual(payload["market_regime"]["hk"]["regime"], "RISK_ON")

    def test_reversal_buy_path_promotes_to_buy(self):
        """新增：反转买入路径应能把 NEAR_YTD_LOW 的标的升到 buy（条件比 trend 更严）。"""
        # 用一个 hk 标的避免 300308 默认 NEW_ALL_TIME_HIGH 走 trend 路径
        add_watch(self.conn, self.acct_id, "hk", "00700", "")

        class ReversalProvider(FakeProvider):
            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                quote = self.fetch_quote(market, code)
                if (market, code) == ("hk", "00700"):
                    return self._attach_fund_flow(market, code, {
                        "fetched_at": quote["fetched_at"],
                        "info": {"name": "腾讯控股"},
                        "quote": {"current": quote["current"], "percent": 1.5},
                        "price_history": {"regime": "NEAR_YTD_LOW"},  # 接近年内低位
                        "announcements": [
                            {"date": "2026-05-01", "title": "回购股份公告", "pdf_url": "h1"},
                            {"date": "2026-05-02", "title": "新品发布合作", "pdf_url": "h2"},
                        ],
                    })
                return super().analyze(market, code, ann_days, with_peers, skip)

        provider = ReversalProvider(fund_flow_overrides={
            ("hk", "00700"): _ff(
                regime="PERSISTENT_INFLOW", reversal="OUTFLOW_TO_INFLOW",
                m3=2.0, m5=4.0, m20=15.0, main_today_yi=1.2,
            ),
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        self.assertTrue(
            any("反转买入路径" in reason for reason in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )
        # 反转路径置信度低于趋势路径（0.68 vs 0.72）
        self.assertEqual(decision["confidence"], "0.68")

    def test_hk_reversal_buy_downgrades_to_probe_under_market_risk_off(self):
        """港股在 RISK_OFF 下，反转修复型 buy 不再一刀切 focus，而是降档为 probe。"""
        add_watch(self.conn, self.acct_id, "hk", "00700", "")

        class ReversalProvider(FakeProvider):
            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                quote = self.fetch_quote(market, code)
                if (market, code) == ("hk", "00700"):
                    return self._attach_fund_flow(market, code, {
                        "fetched_at": quote["fetched_at"],
                        "info": {"name": "腾讯控股"},
                        "quote": {"current": quote["current"], "percent": 1.5},
                        "price_history": {"regime": "NEAR_YTD_LOW"},
                        "announcements": [
                            {"date": "2026-05-01", "title": "回购股份公告", "pdf_url": "h1"},
                            {"date": "2026-05-02", "title": "新品发布合作", "pdf_url": "h2"},
                        ],
                    })
                return super().analyze(market, code, ann_days, with_peers, skip)

        provider = ReversalProvider(
            fund_flow_overrides={
                ("hk", "00700"): _ff(
                    regime="PERSISTENT_INFLOW", reversal="OUTFLOW_TO_INFLOW",
                    m3=2.0, m5=4.0, m20=15.0, main_today_yi=1.2,
                ),
            },
            market_regime_overrides={"hk": "RISK_OFF"},
        )

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "probe")
        self.assertEqual(decision["action_label"], "试探买入")
        self.assertEqual(decision["confidence"], "0.60")
        self.assertTrue(
            any("反转买入路径" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )
        self.assertTrue(
            any("RISK_OFF" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )
        self.assertTrue(
            any("1/4-1/3" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )

    def test_hk_trend_buy_stays_focus_under_market_risk_off(self):
        """港股在 RISK_OFF 下仍不追趋势高位，趋势型 buy 应继续降为 focus。"""
        add_watch(self.conn, self.acct_id, "hk", "00700", "")

        class TrendProvider(FakeProvider):
            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                quote = self.fetch_quote(market, code)
                if (market, code) == ("hk", "00700"):
                    return self._attach_fund_flow(market, code, {
                        "fetched_at": quote["fetched_at"],
                        "info": {"name": "腾讯控股"},
                        "quote": {"current": quote["current"], "percent": 2.5},
                        "price_history": {"regime": "NEW_YTD_HIGH"},
                        "announcements": [
                            {"date": "2026-05-01", "title": "回购股份公告", "pdf_url": "h1"},
                        ],
                    })
                return super().analyze(market, code, ann_days, with_peers, skip)

        provider = TrendProvider(
            fund_flow_overrides={
                ("hk", "00700"): _ff(
                    regime="PERSISTENT_INFLOW",
                    m3=2.0, m5=4.0, m20=15.0, main_today_yi=1.2,
                ),
            },
            market_regime_overrides={"hk": "RISK_OFF"},
        )

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "focus")
        self.assertEqual(decision["confidence"], "0.65")
        self.assertTrue(
            any("趋势追高先降级为 focus" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )

    def test_a_share_reversal_buy_stays_focus_under_market_risk_off(self):
        """A 股在 RISK_OFF 下，反转修复型 buy 应继续降为 focus（不复用港股 probe 通道）。

        守护"港股 probe 分支没有意外波及 A 股"——_evaluate_self_select_buy 里的
        ``if market == MARKET_HK`` 一旦被改坏，本用例会立刻失败。
        """
        add_watch(self.conn, self.acct_id, "a", "300308", "")

        class AReversalProvider(FakeProvider):
            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                quote = self.fetch_quote(market, code)
                if (market, code) == ("a", "300308"):
                    return self._attach_fund_flow(market, code, {
                        "fetched_at": quote["fetched_at"],
                        "info": {"name": "中际旭创"},
                        "quote": {"current": quote["current"], "percent": 1.5},
                        "price_history": {"regime": "NEAR_YTD_LOW"},
                        "concepts": ["通信", "光模块"],
                        "peers": [],
                        "announcements": [
                            {"date": "2026-05-01", "title": "回购股份公告", "pdf_url": "a1"},
                            {"date": "2026-05-02", "title": "新品发布合作", "pdf_url": "a2"},
                        ],
                    })
                return super().analyze(market, code, ann_days, with_peers, skip)

        provider = AReversalProvider(
            fund_flow_overrides={
                ("a", "300308"): _ff(
                    regime="PERSISTENT_INFLOW", reversal="OUTFLOW_TO_INFLOW",
                    m3=2.0, m5=4.0, m20=15.0, main_today_yi=1.2,
                ),
            },
            market_regime_overrides={"a": "RISK_OFF"},
        )

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "focus")
        self.assertEqual(decision["confidence"], "0.62")
        self.assertTrue(
            any("反转买入路径" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )
        self.assertTrue(
            any("RISK_OFF" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )
        # 关键回归断言：A 股不允许走 probe 通道
        self.assertNotEqual(decision["action"], "probe")
        self.assertFalse(
            any("1/4-1/3" in r for r in decision["reasoning"]),
            f"A 股不应出现港股专属的 1/4-1/3 试探文案；reasoning={decision['reasoning']}",
        )

    def test_low_regimes_still_block_buy_even_with_strong_reversal(self):
        """LOW_REGIMES（创新低）即便资金已经掉头流入也禁止 buy；只能 watch / avoid。"""
        add_watch(self.conn, self.acct_id, "hk", "00700", "")

        class CrashProvider(FakeProvider):
            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                quote = self.fetch_quote(market, code)
                if (market, code) == ("hk", "00700"):
                    return self._attach_fund_flow(market, code, {
                        "fetched_at": quote["fetched_at"],
                        "info": {"name": "腾讯控股"},
                        "quote": {"current": quote["current"], "percent": -2.0},
                        "price_history": {"regime": "NEW_YTD_LOW"},  # 创年内新低
                        "announcements": [
                            {"date": "2026-05-01", "title": "回购股份公告", "pdf_url": "h1"},
                            {"date": "2026-05-02", "title": "新品发布合作", "pdf_url": "h2"},
                        ],
                    })
                return super().analyze(market, code, ann_days, with_peers, skip)

        provider = CrashProvider(fund_flow_overrides={
            ("hk", "00700"): _ff(
                regime="PERSISTENT_INFLOW", reversal="OUTFLOW_TO_INFLOW",
                m3=3.0, m5=5.0, m20=10.0, main_today_yi=2.0,
            ),
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        # 创新低 + 公告无风险词 → 落到 avoid（与 LOW_REGIMES 处理一致），不应该 buy / focus
        self.assertEqual(decision["action"], "avoid")
        self.assertTrue(
            any("破位" in r or "创新低" in r for r in decision["risks"]),
            f"risks={decision['risks']}",
        )

    def test_market_regime_risk_off_does_not_force_trim_on_holdings(self):
        """大盘 RISK_OFF 时，普通持仓 hold 只加 risks 提示，不主动降为 trim；
        但已经在 trim/sell 上的 confidence 应 +0.05。"""
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "100", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        # 资金上限 50 万、单票上限 100% → 不会因为仓位上限触发 trim
        # 这样 300750 在常态下只是 hold，能干净地观察大盘联动
        from spc_core.settings import set_capital
        set_capital(self.conn, self.acct_id, "500000", "100")

        provider = FakeProvider(market_regime_overrides={"a": "RISK_OFF"})

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        # 关键断言：仍然 hold，没有自动降为 trim
        self.assertEqual(decision["action"], "hold")
        self.assertTrue(
            any("大盘 RISK_OFF" in r for r in decision["risks"]),
            f"risks={decision['risks']}",
        )

    def test_pnl_summary(self):
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        sync_portfolio(self.conn, self.acct_id, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        summary = pnl_summary(self.conn, self.acct_id)
        self.assertEqual(summary["positions"], 1)
        self.assertEqual(summary["total_position_value_cny"], "260000.00")

    def test_two_accounts_same_stock_different_cost(self):
        """同一只股票在两个账户中以不同成本存在。"""
        # Create second account
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO accounts(slug, display_name, broker, base_currency, is_default, is_archived, created_at, updated_at) VALUES(?, ?, ?, ?, 0, 0, ?, ?)",
            ("swing", "波段账户", "", "CNY", now, now),
        )
        self.conn.commit()
        acct2_id = self.conn.execute("SELECT id FROM accounts WHERE slug='swing'").fetchone()[0]
        set_capital(self.conn, acct2_id, "300000", "25")

        # Same stock, different costs in two accounts
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        add_position_seed(self.conn, acct2_id, "a", "300750", "500", "240.00", None, "2026-05-01 09:30:00", "")

        sync_portfolio(self.conn, self.acct_id, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        sync_portfolio(self.conn, acct2_id, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)

        snap1 = latest_snapshots(self.conn, self.acct_id)[0]
        snap2 = latest_snapshots(self.conn, acct2_id)[0]

        self.assertEqual(snap1["avg_cost_price"], "245.3000")
        self.assertEqual(snap2["avg_cost_price"], "240.0000")
        self.assertEqual(snap1["qty"], "1000.0000")
        self.assertEqual(snap2["qty"], "500.0000")

    def test_watchlist_isolated_by_account(self):
        """自选按账户隔离。"""
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO accounts(slug, display_name, broker, base_currency, is_default, is_archived, created_at, updated_at) VALUES(?, ?, ?, ?, 0, 0, ?, ?)",
            ("swing", "波段账户", "", "CNY", now, now),
        )
        self.conn.commit()
        acct2_id = self.conn.execute("SELECT id FROM accounts WHERE slug='swing'").fetchone()[0]

        add_watch(self.conn, self.acct_id, "a", "300750", "")
        add_watch(self.conn, acct2_id, "hk", "00700", "")

        w1 = list_watch(self.conn, self.acct_id)
        w2 = list_watch(self.conn, acct2_id)

        self.assertEqual(len(w1), 1)
        self.assertEqual(w1[0]["code"], "300750")
        self.assertEqual(len(w2), 1)
        self.assertEqual(w2[0]["code"], "00700")

    def test_trade_delete_cross_account_blocked(self):
        """删除交易时不能跨账户。"""
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO accounts(slug, display_name, broker, base_currency, is_default, is_archived, created_at, updated_at) VALUES(?, ?, ?, ?, 0, 0, ?, ?)",
            ("swing", "波段账户", "", "CNY", now, now),
        )
        self.conn.commit()
        acct2_id = self.conn.execute("SELECT id FROM accounts WHERE slug='swing'").fetchone()[0]

        trade_id = add_trade(
            self.conn, self.acct_id, "a", "300750", "buy", "100", "250.00",
            "2026-05-08 10:00:00", None, None, "0", "0", "0", "0", "",
        )

        # Try to delete from wrong account
        with self.assertRaises(ValueError):
            delete_trade(self.conn, acct2_id, trade_id)

        # Delete from correct account works
        delete_trade(self.conn, self.acct_id, trade_id)
        self.assertEqual(len(list_trades(self.conn, self.acct_id)), 0)

    def test_capital_required_for_analysis(self):
        """capital.total_cny 未设置时 analyze 报错。"""
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO accounts(slug, display_name, broker, base_currency, is_default, is_archived, created_at, updated_at) VALUES(?, ?, ?, ?, 0, 0, ?, ?)",
            ("nocap", "无资金账户", "", "CNY", now, now),
        )
        self.conn.commit()
        nocap_id = self.conn.execute("SELECT id FROM accounts WHERE slug='nocap'").fetchone()[0]
        # Do NOT set capital for this account

        add_watch(self.conn, nocap_id, "a", "300308", "")

        with self.assertRaises(ValueError) as ctx:
            analyze_now(self.conn, nocap_id, "nocap", "无资金账户", "watchlist", analysis_provider=self.provider)
        self.assertIn("capital.total_cny", str(ctx.exception))

    def test_fund_flow_post_close_refresh_window_keeps_short_ttl(self):
        a_after_close = datetime(2026, 5, 12, 15, 30, tzinfo=CN_TZ)
        a_after_window = datetime(2026, 5, 12, 17, 1, tzinfo=CN_TZ)
        hk_after_close = datetime(2026, 5, 12, 16, 30, tzinfo=CN_TZ)
        hk_after_window = datetime(2026, 5, 12, 18, 1, tzinfo=CN_TZ)

        self.assertEqual(_ttl_for_moment("a", a_after_close), 60.0)
        self.assertEqual(_ttl_for_moment("a", a_after_window), 4 * 3600.0)
        self.assertEqual(_ttl_for_moment("hk", hk_after_close), 60.0)
        self.assertEqual(_ttl_for_moment("hk", hk_after_window), 4 * 3600.0)

    def test_fund_flow_historical_cache_can_use_long_ttl(self):
        cached_rows = [{"date": "2026-05-11"}]
        ttl = _ttl_for_call("a", "600276", cached_data=cached_rows)
        self.assertEqual(ttl, 4 * 3600.0)

    def test_cli_smoke(self):
        cmd = [sys.executable, str(SKILL_DIR / "scripts" / "main.py")]
        env = os.environ.copy()
        proc = subprocess.run(
            cmd + ["position", "init", "--account", "default", "--market", "a", "--code", "300750", "--qty", "1000", "--cost", "245.30"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = subprocess.run(
            cmd + ["position", "list", "--account", "default"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("300750", proc.stdout)

    def test_cli_missing_account_errors(self):
        """不传 --account 直接报错。"""
        cmd = [sys.executable, str(SKILL_DIR / "scripts" / "main.py")]
        env = os.environ.copy()
        proc = subprocess.run(
            cmd + ["position", "list"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--account", proc.stderr)

    def test_shared_adapter_with_fake_hub(self):
        fake_hub = Path(self.tmp.name) / "fake-hub"
        fake_scripts = fake_hub / "scripts"
        fake_scripts.mkdir(parents=True)
        (fake_scripts / "analyze_company.py").write_text(
            """
from __future__ import annotations

def analyze(symbol, args):
    return {
        "symbol": symbol,
        "quote": {"current": "12.34", "percent": 1.11},
        "fetched_at": "2026-05-08T14:31:00+08:00",
        "announcements": [],
        "price_history": {"regime": "IN_RANGE"},
    }

def render_text(data):
    return f"TEXT::{data['symbol']}"
""".strip()
            + "\n",
            encoding="utf-8",
        )

        provider = StockMarketHubProvider(hub_dir=str(fake_hub))
        data = provider.analyze("hk", "01810")
        quote = provider.fetch_quote("hk", "01810")
        text = render_analysis_text(data, hub_dir=str(fake_hub))
        direct = analyze_symbol("HK01810", hub_dir=str(fake_hub))

        self.assertEqual(data["symbol"], "HK01810")
        self.assertEqual(data["ann_days"], 30)
        self.assertEqual(quote["current"], "12.34")
        self.assertEqual(text, "TEXT::HK01810")
        self.assertEqual(direct["symbol"], "HK01810")

    def test_openai_yaml_metadata_files(self):
        portfolio_yaml = (REPO_ROOT / "stock-portfolio-copilot" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        market_yaml = (REPO_ROOT / "stock-market-hub" / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn('display_name: "Stock Portfolio Copilot"', portfolio_yaml)
        self.assertIn('default_prompt: "Use $stock-portfolio-copilot', portfolio_yaml)
        self.assertIn('display_name: "Stock Market Hub"', market_yaml)
        self.assertIn('default_prompt: "Use $stock-market-hub', market_yaml)

    def test_stock_market_hub_wrapper_help(self):
        cmd = [sys.executable, str(REPO_ROOT / "stock-market-hub" / "scripts" / "analyze_company.py"), "--help"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--symbol", proc.stdout)

    # ── audit: confidence_trace + spc explain/log/show/diff ───────────

    def _make_analysis_history(self, count: int = 2) -> list[int]:
        """跑 count 次 analyze_now，模拟一段时间复盘历史。返回 analysis_run id 列表。"""
        from spc_core.ledger import list_analysis_runs as _list_runs
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30",
                          None, "2026-05-01 09:30:00", "")
        add_watch(self.conn, self.acct_id, "hk", "01810", "")
        for _ in range(count):
            analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                        "all", analysis_provider=self.provider)
        rows = _list_runs(self.conn, self.acct_id, limit=count + 5)
        return [r["id"] for r in rows[:count]]

    def test_decision_has_confidence_trace(self):
        """每个 decision 都应该附带 confidence_trace 字段，且至少包含 base 起点。"""
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30",
                          None, "2026-05-01 09:30:00", "")
        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                              "holdings", analysis_provider=self.provider)
        self.assertEqual(len(payload["results"]), 1)
        decision = payload["results"][0]["decision"]
        trace = decision.get("confidence_trace")
        self.assertIsNotNone(trace, "decision 应该有 confidence_trace 字段")
        self.assertGreaterEqual(len(trace), 1)
        self.assertEqual(trace[0]["step"], "base")
        self.assertIn("rule", trace[0])
        # confidence_trace 的最后一步 value 应该跟最终 confidence 一致
        final = float(decision["confidence"])
        self.assertAlmostEqual(trace[-1]["value"], final, places=3)

    def test_confidence_trace_records_promotion(self):
        """带风险公告 + 破位 regime → trace 应记录从 0.55 → 0.72 那一跳的 trigger。"""
        provider = FakeProvider()
        add_position_seed(self.conn, self.acct_id, "hk", "01810", "2000", "18.62",
                          None, "2026-05-01 09:30:00", "")
        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                              "holdings", market="hk", code="01810",
                              analysis_provider=provider)
        trace = payload["results"][0]["decision"]["confidence_trace"]
        steps = [s["step"] for s in trace]
        # 小米 01810 在 FakeProvider 里 regime=NEW_YTD_LOW + 风险公告 2 条
        # 应该至少触发 risk_or_low_regime 这步
        self.assertIn("risk_or_low_regime", steps, f"trace={trace}")
        # 该步的 delta 应该为正（升 confidence）
        promo = [s for s in trace if s["step"] == "risk_or_low_regime"][0]
        self.assertGreater(promo["delta"], 0)

    def test_audit_list_analysis_runs(self):
        from spc_core.ledger import list_analysis_runs
        ids = self._make_analysis_history(count=3)
        self.assertEqual(len(ids), 3)
        # 倒序：最新的 id 应该最大
        self.assertEqual(ids, sorted(ids, reverse=True))
        # 限制条数
        partial = list_analysis_runs(self.conn, self.acct_id, limit=2)
        self.assertEqual(len(partial), 2)

    def test_audit_find_runs_covering_symbol(self):
        from spc_core.ledger import find_analysis_runs_covering_symbol
        self._make_analysis_history(count=2)
        runs = find_analysis_runs_covering_symbol(
            self.conn, self.acct_id, "a", "300750", limit=10,
        )
        # FakeProvider 里 300750 是持仓，每次 analyze 都该被 results 包含
        self.assertGreaterEqual(len(runs), 2)
        for r in runs:
            results = r["payload"]["results"]
            self.assertTrue(any(it["market"] == "a" and it["code"] == "300750" for it in results))

    def test_audit_render_explain(self):
        from spc_core.audit import render_explain
        ids = self._make_analysis_history(count=1)
        text = render_explain(self.conn, self.acct_id, self.acct_slug,
                              analysis_id=ids[0], market=None, code=None)
        self.assertIn("置信度构成", text)
        self.assertIn(f"analysis_run id={ids[0]}", text)
        self.assertIn("[1]", text)  # 至少渲染了 base step

    def test_audit_render_explain_filter_symbol(self):
        from spc_core.audit import render_explain
        ids = self._make_analysis_history(count=1)
        text = render_explain(self.conn, self.acct_id, self.acct_slug,
                              analysis_id=ids[0], market="a", code="300750")
        self.assertIn("A 300750", text)
        # 不该出现其它标的的标识
        self.assertNotIn("HK 01810", text)

    def test_audit_render_log(self):
        from spc_core.audit import render_log
        self._make_analysis_history(count=3)
        text = render_log(self.conn, self.acct_id, self.acct_slug,
                          market=None, code=None,
                          since=None, until=None, limit=10)
        self.assertIn("最近", text)
        # 3 次 analyze 应该都出现在列表里（每行至少一个 ID）
        id_lines = [ln for ln in text.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
        self.assertGreaterEqual(len(id_lines), 3)

    def test_audit_render_log_filter_symbol(self):
        from spc_core.audit import render_log
        self._make_analysis_history(count=2)
        text = render_log(self.conn, self.acct_id, self.acct_slug,
                          market="a", code="300750",
                          since=None, until=None, limit=10)
        # symbol 过滤后应该显示该票的 action_label
        self.assertIn("A 300750", text)

    def test_audit_render_show(self):
        from spc_core.audit import render_show
        ids = self._make_analysis_history(count=1)
        text = render_show(self.conn, self.acct_id, self.acct_slug, ids[0])
        self.assertIn("置信度构成", text)
        self.assertIn(f"analysis_run id={ids[0]}", text)
        self.assertIn("资金上限", text)

    def test_audit_render_diff(self):
        from spc_core.audit import render_diff
        self._make_analysis_history(count=2)
        text = render_diff(self.conn, self.acct_id, self.acct_slug,
                           market="a", code="300750",
                           since=None, until=None, between=None)
        self.assertIn("决策 diff", text)
        # 两次 analyze 用同一个 FakeProvider，决策应该完全一样，diff 应说明"无变化"
        self.assertIn("无变化", text)

    def test_audit_render_diff_insufficient_data(self):
        from spc_core.audit import render_diff
        self._make_analysis_history(count=1)
        text = render_diff(self.conn, self.acct_id, self.acct_slug,
                           market="a", code="300750",
                           since=None, until=None, between=None)
        # 只有 1 条记录无法 diff
        self.assertIn("无法 diff", text)

    def test_audit_parse_since(self):
        from spc_core.audit import parse_since
        from datetime import datetime, timezone
        # 相对时长
        seven_days = parse_since("7d")
        self.assertIsNotNone(seven_days)
        dt = datetime.fromisoformat(seven_days)
        diff = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        self.assertAlmostEqual(diff, 7, delta=0.1)
        # 日期格式
        d = parse_since("2026-05-06")
        self.assertEqual(d, "2026-05-06T00:00:00+00:00")
        # ISO 完整
        iso = parse_since("2026-05-06T10:30:00+00:00")
        self.assertEqual(iso, "2026-05-06T10:30:00+00:00")
        # 空
        self.assertIsNone(parse_since(None))
        self.assertIsNone(parse_since(""))
        # 非法格式
        with self.assertRaises(ValueError):
            parse_since("not a date")


if __name__ == "__main__":
    unittest.main()

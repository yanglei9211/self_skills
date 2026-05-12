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
from spc_core.decision import analyze_now  # noqa: E402
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
from stock_core.fund_flow import _ttl_for_call, _ttl_for_moment  # noqa: E402
from stock_core.stock_market_hub import analyze_symbol, render_analysis_text  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402


class FakeProvider:
    def __init__(self, fund_flow_overrides: dict | None = None):
        self.quote_map = {
            ("a", "300750"): {"current": "260.00", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("a", "300308"): {"current": "935.00", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("hk", "01810"): {"current": "19.24", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("hk", "00700"): {"current": "410.20", "fetched_at": "2026-05-08T14:31:00+08:00"},
        }
        # 通过这个字典可以为某只标的注入 fund_flow 摘要；
        # 默认不注入，相当于 analyze_company 拿不到主力资金流（North Stock / 美股 / 接口失败的情形）
        self.fund_flow_overrides = fund_flow_overrides or {}

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

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        os.environ.pop("SPC_DATA_DIR", None)
        os.environ.pop("SPC_DISABLE_FX_HTTP", None)

    def test_position_trade_sync_and_delete_flow(self):
        add_position_seed(self.conn, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        trade_id = add_trade(
            self.conn,
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
        sync_portfolio(self.conn, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        snap = latest_snapshots(self.conn)[0]
        self.assertEqual(snap["qty"], "800.0000")
        self.assertEqual(snap["avg_cost_price"], "245.3000")
        self.assertEqual(snap["last_price"], "260.0000")
        self.assertEqual(snap["realized_pnl_ccy"], "1274.82")
        self.assertEqual(snap["unrealized_pnl_ccy"], "11760.00")

        delete_trade(self.conn, trade_id)
        self.assertEqual(len(list_trades(self.conn)), 0)
        self.assertEqual(len(list_trades(self.conn, include_deleted=True)), 1)

    def test_hk_sync_with_buy_fees_and_fx(self):
        add_position_seed(self.conn, "hk", "1810", "2000", "18.62", None, "2026-05-01 09:30:00", "")
        add_trade(
            self.conn,
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
        sync_portfolio(self.conn, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        snap = latest_snapshots(self.conn)[0]
        self.assertEqual(snap["qty"], "2500.0000")
        self.assertEqual(snap["avg_cost_price"], "18.7241")
        self.assertEqual(snap["total_fees_ccy"], "20.36")
        self.assertEqual(snap["position_value_cny"], "44252.00")

    def test_watch_capital_and_analysis(self):
        add_position_seed(self.conn, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        add_position_seed(self.conn, "hk", "1810", "2000", "18.62", None, "2026-05-01 09:30:00", "")
        add_watch(self.conn, "hk", "00700", "")
        set_capital(self.conn, "500000", "20")

        payload = analyze_now(self.conn, "all", analysis_provider=self.provider)
        self.assertEqual(payload["scope"], "all")
        self.assertEqual(len(payload["results"]), 3)
        actions = {(item["market"], item["code"]): item["decision"]["action"] for item in payload["results"]}
        self.assertEqual(actions[("a", "300750")], "trim")
        self.assertEqual(actions[("hk", "01810")], "trim")
        self.assertEqual(actions[("hk", "00700")], "focus")
        opp_codes = {(item["market"], item["code"]) for item in payload["opportunities"]}
        self.assertIn(("a", "300308"), opp_codes)
        self.assertIn(("a", "300762"), opp_codes)
        last = latest_analysis_run(self.conn)
        self.assertIsNotNone(last)
        self.assertEqual(last["payload"]["scope"], "all")
        self.assertEqual(capital_settings(self.conn)["total_cny"], "500000")
        self.assertEqual(len(list_watch(self.conn)), 1)

    def test_strict_buy_requires_strong_setup(self):
        add_watch(self.conn, "a", "300308", "")

        payload = analyze_now(self.conn, "watchlist", analysis_provider=self.provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        self.assertEqual(decision["action_label"], "买入候选")
        self.assertTrue(any("强趋势" in reason for reason in decision["reasoning"]))

    def test_fund_flow_blocks_buy_on_recent_outflow(self):
        """自选 + 强趋势 + 正向公告，但近 5 日主力反向流出 → buy 候选被否，回退到 focus。"""
        add_watch(self.conn, "a", "300308", "")
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_INFLOW", reversal="INFLOW_TO_OUTFLOW",
                m3=-1.5, m5=-3.0, m20=15.0, main_today_yi=-1.2,
            ),
        })

        payload = analyze_now(self.conn, "watchlist", analysis_provider=provider)
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
        add_watch(self.conn, "a", "300308", "")
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_OUTFLOW",
                m3=-4.0, m5=-8.0, m20=-22.0, main_today_yi=-2.5, super_big_yi=-1.8, big_yi=-0.7,
            ),
        })

        payload = analyze_now(self.conn, "watchlist", analysis_provider=provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "avoid")
        self.assertTrue(
            any("主力 20 日持续净流出" in r for r in decision["risks"]),
            f"risks={decision['risks']}",
        )

    def test_fund_flow_background_weak_but_3d_5d_repair_keeps_focus_not_avoid(self):
        """20 日背景偏弱，但近 3/5 日修复时，不应直接按 avoid 处理。"""
        add_watch(self.conn, "a", "300308", "")
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_OUTFLOW",
                reversal="OUTFLOW_TO_INFLOW",
                m3=2.0, m5=6.0, m20=-18.0, main_today_yi=1.1,
            ),
        })

        payload = analyze_now(self.conn, "watchlist", analysis_provider=provider)
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "focus")
        self.assertTrue(
            any("近 5 日主力资金由流出转为流入" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )

    def test_pnl_summary(self):
        add_position_seed(self.conn, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        sync_portfolio(self.conn, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        summary = pnl_summary(self.conn)
        self.assertEqual(summary["positions"], 1)
        self.assertEqual(summary["total_position_value_cny"], "260000.00")

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
            cmd + ["position", "init", "--market", "a", "--code", "300750", "--qty", "1000", "--cost", "245.30"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = subprocess.run(
            cmd + ["position", "list"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("300750", proc.stdout)

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
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
from stock_core.stock_market_hub import analyze_symbol, render_analysis_text  # noqa: E402


class FakeProvider:
    def __init__(self):
        self.quote_map = {
            ("a", "300750"): {"current": "260.00", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("hk", "01810"): {"current": "19.24", "fetched_at": "2026-05-08T14:31:00+08:00"},
            ("hk", "00700"): {"current": "410.20", "fetched_at": "2026-05-08T14:31:00+08:00"},
        }

    def fetch_quote(self, market, code):
        return self.quote_map.get((market, code), {"current": "10.00", "fetched_at": "2026-05-08T14:31:00+08:00"})

    def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
        quote = self.fetch_quote(market, code)
        if (market, code) == ("a", "300750"):
            return {
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
            }
        if (market, code) == ("hk", "01810"):
            return {
                "fetched_at": quote["fetched_at"],
                "quote": {"current": quote["current"], "percent": -3.5},
                "price_history": {"regime": "NEW_YTD_LOW"},
                "announcements": [
                    {"date": "2026-05-02", "title": "收到监管问询函", "pdf_url": "https://example.com/h1.pdf"},
                    {"date": "2026-05-03", "title": "主要股东减持公告", "pdf_url": "https://example.com/h2.pdf"},
                ],
            }
        return {
            "fetched_at": quote["fetched_at"],
            "quote": {"current": quote["current"], "percent": 0.8},
            "price_history": {"regime": "IN_RANGE"},
            "concepts": ["半导体"],
            "peers": [],
            "announcements": [
                {"date": "2026-05-03", "title": "回购股份公告", "pdf_url": "https://example.com/w1.pdf"},
            ],
        }

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
        self.assertEqual(actions[("hk", "00700")], "buy")
        opp_codes = {(item["market"], item["code"]) for item in payload["opportunities"]}
        self.assertIn(("a", "300308"), opp_codes)
        self.assertIn(("a", "300762"), opp_codes)
        last = latest_analysis_run(self.conn)
        self.assertIsNotNone(last)
        self.assertEqual(last["payload"]["scope"], "all")
        self.assertEqual(capital_settings(self.conn)["total_cny"], "500000")
        self.assertEqual(len(list_watch(self.conn)), 1)

    def test_pnl_summary(self):
        add_position_seed(self.conn, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")
        sync_portfolio(self.conn, analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        summary = pnl_summary(self.conn)
        self.assertEqual(summary["positions"], 1)
        self.assertEqual(summary["total_position_value_cny"], "260000.00")

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

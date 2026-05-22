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
from unittest import mock


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
REPO_ROOT = SKILL_DIR.parent
SHARED_DIR = REPO_ROOT / "shared"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SHARED_DIR))

from spc_core.db import connect  # noqa: E402
from spc_core.decision import analyze_now, render_analysis_text as render_spc_analysis_text  # noqa: E402
from spc_core.ledger import (  # noqa: E402
    add_execution_review,
    add_position_seed,
    add_trade,
    add_watch,
    attach_trade_to_plan,
    cancel_execution_plan,
    create_execution_plan,
    delete_trade,
    get_execution_plan_detail,
    latest_analysis_run,
    latest_snapshot_for_symbol,
    latest_snapshots,
    list_trades,
    list_watch,
)
from spc_core.portfolio import pnl_summary, sync_portfolio  # noqa: E402
from spc_core.settings import capital_settings, set_capital  # noqa: E402
from spc_core.market_bridge import StockMarketHubProvider  # noqa: E402
from spc_core.utils import utc_now_iso  # noqa: E402
from stock_core.fund_flow import _ttl_for_call, _ttl_for_moment, cross_validate  # noqa: E402
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


def _ff(regime: str, *, reversal: str | None = None, m3: float = 0.0, m5: float = 0.0,
        m10: float | None = None, m20: float = 0.0,
        main_today_yi: float = 0.0, super_big_yi: float = 0.0, big_yi: float = 0.0) -> dict:
    """构造 fund_flow 摘要的小工具。

    自动通过 :func:`cross_validate` 挂上 ``cross_validation`` 字段，
    与生产路径 ``summarize_fund_flow`` 的契约保持一致。

    ``m10`` 默认沿用 ``m5``（兼容旧用例）；如需精细控制 10d 单独传。
    """
    if m10 is None:
        m10 = m5
    rolling = {
        "1d": {"main_yi": main_today_yi, "inflow_days": 0, "outflow_days": 0, "days": 1},
        "3d": {"main_yi": m3, "inflow_days": 0, "outflow_days": 0, "days": 3},
        "5d": {"main_yi": m5, "inflow_days": 0, "outflow_days": 0, "days": 5},
        "10d": {"main_yi": m10, "inflow_days": 0, "outflow_days": 0, "days": 10},
        "20d": {"main_yi": m20, "inflow_days": 0, "outflow_days": 0, "days": 20},
    }
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
        "rolling": rolling,
        "regime": regime,
        "reversal": reversal,
        "cross_validation": cross_validate(rolling, reversal),
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
        # 测试默认禁用 LLM 复核（避免误触发真实 codex/claude 调用）。
        # 个别测试需要测复核行为时会在测试内 monkeypatch 或临时 unset。
        os.environ["SPC_LLM_BACKEND"] = "none"
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

    def test_db_schema_upgraded_to_v5(self):
        """v5 给 position_peak 增加 P0a 分档幂等性字段。"""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 5)
        tables = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("position_peak", tables)
        cols = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(position_peak)"
        ).fetchall()}
        self.assertIn("last_trim_tier", cols)
        self.assertIn("last_trim_price", cols)
        self.assertIn("last_trim_time", cols)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        os.environ.pop("SPC_DATA_DIR", None)
        os.environ.pop("SPC_DISABLE_FX_HTTP", None)
        os.environ.pop("SPC_LLM_BACKEND", None)

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

    def test_manual_snapshot_preferred_over_newer_sync_snapshot(self):
        self.conn.execute(
            """
            INSERT INTO portfolio_snapshot(
              account_id, market, code, qty, avg_cost_price, currency, gross_cost_ccy, total_fees_ccy,
              realized_pnl_ccy, last_price, last_price_time, unrealized_pnl_ccy,
              fx_rate_to_cny, position_value_cny, snapshot_time, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.acct_id, "a", "300750", "1000.0000", "245.3000", "CNY", "245300.00", "0.00",
                "0.00", "250.0000", "2026-05-08T10:30:00+08:00", "4700.00",
                "1", "250000.00", "2026-05-08T02:30:00+00:00", "manual_screenshot_calibration",
            ),
        )
        self.conn.execute(
            """
            INSERT INTO portfolio_snapshot(
              account_id, market, code, qty, avg_cost_price, currency, gross_cost_ccy, total_fees_ccy,
              realized_pnl_ccy, last_price, last_price_time, unrealized_pnl_ccy,
              fx_rate_to_cny, position_value_cny, snapshot_time, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.acct_id, "a", "300750", "800.0000", "245.3000", "CNY", "196240.00", "0.00",
                "0.00", "260.0000", "2026-05-08T14:31:00+08:00", "11760.00",
                "1", "208000.00", "2026-05-08T06:31:00+00:00", "sync",
            ),
        )
        self.conn.commit()

        snap = latest_snapshots(self.conn, self.acct_id)[0]
        one = latest_snapshot_for_symbol(self.conn, self.acct_id, "a", "300750")
        self.assertEqual(snap["source"], "manual_screenshot_calibration")
        self.assertEqual(snap["qty"], "1000.0000")
        self.assertEqual(one["source"], "manual_screenshot_calibration")
        self.assertEqual(one["qty"], "1000.0000")

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

    def test_execution_plan_from_analysis_trade_and_review_flow(self):
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        payload = analyze_now(
            self.conn,
            self.acct_id,
            self.acct_slug,
            self.acct_name,
            "watchlist",
            analysis_provider=self.provider,
        )
        self.assertEqual(payload["results"][0]["decision"]["action"], "buy")
        analysis_id = latest_analysis_run(self.conn, self.acct_id)["id"]

        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300308",
            "buy",
            "open",
            "趋势强，准备建仓",
            source_ref_id=analysis_id,
            source_action="buy",
            target_qty="100",
            price_limit_low="930",
            price_limit_high="940",
            stop_loss_price="890",
            confidence="0.72",
            tags="trend,watchlist",
        )
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300308",
            "buy",
            "100",
            "935.00",
            "2026-05-08 10:32:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "首笔建仓",
            plan_id=plan_id,
        )
        review_id = add_execution_review(
            self.conn,
            self.acct_id,
            plan_id=plan_id,
            trade_id=trade_id,
            horizon="five_day",
            outcome="win",
            discipline_score=4,
            execution_score=4,
            thesis_score=4,
            plan_followed=True,
            lesson="按计划成交，后续可以等回踩再加仓",
        )

        detail = get_execution_plan_detail(self.conn, self.acct_id, plan_id)
        self.assertEqual(detail["source_ref_id"], analysis_id)
        self.assertEqual(detail["status"], "filled")
        self.assertEqual(detail["fill_summary"]["trade_count"], 1)
        self.assertEqual(detail["fill_summary"]["filled_qty"], "100.0000")
        self.assertEqual(detail["fill_summary"]["completion_pct"], "100.0000")
        self.assertEqual(detail["trades"][0]["id"], trade_id)
        self.assertEqual(detail["reviews"][0]["id"], review_id)

    def test_attach_trade_to_plan_updates_status(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "hk",
            "01810",
            "buy",
            "probe",
            "弱市里先试探一笔",
            target_qty="500",
        )
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "hk",
            "01810",
            "buy",
            "200",
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
        attach_trade_to_plan(self.conn, self.acct_id, plan_id, trade_id)

        detail = get_execution_plan_detail(self.conn, self.acct_id, plan_id)
        self.assertEqual(detail["status"], "partially_filled")
        self.assertEqual(detail["fill_summary"]["filled_qty"], "200.0000")
        self.assertEqual(detail["fill_summary"]["completion_pct"], "40.0000")

    def test_delete_trade_refreshes_execution_plan_status(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "open",
            "建仓计划",
            target_qty="100",
        )
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "100",
            "250.00",
            "2026-05-08 10:00:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
            plan_id=plan_id,
        )
        self.assertEqual(get_execution_plan_detail(self.conn, self.acct_id, plan_id)["status"], "filled")

        delete_trade(self.conn, self.acct_id, trade_id)

        detail = get_execution_plan_detail(self.conn, self.acct_id, plan_id)
        self.assertEqual(detail["status"], "planned")
        self.assertEqual(detail["fill_summary"]["trade_count"], 0)
        self.assertEqual(detail["fill_summary"]["filled_qty"], "0.0000")
        self.assertEqual(len(detail["trades"]), 0)

    def test_execution_review_does_not_override_plan_status(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "hk",
            "01810",
            "buy",
            "probe",
            "弱市里先试探一笔",
            target_qty="500",
        )
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "hk",
            "01810",
            "buy",
            "200",
            "19.10",
            "2026-05-08 10:32:00",
            None,
            "0.92",
            "0",
            "0",
            "0",
            "0",
            "",
            plan_id=plan_id,
        )
        add_execution_review(
            self.conn,
            self.acct_id,
            plan_id=plan_id,
            trade_id=trade_id,
            horizon="manual",
            outcome="pending",
            lesson="先记录执行质量，不改变计划生命周期状态",
        )

        detail = get_execution_plan_detail(self.conn, self.acct_id, plan_id)
        self.assertEqual(detail["status"], "partially_filled")
        self.assertEqual(len(detail["reviews"]), 1)

    def test_target_cash_cny_plan_can_reach_filled(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "open",
            "按金额建仓",
            target_cash_cny="100000",
        )
        add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "400",
            "250.00",
            "2026-05-08 10:00:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
            plan_id=plan_id,
        )

        detail = get_execution_plan_detail(self.conn, self.acct_id, plan_id)
        self.assertEqual(detail["status"], "filled")
        self.assertEqual(detail["fill_summary"]["filled_cash_cny"], "100000.00")
        self.assertEqual(detail["fill_summary"]["cash_completion_pct"], "100.00")

    def test_target_position_pct_plan_can_reach_filled(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "open",
            "按仓位建仓",
            target_position_pct="20",
        )
        add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "400",
            "250.00",
            "2026-05-08 10:00:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
            plan_id=plan_id,
        )

        detail = get_execution_plan_detail(self.conn, self.acct_id, plan_id)
        self.assertEqual(detail["status"], "filled")
        self.assertEqual(detail["fill_summary"]["filled_position_pct"], "20.00")
        self.assertEqual(detail["fill_summary"]["position_completion_pct"], "100.00")

    def test_duplicate_attach_returns_value_error(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "open",
            "建仓计划",
            target_qty="100",
        )
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "50",
            "250.00",
            "2026-05-08 10:00:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
        )
        attach_trade_to_plan(self.conn, self.acct_id, plan_id, trade_id)
        with self.assertRaisesRegex(ValueError, "已经关联"):
            attach_trade_to_plan(self.conn, self.acct_id, plan_id, trade_id)

    def test_terminal_plan_rejects_attach(self):
        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "open",
            "建仓计划",
            target_qty="100",
        )
        cancel_execution_plan(self.conn, self.acct_id, plan_id, reason="条件失效")
        trade_id = add_trade(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "50",
            "250.00",
            "2026-05-08 10:00:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
        )
        with self.assertRaisesRegex(ValueError, "终态"):
            attach_trade_to_plan(self.conn, self.acct_id, plan_id, trade_id)

    def test_attach_trade_cross_account_blocked(self):
        now = utc_now_iso()
        self.conn.execute(
            "INSERT INTO accounts(slug, display_name, broker, base_currency, is_default, is_archived, created_at, updated_at) VALUES(?, ?, ?, ?, 0, 0, ?, ?)",
            ("swing", "波段账户", "", "CNY", now, now),
        )
        self.conn.commit()
        acct2_id = self.conn.execute("SELECT id FROM accounts WHERE slug='swing'").fetchone()[0]
        set_capital(self.conn, acct2_id, "300000", "25")

        plan_id = create_execution_plan(
            self.conn,
            self.acct_id,
            "a",
            "300750",
            "buy",
            "open",
            "默认账户建仓",
            target_qty="100",
        )
        trade_id = add_trade(
            self.conn,
            acct2_id,
            "a",
            "300750",
            "buy",
            "100",
            "250.00",
            "2026-05-08 10:00:00",
            None,
            None,
            "0",
            "0",
            "0",
            "0",
            "",
        )

        with self.assertRaises(ValueError):
            attach_trade_to_plan(self.conn, self.acct_id, plan_id, trade_id)

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

    def test_fund_flow_sources_surface_xueqiu_fallback_warning(self):
        """如果资金流降级到雪球，sources 里必须明确暴露来源和风险提示。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        ff = _ff(
            regime="OSCILLATING",
            m3=2.0,
            m5=3.0,
            m20=4.0,
            main_today_yi=1.2,
        )
        ff["flow_source"] = "xueqiu_intraday_fallback"
        ff["flow_label"] = "今日盘中累计（雪球兜底，东财盘中不可用，数据不完全准确），截至 2026-05-08T11:15:00+08:00"
        ff["warnings"] = ["当前盘中资金流已降级为雪球兜底口径，数据不完全准确。"]
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): ff,
        })

        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name, "watchlist", analysis_provider=provider)
        decision = payload["results"][0]["decision"]

        self.assertTrue(
            any("fund_flow.source=xueqiu_intraday_fallback" == s for s in decision["sources"]),
            f"sources={decision['sources']}",
        )
        self.assertTrue(
            any("fund_flow.warning=" in s and "数据不完全准确" in s for s in decision["sources"]),
            f"sources={decision['sources']}",
        )

    def test_xueqiu_fallback_trend_buy_subtracts_confidence_and_emits_risk(self):
        """趋势 buy 路径 + 资金流降级为雪球兜底 → confidence 从 0.72 扣到 0.67，
        risks 标注 ⚠️ 雪球口径不可靠，reasoning 标注 confidence -0.05。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")
        ff = _ff(
            regime="PERSISTENT_INFLOW",
            m3=2.0, m5=3.0, m20=4.0,
            main_today_yi=1.2,
        )
        ff["flow_source"] = "xueqiu_intraday_fallback"
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): ff,
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        # 0.72 (trend baseline) - 0.05 (xueqiu fallback) = 0.67
        self.assertEqual(
            decision["confidence"], "0.67",
            f"trend buy + xueqiu_fallback 应 -0.05 → 0.67，实际 {decision['confidence']}",
        )
        self.assertTrue(
            any("雪球兜底口径" in r for r in decision["risks"]),
            f"risks 应含雪球兜底提示；risks={decision['risks']}",
        )
        self.assertTrue(
            any("confidence -0.05" in r and "雪球" in r for r in decision["reasoning"]),
            f"reasoning 应标注 confidence -0.05；reasoning={decision['reasoning']}",
        )

    def test_xueqiu_fallback_reversal_buy_subtracts_confidence_and_emits_risk(self):
        """反转 buy 路径 + 资金流降级为雪球兜底 → confidence 从 0.68 扣到 0.63，
        risks 标注雪球不可靠，reasoning 标注 confidence -0.05。"""
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

        ff = _ff(
            regime="PERSISTENT_INFLOW", reversal="OUTFLOW_TO_INFLOW",
            m3=2.0, m5=4.0, m20=15.0, main_today_yi=1.2,
        )
        ff["flow_source"] = "xueqiu_intraday_fallback"
        provider = ReversalProvider(fund_flow_overrides={
            ("hk", "00700"): ff,
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        # 0.68 (reversal baseline) - 0.05 (xueqiu fallback) = 0.63
        self.assertEqual(
            decision["confidence"], "0.63",
            f"reversal buy + xueqiu_fallback 应 -0.05 → 0.63，实际 {decision['confidence']}",
        )
        self.assertTrue(
            any("雪球兜底口径" in r for r in decision["risks"]),
            f"risks 应含雪球兜底提示；risks={decision['risks']}",
        )
        self.assertTrue(
            any("confidence -0.05" in r and "雪球" in r for r in decision["reasoning"]),
            f"reasoning 应标注 confidence -0.05；reasoning={decision['reasoning']}",
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

    # ── cross_validate（多周期交叉验证）单元测试 ─────────────────
    # 数据契约：cross_validate 输入 rolling = {p: {"main_yi": float|None, ...}}
    # 输出包含 directions / all_aligned / short_long_conflict / acceleration /
    # is_resonance / reversal_confirmed / verdict / verdict_zh 等字段。

    @staticmethod
    def _rolling(*, m1: float, m5: float, m10: float, m20: float) -> dict:
        return {
            "1d": {"main_yi": m1, "days": 1},
            "5d": {"main_yi": m5, "days": 5},
            "10d": {"main_yi": m10, "days": 10},
            "20d": {"main_yi": m20, "days": 20},
        }

    def test_cross_validate_resonance_inflow(self):
        """四周期一致流入 + 1d 日均 > 5d > 10d → RESONANCE_INFLOW。"""
        rolling = self._rolling(m1=2.0, m5=8.0, m10=14.0, m20=24.0)
        # 日均：1d=2.0, 5d=1.6, 10d=1.4 → 1d>5d>10d 加速
        cross = cross_validate(rolling, reversal=None)
        self.assertEqual(cross["verdict"], "RESONANCE_INFLOW")
        self.assertTrue(cross["all_aligned"])
        self.assertEqual(cross["acceleration"], "accelerating_inflow")
        self.assertTrue(cross["is_resonance"])
        self.assertFalse(cross["short_long_conflict"])

    def test_cross_validate_short_long_conflict_weakening(self):
        """20d 仍流入，但 1d 与 5d 已转流出 → WEAKENING_INFLOW + short_long_conflict。"""
        rolling = self._rolling(m1=-1.2, m5=-3.0, m10=-3.0, m20=15.0)
        cross = cross_validate(rolling, reversal="INFLOW_TO_OUTFLOW")
        self.assertTrue(cross["short_long_conflict"])
        self.assertEqual(cross["conflict_kind"], "short_outflow_long_inflow")
        # reversal=INFLOW_TO_OUTFLOW 被 1d/5d 同向流出背书 → 优先级高于 WEAKENING
        self.assertEqual(cross["verdict"], "REVERSAL_OUTFLOW_CONFIRMED")
        self.assertTrue(cross["reversal_confirmed"])

    def test_cross_validate_reversal_unconfirmed(self):
        """reversal=OUTFLOW_TO_INFLOW 但 1d 仍流出 → reversal_confirmed=False。"""
        rolling = self._rolling(m1=-1.5, m5=2.0, m10=2.0, m20=-10.0)
        cross = cross_validate(rolling, reversal="OUTFLOW_TO_INFLOW")
        self.assertFalse(cross["reversal_confirmed"])
        self.assertEqual(cross["verdict"], "REVERSAL_UNCONFIRMED")

    def test_cross_validate_concentration_only_when_aligned(self):
        """concentration_5d_in_20d 仅在 5d 与 20d 同向时定义，反向应为 None。"""
        # 同向：5d=+8, 20d=+15 → 8/15 ≈ 0.533
        rolling_aligned = self._rolling(m1=1.5, m5=8.0, m10=12.0, m20=15.0)
        cross_a = cross_validate(rolling_aligned)
        self.assertAlmostEqual(cross_a["concentration_5d_in_20d"], 0.533, places=2)
        # 反向：5d=-3, 20d=+15 → None
        rolling_conflict = self._rolling(m1=-1.0, m5=-3.0, m10=-3.0, m20=15.0)
        cross_c = cross_validate(rolling_conflict, reversal="INFLOW_TO_OUTFLOW")
        self.assertIsNone(cross_c["concentration_5d_in_20d"])

    def test_cross_validate_missing_period_does_not_crash(self):
        """缺 1d 时仍能给出可用结论（reversal_confirmed=None，不能背书）。"""
        rolling = {
            "5d": {"main_yi": 4.0, "days": 5},
            "10d": {"main_yi": 6.0, "days": 10},
            "20d": {"main_yi": 15.0, "days": 20},
        }
        cross = cross_validate(rolling, reversal="OUTFLOW_TO_INFLOW")
        # 1d 缺失：reversal_confirmed 必须为 None，否则 decision 会误判
        self.assertIsNone(cross["reversal_confirmed"])
        # all_aligned 也应为 False（因为 1d 缺失，方向不全）
        self.assertFalse(cross["all_aligned"])

    def test_summarize_fund_flow_attaches_cross_validation(self):
        """生产路径 summarize_fund_flow 应自动挂 cross_validation 字段，
        让上游消费方 (decision/render) 不需要再各自调 cross_validate。"""
        from stock_core.fund_flow import summarize_fund_flow
        # 构造 5 行最小日 K（5 天连续流入），跑一遍真实摘要
        rows = [
            {"date": f"2026-05-0{i}", "main": 1e8, "small": None, "mid": None,
             "big": None, "super_big": None, "main_pct": None, "small_pct": None,
             "mid_pct": None, "big_pct": None, "super_big_pct": None,
             "close": 100.0, "change_pct": 1.0}
            for i in range(1, 6)
        ]
        summary = summarize_fund_flow(rows)
        self.assertIn("cross_validation", summary)
        cross = summary["cross_validation"]
        self.assertEqual(set(cross["periods"]), {"1d", "5d", "10d", "20d"})
        self.assertIn("verdict", cross)

    def test_get_fund_flow_summary_prefers_eastmoney_intraday(self):
        """盘中优先使用东财分时，不应静默混入雪球口径。"""
        from stock_core.fund_flow import get_fund_flow_summary

        today = datetime.now(CN_TZ).date().isoformat()
        rows = [
            {"date": "2026-05-18", "main": 1e8, "small": None, "mid": None, "big": None, "super_big": None,
             "main_pct": None, "small_pct": None, "mid_pct": None, "big_pct": None, "super_big_pct": None,
             "close": 100.0, "change_pct": 1.0},
            {"date": "2026-05-19", "main": 2e8, "small": None, "mid": None, "big": None, "super_big": None,
             "main_pct": None, "small_pct": None, "mid_pct": None, "big_pct": None, "super_big_pct": None,
             "close": 101.0, "change_pct": 1.0},
        ]
        em_live = {
            "date": today,
            "main": -3e8,
            "small": 1e8,
            "mid": 1e8,
            "big": -2e8,
            "super_big": -1e8,
            "main_pct": None,
            "small_pct": None,
            "mid_pct": None,
            "big_pct": None,
            "super_big_pct": None,
            "close": None,
            "change_pct": None,
        }
        xq_live = dict(em_live)
        xq_live["main"] = 9e8

        with (
            mock.patch("stock_core.fund_flow.fetch_daily_fund_flow", return_value=rows),
            mock.patch("stock_core.fund_flow._infer_today_flow_mode", return_value="intraday_live"),
            mock.patch("stock_core.fund_flow._live_today_row_from_eastmoney", return_value=(em_live, f"{today}T11:20:00+08:00", [])),
            mock.patch("stock_core.fund_flow._live_today_row_from_xueqiu", return_value=(xq_live, f"{today}T11:20:00+08:00", [])) as xq_mock,
        ):
            summary = get_fund_flow_summary("a", "000021")

        self.assertEqual(summary["flow_source"], "eastmoney_intraday")
        self.assertIn("东财分时", summary["flow_label"])
        self.assertAlmostEqual(summary["today"]["main_yi"], -3.0)
        xq_mock.assert_not_called()

    def test_get_fund_flow_summary_marks_xueqiu_fallback(self):
        """东财盘中不可用时，允许降级雪球，但必须显式打标并提示不完全准确。"""
        from stock_core.fund_flow import get_fund_flow_summary

        today = datetime.now(CN_TZ).date().isoformat()
        rows = [
            {"date": "2026-05-18", "main": 1e8, "small": None, "mid": None, "big": None, "super_big": None,
             "main_pct": None, "small_pct": None, "mid_pct": None, "big_pct": None, "super_big_pct": None,
             "close": 100.0, "change_pct": 1.0},
            {"date": "2026-05-19", "main": 2e8, "small": None, "mid": None, "big": None, "super_big": None,
             "main_pct": None, "small_pct": None, "mid_pct": None, "big_pct": None, "super_big_pct": None,
             "close": 101.0, "change_pct": 1.0},
        ]
        xq_live = {
            "date": today,
            "main": 1.2e8,
            "small": 0.1e8,
            "mid": 0.2e8,
            "big": 1.0e8,
            "super_big": 0.2e8,
            "main_pct": 1.5,
            "small_pct": 0.1,
            "mid_pct": 0.2,
            "big_pct": 1.3,
            "super_big_pct": 0.2,
            "close": None,
            "change_pct": None,
        }

        with (
            mock.patch("stock_core.fund_flow.fetch_daily_fund_flow", return_value=rows),
            mock.patch("stock_core.fund_flow._infer_today_flow_mode", return_value="intraday_live"),
            mock.patch("stock_core.fund_flow._live_today_row_from_eastmoney", return_value=(None, None, ["东财盘中资金流不可用：test"])),
            mock.patch("stock_core.fund_flow._live_today_row_from_xueqiu", return_value=(xq_live, f"{today}T11:21:00+08:00", [])),
        ):
            summary = get_fund_flow_summary("a", "000021")

        self.assertEqual(summary["flow_source"], "xueqiu_intraday_fallback")
        self.assertIn("雪球兜底", summary["flow_label"])
        self.assertAlmostEqual(summary["today"]["main_yi"], 1.2)
        self.assertTrue(any("数据不完全准确" in w for w in summary.get("warnings", [])))


class SPCCrossValidationDecisionTestCase(SPCTestCase):
    """spc 决策树新增的多周期交叉验证分支。

    继承 SPCTestCase 拿账户 / 资金 / 数据库 setUp。
    """

    def test_reversal_unconfirmed_blocks_buy_path(self):
        """reversal=OUTFLOW_TO_INFLOW 但 1d 仍流出 → cross.reversal_confirmed=False
        → 反转买入路径应被否决，落到 focus（不再像旧版那样直接 buy）。"""
        add_watch(self.conn, self.acct_id, "hk", "00700", "")

        class ReversalProvider(FakeProvider):
            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                quote = self.fetch_quote(market, code)
                if (market, code) == ("hk", "00700"):
                    return self._attach_fund_flow(market, code, {
                        "fetched_at": quote["fetched_at"],
                        "info": {"name": "腾讯控股"},
                        "quote": {"current": quote["current"], "percent": 1.0},
                        "price_history": {"regime": "NEAR_YTD_LOW"},
                        "announcements": [
                            {"date": "2026-05-01", "title": "回购股份公告", "pdf_url": "h1"},
                            {"date": "2026-05-02", "title": "新品发布合作", "pdf_url": "h2"},
                        ],
                    })
                return super().analyze(market, code, ann_days, with_peers, skip)

        # 关键数据：m3/m5 仍 > 0（旧规则会放行），但 m1d=-1.5 让 cross.reversal_confirmed=False
        provider = ReversalProvider(fund_flow_overrides={
            ("hk", "00700"): _ff(
                regime="OSCILLATING", reversal="OUTFLOW_TO_INFLOW",
                m3=2.0, m5=4.0, m10=4.0, m20=-12.0, main_today_yi=-1.5,
            ),
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertNotEqual(decision["action"], "buy",
                            f"reversal_confirmed=False 时不应升档 buy，实际 action={decision['action']}")
        # sources 必须留下交叉验证审计痕迹（spc explain 反查可用）
        self.assertTrue(
            any("cross_validation" in s for s in decision["sources"]),
            f"sources 缺少 cross_validation 审计；sources={decision['sources']}",
        )

    def test_trend_buy_decelerating_inflow_softens_confidence(self):
        """趋势路径下 cross.acceleration=decelerating_inflow → confidence 0.72 → 0.67。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")

        # 300308 默认走 trend 路径（NEW_ALL_TIME_HIGH + 正向公告）
        # 让 1d/3d/5d 都 > 0（满足 trend 硬约束）但日均在变小：
        #   1d=0.5, 5d=4 (avg 0.8), 10d=10 (avg 1.0), 20d=20 (avg 1.0)
        #   → 1d 日均 (0.5) < 5d 日均 (0.8) → decelerating_inflow
        provider = FakeProvider(fund_flow_overrides={
            ("a", "300308"): _ff(
                regime="PERSISTENT_INFLOW",
                m3=2.0, m5=4.0, m10=10.0, m20=20.0, main_today_yi=0.5,
            ),
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        self.assertEqual(decision["confidence"], "0.67",
                         f"trend buy + decelerating_inflow 应 -0.05 → 0.67，实际 {decision['confidence']}")
        self.assertTrue(
            any("decelerating_inflow" in r for r in decision["reasoning"]),
            f"reasoning 应标注 decelerating_inflow；reasoning={decision['reasoning']}",
        )

    def test_reversal_buy_still_works_when_cross_confirms(self):
        """回归断言：cross.reversal_confirmed=True + short_long_conflict=False 时，
        反转买入路径仍应正常升档为 buy（确保下沉没削弱原能力）。"""
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

        # 四周期一致流入 + reversal=OUTFLOW_TO_INFLOW + 1d/5d 同向流入背书
        provider = ReversalProvider(fund_flow_overrides={
            ("hk", "00700"): _ff(
                regime="PERSISTENT_INFLOW", reversal="OUTFLOW_TO_INFLOW",
                m3=2.0, m5=4.0, m10=8.0, m20=15.0, main_today_yi=1.2,
            ),
        })

        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "buy")
        self.assertEqual(decision["confidence"], "0.68")
        self.assertTrue(
            any("reversal_confirmed=True" in r for r in decision["reasoning"]),
            f"reasoning 应标注 reversal_confirmed=True；reasoning={decision['reasoning']}",
        )

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

    def test_cli_execution_smoke(self):
        cmd = [sys.executable, str(SKILL_DIR / "scripts" / "main.py")]
        env = os.environ.copy()

        proc = subprocess.run(
            cmd + [
                "exec", "plan", "create",
                "--account", "default",
                "--market", "a",
                "--code", "300750",
                "--side", "buy",
                "--action-type", "open",
                "--thesis", "测试建仓计划",
                "--target-qty", "100",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("已创建执行计划", proc.stdout)

        proc = subprocess.run(
            cmd + ["exec", "plan", "list", "--account", "default"],
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


# ─────────────────────────────────────────────────────────────────
# P0 / P1 / P2 持仓侧风控测试
#
# 共用 SPCTestCase 的 setUp（账户 / 资金 / FakeProvider），各路径自带一个
# 专属 Provider 覆盖 quote / regime / fund_flow，让"被测特征"完全可控。
# ─────────────────────────────────────────────────────────────────


class _CustomQuoteProvider(FakeProvider):
    """允许测试动态注入 quote.current / regime / fund_flow 的便捷 Provider。"""

    def __init__(
        self,
        *,
        quote_overrides: dict | None = None,
        regime_overrides: dict | None = None,
        fund_flow_overrides: dict | None = None,
        market_regime_overrides: dict | None = None,
        market_regime: str | None = None,
        announcement_overrides: dict | None = None,
    ):
        # market_regime 是 shortcut：把同一个 regime 套到 a 和 hk 两个市场
        # （多数测试只关注单一标的，不想逐 market 写一遍）。
        if market_regime is not None:
            mr = dict(market_regime_overrides or {})
            mr.setdefault("a", market_regime)
            mr.setdefault("hk", market_regime)
            market_regime_overrides = mr
        super().__init__(
            fund_flow_overrides=fund_flow_overrides,
            market_regime_overrides=market_regime_overrides,
        )
        self.quote_overrides = quote_overrides or {}
        self.regime_overrides = regime_overrides or {}
        self.announcement_overrides = announcement_overrides or {}

    def fetch_quote(self, market, code):
        key = (market, code)
        if key in self.quote_overrides:
            return self.quote_overrides[key]
        return super().fetch_quote(market, code)

    def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
        key = (market, code)
        # 拿一份 baseline 分析（来自父类 FakeProvider）
        base = super().analyze(market, code, ann_days, with_peers, skip)
        # 让 quote.current 与 fetch_quote 保持一致
        cur_quote = self.fetch_quote(market, code)
        base.setdefault("quote", {})
        base["quote"]["current"] = cur_quote.get("current", base["quote"].get("current"))
        if "percent" in cur_quote:
            base["quote"]["percent"] = cur_quote["percent"]
        if key in self.regime_overrides:
            base.setdefault("price_history", {})["regime"] = self.regime_overrides[key]
        if key in self.announcement_overrides:
            base["announcements"] = self.announcement_overrides[key]
        return base


class SPCHoldingRiskControlTestCase(SPCTestCase):
    """P0a / P0b / P1a / P1b / P2a / P2b 持仓侧风控的端到端测试。"""

    # ── P0a: 分级硬止损（T1 trim / T2 trim / T3 sell） ──────────
    #
    # 新策略关键点：
    #   - 默认阈值（A 股）：T1=8%、T2=12%、T3=18%
    #   - 默认阈值（港股）：T1=12%、T2=18%、T3=25%
    #   - 默认阈值（ETF）：T1=10%、T2=15%、T3=22%
    #   - confidence 按大盘 regime 联动：RISK_OFF 时下调，RISK_ON 时上调
    #   - L4 旧规则（跌 8% + 风险公告）升档为 sell @ 0.80，跨过 T1/T2 直接 sell

    def _seed_a_stock_300750(self, qty="1000", cost="300.00"):
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", qty, cost,
            None, "2026-05-01 09:30:00", "",
        )
        set_capital(self.conn, self.acct_id, "5000000", "100")

    def test_p0a_t1_triggers_trim_for_a_stock_8_to_12_pct_loss(self):
        """A 股浮亏在 T1(8%)~T2(12%) 区间 → 触发首道防线 trim，而非 sell。"""
        self._seed_a_stock_300750()
        # 现价 270 → 浮亏 10%（在 T1=8% 和 T2=12% 之间）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim",
                         f"A 股 -10% 应在 T1~T2 区间触发 trim；reasoning={decision['reasoning']}")
        # trace 应记录 hard_stop_t1
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("hard_stop_t1", steps, f"trace={decision['confidence_trace']}")
        # reasoning 应包含"T1 首道防线"字样
        self.assertTrue(
            any("T1" in r and "首道防线" in r for r in decision["reasoning"]),
            f"reasoning 缺少 T1 首道防线说明；reasoning={decision['reasoning']}",
        )

    def test_p0a_t2_triggers_deeper_trim_for_a_stock_12_to_18_pct_loss(self):
        """A 股浮亏在 T2(12%)~T3(18%) 区间 → trim @ 0.78（深防线，比 T1 高一档）。"""
        self._seed_a_stock_300750()
        # 现价 255 → 浮亏 15%（在 T2=12% 和 T3=18% 之间）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "255.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("hard_stop_t2", steps, f"trace={decision['confidence_trace']}")
        self.assertNotIn("hard_stop_t1", steps,
                         "T2 与 T1 互斥，不应同时记录")
        # confidence ≥ 0.75（T2 NEUTRAL = 0.78）
        self.assertGreaterEqual(float(decision["confidence"]), 0.75,
                                f"T2 trim NEUTRAL 应 ≥ 0.78；actual={decision['confidence']}")

    def test_p0a_t3_triggers_sell_for_a_stock_loss_over_18pct(self):
        """A 股浮亏 ≥ T3(18%) → sell @ 0.85（硬底线，强制全退）。"""
        self._seed_a_stock_300750()
        # 现价 240 → 浮亏 20%（> T3=18%）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "240.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "sell",
                         f"A 股 -20% 应触发 T3 sell；reasoning={decision['reasoning']}")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("hard_stop_t3", steps, f"trace={decision['confidence_trace']}")
        # confidence ≥ 0.85（T3 NEUTRAL/RISK_ON 默认）
        self.assertGreaterEqual(float(decision["confidence"]), 0.84)

    def test_p0a_hk_thresholds_t1_t2_t3(self):
        """港股阈值：T1=12% / T2=18% / T3=25%，跌 11%/13%/19%/26% 分别对应 hold/T1/T2/T3。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "hk", "00700", "100", "500.00",
            None, "2026-05-01 09:30:00", "",
        )

        # 注意：避免端到端测试因前一次 analyze_now 更新 peak_price，
        # 导致下一次 trailing stop 误触发。这里每次都用独立的成本 / 现价。
        def _run(current_price: str):
            provider = _CustomQuoteProvider(
                quote_overrides={("hk", "00700"): {"current": current_price, "percent": -1.5,
                                                    "fetched_at": "2026-05-08T14:31:00+08:00"}},
                regime_overrides={("hk", "00700"): "IN_RANGE"},
                announcement_overrides={("hk", "00700"): []},
            )
            payload = analyze_now(
                self.conn, self.acct_id, self.acct_slug, self.acct_name,
                "holdings", market="hk", code="00700", analysis_provider=provider,
            )
            decision = payload["results"][0]["decision"]
            return decision["action"], [s["step"] for s in decision["confidence_trace"]]

        # 跌 11%（445）：不触发任何硬止损档（T1=12%）
        action1, steps1 = _run("445.00")
        self.assertNotIn("hard_stop_t1", steps1)
        self.assertNotIn("hard_stop_t2", steps1)
        self.assertNotIn("hard_stop_t3", steps1)
        self.assertNotEqual(action1, "sell")

        # 跌 13%（435）：T1 trim
        action2, steps2 = _run("435.00")
        self.assertEqual(action2, "trim")
        self.assertIn("hard_stop_t1", steps2)

        # 跌 19%（405）：T2 trim
        action3, steps3 = _run("405.00")
        self.assertEqual(action3, "trim")
        self.assertIn("hard_stop_t2", steps3)

        # 跌 26%（370）：T3 sell
        action4, steps4 = _run("370.00")
        self.assertEqual(action4, "sell")
        self.assertIn("hard_stop_t3", steps4)

    def test_p0a_l4_upgrades_to_sell_when_risk_announcement_present(self):
        """跌 8%（A 股 T1 边界）+ 风险公告 → L4 升档为 sell，而非 trim。

        L4 旧规则保留触发条件（跌 8% + 风险公告），但 confidence 从 0.78 升到 0.80。
        有公告佐证时应"跨过"P0a 的 T1 trim 防线直接到位 → 等价于"信号 + 公告"全退。
        """
        self._seed_a_stock_300750()
        # 现价 270 → 浮亏 10%（同 T1 区间，但有风险公告 → L4 升档 sell）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): [
                {"title": "公司涉嫌信息披露违规被立案调查",
                 "publish_date": "2026-05-08",
                 "category": "重大风险"},
            ]},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "sell",
                         f"跌 8% + 风险公告应升档 sell；reasoning={decision['reasoning']}")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("price_loss+risk_announce", steps,
                      f"应记录 L4 step；trace={decision['confidence_trace']}")

    def test_p0a_regime_risk_off_lowers_confidence(self):
        """大盘 RISK_OFF 时 T1 confidence 由 0.70 → 0.65（留更多人工判断空间）。"""
        self._seed_a_stock_300750()
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
            market_regime="RISK_OFF",
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("hard_stop_t1", steps)
        # 找到 hard_stop_t1 那条 step 的 confidence（trace 字段名是 value）
        t1_step = next(s for s in decision["confidence_trace"] if s["step"] == "hard_stop_t1")
        self.assertAlmostEqual(float(t1_step["value"]), 0.65, places=2,
                               msg=f"RISK_OFF 时 T1 confidence 应 ≈ 0.65；trace={decision['confidence_trace']}")

    def test_p0a_account_settings_overrides_per_tier(self):
        """新版 settings key 按档配置：.a_stock.t1 / .t2 / .t3 都可单独覆盖。"""
        from spc_core.settings import set_account_setting
        self._seed_a_stock_300750()
        # 把 T1 调到 5%（默认 8%）
        set_account_setting(self.conn, self.acct_id, "decision.hard_stop_pct.a_stock.t1", "0.05")
        # 跌 6%（282）：默认 T1=8% 不触发，但覆盖后 5% 触发 T1 trim
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "282.00", "percent": -1.0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("hard_stop_t1", steps)

    # ── P0a 分档幂等性（schema v5）：避免重复建议同档减仓 ────────
    #
    # 设计意图：
    #   - 首次跌过 T1 → trim（建议减半）+ DB 写入 last_trim_tier="T1"
    #   - 价格停在 T1 区间不动 + last_trim_tier="T1" → 不重发 trim，只软提示
    #   - 跌到 T2 → trim 升档，last_trim_tier 更新为 "T2"
    #   - 跌到 T3 → sell（不依赖 tier 标记，硬底线永不静默）
    #   - 浮亏回升到 T1 以下 → 自动清空 tier，下次再跌过 T1 可重新触发

    def _seed_a_stock_with_peak(self, qty="1000", cost="300.00"):
        """种持仓 + 通过一次 sync 让 position_peak 行被初始化。"""
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", qty, cost,
            None, "2026-05-01 09:30:00", "",
        )
        set_capital(self.conn, self.acct_id, "5000000", "100")
        # 注意：position_peak 的初始化由 portfolio.sync 触发；
        # 但端到端测试 analyze_now 内部会路过 sync 流程么？测试里通常用 add_position_seed
        # 直接种数据后跑 analyze_now，sync 由 analyze_now 触发；初始 peak 会被建立
        # （后续幂等读写都依赖这条 peak 记录）

    def _query_trim_tier(self):
        row = self.conn.execute(
            "SELECT last_trim_tier, last_trim_price FROM position_peak "
            "WHERE account_id=? AND market='a' AND code='300750'",
            (self.acct_id,),
        ).fetchone()
        return (row["last_trim_tier"], row["last_trim_price"]) if row else (None, None)

    def test_p0a_idempotency_first_t1_writes_tier(self):
        """首次跌入 T1 区间：触发 trim + DB 写入 last_trim_tier='T1'。"""
        self._seed_a_stock_with_peak()
        # 现价 270 → 浮亏 10%（在 T1=8% 和 T2=12% 之间）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        tier, price = self._query_trim_tier()
        self.assertEqual(tier, "T1", "首次 T1 触发后 DB 应记录 last_trim_tier='T1'")
        # last_trim_price 应该 ≈ 270
        self.assertAlmostEqual(float(price), 270.00, places=2)

    def test_p0a_idempotency_t1_repeat_silences_to_hint(self):
        """已有 last_trim_tier='T1' + 价格仍在 T1 区间 → 不重发 trim，只软提示。"""
        self._seed_a_stock_with_peak()
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        # 第 1 次 analyze：写入 last_trim_tier='T1'
        analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                    "holdings", analysis_provider=provider)
        self.assertEqual(self._query_trim_tier()[0], "T1")

        # 第 2 次 analyze：价格不变，应静默（不重发 trim）
        payload2 = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                               "holdings", analysis_provider=provider)
        decision2 = payload2["results"][0]["decision"]
        self.assertNotEqual(decision2["action"], "trim",
                            f"重复 T1 不应再发 trim；action={decision2['action']}")
        steps2 = [s["step"] for s in decision2["confidence_trace"]]
        self.assertNotIn("hard_stop_t1", steps2,
                         f"重复 T1 不应出现 hard_stop_t1 step；trace={decision2['confidence_trace']}")
        # reasoning 应包含"幂等保护已生效"
        self.assertTrue(
            any("幂等保护" in r for r in decision2["reasoning"]),
            f"应有幂等保护说明；reasoning={decision2['reasoning']}",
        )

    def test_p0a_idempotency_t1_to_t2_upgrades(self):
        """已有 last_trim_tier='T1' + 价格跌到 T2 → trim 触发并升档为 'T2'。"""
        self._seed_a_stock_with_peak()
        # 先跌到 T1
        p_t1 = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                    "holdings", analysis_provider=p_t1)
        self.assertEqual(self._query_trim_tier()[0], "T1")

        # 再跌到 T2：现价 255 → 浮亏 15%（在 T2=12% 和 T3=18% 之间）
        p_t2 = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "255.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload2 = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                               "holdings", analysis_provider=p_t2)
        decision2 = payload2["results"][0]["decision"]
        self.assertEqual(decision2["action"], "trim",
                         "T1→T2 升档应触发 trim")
        steps2 = [s["step"] for s in decision2["confidence_trace"]]
        self.assertIn("hard_stop_t2", steps2)
        # tier 升档为 T2
        self.assertEqual(self._query_trim_tier()[0], "T2")

    def test_p0a_idempotency_t3_never_silenced(self):
        """已有 last_trim_tier='T2' + 跌到 T3 → 必须 sell（硬底线不静默）。"""
        self._seed_a_stock_with_peak()
        # 模拟之前已经触发过 T2：直接 set
        from spc_core.ledger import set_position_trim_tier
        from decimal import Decimal as D
        # 先确保 peak 行存在
        provider_setup = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "300.00", "percent": 0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                    "holdings", analysis_provider=provider_setup)
        # 手动写入 last_trim_tier='T2'
        set_position_trim_tier(self.conn, self.acct_id, "a", "300750",
                               tier="T2", price=D("265.00"))

        # 跌到 T3：现价 240 → 浮亏 20%
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "240.00", "percent": -2.0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                              "holdings", analysis_provider=provider)
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "sell",
                         f"T3 硬底线应永不静默；reasoning={decision['reasoning']}")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("hard_stop_t3", steps)

    def test_p0a_idempotency_recovery_clears_tier(self):
        """已有 last_trim_tier='T1' + 价格回升到 T1 以下 → DB 自动清空 tier。"""
        self._seed_a_stock_with_peak()
        # 先触发 T1
        p_down = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                    "holdings", analysis_provider=p_down)
        self.assertEqual(self._query_trim_tier()[0], "T1")

        # 价格反弹：现价 285 → 浮亏 5%（< T1=8%）
        p_up = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "285.00", "percent": 5.5,
                                                "fetched_at": "2026-05-09T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                    "holdings", analysis_provider=p_up)
        # tier 应被清空
        self.assertIsNone(
            self._query_trim_tier()[0],
            "浮亏回升至 T1 阈值以下时，last_trim_tier 应被自动清空",
        )

    def test_p0a_idempotency_after_clear_t1_can_trigger_again(self):
        """T1 触发 → 反弹清空 → 再跌过 T1 → 可重新触发（完整周期）。"""
        self._seed_a_stock_with_peak()
        # Cycle 1: 跌过 T1
        p_down1 = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        p1 = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                         "holdings", analysis_provider=p_down1)
        self.assertEqual(p1["results"][0]["decision"]["action"], "trim")

        # Cycle 2: 反弹回升 → 清空
        p_up = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "290.00", "percent": 7.4,
                                                "fetched_at": "2026-05-09T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                    "holdings", analysis_provider=p_up)
        self.assertIsNone(self._query_trim_tier()[0])

        # Cycle 3: 再次跌过 T1 → 重新触发 trim
        p_down2 = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "268.00", "percent": -7.6,
                                                "fetched_at": "2026-05-10T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        p3 = analyze_now(self.conn, self.acct_id, self.acct_slug, self.acct_name,
                         "holdings", analysis_provider=p_down2)
        decision3 = p3["results"][0]["decision"]
        self.assertEqual(decision3["action"], "trim",
                         "回升清空后再跌过 T1 应能重新触发 trim")
        steps3 = [s["step"] for s in decision3["confidence_trace"]]
        self.assertIn("hard_stop_t1", steps3)
        self.assertEqual(self._query_trim_tier()[0], "T1")

    # ── P0b: 分级止盈 ────────────────────────────────────────

    def test_p0b_take_profit_t1_at_20pct_gain(self):
        """A 股持仓浮盈 ≥ 20%（默认 t1）→ trim @ 0.65（温和提示）。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")  # 不触发仓位超限
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "200.00",
            None, "2026-05-01 09:30:00", "",
        )
        # 现价 245 → 浮盈 22.5%（> t1=20%）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "245.00", "percent": 1.2,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("take_profit_t1", steps)
        self.assertTrue(any("浮盈" in r for r in decision["reasoning"]))

    def test_p0b_take_profit_t2_upgrades_with_top_signal(self):
        """浮盈 ≥ 50% + 创新高 + 主力 PERSISTENT_OUTFLOW → 升级为 sell @ 0.80。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "100.00",
            None, "2026-05-01 09:30:00", "",
        )
        # 现价 160 → 浮盈 60%
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "160.00", "percent": 1.0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "NEW_ALL_TIME_HIGH"},
            announcement_overrides={("a", "300750"): []},
            fund_flow_overrides={
                ("a", "300750"): _ff(
                    regime="PERSISTENT_OUTFLOW",
                    m3=-2.0, m5=-4.0, m20=-10.0, main_today_yi=-1.5,
                ),
            },
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "sell",
                         f"浮盈 60% + 顶部信号应升 sell；实际 {decision['action']}")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertTrue(
            any("take_profit_t2" in step for step in steps),
            f"trace 未记录 t2 升级；steps={steps}",
        )

    def test_p0b_take_profit_t3_triggers_aggressive_trim(self):
        """浮盈 ≥ 100% → trim @ 0.78（建议大幅止盈）。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "100.00",
            None, "2026-05-01 09:30:00", "",
        )
        # 现价 210 → 浮盈 110%
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "210.00", "percent": 1.0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        self.assertEqual(decision["confidence"], "0.78")

    def test_p0b_no_take_profit_when_gain_below_threshold(self):
        """浮盈 < 20% 不应触发任何止盈规则。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "240.00",
            None, "2026-05-01 09:30:00", "",
        )
        # 现价 260 → 浮盈 8.3%
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "260.00", "percent": 1.2,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "hold")

    # ── P1a: 加仓 add ────────────────────────────────────────

    def test_p1a_add_action_label_exists(self):
        """ACTION_LABELS 必须包含 add → '加仓'。"""
        from spc_core.decision import ACTION_LABELS, ACTION_DESCRIPTIONS
        self.assertIn("add", ACTION_LABELS)
        self.assertEqual(ACTION_LABELS["add"], "加仓")
        self.assertIn("add", ACTION_DESCRIPTIONS)

    def test_p1a_trend_add_when_holding_and_buy_conditions_hold(self):
        """已持仓 + 浮盈 + 仓位还有空间 + trend buy 条件成立 → action=add @ 0.68。"""
        set_capital(self.conn, self.acct_id, "5000000", "20")  # 单票上限 100 万
        # 持仓 100 股 × 1000 元 = 10 万，远低于上限 100 万 × 0.85 = 85 万
        add_position_seed(
            self.conn, self.acct_id, "a", "300308", "100", "900.00",
            None, "2026-05-01 09:30:00", "",
        )
        # FakeProvider 默认 300308: regime=NEW_ALL_TIME_HIGH, 1 条正向公告
        # 加资金面 trend 条件
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300308"): {"current": "935.00", "percent": 1.2,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            fund_flow_overrides={
                ("a", "300308"): _ff(
                    regime="PERSISTENT_INFLOW",
                    m3=2.0, m5=5.0, m20=15.0, main_today_yi=1.0,
                ),
            },
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "add",
                         f"应该触发持仓加仓，实际 action={decision['action']}")
        self.assertEqual(decision["action_label"], "加仓")
        self.assertTrue(
            any("持仓加仓" in r for r in decision["reasoning"]),
            f"reasoning 缺少加仓说明；reasoning={decision['reasoning']}",
        )

    def test_p1a_add_blocked_when_weight_near_cap(self):
        """权重接近上限（≥ 85% × cap）时不应建议加仓，保留 hold。"""
        # 单票上限 20% × 100 万 = 20 万；持仓 220 股 × 935 = 20.57 万 = 20.57%，
        # 20.57% > 20% × 0.85 = 17%，应被阻挡
        set_capital(self.conn, self.acct_id, "1000000", "20")
        add_position_seed(
            self.conn, self.acct_id, "a", "300308", "220", "900.00",
            None, "2026-05-01 09:30:00", "",
        )
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300308"): {"current": "935.00", "percent": 1.2,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            fund_flow_overrides={
                ("a", "300308"): _ff(
                    regime="PERSISTENT_INFLOW",
                    m3=2.0, m5=5.0, m20=15.0, main_today_yi=1.0,
                ),
            },
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        action = payload["results"][0]["decision"]["action"]
        # 既不是 add（被 headroom 阻挡）也不是 trim（仓位超限要求 > 20%，这里 20.57% > 20% 会触发）
        # 注意：20.57% > 20%，所以触发 weight_over_cap trim @ 0.70
        self.assertEqual(action, "trim")

    def test_p1a_add_blocked_under_market_risk_off(self):
        """大盘 RISK_OFF 时即便 buy 条件成立也不应主动加仓（持仓侧弱市保守）。"""
        set_capital(self.conn, self.acct_id, "5000000", "20")
        add_position_seed(
            self.conn, self.acct_id, "a", "300308", "100", "900.00",
            None, "2026-05-01 09:30:00", "",
        )
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300308"): {"current": "935.00", "percent": 1.2,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            fund_flow_overrides={
                ("a", "300308"): _ff(
                    regime="PERSISTENT_INFLOW",
                    m3=2.0, m5=5.0, m20=15.0, main_today_yi=1.0,
                ),
            },
            market_regime_overrides={"a": "RISK_OFF"},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        action = payload["results"][0]["decision"]["action"]
        self.assertNotEqual(action, "add",
                            f"RISK_OFF 不应触发加仓；action={action}")

    def test_p1a_add_blocked_when_at_loss(self):
        """成本之下不加仓（避免补仓陷阱），即便 buy 条件成立。"""
        set_capital(self.conn, self.acct_id, "5000000", "20")
        add_position_seed(
            self.conn, self.acct_id, "a", "300308", "100", "1000.00",
            None, "2026-05-01 09:30:00", "",
        )
        # 现价 935 < 成本 1000 → 浮亏 6.5%（不到 10% 硬止损线）
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300308"): {"current": "935.00", "percent": -0.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            fund_flow_overrides={
                ("a", "300308"): _ff(
                    regime="PERSISTENT_INFLOW",
                    m3=2.0, m5=5.0, m20=15.0, main_today_yi=1.0,
                ),
            },
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        action = payload["results"][0]["decision"]["action"]
        self.assertNotEqual(action, "add",
                            f"浮亏中不应加仓；action={action}")

    # ── P1b: cross_validation 软提示 ────────────────────────

    def test_p1b_decelerating_inflow_adds_hint_in_hold_state(self):
        """持仓 hold 状态下 + acceleration=decelerating_inflow → reasons 加风险提示。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "240.00",
            None, "2026-05-01 09:30:00", "",
        )
        # 现价 260 → 浮盈 8.3%（不触发任何止盈/止损/加仓）
        # m1=0.5 < 5d 日均 0.8 < 10d 日均 1.0 → decelerating_inflow
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "260.00", "percent": 1.0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
            fund_flow_overrides={
                ("a", "300750"): _ff(
                    regime="PERSISTENT_INFLOW",
                    m3=2.0, m5=4.0, m10=10.0, m20=20.0, main_today_yi=0.5,
                ),
            },
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "hold")
        self.assertTrue(
            any("decelerating_inflow" in r for r in decision["reasoning"]),
            f"hold 状态下应加 decelerating_inflow 提示；reasoning={decision['reasoning']}",
        )

    # ── P2a: execution_plan 预案价位 ─────────────────────────

    def test_p2a_plan_stop_loss_triggers_sell(self):
        """有 active plan + 现价 ≤ 预案止损价 → record sell @ 0.82。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        # 持仓 1000 股 @ 245.30；现价 250；预案止损价 252（高于现价 → 触发）
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        # 建一个非终态 plan，stop_loss_price=252
        create_execution_plan(
            self.conn, self.acct_id, "a", "300750", "buy", "open",
            "测试预案",
            target_qty="100",
            stop_loss_price="252.00",
            take_profit_price=None,
        )
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "250.00", "percent": -0.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "sell")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("plan_stop_loss", steps)
        self.assertTrue(
            any("预案止损价" in r for r in decision["reasoning"]),
            f"reasoning={decision['reasoning']}",
        )

    def test_p2a_plan_take_profit_triggers_trim(self):
        """有 active plan + 现价 ≥ 预案止盈价 → trim @ 0.78。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        create_execution_plan(
            self.conn, self.acct_id, "a", "300750", "buy", "open",
            "测试止盈预案",
            target_qty="100",
            stop_loss_price=None,
            take_profit_price="258.00",  # 低于当前价 260 → 触发
        )
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "260.00", "percent": 1.0,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("plan_take_profit", steps)

    def test_p2a_terminal_plan_ignored(self):
        """cancelled / expired 状态的 plan 价位不应参与决策。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        plan_id = create_execution_plan(
            self.conn, self.acct_id, "a", "300750", "buy", "open",
            "已取消的预案",
            target_qty="100",
            stop_loss_price="252.00",
        )
        cancel_execution_plan(self.conn, self.acct_id, plan_id, reason="条件失效")

        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "250.00", "percent": -0.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]
        # 取消的 plan 不参与决策；现价 250 不触发 10% 硬止损（成本 245.30 × 0.9 = 220.77）
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertNotIn("plan_stop_loss", steps)

    # ── P2b: trailing stop ──────────────────────────────────

    def test_p2b_position_peak_initialized_on_first_sync(self):
        """首次 sync 时 position_peak 记录应被初始化为 max(avg_cost, last_price)。"""
        from spc_core.ledger import get_position_peak
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        sync_portfolio(
            self.conn, self.acct_id,
            analysis_provider=self.provider, fx_rate_provider=self.fx_provider,
        )
        peak = get_position_peak(self.conn, self.acct_id, "a", "300750")
        self.assertIsNotNone(peak)
        # FakeProvider 现价 260 > 成本 245.30 → peak = 260
        self.assertEqual(Decimal(peak["peak_price"]), Decimal("260.0000"))

    def test_p2b_position_peak_only_grows(self):
        """peak 只升不降：current 低于历史 peak 时 peak 不变。"""
        from spc_core.ledger import get_position_peak
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        # 第一次 sync：peak=260
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=self.provider, fx_rate_provider=self.fx_provider)

        # 第二次 sync 用更低的现价
        provider2 = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "250.00", "percent": -2.0,
                                                "fetched_at": "2026-05-08T15:01:00+08:00"}},
        )
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=provider2, fx_rate_provider=self.fx_provider)
        peak = get_position_peak(self.conn, self.acct_id, "a", "300750")
        self.assertEqual(Decimal(peak["peak_price"]), Decimal("260.0000"))

        # 第三次 sync 用更高现价
        provider3 = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "280.00", "percent": 3.0,
                                                "fetched_at": "2026-05-08T15:02:00+08:00"}},
        )
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=provider3, fx_rate_provider=self.fx_provider)
        peak = get_position_peak(self.conn, self.acct_id, "a", "300750")
        self.assertEqual(Decimal(peak["peak_price"]), Decimal("280.0000"))

    def test_p2b_trailing_stop_triggers_trim_on_15pct_drawdown(self):
        """peak=300 + 现价=255 → 回撤 15% → trim @ 0.72。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "200.00",
            None, "2026-05-01 09:30:00", "",
        )

        # 第一次 sync 用 quote=300 → peak=300（也触发 t2 止盈，但我们关注 peak）
        provider_peak = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "300.00", "percent": 5.0,
                                                "fetched_at": "2026-05-08T10:00:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=provider_peak, fx_rate_provider=self.fx_provider)

        # 第二次 analyze 用 quote=255 → 浮盈 27.5%，从 peak 回撤 15%
        provider_now = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "255.00", "percent": -3.0,
                                                "fetched_at": "2026-05-08T14:00:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider_now,
        )
        decision = payload["results"][0]["decision"]
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("trailing_stop", steps,
                      f"应触发 trailing_stop；trace steps={steps}")
        # 同时浮盈 27.5% 也会触发 take_profit_t1，最终 action 是较高 confidence 的那条
        self.assertIn(decision["action"], ("trim", "sell"))

    def test_p2b_trailing_stop_severe_triggers_sell_on_25pct_drawdown(self):
        """peak=300 + 现价=224 → 回撤 25.3% → severe trailing → sell @ 0.78。"""
        set_capital(self.conn, self.acct_id, "5000000", "100")
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "200.00",
            None, "2026-05-01 09:30:00", "",
        )
        provider_peak = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "300.00", "percent": 5.0,
                                                "fetched_at": "2026-05-08T10:00:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=provider_peak, fx_rate_provider=self.fx_provider)

        provider_now = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "224.00", "percent": -5.0,
                                                "fetched_at": "2026-05-08T14:00:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider_now,
        )
        decision = payload["results"][0]["decision"]
        steps = [s["step"] for s in decision["confidence_trace"]]
        self.assertIn("trailing_stop_severe", steps)
        self.assertEqual(decision["action"], "sell")

    def test_p2b_position_peak_deleted_on_clear_position(self):
        """卖出全部持仓 → position_peak 应被删除（清仓重置）。"""
        from spc_core.ledger import get_position_peak
        add_position_seed(
            self.conn, self.acct_id, "a", "300750", "1000", "245.30",
            None, "2026-05-01 09:30:00", "",
        )
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        self.assertIsNotNone(get_position_peak(self.conn, self.acct_id, "a", "300750"))

        # 全部卖出
        add_trade(
            self.conn, self.acct_id, "a", "300750", "sell",
            "1000", "260.00", "2026-05-09 14:15:00",
            None, None, "0", "0", "0", "0", "",
        )
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        self.assertIsNone(get_position_peak(self.conn, self.acct_id, "a", "300750"))


class SPCSelectTargetsTestCase(SPCTestCase):
    """analyze 标的选择：持仓侧应跳过 qty=0 清仓快照。"""

    def test_analyze_holdings_skips_cleared_positions(self):
        """qty=0 的清仓快照不应被 analyze（与 portfolio show 默认行为一致）。"""
        # 一个正常持仓 + 一个已清仓（先建仓再卖空）
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30",
                          None, "2026-05-01 09:30:00", "")
        # 模拟另一个标的：建仓后清仓
        add_position_seed(self.conn, self.acct_id, "a", "000568", "100", "100.00",
                          None, "2026-05-01 09:30:00", "")
        from spc_core.ledger import add_trade
        # add_trade(conn, account_id, market, code, side, qty, price, time_text, currency, fx_rate, fees..., note)
        add_trade(self.conn, self.acct_id, "a", "000568", "sell",
                  "100", "90.00", "2026-05-10 14:30:00", None, None,
                  "0", "0", "0", "0", "")
        from spc_core.portfolio import sync_portfolio
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=self.provider,
        )
        codes_analyzed = {r["code"] for r in payload["results"]}
        self.assertIn("300750", codes_analyzed,
                      "正常持仓应该被 analyze")
        self.assertNotIn("000568", codes_analyzed,
                         "qty=0 的清仓快照不应被 analyze")

    def test_analyze_all_scope_skips_cleared_positions(self):
        """scope=all 也应该跳过 qty=0 清仓快照（而不是把它们当 watchlist 复用）。"""
        add_position_seed(self.conn, self.acct_id, "a", "000568", "100", "100.00",
                          None, "2026-05-01 09:30:00", "")
        from spc_core.ledger import add_trade, add_watch
        add_trade(self.conn, self.acct_id, "a", "000568", "sell",
                  "100", "90.00", "2026-05-10 14:30:00", None, None,
                  "0", "0", "0", "0", "")
        from spc_core.portfolio import sync_portfolio
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        add_watch(self.conn, self.acct_id, "a", "300750", "")
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "all", analysis_provider=self.provider,
        )
        codes_analyzed = {r["code"] for r in payload["results"]}
        self.assertIn("300750", codes_analyzed)
        self.assertNotIn("000568", codes_analyzed,
                         "scope=all 也应跳过清仓快照")

    def test_analyze_single_target_allows_cleared_position(self):
        """单标的 spot-check 允许查询已清仓标的（用户可能想看跌没跌透）。"""
        add_position_seed(self.conn, self.acct_id, "a", "000568", "100", "100.00",
                          None, "2026-05-01 09:30:00", "")
        from spc_core.ledger import add_trade
        add_trade(self.conn, self.acct_id, "a", "000568", "sell",
                  "100", "90.00", "2026-05-10 14:30:00", None, None,
                  "0", "0", "0", "0", "")
        from spc_core.portfolio import sync_portfolio
        sync_portfolio(self.conn, self.acct_id,
                       analysis_provider=self.provider, fx_rate_provider=self.fx_provider)
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", market="a", code="000568",
            analysis_provider=self.provider,
        )
        codes_analyzed = {r["code"] for r in payload["results"]}
        self.assertIn("000568", codes_analyzed,
                      "单标的 spot-check 应允许查询已清仓标的")

    def test_watchlist_non_holding_skips_fund_flow_on_first_pass(self):
        """普通自选先不拉资金流，若初判只是 watch，则不做二次复评。"""
        add_watch(self.conn, self.acct_id, "a", "301391", "")

        class DeferredFFProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.calls = []

            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                self.calls.append({"market": market, "code": code, "skip": skip})
                quote = self.fetch_quote(market, code)
                return {
                    "fetched_at": quote["fetched_at"],
                    "info": {"name": "卡莱特"},
                    "quote": {"current": quote["current"], "percent": 0.5},
                    "price_history": {"regime": "IN_RANGE"},
                    "announcements": [],
                    "peers": [],
                    "concepts": [],
                }

        provider = DeferredFFProvider()
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "watch")
        self.assertEqual(len(provider.calls), 1, provider.calls)
        self.assertEqual(provider.calls[0]["skip"], "fund_flow")

    def test_watchlist_non_holding_second_pass_fetches_fund_flow_for_candidate(self):
        """自选初判若进入 buy/focus/probe 候选，应补资金流做二次复评。"""
        add_watch(self.conn, self.acct_id, "a", "300308", "")

        class DeferredFFProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.calls = []

            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                self.calls.append({"market": market, "code": code, "skip": skip})
                quote = self.fetch_quote(market, code)
                payload = {
                    "fetched_at": quote["fetched_at"],
                    "info": {"name": "中际旭创"},
                    "quote": {"current": quote["current"], "percent": 3.2},
                    "price_history": {"regime": "NEW_ALL_TIME_HIGH"},
                    "announcements": [
                        {"date": "2026-05-03", "title": "新品合作公告", "pdf_url": "https://example.com/b1.pdf"},
                    ],
                    "peers": [],
                    "concepts": ["通信", "光模块"],
                }
                if skip != "fund_flow":
                    payload["fund_flow"] = _ff(
                        regime="PERSISTENT_OUTFLOW",
                        m3=-4.0, m5=-8.0, m20=-22.0, main_today_yi=-2.5,
                        super_big_yi=-1.8, big_yi=-0.7,
                    )
                return payload

        provider = DeferredFFProvider()
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "watchlist", analysis_provider=provider,
        )
        decision = payload["results"][0]["decision"]

        self.assertEqual(decision["action"], "avoid")
        self.assertEqual(len(provider.calls), 2, provider.calls)
        self.assertEqual(provider.calls[0]["skip"], "fund_flow")
        self.assertEqual(provider.calls[1]["skip"], "")

    def test_holdings_still_fetch_fund_flow_directly(self):
        """持仓默认直接带资金流，不走无资金流初筛。"""
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30", None, "2026-05-01 09:30:00", "")

        class DeferredFFProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.calls = []

            def analyze(self, market, code, ann_days=30, with_peers=False, skip=""):
                self.calls.append({"market": market, "code": code, "skip": skip})
                return super().analyze(market, code, ann_days=ann_days, with_peers=with_peers, skip=skip)

        provider = DeferredFFProvider()
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
        )

        self.assertEqual(payload["results"][0]["code"], "300750")
        self.assertTrue(provider.calls, "应至少分析一次持仓")
        self.assertTrue(all(call["skip"] == "" for call in provider.calls), provider.calls)


class SPCLLMReviewTestCase(SPCTestCase):
    """LLM 复核的单元测试（mock backend，不真实调用 codex/claude）。"""

    def setUp(self):
        super().setUp()
        # 这一组测试需要按场景调整 backend；先确保 baseline 是 unavailable
        os.environ["SPC_LLM_BACKEND"] = "none"

    def test_llm_review_unavailable_when_backend_none(self):
        """SPC_LLM_BACKEND=none + 显式 --llm-review → 敏感 action 标记 unavailable，主决策不变。"""
        # 用 trim 触发场景：A 股浮亏 -10% → T1 trim
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "300.00",
                          None, "2026-05-01 09:30:00", "")
        set_capital(self.conn, self.acct_id, "5000000", "100")
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
            llm_review_enabled=True,
        )
        # 主决策仍是 trim
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        # llm_review 字段应为 unavailable
        review = decision.get("llm_review") or {}
        self.assertEqual(review.get("status"), "unavailable",
                         f"LLM 后端 none 时应标记 unavailable；got={review}")
        # payload meta
        meta = payload.get("llm_review_meta") or {}
        self.assertTrue(meta.get("unavailable"))
        self.assertTrue(meta.get("enabled"))

    def test_llm_review_skipped_for_hold_action(self):
        """hold action 不应触发 LLM 复核（节省成本）。"""
        # 不种持仓也不种自选，scope=all 会无结果；改成种一个 hold 标的
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "245.30",
                          None, "2026-05-01 09:30:00", "")
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=self.provider,
        )
        decision = payload["results"][0]["decision"]
        # 默认 FakeProvider 给的 300750 现价 ≈ 245.30 → 浮亏接近 0 → hold
        if decision["action"] == "hold":
            # hold 不该有 llm_review 字段（被 should_review 过滤）
            self.assertNotIn("llm_review", decision,
                             "hold 不应触发 LLM 复核")

    def test_llm_review_default_off_renders_review_candidates_block(self):
        """analyze_now 不传 llm_review_enabled → 默认关闭；
        渲染文本末尾应列出"建议复核清单"和可直接复制的命令。
        """
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "300.00",
                          None, "2026-05-01 09:30:00", "")
        set_capital(self.conn, self.acct_id, "5000000", "100")
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
            # 不传 llm_review_enabled，验证默认关闭
        )
        decision = payload["results"][0]["decision"]
        # 主决策仍是 trim
        self.assertEqual(decision["action"], "trim")
        # results 内不应该挂 llm_review 字段
        self.assertNotIn("llm_review", decision)
        # meta 标 enabled=False
        meta = payload.get("llm_review_meta") or {}
        self.assertFalse(meta.get("enabled"))
        # 渲染文本里应该有"建议复核清单"
        text = render_spc_analysis_text(payload)
        self.assertIn("LLM 复核建议（默认未开启）", text)
        self.assertIn("300750", text)
        self.assertIn("人工复核", text,
                      "渲染应包含让 agent 复核的提示")

    def test_llm_review_default_off_hides_block_when_no_candidates(self):
        """全 hold 时不应渲染"建议复核清单"（避免噪声）。"""
        # 不种持仓，scope=holdings 无 results → 不应有清单
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=self.provider,
        )
        text = render_spc_analysis_text(payload)
        self.assertNotIn("LLM 复核建议", text,
                         "没有 trim/sell/add/buy/probe 标的时不应渲染该区块")

    def test_llm_review_disabled_no_field(self):
        """显式 disable LLM 复核 → results 完全没有 llm_review 字段。"""
        add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "300.00",
                          None, "2026-05-01 09:30:00", "")
        set_capital(self.conn, self.acct_id, "5000000", "100")
        provider = _CustomQuoteProvider(
            quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                "fetched_at": "2026-05-08T14:31:00+08:00"}},
            regime_overrides={("a", "300750"): "IN_RANGE"},
            announcement_overrides={("a", "300750"): []},
        )
        payload = analyze_now(
            self.conn, self.acct_id, self.acct_slug, self.acct_name,
            "holdings", analysis_provider=provider,
            llm_review_enabled=False,
        )
        decision = payload["results"][0]["decision"]
        self.assertEqual(decision["action"], "trim")
        # disabled 时 result 内不挂 llm_review 字段
        self.assertNotIn("llm_review", decision)
        meta = payload.get("llm_review_meta") or {}
        self.assertFalse(meta.get("enabled"))

    def test_llm_review_with_mock_backend_success(self):
        """mock review_decision 模拟成功复核 → 字段正确填充。"""
        from spc_core import llm_review as llm_review_mod
        # 替换 review_decision 为 mock，模拟一个 verdict=question 的复核
        original_review = llm_review_mod.review_decision

        def mock_review(*, result, market_regime_payload=None, analysis=None,
                        backend=None, timeout=180):
            action = (result.get("decision") or {}).get("action")
            if action not in llm_review_mod.REVIEW_ACTIONS:
                return {"status": "skipped"}
            return {
                "status": "ok",
                "backend": "mock",
                "verdict": "question",
                "confidence": 0.65,
                "concerns": ["mock 测试 concern"],
                "missing_context": [],
                "execution_hint": "mock 测试 hint",
                "elapsed_ms": 100,
            }

        # 同时让 detect_llm_backend 返回非空
        original_detect = llm_review_mod.detect_llm_backend
        llm_review_mod.detect_llm_backend = lambda: "mock"
        llm_review_mod.review_decision = mock_review
        try:
            add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "300.00",
                              None, "2026-05-01 09:30:00", "")
            set_capital(self.conn, self.acct_id, "5000000", "100")
            provider = _CustomQuoteProvider(
                quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                    "fetched_at": "2026-05-08T14:31:00+08:00"}},
                regime_overrides={("a", "300750"): "IN_RANGE"},
                announcement_overrides={("a", "300750"): []},
            )
            payload = analyze_now(
                self.conn, self.acct_id, self.acct_slug, self.acct_name,
                "holdings", analysis_provider=provider,
                llm_review_enabled=True,
                llm_review_backend="mock",
            )
            decision = payload["results"][0]["decision"]
            self.assertEqual(decision["action"], "trim")
            review = decision.get("llm_review") or {}
            self.assertEqual(review.get("status"), "ok")
            self.assertEqual(review.get("verdict"), "question")
            self.assertEqual(review.get("backend"), "mock")
            self.assertIn("mock 测试 concern", review.get("concerns") or [])
            meta = payload.get("llm_review_meta") or {}
            self.assertEqual(meta.get("reviewed"), 1)
        finally:
            llm_review_mod.review_decision = original_review
            llm_review_mod.detect_llm_backend = original_detect

    def test_llm_review_fail_open_on_exception(self):
        """mock backend 抛异常 → fail-open，主决策不变，标记 failed。"""
        from spc_core import llm_review as llm_review_mod

        def mock_review_raises(*, result, market_regime_payload=None, analysis=None,
                                backend=None, timeout=180):
            action = (result.get("decision") or {}).get("action")
            if action not in llm_review_mod.REVIEW_ACTIONS:
                return {"status": "skipped"}
            # 模拟 codex/claude 在内部 catch 后返回 failed（保持 fail-open 契约）
            return {
                "status": "failed",
                "backend": "mock",
                "error": "mock connection refused",
                "elapsed_ms": 50,
            }

        original_review = llm_review_mod.review_decision
        original_detect = llm_review_mod.detect_llm_backend
        llm_review_mod.detect_llm_backend = lambda: "mock"
        llm_review_mod.review_decision = mock_review_raises
        try:
            add_position_seed(self.conn, self.acct_id, "a", "300750", "1000", "300.00",
                              None, "2026-05-01 09:30:00", "")
            set_capital(self.conn, self.acct_id, "5000000", "100")
            provider = _CustomQuoteProvider(
                quote_overrides={("a", "300750"): {"current": "270.00", "percent": -1.5,
                                                    "fetched_at": "2026-05-08T14:31:00+08:00"}},
                regime_overrides={("a", "300750"): "IN_RANGE"},
                announcement_overrides={("a", "300750"): []},
            )
            payload = analyze_now(
                self.conn, self.acct_id, self.acct_slug, self.acct_name,
                "holdings", analysis_provider=provider,
                llm_review_enabled=True,
                llm_review_backend="mock",
            )
            decision = payload["results"][0]["decision"]
            # 主决策不变
            self.assertEqual(decision["action"], "trim",
                             "fail-open：LLM 失败不应影响主决策")
            review = decision.get("llm_review") or {}
            self.assertEqual(review.get("status"), "failed")
            self.assertIn("connection refused", (review.get("error") or ""))
            meta = payload.get("llm_review_meta") or {}
            self.assertEqual(meta.get("failed"), 1)
            # 渲染应能正常处理失败 review
            text = render_spc_analysis_text(payload)
            self.assertIn("LLM 复核失败", text)
        finally:
            llm_review_mod.review_decision = original_review
            llm_review_mod.detect_llm_backend = original_detect


class XueqiuScreenerMarketFilterTestCase(unittest.TestCase):
    """雪球 screener 在 gem/st/stib 三个市场的客户端过滤逻辑。

    背景：雪球 screener API 在 type=gem/st/stib 时服务端过滤 silently 失败，
    会返回全 A 数据（count 字段一直 5000）。我们在客户端做 post-filter：
      - gem / st：从全 A（type=sh_sz）分页拉取 + 按 symbol 前缀/name 过滤
      - stib：走 native + 结果验证（丢掉非 BJ 开头的 spillover）
    """

    def _make_client_with_fake_raw(self, pages: dict[int, list[dict]]):
        """构造一个 XueqiuClient，把 _screener_raw 替换成 pages 字典 lookup。

        Args:
            pages: {page_num: [item, ...]}；缺失 page 视为空（结束分页）

        Returns:
            (client, call_log)：call_log 记录所有 _screener_raw 调用参数
        """
        from stock_core.xueqiu import XueqiuClient
        cli = XueqiuClient.__new__(XueqiuClient)  # 不走 __init__ 避免起 session
        cli.user_cookie = ""
        cli._has_login_cookie = False
        cli.cookie_expired = False
        cli._cookie_warning_shown = False

        call_log: list[dict] = []

        def fake_raw(m, t, order_by, order, size, page, extras=None):
            call_log.append({"m": m, "t": t, "order_by": order_by, "order": order,
                             "size": size, "page": page, "extras": extras})
            return {"list": pages.get(page, []), "count": 0}

        cli._screener_raw = fake_raw
        return cli, call_log

    def test_gem_post_filter_returns_only_sz300_sz301(self):
        """gem 应从全 A 池过滤出 SZ300/SZ301 创业板代码。"""
        pages = {
            1: [
                {"symbol": "SH600519", "name": "贵州茅台", "followers": 3000000},
                {"symbol": "SZ300750", "name": "宁德时代", "followers": 1800000},
                {"symbol": "SZ301599", "name": "理奇智能", "followers": 2000},
                {"symbol": "SH601318", "name": "中国平安", "followers": 3000000},
                {"symbol": "SZ300059", "name": "东方财富", "followers": 1900000},
            ],
        }
        cli, log = self._make_client_with_fake_raw(pages)
        r = cli.screener("gem", "followers", "desc", 5)
        symbols = [it["symbol"] for it in r["list"]]
        self.assertEqual(symbols, ["SZ300750", "SZ301599", "SZ300059"],
                         "gem 应只返回 SZ300/SZ301 代码")
        self.assertTrue(r.get("_post_filter"))
        # 应走的 native 是 cn/sh_sz 全 A 池
        self.assertEqual(log[0]["m"], "cn")
        self.assertEqual(log[0]["t"], "sh_sz")

    def test_st_post_filter_uses_name_field(self):
        """ST 用 name 字段判断（含 "ST" 或 "*ST"）。"""
        pages = {
            1: [
                {"symbol": "SH600519", "name": "贵州茅台", "followers": 3000000},
                {"symbol": "SZ002024", "name": "ST易购", "followers": 697609},
                {"symbol": "SH600745", "name": "*ST闻泰", "followers": 260569},
                {"symbol": "SH600036", "name": "招商银行", "followers": 2800000},
            ],
        }
        cli, _ = self._make_client_with_fake_raw(pages)
        r = cli.screener("st", "followers", "desc", 5)
        symbols = [it["symbol"] for it in r["list"]]
        self.assertEqual(symbols, ["SZ002024", "SH600745"],
                         "st 应只返回 name 含 ST 的标的")
        self.assertTrue(r.get("_post_filter"))

    def test_post_filter_paginates_until_size_reached(self):
        """post-filter 不够 N 个就翻页拉取。"""
        pages = {
            1: [{"symbol": "SH600519", "name": "贵州茅台"},
                {"symbol": "SZ300750", "name": "宁德时代"}],
            2: [{"symbol": "SH601318", "name": "中国平安"},
                {"symbol": "SZ300059", "name": "东方财富"}],
            3: [{"symbol": "SZ300015", "name": "爱尔眼科"}],
        }
        cli, log = self._make_client_with_fake_raw(pages)
        r = cli.screener("gem", "followers", "desc", 3)  # 想要 3 个 gem
        symbols = [it["symbol"] for it in r["list"]]
        self.assertEqual(symbols, ["SZ300750", "SZ300059", "SZ300015"])
        # 应该翻了 3 页
        self.assertEqual(r["_pages_scanned"], 3)
        self.assertEqual(len(log), 3)

    def test_post_filter_truncates_at_max_pages(self):
        """翻到 max_pages 上限还没凑够 → 标 truncated 并返回找到的。"""
        from stock_core.xueqiu import XueqiuClient
        # 所有页都没 gem 标的
        pages = {i: [{"symbol": "SH600519", "name": "贵州茅台"}]
                 for i in range(1, XueqiuClient._POST_FILTER_MAX_PAGES + 1)}
        cli, log = self._make_client_with_fake_raw(pages)
        r = cli.screener("gem", "followers", "desc", 5)
        self.assertEqual(r["list"], [])
        self.assertTrue(r["_truncated"])
        self.assertEqual(r["_pages_scanned"],
                         XueqiuClient._POST_FILTER_MAX_PAGES)

    def test_stib_native_verified_filters_spillover(self):
        """stib 走 native + 验证：返回有非 BJ 开头的标的时丢弃 spillover。"""
        # 模拟 native stib 在 percent 下返回 BJ + SZ 混合
        cli, _ = self._make_client_with_fake_raw({
            1: [
                {"symbol": "BJ920096", "name": "N嘉晨", "percent": 676},
                {"symbol": "SZ001365", "name": "N天海电子", "percent": 146},
                {"symbol": "BJ920178", "name": "锐翔智能", "percent": 30},
                {"symbol": "SH688811", "name": "有研复材", "percent": 20},
            ],
        })
        r = cli.screener("stib", "percent", "desc", 5)
        symbols = [it["symbol"] for it in r["list"]]
        self.assertEqual(symbols, ["BJ920096", "BJ920178"],
                         "stib 应只保留 BJ 开头标的")
        self.assertTrue(r.get("_verified"))

    def test_stib_native_returns_empty_when_no_bj(self):
        """stib 在 followers 下 native 完全没 BJ 时返回空 + 警告。"""
        cli, _ = self._make_client_with_fake_raw({
            1: [
                {"symbol": "SH600519", "name": "贵州茅台"},
                {"symbol": "SH601318", "name": "中国平安"},
            ],
        })
        r = cli.screener("stib", "followers", "desc", 5)
        self.assertEqual(r["list"], [])
        self.assertEqual(r["count"], 0)
        self.assertTrue(r.get("_verified"))

    def test_kcb_native_works_without_post_filter(self):
        """kcb 雪球 native 本来就 work，不应走 post-filter。"""
        cli, log = self._make_client_with_fake_raw({
            1: [{"symbol": "SH688981", "name": "中芯国际", "followers": 383105},
                {"symbol": "SH688256", "name": "寒武纪", "followers": 205720}],
        })
        r = cli.screener("kcb", "followers", "desc", 5)
        self.assertEqual(len(r["list"]), 2)
        self.assertFalse(r.get("_post_filter"))
        self.assertFalse(r.get("_verified"))
        # 应该用 native kcb 不是 sh_sz
        self.assertEqual(log[0]["t"], "kcb")

    def test_all_a_native_unaffected(self):
        """all_a 是默认全 A，走 native 不动。"""
        cli, log = self._make_client_with_fake_raw({
            1: [{"symbol": "SH600519", "name": "贵州茅台"}],
        })
        r = cli.screener("all_a", "followers", "desc", 1)
        self.assertEqual(len(r["list"]), 1)
        self.assertFalse(r.get("_post_filter"))
        self.assertEqual(log[0]["t"], "sh_sz")


class SPCLLMReviewUnitTestCase(unittest.TestCase):
    """LLM 复核模块的纯单元测试（不依赖 SPCTestCase 完整 setUp）。"""

    def test_should_review_actions(self):
        from spc_core.llm_review import should_review
        for a in ["add", "trim", "sell", "buy", "probe"]:
            self.assertTrue(should_review(a), f"{a} 应被复核")
        for a in ["hold", "watch", "avoid", "focus"]:
            self.assertFalse(should_review(a), f"{a} 不应被复核")

    def test_detect_llm_backend_respects_env(self):
        from spc_core.llm_review import detect_llm_backend
        original = os.environ.get("SPC_LLM_BACKEND")
        try:
            os.environ["SPC_LLM_BACKEND"] = "none"
            self.assertIsNone(detect_llm_backend())
        finally:
            if original is None:
                os.environ.pop("SPC_LLM_BACKEND", None)
            else:
                os.environ["SPC_LLM_BACKEND"] = original

    def test_parse_llm_json_handles_codex_streaming_output(self):
        """codex 输出含 banner + tokens used + 最后 JSON，需要倒序定位。"""
        from spc_core.llm_review import _parse_llm_json
        fake_output = """OpenAI Codex v0.130.0
--------
workdir: /tmp
model: gpt-5.4
session id: xxx
--------
user
do the review

web search: ...
codex
{"verdict":"confirm","confidence":0.88,"concerns":["前提偏激进"],"missing_context":[],"execution_hint":""}
tokens used
12345
"""
        obj = _parse_llm_json(fake_output)
        self.assertEqual(obj["verdict"], "confirm")
        self.assertAlmostEqual(obj["confidence"], 0.88)

    def test_parse_llm_json_handles_claude_wrapped_output(self):
        """claude --output-format json 把答案 wrap 在 {"result":"<json>","type":"result"}。"""
        from spc_core.llm_review import _parse_llm_json
        inner = json.dumps({
            "verdict": "reject", "confidence": 0.9,
            "concerns": ["反对理由"], "missing_context": [], "execution_hint": "",
        })
        fake_output = json.dumps({"type": "result", "result": inner, "model": "sonnet"})
        obj = _parse_llm_json(fake_output)
        self.assertEqual(obj["verdict"], "reject")
        self.assertEqual(obj["concerns"], ["反对理由"])

    def test_build_review_prompt_includes_critical_data(self):
        """prompt 应包含 action / reasoning / 大盘 regime 等关键上下文。"""
        from spc_core.llm_review import _build_user_prompt
        result = {
            "market": "hk", "code": "01810", "name": "小米集团-W",
            "scope": "holdings",
            "position": {"qty": "11200", "avg_cost_price": "34.6165",
                         "last_price": "30.46", "currency": "HKD",
                         "unrealized_pnl_ccy": "-46552.80"},
            "market_data": {"last_price": "30.46", "change_pct": -1.5},
            "decision": {
                "action": "trim", "action_label": "减仓",
                "confidence": "0.70", "description": "test desc",
                "reasoning": ["现价已触发 T1 首道防线（浮亏 12% ≥ 12%）"],
                "risks": ["大盘 RISK_OFF"],
                "sources": ["test source"],
                "weight_pct": "28.83",
                "confidence_trace": [
                    {"step": "base", "action_to": "hold", "value": "0.55"},
                    {"step": "hard_stop_t1", "action_to": "trim", "value": "0.65"},
                ],
            },
        }
        mr = {"hk": {"regime": "RISK_OFF", "reasons": ["恒科 -28%"]}}
        prompt = _build_user_prompt(result=result, market_regime_payload=mr)
        # 关键字段都在
        self.assertIn("trim", prompt)
        self.assertIn("01810", prompt)
        self.assertIn("RISK_OFF", prompt)
        self.assertIn("hard_stop_t1", prompt)
        self.assertIn("stock-market-hub", prompt, "应告知 LLM 可用 skill")
        # 输出契约说明
        self.assertIn("verdict", prompt)


if __name__ == "__main__":
    unittest.main()

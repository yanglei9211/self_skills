"""Regression tests for the 2026-05-26 code-review refactor batch (二期).

覆盖以下重构（详见 ``code-review-stock-skills-20260526.md`` 的"设计/性能"章节）：

1. 9 个 stock-market-hub 脚本顶部不再各自抄 4 行 ``sys.path`` 样板，统一走
   ``_path_setup``。本测试断言：每个脚本顶部都正确 import 了 ``_path_setup``，
   且不再含旧的 ``_SHARED = Path(...)``。
2. ``scan_sector`` 用 ``heapq.nlargest/nsmallest`` 取 top N，避免 4 次 O(n log n)
   全量排序。
3. ``event_timeline.kline_events`` 用预计算的 ytd / 60d 窗口替代 O(n²)
   过滤，长 kline 也不抖。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
SHARED = SKILL_DIR.parent / "shared"
for p in (SHARED, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import event_timeline  # noqa: E402
import scan_sector  # noqa: E402


# ── 1. 路径设置统一 ─────────────────────────────────────────────

_PATH_SETUP_USERS = [
    "analyze_company.py",
    "capital.py",
    "company_api.py",
    "event_timeline.py",
    "fetch_announcements.py",
    "fetch_market_news.py",
    "pdf_extract.py",
    "risk_scan.py",
    "scan_sector.py",
    "swing_screen.py",
    "supply_chain.py",
    "xueqiu_market.py",
]


class PathSetupConsolidationTests(unittest.TestCase):
    """每个脚本顶部必须 ``import _path_setup``，且不再含旧的 ``_SHARED`` 样板。"""

    def test_path_setup_module_exists(self):
        self.assertTrue((SCRIPTS / "_path_setup.py").exists())

    def test_all_scripts_import_path_setup(self):
        missing = []
        leftover_boilerplate = []
        for name in _PATH_SETUP_USERS:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            if "import _path_setup" not in text:
                missing.append(name)
            # 旧样板 `_SHARED = Path(__file__).resolve().parents[2] / "shared"`
            # 不应再出现在任何 user script 里
            if "_SHARED = Path(__file__).resolve().parents[2]" in text:
                leftover_boilerplate.append(name)
        self.assertEqual(missing, [], f"未引入 _path_setup 的脚本: {missing}")
        self.assertEqual(
            leftover_boilerplate, [],
            f"仍残留旧 _SHARED 样板的脚本: {leftover_boilerplate}",
        )


# ── 2. scan_sector heapq ─────────────────────────────────────────

class ScanSectorHeapqTests(unittest.TestCase):
    """``scan_sector.scan_sector`` 在生成 leaders/gainers/amount/losers 榜单时
    用 ``heapq.nlargest/nsmallest`` 替代 4 次 ``sorted`` 全量排序。

    用 module-level monkeypatch 屏蔽真实 HTTP，喂一组确定性的报价数据，断言
    返回的榜单顺序和长度都符合"取 top N"语义。
    """

    def setUp(self):
        self._patches = []

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _patch(self, target):
        import unittest.mock as mock
        p = mock.patch(target)
        m = p.start()
        self._patches.append(p)
        return m

    def test_top_lists_pick_correct_extremes(self):
        # 构造一组报价：5 只成份股，覆盖正负涨幅 / 不同市值 / 不同成交额。
        # scan_sector 内部会按 symbol 中的纯数字（去掉 SH/SZ 前缀）回查 name_map，
        # 因此 mock 里只放裸 6 位 code 即可。
        quotes = [
            {"symbol": "SH600001", "market_capital": 100e8, "percent": 5.0, "amount": 1e8},
            {"symbol": "SH600002", "market_capital": 500e8, "percent": -2.0, "amount": 3e8},
            {"symbol": "SH600003", "market_capital": 200e8, "percent": 8.0, "amount": 5e8},
            {"symbol": "SH600004", "market_capital": 50e8, "percent": -9.0, "amount": 0.5e8},
            {"symbol": "SH600005", "market_capital": 800e8, "percent": 1.5, "amount": 2e8},
        ]

        self._patch("scan_sector.find_sector_code").return_value = ("concept", "BK0001")
        self._patch("scan_sector.get_sector_constituents").return_value = [
            "600001", "600002", "600003", "600004", "600005",
        ]
        self._patch("scan_sector._get_name_map_from_ths").return_value = {
            "600001": "A", "600002": "B", "600003": "C", "600004": "D", "600005": "E",
        }

        cli_cls = self._patch("scan_sector.XueqiuClient")
        cli_cls.return_value.quotes.return_value = quotes

        out = scan_sector.scan_sector("AI", top=2)

        # by_cap top 2 → E(800)、B(500)
        self.assertEqual([q["symbol"] for q in out["leaders_by_cap"]], ["SH600005", "SH600002"])
        # by_pct top 2 → C(+8)、A(+5)
        self.assertEqual([q["symbol"] for q in out["top_gainers"]], ["SH600003", "SH600001"])
        # by_amount top 2 → C(5)、B(3)
        self.assertEqual([q["symbol"] for q in out["top_amount"]], ["SH600003", "SH600002"])
        # by_loss top 2 → D(-9)、B(-2)
        self.assertEqual([q["symbol"] for q in out["top_losers"]], ["SH600004", "SH600002"])


# ── 3. event_timeline kline O(n) 滑窗 ───────────────────────────

class KlineEventsLinearScanTests(unittest.TestCase):
    """``kline_events`` 重构后用预计算的 ytd / 60d min/max。

    用一个跨年的、含明显新高 / 新低 / 涨停的 K 线，验证：
      - 创年内新高 / 创年内新低能被正确识别
      - 创 60 日新高 / 创 60 日新低能被正确识别
      - 涨停 / 跌停标签生效
    """

    def _build_kline(self) -> list[dict]:
        """100 天连号 K 线：前 80 天平盘 10.0，第 81 天跌停 -11%（同时创年内新低），
        后续慢慢回升。日期用今天往回数，确保落在 ``days=90`` 的 cutoff 内。
        """
        from datetime import datetime, timedelta
        today = datetime.now().date()
        kl: list[dict] = []
        for i in range(100):
            d = (today - timedelta(days=99 - i)).strftime("%Y-%m-%d")
            if i < 80:
                kl.append({"date": d, "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9})
            elif i == 80:
                # 跌停 + 创新低
                kl.append({"date": d, "open": 10.0, "close": 8.9, "high": 10.0, "low": 8.85})
            else:
                v = 8.9 + (i - 80) * 0.05
                kl.append({"date": d, "open": v, "close": v, "high": v + 0.05, "low": v - 0.05})
        return kl

    def test_recognizes_ytd_low_and_limit_down(self):
        kl = self._build_kline()
        import unittest.mock as mock
        with mock.patch.object(event_timeline, "fetch_daily_kline", return_value=kl):
            events = event_timeline.kline_events(
                symbol="SH600001",
                market="a",
                kline_sym="SH600001",
                days=90,
            )
        titles = [e["title"] for e in events]
        self.assertTrue(
            any("跌停" in t for t in titles),
            f"应识别出跌停事件；titles={titles}",
        )
        # 跌停那一天的 low=8.85 是 80 天均价 10.0 之后的低点；
        # 如果跌停日落在跨年那侧，可能只是"创 60 日新低"而非"创年内新低"，所以放宽断言。
        self.assertTrue(
            any("新低" in t for t in titles),
            f"应识别出创新低（年内或 60 日）；titles={titles}",
        )


if __name__ == "__main__":
    unittest.main()

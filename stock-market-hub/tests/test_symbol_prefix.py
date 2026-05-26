"""Regression test for the symbol-prefix resolution shared between
``scan_sector.py`` and ``supply_chain.py``.

历史包袱：scan_sector 曾经自己写过一个简化版 ``code_to_xueqiu``，规则不完整
（漏 5/9/1 三个前缀段），导致：
  - 5 开头的上交所 ETF（510/511/512/513/515/518/588 等）拿不到 SH 前缀
  - 9 开头的上交所 B 股
  - 1 开头的深交所 ETF/LOF（150/159/160/161/164/165 等）拿不到 SZ 前缀
雪球 ``quotes(["513050"])`` 调用因为没有前缀就会丢数据。

2026-05-22 已经把 ``scan_sector.code_to_xueqiu`` 删除、所有调用统一到
``shared.stock_core.symbols.parts_to_symbol("a", code)``。这个测试就是把这件
事情固化下来——如果将来又有人"看 scan_sector 太长就简化重写"，这个测试会
立刻报错。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SHARED = SKILL_DIR.parent / "shared"
SCRIPTS = SKILL_DIR / "scripts"
for p in (SHARED, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import scan_sector  # noqa: E402
import supply_chain  # noqa: E402
from stock_core.symbols import parts_to_symbol  # noqa: E402


class SymbolPrefixRegressionTests(unittest.TestCase):
    """所有跨 skill 共用的 A 股 6 位代码 → 雪球前缀代码的规则都收敛在
    ``parts_to_symbol``，scan_sector / supply_chain 都通过它走。
    """

    # ---- 上交所 ---- #
    def test_sh_stock(self):
        # 6 开头：上交所股票
        self.assertEqual(parts_to_symbol("a", "600519"), "SH600519")  # 茅台
        self.assertEqual(parts_to_symbol("a", "688981"), "SH688981")  # 科创板

    def test_sh_b_share(self):
        # 9 开头：上交所 B 股（曾被 code_to_xueqiu 漏掉，落到 fallthrough）
        self.assertEqual(parts_to_symbol("a", "900903"), "SH900903")

    def test_sh_etf(self):
        # 5 开头：上交所 ETF / 基金（漏掉的话 quotes 拿不到行情）
        self.assertEqual(parts_to_symbol("a", "510300"), "SH510300")  # 沪深300 ETF
        self.assertEqual(parts_to_symbol("a", "513050"), "SH513050")  # 中概互联 ETF
        self.assertEqual(parts_to_symbol("a", "588000"), "SH588000")  # 科创 50 ETF

    # ---- 深交所 ---- #
    def test_sz_stock(self):
        # 0 / 3 开头：深交所
        self.assertEqual(parts_to_symbol("a", "000001"), "SZ000001")  # 平安
        self.assertEqual(parts_to_symbol("a", "300750"), "SZ300750")  # 宁德

    def test_sz_etf(self):
        # 1 开头：深交所 ETF / LOF（同样曾被 code_to_xueqiu 漏掉）
        self.assertEqual(parts_to_symbol("a", "159915"), "SZ159915")  # 创业板 ETF
        self.assertEqual(parts_to_symbol("a", "150153"), "SZ150153")  # 分级基金 LOF

    # ---- 北交所 ---- #
    def test_bj(self):
        # 4 / 8 开头：北交所
        self.assertEqual(parts_to_symbol("a", "430047"), "BJ430047")
        self.assertEqual(parts_to_symbol("a", "830799"), "BJ830799")

    # ---- 港股（虽然这次重构不动它，但顺带做个回归） ---- #
    def test_hk(self):
        self.assertEqual(parts_to_symbol("hk", "00700"), "HK00700")


class CodeToXueqiuRemovedTests(unittest.TestCase):
    """``scan_sector.code_to_xueqiu`` 必须已经从代码里消失。再次出现意味着
    历史 bug 被复活。
    """

    def test_no_module_level_function(self):
        self.assertFalse(
            hasattr(scan_sector, "code_to_xueqiu"),
            "scan_sector.code_to_xueqiu 已被 parts_to_symbol 替代，不应再回归",
        )

    def test_supply_chain_uses_parts_to_symbol(self):
        # 反向断言：supply_chain 顶部应该 import 了 parts_to_symbol
        self.assertTrue(
            hasattr(supply_chain, "parts_to_symbol"),
            "supply_chain 应该直接 import parts_to_symbol，而不是再绕回 scan_sector",
        )


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the 2026-05-26 code-review batch.

覆盖以下修复（详见 ``code-review-stock-skills-20260526.md`` 的 Bug + 缓存章节）：

1. ``risk_scan.rule_announcement_keyword`` 不应再用
   ``"SH"+sym if sym.startswith("6") else "SZ"+sym`` 这种残缺写法，必须走
   ``stock_core.symbols.parts_to_symbol`` 同源逻辑（覆盖 SH 5/9、SZ 1、BJ 4/8）。
2. ``supply_chain.get_peers_legacy`` 删掉了一次冗余的 HTTP 调用
   （``get_sector_constituents("concept", ...)`` 结果立刻被下一行覆盖）。
3. ``pdf_extract.download_pdf`` 改为：
   - 原子写入（先写 ``.part`` 再 rename），中途被 kill 不会留下截断文件；
   - 30 天 TTL，让年报勘误版能拿到；
   - 下载内容 <= 1KB 时拒绝写入缓存，避免毒化。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
SHARED = SKILL_DIR.parent / "shared"
for p in (SHARED, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pdf_extract  # noqa: E402
import risk_scan  # noqa: E402
import supply_chain  # noqa: E402


# ── risk_scan: 公告聚合时的符号前缀 ────────────────────────────────

class RiskScanAnnouncementSymbolTests(unittest.TestCase):
    """``rule_announcement_keyword`` 把公告聚合成"按股票"时，必须用
    ``parts_to_symbol`` 派生 xueqiu 符号；不再使用残缺的 ``"SH" if startswith("6")`` 写法。
    """

    def _run_with_fake_anns(self, anns: list[dict]) -> dict[str, dict]:
        """mock fetch_announcements 返回固定列表，跑一次 rule_announcement_keyword。"""
        with mock.patch.object(risk_scan, "__name__", "risk_scan"):
            # rule_announcement_keyword 内部 `from fetch_announcements import cninfo_announcements`
            # 所以需要 mock 模块层级 import
            with mock.patch.dict(sys.modules):
                fake = mock.MagicMock()
                fake.cninfo_announcements.return_value = anns
                sys.modules["fetch_announcements"] = fake
                out = risk_scan.rule_announcement_keyword("立案调查", days=30)
        return {item["_announcements"][0]["title"]: item for item in out}

    def test_sh_main_board_6(self):
        out = self._run_with_fake_anns([
            {"symbol": "600519", "name": "贵州茅台",
             "date": "2026-05-20", "title": "T6", "pdf_url": "http://x"},
        ])
        self.assertEqual(out["T6"]["symbol"], "SH600519")

    def test_sh_etf_5_was_missing_in_old_impl(self):
        """旧实现把 510300 当成深市 → ``SZ510300``，雪球行情会拿不到。"""
        out = self._run_with_fake_anns([
            {"symbol": "510300", "name": "沪深300ETF",
             "date": "2026-05-20", "title": "T5", "pdf_url": "http://x"},
        ])
        self.assertEqual(out["T5"]["symbol"], "SH510300")

    def test_sh_b_share_9_was_missing_in_old_impl(self):
        out = self._run_with_fake_anns([
            {"symbol": "900903", "name": "大众B股",
             "date": "2026-05-20", "title": "T9", "pdf_url": "http://x"},
        ])
        self.assertEqual(out["T9"]["symbol"], "SH900903")

    def test_sz_etf_1_was_missing_in_old_impl(self):
        """旧实现把 159915 也当成 SZ 是对的，但 150xxx 同样会被旧逻辑误判。"""
        out = self._run_with_fake_anns([
            {"symbol": "159915", "name": "易方达创业板ETF",
             "date": "2026-05-20", "title": "TZE", "pdf_url": "http://x"},
        ])
        self.assertEqual(out["TZE"]["symbol"], "SZ159915")

    def test_bj_4_was_missing_in_old_impl(self):
        """旧实现：430047 不以 '6' 开头 → 'SZ430047'，但其实是北交所。"""
        out = self._run_with_fake_anns([
            {"symbol": "430047", "name": "诺思兰德",
             "date": "2026-05-20", "title": "TBJ4", "pdf_url": "http://x"},
        ])
        self.assertEqual(out["TBJ4"]["symbol"], "BJ430047")

    def test_bj_8_was_missing_in_old_impl(self):
        out = self._run_with_fake_anns([
            {"symbol": "830799", "name": "成大生物",
             "date": "2026-05-20", "title": "TBJ8", "pdf_url": "http://x"},
        ])
        self.assertEqual(out["TBJ8"]["symbol"], "BJ830799")


# ── supply_chain: get_peers_legacy 不再发冗余 HTTP ───────────────────

class SupplyChainPeersHttpTests(unittest.TestCase):
    """``get_peers_legacy`` 在概念命中分支里曾经有一行废 HTTP：
    ``consts = get_sector_constituents("concept", code)`` 结果立刻被
    ``consts = get_sector_constituents(sector_kind, code)`` 覆盖。

    修复后该路径下 ``get_sector_constituents`` 只被调用一次（命中概念时）。
    """

    def test_concept_path_calls_constituents_once(self):
        # mock 整条依赖链：normalize_symbol → a 股、concepts、sector map、constituents
        # （这条路径设计就是命中第一个 concept，走非 industry fallback 分支）
        with mock.patch.dict(sys.modules):
            ca_stub = mock.MagicMock()
            ca_stub.get_a_concepts.return_value = ["动力电池"]
            sys.modules["stock_core.company_analysis"] = ca_stub

            scan_stub = mock.MagicMock()
            scan_stub.get_sector_map.return_value = {"动力电池": "BK1234"}
            constituents_calls: list[tuple] = []

            def _fake_constituents(kind, code, max_pages=5):
                constituents_calls.append((kind, code))
                return ["300750", "002460", "300014"]

            scan_stub.get_sector_constituents.side_effect = _fake_constituents
            sys.modules["scan_sector"] = scan_stub

            # XueqiuClient.screener_by_symbols 也要 mock，避免真发请求
            with mock.patch.object(supply_chain, "XueqiuClient") as Cli:
                Cli.return_value.screener_by_symbols.return_value = []
                _ = supply_chain.get_peers_legacy("SZ300750", top=8)

        # 期望：只调用一次 get_sector_constituents（旧版会调两次）
        self.assertEqual(
            len(constituents_calls), 1,
            f"get_sector_constituents 不应被冗余调用，实际：{constituents_calls}",
        )
        self.assertEqual(constituents_calls[0], ("concept", "BK1234"))


# ── pdf_extract: 缓存 TTL + 原子写入 ─────────────────────────────────

class PdfCacheTests(unittest.TestCase):
    """``download_pdf`` 不再产生永久毒化的截断文件。"""

    def setUp(self):
        self.tmpdir = Path(self._make_tmpdir())
        self._patch_cache = mock.patch.object(pdf_extract, "CACHE_DIR", self.tmpdir)
        self._patch_cache.start()

    def tearDown(self):
        self._patch_cache.stop()
        for p in self.tmpdir.iterdir():
            try:
                p.unlink()
            except IsADirectoryError:
                pass
        self.tmpdir.rmdir()

    def _make_tmpdir(self) -> str:
        import tempfile
        d = tempfile.mkdtemp(prefix="pdf_cache_test_")
        return d

    def _make_fake_response(self, content: bytes, status: int = 200):
        r = mock.MagicMock()
        r.status_code = status
        r.content = content
        return r

    def test_small_content_is_not_cached(self):
        """<=1KB 的 "PDF"（多半是反爬错误页）不应被写入缓存。"""
        small = b"<html>blocked</html>"
        with mock.patch.object(pdf_extract, "fetch", return_value=self._make_fake_response(small)):
            with self.assertRaises(RuntimeError):
                pdf_extract.download_pdf("http://example.com/report.pdf")
        cached = list(self.tmpdir.glob("*"))
        self.assertEqual(cached, [], f"截断内容不应被缓存: {cached}")

    def test_atomic_write_no_part_file_left(self):
        """正常写入完成后，``.part`` 临时文件应被 rename，不残留。"""
        good = b"%PDF-1.4\n" + b"x" * 4096 + b"\n%%EOF\n"
        with mock.patch.object(pdf_extract, "fetch", return_value=self._make_fake_response(good)):
            path = pdf_extract.download_pdf("http://example.com/a.pdf")
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1024)
        # 不应残留 .part 文件
        parts = list(self.tmpdir.glob("*.part"))
        self.assertEqual(parts, [])

    def test_ttl_expired_re_downloads(self):
        """超过 TTL 的缓存应被丢弃并重新拉取。"""
        good = b"%PDF-1.4\n" + b"x" * 4096 + b"\n%%EOF\n"
        with mock.patch.object(pdf_extract, "fetch", return_value=self._make_fake_response(good)) as f1:
            path = pdf_extract.download_pdf("http://example.com/b.pdf")
            self.assertEqual(f1.call_count, 1)

        # 把 mtime 推回到 TTL 之前
        old = time.time() - (pdf_extract._PDF_CACHE_TTL_SECONDS + 86400)
        import os as _os
        _os.utime(path, (old, old))

        with mock.patch.object(pdf_extract, "fetch", return_value=self._make_fake_response(good)) as f2:
            path2 = pdf_extract.download_pdf("http://example.com/b.pdf")
            self.assertEqual(f2.call_count, 1, "TTL 过期后应重新发起 fetch")
        self.assertEqual(path, path2)

    def test_fresh_cache_hits(self):
        """TTL 内、大小 > 1KB 的缓存命中时不发新请求。"""
        good = b"%PDF-1.4\n" + b"x" * 4096 + b"\n%%EOF\n"
        with mock.patch.object(pdf_extract, "fetch", return_value=self._make_fake_response(good)) as f1:
            pdf_extract.download_pdf("http://example.com/c.pdf")
            self.assertEqual(f1.call_count, 1)
        with mock.patch.object(pdf_extract, "fetch") as f2:
            pdf_extract.download_pdf("http://example.com/c.pdf")
            self.assertEqual(f2.call_count, 0, "新鲜缓存不应触发再次下载")


if __name__ == "__main__":
    unittest.main()

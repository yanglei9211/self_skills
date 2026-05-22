"""Unit tests for ``company_api.py --deep`` orchestration and rendering.

只测纯函数 + monkeypatch 后的降级行为；不会真发 HTTP（巨潮 / 雪球 / 东财）。
覆盖范围：

- ``build_supply_chain_payload``：找不到年报 / PDF 失败 / peers 失败时不抛异常
- ``_build_deep_payload``：``supply_chain`` 抛异常时退化为 ``{"error": "..."}``
- ``_render_deep_text``：空 / error / pdf_error / 完整 payload 的渲染都不崩
- ``main`` 端到端：``--deep`` 真的把 deep 章节追加进 text 输出（用 monkeypatch 屏蔽真实 HTTP）
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
SHARED = SKILL_DIR.parent / "shared"
for p in (SHARED, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import company_api  # noqa: E402  pylint: disable=wrong-import-position
import supply_chain  # noqa: E402


_FAKE_REPORT = {
    "title": "2025 年年度报告",
    "date": "2026-03-10",
    "pdf_url": "http://example.com/300750_2025.pdf",
}


def _fake_pdf_section(section_key: str) -> dict:
    """模拟 pdf_extract.find_section 的返回值（部分章节 found，部分未找到）。"""
    if section_key == "business":
        return {
            "label": "业务概要 / 主营业务",
            "found": True,
            "start_page": 12,
            "end_page": 24,
            "char_count": 4321,
            "text": "公司主要从事动力电池研发与销售，主要客户包括宁德时代、特斯拉、比亚迪……" * 30,
        }
    if section_key == "customers":
        return {
            "label": "前五名客户 / 主要客户",
            "found": True,
            "start_page": 80,
            "end_page": 81,
            "char_count": 800,
            "text": (
                "客户一  特斯拉有限公司   销售额 1234567  占比 12.34%\n"
                "客户二  比亚迪股份有限公司   销售额 999888  占比 9.87%\n"
            ),
        }
    if section_key == "risks":
        return {
            "label": "风险因素",
            "found": True,
            "start_page": 120,
            "end_page": 130,
            "char_count": 2500,
            "text": "原材料价格波动、海外政策风险、技术替代风险……",
        }
    return {
        "label": section_key,
        "found": False,
        "start_page": None,
        "end_page": None,
        "char_count": 0,
        "text": "",
    }


def _fake_peers(_symbol: str, *, top: int = 8) -> list[dict]:
    return [
        {
            "symbol": "SZ002594",
            "name": "比亚迪",
            "current": 250.0,
            "percent": 1.23,
            "market_cap_yi": 7200,
            "pe_ttm": 22.1,
            "pb": 4.5,
            "ps": 1.2,
            "roe_ttm": 18.0,
            "net_profit_cagr": 25.0,
            "income_cagr": 30.0,
            "main_inflow_yi": 1.2,
            "ytd_pct": 10.0,
        },
    ][:top]


class RenderDeepTextTests(unittest.TestCase):
    """``_render_deep_text`` 必须对各种降级 payload 保持鲁棒。"""

    def test_empty_deep_returns_empty_string(self):
        self.assertEqual(company_api._render_deep_text({}), "")

    def test_market_not_supported_only_emits_skip_block(self):
        text = company_api._render_deep_text({
            "symbol": "HK00700",
            "error": "未找到 HK00700 的最新 annual 报告（A 股以外或巨潮无对应记录）",
        })
        self.assertIn("六~八 深度模式：跳过", text)
        self.assertIn("HK00700", text)
        # 没有 §6 §7 §8 标题（被 skip block 覆盖了）
        self.assertNotIn("## 六、商业模式", text)

    def test_full_payload_emits_all_three_sections(self):
        payload = {
            "symbol": "SZ300750",
            "report": _FAKE_REPORT,
            "sections": {
                "business": _fake_pdf_section("business"),
                "customers": _fake_pdf_section("customers"),
                "suppliers": _fake_pdf_section("suppliers"),
                "mda": _fake_pdf_section("mda"),
                "risks": _fake_pdf_section("risks"),
            },
            "extracted_entities": {
                "from_customers": ["特斯拉有限公司", "比亚迪股份有限公司"],
                "from_suppliers": [],
                "from_business": ["宁德时代", "特斯拉"],
            },
            "amount_pairs": {
                "customers": [
                    {"name": "特斯拉有限公司", "amount_raw": "1234567", "pct": "12.34%"}
                ],
                "suppliers": [],
            },
            "peers": _fake_peers("SZ300750"),
        }
        text = company_api._render_deep_text(payload)
        self.assertIn("## 六、商业模式 / 上下游", text)
        self.assertIn("## 七、风险提示", text)
        self.assertIn("## 八、同业对比", text)
        self.assertIn("特斯拉", text)
        self.assertIn("SZ002594", text)
        # peers 表头
        self.assertIn("| symbol | 名称 |", text)

    def test_pdf_error_keeps_sections_and_warns(self):
        payload = {
            "symbol": "SZ300750",
            "report": _FAKE_REPORT,
            "pdf_error": "RuntimeError: PDF 下载失败 HTTP 503",
            "sections": {},
            "extracted_entities": {"from_customers": [], "from_suppliers": [], "from_business": []},
            "amount_pairs": {"customers": [], "suppliers": []},
            "peers": _fake_peers("SZ300750"),
        }
        text = company_api._render_deep_text(payload)
        self.assertIn("PDF 解析失败", text)
        self.assertIn("PDF 下载失败 HTTP 503", text)
        # peers 没受 PDF 失败影响
        self.assertIn("## 八、同业对比", text)
        self.assertIn("SZ002594", text)
        # PDF 章节有"未找到"占位，不抛 KeyError
        self.assertIn("未找到", text)

    def test_peers_error_emits_warning(self):
        payload = {
            "symbol": "SZ300750",
            "report": _FAKE_REPORT,
            "sections": {"business": _fake_pdf_section("business")},
            "extracted_entities": {"from_customers": [], "from_suppliers": [], "from_business": []},
            "amount_pairs": {"customers": [], "suppliers": []},
            "peers": [],
            "peers_error": "RuntimeError: xueqiu screener 502",
        }
        text = company_api._render_deep_text(payload)
        self.assertIn("peers 拉取失败", text)
        self.assertIn("xueqiu screener 502", text)


class BuildSupplyChainPayloadDegradationTests(unittest.TestCase):
    """``supply_chain.build_supply_chain_payload`` 在各种失败下都得返回 dict，不抛。"""

    def test_no_report_for_hk_us_returns_error_only(self):
        with mock.patch.object(supply_chain, "find_latest_report", return_value=None):
            payload = supply_chain.build_supply_chain_payload("HK00700")
        self.assertIn("error", payload)
        self.assertIsNone(payload["report"])
        self.assertEqual(payload["peers"], [])
        # 不应该有 pdf_error / peers_error，因为压根没走到那两步
        self.assertNotIn("pdf_error", payload)
        self.assertNotIn("peers_error", payload)

    def test_find_latest_report_raises_is_caught(self):
        with mock.patch.object(
            supply_chain, "find_latest_report",
            side_effect=RuntimeError("巨潮 503"),
        ):
            payload = supply_chain.build_supply_chain_payload("SZ300750")
        # 异常被吞，payload 仍然返回
        self.assertIn("error", payload)
        self.assertIsNone(payload["report"])

    def test_pdf_failure_does_not_block_peers(self):
        # 找到了年报，但 pdf_extract.download_pdf 抛错
        fake_pdf_module = mock.MagicMock()
        fake_pdf_module.download_pdf.side_effect = RuntimeError("PDF 下载失败 HTTP 503")
        with mock.patch.object(supply_chain, "find_latest_report", return_value=_FAKE_REPORT), \
             mock.patch.object(supply_chain, "get_peers_from_concept", _fake_peers), \
             mock.patch.dict(sys.modules, {"pdf_extract": fake_pdf_module}):
            payload = supply_chain.build_supply_chain_payload("SZ300750")
        self.assertIn("pdf_error", payload)
        self.assertEqual(payload["report"]["title"], _FAKE_REPORT["title"])
        self.assertEqual(payload["peers"][0]["symbol"], "SZ002594")
        self.assertNotIn("error", payload)

    def test_peers_failure_does_not_block_pdf_sections(self):
        # PDF 抽取成功，但 peers 抛错
        fake_pdf_module = mock.MagicMock()

        def _fake_download(_url):
            return Path("/tmp/fake.pdf")

        def _fake_extract(_path, max_pages=None):  # noqa: ARG001
            return ["dummy text"], [1]

        fake_pdf_module.download_pdf.side_effect = _fake_download
        fake_pdf_module.extract_full_text.side_effect = _fake_extract
        fake_pdf_module.find_section.side_effect = lambda *_a, **_k: _fake_pdf_section(_a[2] if len(_a) > 2 else "x")

        with mock.patch.object(supply_chain, "find_latest_report", return_value=_FAKE_REPORT), \
             mock.patch.object(
                supply_chain, "get_peers_from_concept",
                side_effect=RuntimeError("xueqiu 502"),
             ), \
             mock.patch.dict(sys.modules, {"pdf_extract": fake_pdf_module}):
            payload = supply_chain.build_supply_chain_payload("SZ300750")
        self.assertIn("peers_error", payload)
        self.assertEqual(payload["peers"], [])
        # PDF 章节字段还在
        self.assertTrue(payload["sections"]["business"]["found"])

    def test_include_peers_false_skips_peers_call(self):
        # 即使 peers 实现可用，include_peers=False 也不应调用它
        peers_mock = mock.MagicMock(return_value=_fake_peers("SZ300750"))
        with mock.patch.object(supply_chain, "find_latest_report", return_value=None), \
             mock.patch.object(supply_chain, "get_peers_from_concept", peers_mock):
            supply_chain.build_supply_chain_payload(
                "SZ300750", include_peers=False
            )
        peers_mock.assert_not_called()


class CompanyApiBuildDeepPayloadTests(unittest.TestCase):
    """``_build_deep_payload`` 自己也要兜一层兜底。"""

    def test_normal_flow_delegates_to_supply_chain(self):
        expected = {"symbol": "SZ300750", "report": _FAKE_REPORT}
        with mock.patch.object(supply_chain, "build_supply_chain_payload", return_value=expected) as m:
            out = company_api._build_deep_payload(
                "SZ300750", report_type="annual", max_pdf_pages=300
            )
        self.assertIs(out, expected)
        m.assert_called_once()

    def test_supply_chain_raises_is_caught(self):
        with mock.patch.object(
            supply_chain, "build_supply_chain_payload",
            side_effect=RuntimeError("boom"),
        ):
            out = company_api._build_deep_payload(
                "SZ300750", report_type="annual", max_pdf_pages=300
            )
        self.assertEqual(out["symbol"], "SZ300750")
        self.assertIn("error", out)
        self.assertIn("boom", out["error"])


class CompanyApiMainE2ETests(unittest.TestCase):
    """``main`` 端到端：--deep 真的把 §6/§7/§8 拼进 text 输出。

    用 monkeypatch 屏蔽真实 HTTP；只验证编排和合并行为。
    """

    def setUp(self):
        self._stub_analyze = mock.patch.object(
            company_api, "analyze_symbol",
            return_value={"info": {"name": "宁德时代"}, "quote": {"current": "260.00"}},
        )
        self._stub_render = mock.patch.object(
            company_api, "render_analysis_text",
            return_value="# 宁德时代速查卡片占位\n",
        )
        self._stub_deep = mock.patch.object(
            company_api, "_build_deep_payload",
            return_value={
                "symbol": "SZ300750",
                "report": _FAKE_REPORT,
                "sections": {
                    "business": _fake_pdf_section("business"),
                    "customers": _fake_pdf_section("customers"),
                    "risks": _fake_pdf_section("risks"),
                },
                "extracted_entities": {
                    "from_customers": ["特斯拉"],
                    "from_suppliers": [],
                    "from_business": [],
                },
                "amount_pairs": {"customers": [], "suppliers": []},
                "peers": _fake_peers("SZ300750"),
            },
        )

    def _run_main(self, argv: list[str]) -> str:
        stdout = io.StringIO()
        with self._stub_analyze, self._stub_render, self._stub_deep, \
             mock.patch.object(sys, "argv", ["company_api.py", *argv]), \
             mock.patch.object(sys, "stdout", stdout):
            company_api.main()
        return stdout.getvalue()

    def test_deep_text_output_includes_three_sections(self):
        out = self._run_main(["--symbol", "SZ300750", "--deep", "--format", "text"])
        self.assertIn("# 宁德时代速查卡片占位", out)
        self.assertIn("## 六、商业模式 / 上下游", out)
        self.assertIn("## 七、风险提示", out)
        self.assertIn("## 八、同业对比", out)

    def test_deep_json_output_has_deep_key(self):
        out = self._run_main(["--symbol", "SZ300750", "--deep", "--format", "json"])
        parsed = json.loads(out)
        self.assertIn("deep", parsed)
        self.assertEqual(parsed["deep"]["report"]["title"], _FAKE_REPORT["title"])
        # 速查字段也在
        self.assertEqual(parsed["info"]["name"], "宁德时代")

    def test_without_deep_no_deep_key(self):
        # 没传 --deep 时不应该调用 _build_deep_payload，也不应该有 deep 字段
        stdout = io.StringIO()
        with self._stub_analyze, self._stub_render, \
             mock.patch.object(company_api, "_build_deep_payload") as deep_mock, \
             mock.patch.object(sys, "argv", ["company_api.py", "--symbol", "SZ300750", "--format", "json"]), \
             mock.patch.object(sys, "stdout", stdout):
            company_api.main()
        deep_mock.assert_not_called()
        parsed = json.loads(stdout.getvalue())
        self.assertNotIn("deep", parsed)


if __name__ == "__main__":
    unittest.main()

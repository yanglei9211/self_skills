from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
SHARED = SKILL_DIR.parent / "shared"
for p in (SHARED, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import swing_screen  # noqa: E402


class SwingScreenCliTests(unittest.TestCase):
    def test_parser_defaults_to_swing_horizon_and_text_output(self):
        parser = swing_screen.build_parser()
        args = parser.parse_args([])

        self.assertEqual(args.horizon, "swing")
        self.assertEqual(args.top, 15)
        self.assertEqual(args.pool_size, 600)
        self.assertEqual(args.format, "text")

    def test_json_output_is_machine_readable(self):
        payload = swing_screen.render_json({"trend_continuation": [], "bottom_reversal": []}, scanned=12)
        data = json.loads(payload)

        self.assertEqual(data["scanned"], 12)
        self.assertEqual(data["buckets"]["trend_continuation"], [])
        self.assertEqual(data["buckets"]["bottom_reversal"], [])

    def test_is_a_share_symbol_boundaries(self):
        cases = {
            "": False,
            "SH": False,
            "SZ400001": False,
            "SH688001": True,
            "BJ430000": False,
            "600001": False,
            "SH600001": True,
        }
        for symbol, expected in cases.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(swing_screen._is_a_share_symbol(symbol), expected)

    def test_debug_mode_prints_traceback_for_unexpected_analysis_error(self):
        quote = {"symbol": "SH600001"}
        with mock.patch("stock_core.symbols.normalize_symbol", side_effect=KeyError("broken")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = swing_screen._analyze_one(quote, debug=True)

        self.assertIsNone(result)
        self.assertIn("Traceback", stderr.getvalue())
        self.assertIn("KeyError", stderr.getvalue())

    def test_smh_dispatches_screen_command(self):
        text = (SKILL_DIR / "bin" / "smh").read_text(encoding="utf-8")

        self.assertIn("screen|scr)", text)
        self.assertIn("swing_screen.py", text)

    def test_skill_doc_describes_swing_screen(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("smh screen", text)
        self.assertIn("趋势延续", text)
        self.assertIn("筑底反转", text)


if __name__ == "__main__":
    unittest.main()

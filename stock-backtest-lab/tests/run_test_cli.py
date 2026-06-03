"""CLI 模块测试。"""
import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test(desc, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL: {desc}")
        import traceback
        traceback.print_exc()
        raise
    print(f"PASS: {desc}")


def run_all():
    test("_parse_hold_days", lambda: _test_parse_hold_days())
    test("策略注册表完整性", lambda: _test_registry())
    test("CLI 模块可导入", lambda: _test_import())

    print("\nALL CLI TESTS PASSED")


def _test_parse_hold_days():
    from backtest_core.cli import _parse_hold_days
    assert _parse_hold_days("5,10,20,60") == [5, 10, 20, 60]
    assert _parse_hold_days("5") == [5]
    assert _parse_hold_days(" 5 , 10 ") == [5, 10]


def _test_registry():
    from backtest_core.strategies import STRATEGIES
    assert "boll_squeeze_entry" in STRATEGIES
    assert "ma_breakout" in STRATEGIES
    assert "price_new_high" in STRATEGIES
    assert callable(STRATEGIES["boll_squeeze_entry"])


def _test_import():
    """验证所有模块可正常导入。"""
    from backtest_core import cli
    from backtest_core import store
    from backtest_core import sync
    from backtest_core import snapshot
    from backtest_core import signals
    from backtest_core import strategies
    from backtest_core import tracker
    from backtest_core import stats
    from backtest_core import report
    from backtest_core import models
    assert cli is not None
    assert store is not None
    assert sync is not None
    assert snapshot is not None
    assert signals is not None
    assert strategies is not None
    assert tracker is not None
    assert stats is not None
    assert report is not None
    assert models is not None


if __name__ == "__main__":
    run_all()

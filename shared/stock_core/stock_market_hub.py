from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from stock_core.company_analysis import analyze, render_text
from stock_core.market_snapshot import market_board


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def shared_root() -> Path:
    return repo_root() / "shared"


def stock_market_hub_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return (repo_root() / "stock-market-hub").resolve()


def analyze_company_script_path(hub_dir: str | None = None) -> Path:
    return stock_market_hub_dir(hub_dir) / "scripts" / "analyze_company.py"


def _load_external_module(script_path: Path):
    scripts_dir = str(script_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(f"stock_market_hub_external_{abs(hash(str(script_path)))}", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载分析模块: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_args(
    *,
    top_managers: int = 10,
    top_holders: int = 10,
    ann_days: int = 30,
    ann_limit: int = 20,
    kline_count: int = 1500,
    with_peers: bool = False,
    skip: str = "",
    format: str = "json",
) -> argparse.Namespace:
    return argparse.Namespace(
        top_managers=top_managers,
        top_holders=top_holders,
        ann_days=ann_days,
        ann_limit=ann_limit,
        kline_count=kline_count,
        with_peers=with_peers,
        skip=skip,
        format=format,
    )


def analyze_symbol(
    symbol: str,
    *,
    hub_dir: str | None = None,
    top_managers: int = 10,
    top_holders: int = 10,
    ann_days: int = 30,
    ann_limit: int = 20,
    kline_count: int = 1500,
    with_peers: bool = False,
    skip: str = "",
) -> dict:
    default_hub = stock_market_hub_dir()
    resolved_hub = stock_market_hub_dir(hub_dir) if hub_dir else default_hub
    args = build_args(
        top_managers=top_managers,
        top_holders=top_holders,
        ann_days=ann_days,
        ann_limit=ann_limit,
        kline_count=kline_count,
        with_peers=with_peers,
        skip=skip,
    )
    if resolved_hub == default_hub:
        data = analyze(symbol, args)
    else:
        external = _load_external_module(analyze_company_script_path(str(resolved_hub)))
        data = external.analyze(symbol, args)
    data["ann_days"] = ann_days
    return data


def render_analysis_text(data: dict, *, hub_dir: str | None = None) -> str:
    default_hub = stock_market_hub_dir()
    resolved_hub = stock_market_hub_dir(hub_dir) if hub_dir else default_hub
    if resolved_hub == default_hub:
        return render_text(data)
    external = _load_external_module(analyze_company_script_path(str(resolved_hub)))
    return external.render_text(data)


def fetch_market_board(market: str = "all_a", board: str = "gainers", top: int = 10) -> dict:
    return market_board(market=market, board=board, top=top)

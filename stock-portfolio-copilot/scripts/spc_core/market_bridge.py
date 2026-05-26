from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

# 注：``spc_core.utils`` 已经把 shared 路径加入 sys.path，所以这里可以直接 import。
from spc_core.settings import get_setting
from spc_core.utils import build_analysis_symbol, q_ratio
from stock_core.market_regime import classify_market_regime
from stock_core.stock_market_hub import analyze_symbol, fetch_market_board


class StockMarketHubProvider:
    def __init__(self, hub_dir: str | None = None) -> None:
        # 注：以前这里会硬要求 `<hub_dir>/scripts/analyze_company.py` 存在并把它
        # 存到 `self.script_path`，但 ``analyze_symbol()`` 走默认 hub 时根本不用
        # 这个文件（直接 import shared.stock_core.company_analysis.analyze），所以
        # 该校验会产生误导性错误。改成只在解析后保留 hub_dir，不做存在性检查；
        # 真的指向了不存在的外部 hub_dir 时，``stock_market_hub._load_external_module``
        # 会在第一次调用时报清楚的 ImportError。
        self.hub_dir = Path(hub_dir or self._resolve_hub_dir())

    def _resolve_hub_dir(self) -> str:
        env_dir = os.environ.get("STOCK_MARKET_HUB_DIR")
        if env_dir:
            return env_dir
        current = Path(__file__).resolve()
        repo_root = current.parents[3]
        return str(repo_root / "stock-market-hub")

    def _run(self, symbol: str, skip: str = "", ann_days: int = 30) -> dict:
        return analyze_symbol(symbol, hub_dir=str(self.hub_dir), skip=skip, ann_days=ann_days)

    def analyze(self, market: str, code: str, ann_days: int = 30, with_peers: bool = False, skip: str = "") -> dict:
        symbol = build_analysis_symbol(market, code)
        return analyze_symbol(symbol, hub_dir=str(self.hub_dir), ann_days=ann_days, with_peers=with_peers, skip=skip)

    def fetch_quote(self, market: str, code: str) -> dict:
        symbol = build_analysis_symbol(market, code)
        skip = ",".join(
            [
                "price_history",
                "info",
                "managers",
                "shareholders",
                "concepts",
                "announcements",
                "filings",
                "financial_summary",
                "peers",
            ]
        )
        data = self._run(symbol, skip=skip)
        return {
            "current": data.get("quote", {}).get("current"),
            "fetched_at": data.get("fetched_at"),
        }

    def market_board(self, market: str = "all_a", board: str = "gainers", top: int = 10) -> dict:
        return fetch_market_board(market=market, board=board, top=top)

    def get_market_regime(self, market: str) -> dict:
        """评估指定市场（``a`` / ``hk``）的整体 regime（RISK_OFF/NEUTRAL/RISK_ON）。

        失败时返回带 ``error`` 字段的占位字典；调用方应当只读 ``regime`` 字段就好，
        看不到该字段时按 NEUTRAL 处理。
        """
        return classify_market_regime(market)


class FXRateProvider:
    def get_rate(self, conn, from_currency: str, to_currency: str) -> Decimal:
        from_ccy = from_currency.upper()
        to_ccy = to_currency.upper()
        if from_ccy == to_ccy:
            return Decimal("1")
        if {from_ccy, to_ccy} == {"HKD", "CNY"}:
            configured = get_setting(conn, "fx.hkd_cny", "0.92") or "0.92"
            if os.environ.get("SPC_DISABLE_FX_HTTP") == "1":
                rate = Decimal(configured)
            else:
                try:
                    import urllib.request

                    with urllib.request.urlopen("https://open.er-api.com/v6/latest/HKD", timeout=8) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                    raw = payload["rates"]["CNY"]
                    rate = Decimal(str(raw))
                except Exception:  # noqa: BLE001
                    rate = Decimal(configured)
            if from_ccy == "HKD":
                return q_ratio(rate)
            return q_ratio(Decimal("1") / rate)
        raise ValueError(f"暂不支持汇率转换: {from_ccy}->{to_ccy}")

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP
from pathlib import Path
from typing import Iterable

# 让 spc_core 在被 main.py / tests 直接调用时也能 import 到 ``shared/stock_core``。
_SHARED = Path(__file__).resolve().parents[3] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from stock_core.symbols import parts_to_symbol  # noqa: E402
from stock_core.tz import LOCAL_TZ  # noqa: E402
QTY_PREC = Decimal("0.0001")
PRICE_PREC = Decimal("0.0001")
MONEY_PREC = Decimal("0.01")
RATIO_PREC = Decimal("0.000001")


# ── ETF 识别 ───────────────────────────────────────────────────
# 决策模块需要区分「个股」和「场内基金」，因为后者没有公告 / 管理层 / 股东等
# 维度，但有跟踪指数 / 主题集中度 / 跨境溢价等独有维度。

ETF_CATEGORY_CROSS_BORDER = "cross_border"        # QDII 跨境（513 / 164 等）
ETF_CATEGORY_STAR_MARKET = "star_market"          # 科创板 ETF（588 开头）
ETF_CATEGORY_CHINEXT = "chinext"                  # 创业板 ETF（159 开头部分）
ETF_CATEGORY_BROAD_OR_SECTOR = "broad_or_sector"  # 主板宽基 / 行业 ETF
ETF_CATEGORY_COMMODITY = "commodity"              # 商品 ETF（黄金 518 / 白银 / 原油等）
ETF_CATEGORY_BOND = "bond"                        # 债券 ETF（511 / 152xxx 等）
ETF_CATEGORY_OTHER = "other_fund"                 # 其它场内基金 / LOF / 货币 ETF


def is_etf(market: str, code: str) -> bool:
    """A 股场内基金（含 ETF / LOF）识别。

    代码段规则：
      - 上交所：``5xxxxx``（510-588）—— 含所有 ETF / 跨境 ETF / 黄金 ETF
        以及部分 LOF（如 502 系列）；9 开头是 B 股不在内
      - 深交所：``1xxxxx``（150-189）—— 含 159 ETF / 16x LOF / 18x 分级 LOF
        以及部分 ETF；0/3 开头是股票不在内

    本函数不细分 ETF / LOF / QDII：决策侧用 ``etf_category()`` 进一步分类。
    """
    if market != "a":
        return False
    if not code or not str(code).isdigit() or len(str(code)) != 6:
        return False
    return str(code).startswith(("5", "1"))


def etf_category(code: str) -> str | None:
    """识别 ETF 子类型（用于决策时给"跨境提示""商品 ETF 跳过资金面"等差异化处理）。

    分类不追求 100% 精确（很多 ETF 既是行业 ETF 又是宽基的混合），
    主要满足两类下游需求：
      1. **跨境**（cross_border）：A 股主力资金面对它没意义，要在分析里加提示
      2. **商品 / 债券**：资金面参考价值也不大，但风险性质不同

    返回 None 表示不是 ETF（或代码非法）。
    """
    if not code or not str(code).isdigit() or len(str(code)) != 6:
        return None
    c = str(code)
    # 跨境 QDII 系列
    if c.startswith("513"):
        return ETF_CATEGORY_CROSS_BORDER
    if c.startswith("164"):  # 深交所跨境 LOF（如 164906 标普 500）
        return ETF_CATEGORY_CROSS_BORDER
    # 科创板 ETF
    if c.startswith("588"):
        return ETF_CATEGORY_STAR_MARKET
    # 商品 ETF：黄金 518 / 白银 / 原油等也在 518-519 段
    if c.startswith(("518", "159980", "159981", "159985")):  # 黄金/原油/有色商品
        return ETF_CATEGORY_COMMODITY
    # 债券 ETF：511（上交所国债 / 利率债）/ 152xxx 深交所部分债基
    if c.startswith(("511", "152")):
        return ETF_CATEGORY_BOND
    # 创业板系列：159915 / 159949 / 159363 / 159381 / 159995 等
    if c.startswith("159"):
        return ETF_CATEGORY_CHINEXT
    # 主板宽基 / 行业 ETF：510 / 512 / 515 / 516（深交所行业 LOF）
    if c.startswith(("510", "512", "515", "516", "160", "161", "165")):
        return ETF_CATEGORY_BROAD_OR_SECTOR
    return ETF_CATEGORY_OTHER


def is_cross_border_etf(market: str, code: str) -> bool:
    """便利函数：是否跨境 QDII ETF（A 股主力资金面对它参考价值低）。"""
    return is_etf(market, code) and etf_category(code) == ETF_CATEGORY_CROSS_BORDER


def is_commodity_or_bond_etf(market: str, code: str) -> bool:
    """便利函数：是否商品 / 债券 ETF（资金面 / 主题分析意义有限）。"""
    if not is_etf(market, code):
        return False
    return etf_category(code) in (ETF_CATEGORY_COMMODITY, ETF_CATEGORY_BOND)


def data_dir() -> Path:
    base = os.environ.get("SPC_DATA_DIR")
    if base:
        p = Path(base).expanduser()
    else:
        p = Path.home() / ".local" / "share" / "stock-portfolio-copilot"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "portfolio.db"


def to_decimal(value: object, field: str) -> Decimal:
    try:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value).strip())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{field} 不是合法数字: {value}") from exc


def ensure_positive(value: Decimal, field: str) -> Decimal:
    if value <= 0:
        raise ValueError(f"{field} 必须大于 0")
    return value


def q_qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_PREC, rounding=ROUND_HALF_UP)


def q_price(value: Decimal) -> Decimal:
    return value.quantize(PRICE_PREC, rounding=ROUND_HALF_UP)


def q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PREC, rounding=ROUND_HALF_UP)


def q_ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PREC, rounding=ROUND_HALF_UP)


def hk_stamp_round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_UP)


def decimal_str(value: Decimal) -> str:
    return format(value, "f")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_user_time(value: str | None) -> str:
    if not value:
        return utc_now_iso()
    text = value.strip()
    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
    ]
    dt = None
    for fmt in candidates:
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=LOCAL_TZ)
            return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        except ValueError as exc:
            raise ValueError(f"无法识别的时间格式: {value}") from exc
    dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def to_local_display(iso_utc: str | None) -> str:
    if not iso_utc:
        return "-"
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def normalize_market(market: str) -> str:
    m = market.strip().lower()
    if m in {"a", "ashare", "a-share"}:
        return "a"
    if m in {"h", "hk", "hongkong", "hong-kong"}:
        return "hk"
    raise ValueError(f"不支持的 market: {market}")


def normalize_code(market: str, code: str) -> str:
    raw = code.strip().upper()
    if market == "a":
        digits = raw.replace("SH", "").replace("SZ", "").replace("BJ", "")
        if not digits.isdigit() or len(digits) != 6:
            raise ValueError(f"A 股代码必须是 6 位数字: {code}")
        return digits
    if market == "hk":
        digits = raw.replace("HK", "")
        if not digits.isdigit() or len(digits) > 5:
            raise ValueError(f"港股代码必须是 1-5 位数字: {code}")
        return digits.zfill(5)
    raise ValueError(f"不支持的 market: {market}")


def default_currency(market: str) -> str:
    return "CNY" if market == "a" else "HKD"


def build_analysis_symbol(market: str, code: str) -> str:
    """从 (market, code) 组装 hub 接受的 symbol，规则与 hub 的 normalize_symbol 反向对称。

    具体规则统一维护在 ``shared.stock_core.symbols.parts_to_symbol``。
    """
    return parts_to_symbol(market, code)


def format_percent(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def format_money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return decimal_str(q_money(value))


def render_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    str_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in str_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    lines = []
    lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for row in str_rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def pretty_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)

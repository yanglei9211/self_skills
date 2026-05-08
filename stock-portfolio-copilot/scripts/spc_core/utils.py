from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP
from pathlib import Path
from typing import Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


LOCAL_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone.utc
QTY_PREC = Decimal("0.0001")
PRICE_PREC = Decimal("0.0001")
MONEY_PREC = Decimal("0.01")
RATIO_PREC = Decimal("0.000001")


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
    if market == "hk":
        return f"HK{code}"
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"


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

"""Symbol normalization shared by stock-market-hub and stock-portfolio-copilot.

两套规则原本散落在两处：
  - stock-market-hub 的 ``normalize_symbol``：从 ``SZ300750`` / ``HK00700`` /
    ``BABA`` 解析为 (market, code, xq_symbol)
  - stock-portfolio-copilot 的 ``build_analysis_symbol``：从 (market, code) 反向
    组装出 hub 接受的 symbol

判断规则（A 股按首位选交易所、港股 5 位补零、美股按字母）是同一套，必须保持
一致——这里集中维护。
"""
from __future__ import annotations

import re


def normalize_symbol(symbol: str) -> tuple[str, str, str]:
    """解析任意 symbol 写法 -> ``(market, code, xueqiu_symbol)``。

    ``market``: ``'a'`` / ``'hk'`` / ``'us'``
    ``code``: 纯数字代码（A 股、港股）或 ticker（美股）
    ``xueqiu_symbol``: 雪球 API 用的标识（A 股带前缀，港股是 5 位数字）

    例：
        ``SZ300750`` -> ``('a', '300750', 'SZ300750')``
        ``HK00700``  -> ``('hk', '00700', '00700')``
        ``BABA``     -> ``('us', 'BABA', 'BABA')``
        裸 6 位数字按首位推断市场。
    """
    s = symbol.upper().strip()
    if s.startswith("SZ") and s[2:].isdigit():
        return "a", s[2:], s
    if s.startswith("SH") and s[2:].isdigit():
        return "a", s[2:], s
    if s.startswith("BJ") and s[2:].isdigit():
        return "a", s[2:], s
    if s.startswith("HK") and s[2:].isdigit():
        code = s[2:].zfill(5)
        return "hk", code, code
    if s.isdigit():
        if len(s) == 6:
            if s.startswith("6"):
                return "a", s, "SH" + s
            if s.startswith(("0", "3")):
                return "a", s, "SZ" + s
            if s.startswith(("4", "8")):
                return "a", s, "BJ" + s
        if len(s) == 5:
            return "hk", s, s
        if len(s) <= 4:
            return "hk", s.zfill(5), s.zfill(5)
    if re.match(r"^[A-Z]{1,5}$", s):
        return "us", s, s
    raise ValueError(f"无法识别的代码：{symbol}")


def parts_to_symbol(market: str, code: str) -> str:
    """从 ``(market, code)`` 组装出 hub 接受的 symbol。

    与 :func:`normalize_symbol` 反向对称：
        ``('a', '300750')`` -> ``'SZ300750'``
        ``('a', '600519')`` -> ``'SH600519'``
        ``('hk', '00700')`` -> ``'HK00700'``

    入参假定 ``code`` 已经是规范化后的纯数字代码（A 股 6 位、港股 5 位补零）。
    """
    if market == "hk":
        return f"HK{code}"
    if market == "a":
        if code.startswith("6"):
            return f"SH{code}"
        if code.startswith(("4", "8")):
            return f"BJ{code}"
        return f"SZ{code}"
    return code


def eastmoney_secid(market: str, code: str) -> str:
    """组装东方财富 secid。

    用于东财 push2/push2his 系列接口（fflow daykline、行情等）。secid 规则：
      - 上交所 A 股（6 开头）：``1.<code>``
      - 深交所 A 股（0/3 开头）：``0.<code>``
      - 港股：``116.<code>``（5 位补零）

    **不支持**：北交所（4/8 开头）和美股；调用方应在调用前自行跳过。
    """
    if market == "a":
        if code.startswith("6"):
            return f"1.{code}"
        if code.startswith(("0", "3")):
            return f"0.{code}"
        raise ValueError(
            f"eastmoney_secid 不支持北交所代码 {code}（4/8 开头）；"
            "调用方应跳过北交所标的"
        )
    if market == "hk":
        return f"116.{code.zfill(5)}"
    raise ValueError(f"eastmoney_secid 暂不支持 market={market!r}")

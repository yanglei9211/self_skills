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
            # 上交所：6 开头股票、9 开头 B 股、5 开头基金/ETF（510-588 等）
            if s.startswith(("6", "9", "5")):
                return "a", s, "SH" + s
            # 深交所：0 开头主板、3 开头创业板/科创、1 开头基金/ETF/LOF（150-189 等）
            if s.startswith(("0", "3", "1")):
                return "a", s, "SZ" + s
            # 北交所
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
        # 上交所：股票 6 / B股 9 / 基金 5（510/511/512/513/515/518/588 等场内基金 / ETF）
        if code.startswith(("6", "9", "5")):
            return f"SH{code}"
        # 北交所：4 / 8（注：必须放在深交所之前判断，因为 8 也是上交所 B 股老段位但已退市）
        if code.startswith(("4", "8")):
            return f"BJ{code}"
        # 深交所：股票 0 / 3 / 基金 1（150/159/160/161/164/165 等场内基金 / ETF / LOF）
        return f"SZ{code}"
    return code


def eastmoney_secid(market: str, code: str) -> str:
    """组装东方财富 secid。

    用于东财 push2/push2his 系列接口（fflow daykline、行情等）。secid 规则：
      - **上交所**（前缀 ``1.``）：股票 6 开头、B 股 9 开头、基金/ETF 5 开头
        （510 股票 ETF / 511 国债 ETF / 512 行业 ETF / 513 跨境 ETF /
         515 主题 ETF / 518 黄金 ETF / 588 科创板 ETF 等）
      - **深交所**（前缀 ``0.``）：股票 0/3 开头、基金/ETF 1 开头
        （150 LOF / 159 ETF / 160 LOF / 161 LOF / 164 / 165 ETF 等）
      - **港股**（前缀 ``116.``）：5 位代码补零
      - **北交所**（4/8 开头）：⚠️ 不支持，调用方应跳过
    """
    if market == "a":
        # 上交所：股票 6 / B 股 9 / 基金 5（510/511/512/513/515/518/588 等）
        if code.startswith(("6", "9", "5")):
            return f"1.{code}"
        # 深交所：股票 0/3 / 基金 1（150/159/160/161/164/165 等）
        if code.startswith(("0", "3", "1")):
            return f"0.{code}"
        # 北交所
        raise ValueError(
            f"eastmoney_secid 不支持北交所代码 {code}（4/8 开头）；"
            "调用方应跳过北交所标的"
        )
    if market == "hk":
        return f"116.{code.zfill(5)}"
    raise ValueError(f"eastmoney_secid 暂不支持 market={market!r}")

"""
雪球 API 客户端。

实测可用接口（2026-04，IP 段在大陆）：

[公开] 无需 token：
  - /service/v5/stock/screener/quote/list  全市场筛选器
  - /v5/stock/realtime/quotec.json         实时行情快照

[需要登录 cookie] —— 用户在浏览器登录雪球后导出 cookie 启用：
  - /v4/statuses/*                          用户帖子流
  - /v5/stock/news/snowflake_news.json      个股新闻
  - /v5/stock/news/feed.json                个股资讯
  - /statuses/hot/listV2.json               热门帖子

启用方式：
  把你浏览器（已登录雪球）的 cookie 写到 ~/.config/stock-market-hub/xueqiu.cookie
  格式：cl_a_token=xxx; xqat=xxx; xq_r_token=xxx; xq_id_token=xxx; ...
  （直接 Cookie header 整段拷贝即可）

  或者环境变量：export XUEQIU_COOKIE="..."

绕反爬要点：
  - 必须用 curl_cffi + Chrome 指纹
  - 必须先访问 https://xueqiu.com/ 拿到 acw_tc cookie，且用同一 Session 调 API
  - 必须带 Referer: https://xueqiu.com/

字段说明（screener 返回 list 项）：
    symbol              代码（SH600519 / SZ000001 / HK00700 / US:BABA）
    name                公司中文名（可能在 mapping_quote_current 里）
    current             最新价
    percent             今日涨跌幅 %
    chg                 涨跌额
    amount              成交额（元）
    volume              成交量（股/手）
    market_capital      总市值（元）
    float_market_capital 流通市值
    turnover_rate       换手率 %
    pe_ttm              滚动市盈率
    pb / pb_ttm         市净率
    ps                  市销率
    roe_ttm             净资产收益率 %
    eps                 每股收益
    dividend_yield      股息率 %
    net_profit_cagr     净利润增长率 %
    income_cagr         营业收入增长率 %
    main_net_inflows    主力净流入（元）
    north_net_inflow    北向资金净流入
    followers           雪球关注者数（散户热度指标）
    amplitude           振幅 %
    volume_ratio        量比
    issue_date_ts       上市日期（毫秒）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

from curl_cffi import requests as cffi


_HEADERS = {
    "Referer": "https://xueqiu.com/",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _load_user_cookie() -> str:
    """读取用户配置的 xueqiu cookie。

    优先级：环境变量 XUEQIU_COOKIE > ~/.config/stock-market-hub/xueqiu.cookie
    """
    env = os.environ.get("XUEQIU_COOKIE", "").strip()
    if env:
        return env
    cfg_path = Path.home() / ".config" / "stock-market-hub" / "xueqiu.cookie"
    if cfg_path.exists():
        try:
            txt = cfg_path.read_text(encoding="utf-8").strip()
            # 过滤注释行
            lines = [
                ln.strip() for ln in txt.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            return "; ".join(lines) if len(lines) > 1 else (lines[0] if lines else "")
        except Exception:
            pass
    return ""


class XueqiuClient:
    """带 acw_tc 预热的雪球客户端。一个进程内复用即可。"""

    BASE = "https://xueqiu.com"
    STOCK_BASE = "https://stock.xueqiu.com"

    # market / type 映射
    # market: cn / hk / us
    # type: sh_sz / kcb（科创板）/ gem（创业板）/ stib / hk / us / st
    MARKETS = {
        "all_a": ("cn", "sh_sz"),
        "kcb": ("cn", "kcb"),
        "gem": ("cn", "gem"),
        "stib": ("cn", "stib"),
        "st": ("cn", "st"),
        "hk": ("hk", "hk"),
        "us": ("us", "us"),
    }

    def __init__(self, impersonate: str = "chrome", user_cookie: str | None = None):
        self.session = cffi.Session(impersonate=impersonate)
        self._warmed = False
        # user_cookie：登录后的 cookie（用于热门帖/个股新闻等需要 token 的接口）
        self.user_cookie = user_cookie if user_cookie is not None else _load_user_cookie()
        self._has_login_cookie = bool(self.user_cookie)
        if self._has_login_cookie:
            print(f"[xueqiu] 已加载用户登录 cookie（{len(self.user_cookie)} 字符）", file=sys.stderr)

    def _warmup(self) -> None:
        if self._warmed:
            return
        try:
            self.session.get(f"{self.BASE}/", timeout=10)
            self._warmed = True
        except Exception as e:  # noqa: BLE001
            print(f"[xueqiu] warmup failed: {e}", file=sys.stderr)

    def _logged_in_headers(self) -> dict:
        """合并用户 cookie 后的 header（用于需要登录的接口）"""
        h = dict(_HEADERS)
        if self.user_cookie:
            h["Cookie"] = self.user_cookie
        return h

    @property
    def is_logged_in(self) -> bool:
        return self._has_login_cookie

    # -------- 行情快照 -------- #

    def quotes(self, symbols: Iterable[str]) -> list[dict]:
        """
        实时行情快照。symbols 例：['SH600519', 'SZ000001', 'HK00700']
        美股代码格式：'BABA' / 'AAPL'（不需要 US 前缀）
        """
        self._warmup()
        symbol_str = ",".join(symbols)
        url = f"{self.STOCK_BASE}/v5/stock/realtime/quotec.json"
        r = self.session.get(url, params={"symbol": symbol_str}, headers=_HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("data") or []

    def screener_by_symbols(
        self,
        symbols: list[str],
        market: str = "all_a",
        max_pages: int = 200,
        page_size: int = 90,
    ) -> list[dict]:
        """从 screener 全市场列表里筛出指定 symbols 的完整数据（含 PE/PB/ROE 等）。

        雪球 screener 单页 size 上限较小（实测约 30-100），全 A 有 5000+ 只，
        必须分页拉取并在内存中过滤。

        策略：
          - 按市值降序拉，先大盘股后小盘股
          - 边拉边匹配，匹配完所有 symbols 即提前停
        """
        wanted = set(symbols)
        if not wanted:
            return []
        results: list[dict] = []
        for page in range(1, max_pages + 1):
            try:
                data = self.screener(market, "market_capital", "desc", page_size, page=page)
            except Exception:
                break
            items = data.get("list") or []
            if not items:
                break
            hits = [q for q in items if q.get("symbol") in wanted]
            results.extend(hits)
            for h in hits:
                wanted.discard(h.get("symbol"))
            if not wanted:
                break
            count = data.get("count") or 0
            if page * page_size >= count:
                break
        return results

    # -------- 全市场筛选 -------- #

    def screener(
        self,
        market: str = "all_a",
        order_by: str = "percent",
        order: str = "desc",
        size: int = 20,
        page: int = 1,
        extras: dict | None = None,
    ) -> dict:
        """
        市场筛选器。返回 {"count": int, "list": [...]}.

        market: 见 MARKETS（all_a/kcb/gem/stib/st/hk/us）
        order_by: percent / amount / market_capital / turnover_rate / volume_ratio
                  / pe_ttm / pb / roe_ttm / followers / amplitude / main_net_inflows
                  / net_profit_cagr / income_cagr 等
        order: desc / asc
        extras: 附加筛选条件（如 {"market_capital_gte": 1e10}）
        """
        if market not in self.MARKETS:
            raise ValueError(f"unknown market: {market}; available: {list(self.MARKETS)}")
        m, t = self.MARKETS[market]
        self._warmup()
        url = f"{self.BASE}/service/v5/stock/screener/quote/list"
        params = {
            "order": order,
            "order_by": order_by,
            "market": m,
            "type": t,
            "size": size,
            "page": page,
        }
        if extras:
            params.update(extras)
        r = self.session.get(url, params=params, headers=_HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("data") or {}

    # -------- 便捷封装 -------- #

    def top_gainers(self, market: str = "all_a", size: int = 10) -> list[dict]:
        """涨幅榜"""
        return self.screener(market, "percent", "desc", size).get("list", [])

    def top_losers(self, market: str = "all_a", size: int = 10) -> list[dict]:
        """跌幅榜（找潜在风险）"""
        return self.screener(market, "percent", "asc", size).get("list", [])

    def top_amount(self, market: str = "all_a", size: int = 10) -> list[dict]:
        """成交额榜（市场关注度）"""
        return self.screener(market, "amount", "desc", size).get("list", [])

    def top_turnover(self, market: str = "all_a", size: int = 10) -> list[dict]:
        """换手率榜（资金活跃度，识别游资标的）"""
        return self.screener(market, "turnover_rate", "desc", size).get("list", [])

    def top_main_inflow(self, market: str = "all_a", size: int = 10) -> list[dict]:
        """主力净流入榜"""
        return self.screener(market, "main_net_inflows", "desc", size).get("list", [])

    def top_followers(self, market: str = "all_a", size: int = 10) -> list[dict]:
        """雪球关注者数榜（散户热度）"""
        return self.screener(market, "followers", "desc", size).get("list", [])

    # -------- 需要登录 cookie 的接口 -------- #

    def hot_topics(self, size: int = 20) -> list[dict]:
        """雪球热门帖。需要登录 cookie。"""
        if not self._has_login_cookie:
            print(
                "[xueqiu] hot_topics 需要登录 cookie。"
                "请在 ~/.config/stock-market-hub/xueqiu.cookie 配置",
                file=sys.stderr,
            )
            return []
        self._warmup()
        url = f"{self.BASE}/statuses/hot/listV2.json"
        r = self.session.get(
            url, params={"since_id": -1, "size": size},
            headers=self._logged_in_headers(), timeout=10,
        )
        try:
            data = r.json()
        except Exception:
            print(f"[xueqiu] hot_topics WAF 拦截或 cookie 失效（HTTP {r.status_code}）", file=sys.stderr)
            return []
        return data.get("items") or []

    def stock_news(self, symbol: str, size: int = 10) -> list[dict]:
        """个股新闻流。需要登录 cookie。symbol: 雪球格式（SH600519/00700/BABA）。"""
        if not self._has_login_cookie:
            return []
        self._warmup()
        url = f"{self.STOCK_BASE}/v5/stock/news/snowflake_news.json"
        r = self.session.get(
            url, params={"symbol": symbol, "page": 1, "size": size, "type": 1},
            headers=self._logged_in_headers(), timeout=10,
        )
        try:
            data = r.json()
            return (data.get("data") or {}).get("items") or []
        except Exception:
            return []

    def stock_comments(self, symbol: str, size: int = 10) -> list[dict]:
        """个股评论/讨论。需要登录 cookie。"""
        if not self._has_login_cookie:
            return []
        self._warmup()
        url = f"{self.STOCK_BASE}/v5/stock/news/feed.json"
        r = self.session.get(
            url, params={"symbol": symbol, "size": size},
            headers=self._logged_in_headers(), timeout=10,
        )
        try:
            data = r.json()
            return (data.get("data") or {}).get("items") or []
        except Exception:
            return []

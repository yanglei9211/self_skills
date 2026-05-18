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


# ─── 雪球 cookie 过期告警 ───────────────────────────────────────────
# 浏览器登录态 cookie 会过期（通常 7-30 天），过期后需要登录态的接口
# （capital/assort、capital/history、热门帖、个股新闻 等）会返回 OAuth 错误。
#
# 我们的策略：
#   - 第一次识别到 OAuth 错误时，打多行醒目 stderr 提示
#   - 标记 client._cookie_expired = True、_has_login_cookie = False
#   - 后续登录态接口直接快速返回（不再打告警），让 fund_flow 等 caller
#     自动降级到东财源
#   - 上层（fund_flow.get_fund_flow_summary）查 ``client.cookie_expired``
#     把告警透传到最终 summary 的 ``warnings`` 字段，让 Agent / render_text
#     在用户看到的报告末尾再次提醒
# ────────────────────────────────────────────────────────────────


class XueqiuCookieExpired(RuntimeError):
    """雪球登录 cookie 已过期 / 失效。

    本类只作为标记 / 类型提示，主入口（capital_* / stock_news / hot_topics）
    捕捉到 OAuth 错误后选择"打告警 + 软降级返回空"而不是抛异常，避免破坏
    Agent 工作流。如果调用方真的需要异常上抛，可以传 ``raise_on_expired=True``。
    """


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
    #
    # ⚠️ 雪球 screener API 实测行为（2026-05 验证）：
    #   - type=sh_sz / kcb / hk / us：服务端正确过滤
    #   - type=gem / stib / st：**服务端 silently 失败**，返回全 A 数据
    #     （count 字段一直是 5000 = 全 A 数量）
    #
    # 兼容方案：把 gem/stib/st 标记为"客户端 post-filter"，
    # ``screener()`` 检测到这些 market 时，自动改用 type=sh_sz 拉全 A，
    # 然后按代码前缀（gem/stib）或名称（st）在本地过滤。
    MARKETS = {
        "all_a": ("cn", "sh_sz"),
        "kcb": ("cn", "kcb"),
        "gem": ("cn", "gem"),
        "stib": ("cn", "stib"),
        "st": ("cn", "st"),
        "hk": ("hk", "hk"),
        "us": ("us", "us"),
    }

    # 需要客户端 post-filter 的 market：值为判定函数（接收 screener 单条 dict 返回 bool）
    # 注意：这两个市场的标的都在雪球 ``type=sh_sz`` 大池里（沪深 A），
    # 可以靠分页 + 前缀/名称过滤拿到正确子集。
    _POST_FILTER: dict[str, "callable"] = {
        "gem": lambda it: (it.get("symbol") or "").startswith(("SZ300", "SZ301")),
        "st": lambda it: (
            "ST" in (it.get("name") or "") or "*ST" in (it.get("name") or "")
        ),
    }

    # post-filter 模式下分页拉取的上限（避免极端慢查询）
    # 100 条/页 × 30 页 = 3000 只全 A 扫描；ST 股全 A ≈ 100-200 只，3000 足够
    _POST_FILTER_MAX_PAGES = 30
    _POST_FILTER_PAGE_SIZE = 100

    # 验证型市场：走 native screener，但额外检测返回是否含目标前缀；
    # 不含说明 native 此次不工作（雪球 API 不稳定），返回空 + warning 让 caller 知情。
    # 用于"沪深 A 大池外"的市场（如北交所 BJ4/8/9）。
    _VERIFY_PREFIX: dict[str, tuple[str, ...]] = {
        "stib": ("BJ4", "BJ8", "BJ9"),
    }

    def __init__(self, impersonate: str = "chrome", user_cookie: str | None = None):
        self.session = cffi.Session(impersonate=impersonate)
        self._warmed = False
        # user_cookie：登录后的 cookie（用于热门帖/个股新闻等需要 token 的接口）
        self.user_cookie = user_cookie if user_cookie is not None else _load_user_cookie()
        self._has_login_cookie = bool(self.user_cookie)
        # cookie 过期状态：被任何登录态接口检测到 OAuth 错误时翻转为 True
        # 上层（fund_flow.get_fund_flow_summary 等）可以读这个状态把告警透传到最终输出
        self.cookie_expired = False
        self._cookie_warning_shown = False
        if self._has_login_cookie:
            print(f"[xueqiu] 已加载用户登录 cookie（{len(self.user_cookie)} 字符）", file=sys.stderr)

    # ── cookie 过期检测 / 告警 ──────────────────────────────────────

    @staticmethod
    def _looks_like_oauth_error(payload: object) -> tuple[bool, str, str]:
        """判定 response payload 是否为 OAuth 错误（cookie 失效典型表现）。

        返回 (是否过期, error_code, error_description)。
        """
        if not isinstance(payload, dict):
            return False, "", ""
        ec = payload.get("error_code")
        # 雪球正常响应没有 error_code，或 error_code=0
        if ec in (None, 0, "0", ""):
            return False, "", ""
        desc = str(payload.get("error_description") or payload.get("error_data") or "")[:160]
        return True, str(ec), desc

    def _signal_cookie_expired(self, endpoint: str, code: str, desc: str) -> None:
        """标记 cookie 失效 + 打一次多行醒目告警。"""
        # 后续登录态接口直接快速返回（has_login_cookie 翻为 False）
        self.cookie_expired = True
        self._has_login_cookie = False

        if self._cookie_warning_shown:
            return
        self._cookie_warning_shown = True

        bar = "═" * 67
        msg = (
            "\n" + bar + "\n"
            "🚨  雪球登录 cookie 已过期 / 失效，需要重新导出\n"
            "\n"
            f"   失败接口：{endpoint}\n"
            f"   错误码：  {code}\n"
            f"   错误描述：{desc}\n"
            "\n"
            "   后续依赖 cookie 的雪球接口（capital/assort + capital/history +\n"
            "   stock_news + hot_topics）会自动降级；东财 fund_flow 主源仍\n"
            "   正常工作，但当日资金分层占比、雪球口径的 sum3/5/10/20 等\n"
            "   增强字段暂时拿不到。\n"
            "\n"
            "   👉 重新获取 cookie（约 1 分钟）：\n"
            "      1. 浏览器登录 https://xueqiu.com/\n"
            "      2. Cookie-Editor 插件 → Export → Header String\n"
            "      3. 粘到 ~/.config/stock-market-hub/xueqiu.cookie\n"
            "      4. chmod 600 ~/.config/stock-market-hub/xueqiu.cookie\n"
            "\n"
            "   详细步骤见 stock-market-hub/SKILL.md §0\n"
            + bar + "\n"
        )
        print(msg, file=sys.stderr, flush=True)

    def _request_logged_in(self, url: str, params: dict, endpoint_label: str) -> dict | None:
        """需要登录的 GET 接口统一入口。

        - 若 cookie 缺失 / 已被标记失效，直接 return None（不发请求、不打告警）
        - 发出请求后若返回 OAuth 错误，触发 _signal_cookie_expired 并 return None
        - 正常时返回 payload dict（caller 自己提取 ``data`` 字段）
        """
        if not self._has_login_cookie:
            return None
        self._warmup()
        try:
            r = self.session.get(
                url, params=params,
                headers=self._logged_in_headers(), timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[xueqiu] {endpoint_label} request failed: {e}", file=sys.stderr)
            return None
        try:
            payload = r.json()
        except Exception:
            print(
                f"[xueqiu] {endpoint_label} 返回非 JSON（HTTP {r.status_code}），"
                f"可能是 WAF 拦截或 cookie 失效",
                file=sys.stderr,
            )
            return None
        expired, code, desc = self._looks_like_oauth_error(payload)
        if expired:
            self._signal_cookie_expired(endpoint_label, code, desc)
            return None
        return payload

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

    def _screener_raw(
        self,
        m: str,
        t: str,
        order_by: str,
        order: str,
        size: int,
        page: int,
        extras: dict | None = None,
    ) -> dict:
        """直接命中雪球 screener API（不做 post-filter），caller 必须传 native 的 m/t。"""
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

        ⚠️ ``market in {"gem","stib","st"}`` 时走客户端 post-filter（雪球 API 服务端过滤
        不可靠），内部会自动从全 A 分页拉取再按规则过滤。代价是多次 API call，但保证
        结果正确。``page`` 参数在 post-filter 模式下被忽略（总是从第 1 页开始累积），
        ``extras`` 也不会被透传（避免与 post-filter 语义打架）。
        """
        if market not in self.MARKETS:
            raise ValueError(f"unknown market: {market}; available: {list(self.MARKETS)}")

        # ── 客户端 post-filter（gem/st，全 A 池里能查到）──
        if market in self._POST_FILTER:
            return self._screener_post_filter(market, order_by, order, size)

        # ── native 模式（雪球服务端过滤 work 的市场，如 kcb/all_a/hk/us）──
        m, t = self.MARKETS[market]
        data = self._screener_raw(m, t, order_by, order, size, page, extras)

        # ── 验证型市场（stib 北交所，雪球过滤不稳定，需检测结果）──
        if market in self._VERIFY_PREFIX:
            wanted_prefixes = self._VERIFY_PREFIX[market]
            items = data.get("list") or []
            kept = [it for it in items if (it.get("symbol") or "").startswith(wanted_prefixes)]
            dropped = len(items) - len(kept)
            if dropped > 0:
                print(
                    f"[xueqiu] market={market} 雪球 native 返回 {len(items)} 条但"
                    f"只有 {len(kept)} 条匹配前缀 {wanted_prefixes}；"
                    f"已丢弃 {dropped} 条 spillover（雪球 API 在 {market}+{order_by} "
                    "组合下过滤不可靠）",
                    file=sys.stderr,
                )
            data["list"] = kept
            data["count"] = len(kept)
            data["_verified"] = True
        return data

    def _screener_post_filter(
        self,
        market: str,
        order_by: str,
        order: str,
        size: int,
    ) -> dict:
        """对雪球不可靠过滤的市场（gem/stib/st）做客户端分页 + 过滤。

        策略：从全 A（type=sh_sz）按 order_by 排序分页拉取，每页 100 条，
        本地用 ``_POST_FILTER[market]`` 判定函数筛留，累计到 size 条或翻到
        ``_POST_FILTER_MAX_PAGES`` 上限为止。

        返回结构跟 ``_screener_raw`` 兼容，额外字段：
          - ``_post_filter``: True   — 标记本次是 post-filter 模式（caller 可据此提示用户）
          - ``_pages_scanned``: int  — 实际翻了多少页
          - ``_truncated``: bool     — 是否因为 max_pages 上限而提前停（可能漏掉低排名的标的）
        """
        filter_fn = self._POST_FILTER[market]
        accumulated: list[dict] = []
        pages_scanned = 0
        for page in range(1, self._POST_FILTER_MAX_PAGES + 1):
            pages_scanned = page
            raw = self._screener_raw(
                "cn", "sh_sz", order_by, order,
                self._POST_FILTER_PAGE_SIZE, page,
            )
            items = raw.get("list") or []
            if not items:
                break
            accumulated.extend(it for it in items if filter_fn(it))
            if len(accumulated) >= size:
                break
        truncated = (
            pages_scanned >= self._POST_FILTER_MAX_PAGES
            and len(accumulated) < size
        )
        if truncated:
            print(
                f"[xueqiu] market={market} post-filter 翻完 {pages_scanned} 页 "
                f"仅找到 {len(accumulated)} 个匹配，目标 size={size}；"
                "可能因为该过滤条件下符合条件的标的较少（如 ST 股的 followers 普遍偏低）",
                file=sys.stderr,
            )
        return {
            "count": len(accumulated),
            "list": accumulated[:size],
            "_post_filter": True,
            "_pages_scanned": pages_scanned,
            "_truncated": truncated,
        }

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
        """雪球热门帖。需要登录 cookie；cookie 失效时打告警并返回 []。"""
        payload = self._request_logged_in(
            f"{self.BASE}/statuses/hot/listV2.json",
            {"since_id": -1, "size": size},
            "hot_topics",
        )
        if payload is None:
            return []
        return payload.get("items") or []

    def stock_news(self, symbol: str, size: int = 10) -> list[dict]:
        """个股新闻流。需要登录 cookie。symbol: 雪球格式（SH600519/00700/BABA）。"""
        payload = self._request_logged_in(
            f"{self.STOCK_BASE}/v5/stock/news/snowflake_news.json",
            {"symbol": symbol, "page": 1, "size": size, "type": 1},
            "stock_news",
        )
        if payload is None:
            return []
        return (payload.get("data") or {}).get("items") or []

    def stock_comments(self, symbol: str, size: int = 10) -> list[dict]:
        """个股评论/讨论。需要登录 cookie。"""
        payload = self._request_logged_in(
            f"{self.STOCK_BASE}/v5/stock/news/feed.json",
            {"symbol": symbol, "size": size},
            "stock_comments",
        )
        if payload is None:
            return []
        return (payload.get("data") or {}).get("items") or []

    # -------- 主力资金流（雪球口径，补全东财 fflow 缺的字段） -------- #
    #
    # 三个接口都需要登录 cookie。cookie 过期时统一走 _request_logged_in 软降级：
    # 返回 None / 空 dict，让 caller 自己决定怎么 fallback。
    #
    # symbol 格式：A 股 'SZ300750' / 'SH600519'；港股 'HK00700'（雪球资金流仅 A 股有效）
    #
    # capital_assort: 当日资金分层（buy/sell × large/medium/small × 金额）
    #   data: {
    #     'buy_large', 'buy_medium', 'buy_small', 'buy_total',
    #     'sell_large', 'sell_medium', 'sell_small', 'sell_total',
    #     'buy_xlarge', 'sell_xlarge',   # 可能为 None（雪球 A 股网页只显示三档）
    #     'timestamp',
    #   }
    #   主力净额 = (buy_large + buy_xlarge) - (sell_large + sell_xlarge)
    #
    # capital_intraday: 当日分钟级流速
    #   items: [{'timestamp': ms, 'amount': 主力净流入(元), 'type': None}, ...]
    #
    # capital_history_daily: 日级历史（雪球网页"资金流"页面源数据，含 sum3/5/10/20 滚动）
    #   {'sum3', 'sum5', 'sum10', 'sum20', 'items': [{'timestamp': ms, 'amount': 元}, ...]}
    #   ⚠️ 注意：雪球 ``amount`` 的字段语义与东财 ``main`` 可能有差异（实测同一标的
    #   20 日累计差 2 倍量级），决策树**仍以东财 fflow 为主**，雪球 capital_history
    #   作为审计 / 显示对照源。

    def capital_assort(self, symbol: str) -> dict | None:
        """当日资金分层（A 股有效；港股雪球返回 data=None）。"""
        payload = self._request_logged_in(
            f"{self.STOCK_BASE}/v5/stock/capital/assort.json",
            {"symbol": symbol},
            "capital_assort",
        )
        if payload is None:
            return None
        return payload.get("data")

    def capital_intraday(self, symbol: str) -> list[dict]:
        """当日分钟级主力净流入序列（约 240 条 / 交易日）。"""
        payload = self._request_logged_in(
            f"{self.STOCK_BASE}/v5/stock/capital/flow.json",
            {"symbol": symbol},
            "capital_intraday",
        )
        if payload is None:
            return []
        return ((payload.get("data") or {}).get("items")) or []

    def capital_history_daily(self, symbol: str, count: int = 60) -> dict | None:
        """日级历史主力净流入 + 已聚合的 sum3/5/10/20。

        雪球网页"资金流"页面那张几十天柱状图的源数据。
        """
        payload = self._request_logged_in(
            f"{self.STOCK_BASE}/v5/stock/capital/history.json",
            {"symbol": symbol, "period": "day", "count": count},
            "capital_history",
        )
        if payload is None:
            return None
        return payload.get("data")

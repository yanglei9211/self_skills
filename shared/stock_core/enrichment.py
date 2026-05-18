"""个股决策辅助维度（v1）：3 个数据获取函数，给 spc decision 提供额外维度。

新增维度（在公司分析 payload 里以独立字段存放）：

  1. ``stock_news``       - 最近 N 天与本标的强相关的新闻（财联社电报为主）
  2. ``sector_strength``  - 标的所属板块/行业今日涨跌 + 个股 vs 板块强弱
  3. ``attention``        - 雪球关注者数（散户热度）+ 拥挤度判定

设计原则
--------
- **只贡献 reasons / risks 文案 + sources 展示**，不直接驱动 action 触发。
  让人 / LLM 能看到更全面归因（"今日大跌是因为板块弱"、"近期有相关重大新闻"、
  "散户热度过热"），但避免新维度直接改 buy/trim 决策树，确保现有决策可解释性。
- **失败优雅降级**：每个函数失败时返回带 ``error`` 字段的占位 dict，
  不抛异常，不阻塞 ``analyze`` 主流程。
- **HTTP 走统一层** ``shared/stock_core/http.py``。
- **缓存策略**：盘中 5 分钟 / 盘后 30 分钟（按市场判断），写在装饰器里。

调用方约定
----------
- ``analyze`` 在 ``tasks`` 字典里以 ``stock_news`` / ``sector_strength``
  / ``attention`` 三个 key 异步调用本模块，与现有维度并发。
- spc ``decision`` 在 ``Features`` 里读取 ``analysis["stock_news"]`` 等
  字段，缺数据时 collector 直接返回空 reasons / risks。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from typing import Any

from stock_core.cache import cached
from stock_core.http import fetch_json
from stock_core.tz import CN_TZ
from stock_core.xueqiu import XueqiuClient


__all__ = [
    "fetch_stock_news_relevance",
    "fetch_sector_strength",
    "fetch_xueqiu_attention",
]


# ─── 1. 个股相关新闻聚合 ──────────────────────────────────────────────


@cached(
    ttl=300, key_prefix="stock_news_pool", schema_version=3,
    skip_if=lambda r: not r,
)
def _fetch_cls_pool(limit: int = 50) -> list[dict]:
    """抓取财联社电报最近 N 条作为关键词匹配的池子。

    ⚠️ 实测：cls.cn 的 ``updateTelegraphList`` 接口**不支持** ``lastTime`` 翻页
    （第二页返回 count=0），单次最多返回 50 条（约覆盖盘中 1-2 小时）。所以
    ``limit > 50`` 时不会真正拉到更多——这是 cls 接口的固有限制。

    后续如要扩大池子，需要融合新浪滚动 / 巨潮搜索；v1 接受这个局限：
    个股新闻仅在该股名称在最近 50 条电报里被点名时命中。

    缓存 5 分钟，多个标的共享同一个池子，避免每只标的都打一次源。
    返回按时间倒序排列的 ``[{title, content, source, link, pubDate, level, ctime}, ...]``。
    """
    url = "https://www.cls.cn/nodeapi/updateTelegraphList"
    params = {
        "app": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "rn": min(limit, 50),
        "lastTime": 0,
        "last_time": 0,
    }
    items: list[dict] = []
    try:
        data = fetch_json(
            url,
            params=params,
            headers={"Referer": "https://www.cls.cn/telegraph"},
            timeout=8,
        )
        for it in (data.get("data", {}) or {}).get("roll_data", []) or []:
            ctime = it.get("ctime") or it.get("modified_time")
            try:
                ctime_int = int(ctime) if ctime else 0
                dt = datetime.fromtimestamp(ctime_int, tz=CN_TZ) if ctime_int else None
            except Exception:  # noqa: BLE001
                ctime_int = 0
                dt = None
            title = (it.get("title") or "").strip() or (it.get("brief") or "").strip()
            content = (it.get("content") or "").strip()
            if not title:
                title = content[:80] + ("..." if len(content) > 80 else "")
            link_id = it.get("id")
            items.append({
                "title": title,
                "content": content,
                "source": "财联社电报",
                "link": f"https://www.cls.cn/detail/{link_id}" if link_id else "",
                "pubDate": dt.isoformat() if dt else None,
                "level": it.get("level") or "B",  # A=红色重磅 B=普通
                "ctime": ctime_int,
            })
    except Exception as e:  # noqa: BLE001
        print(f"[stock_news] cls 抓取失败: {type(e).__name__}: {e}", file=sys.stderr)

    items.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
    return items


def _build_keywords(name: str, short_name: str, code: str, aliases: list[str] | None) -> list[str]:
    """构建匹配关键词列表，长词优先（避免 "宁德" 误命中 "宁德港"）。"""
    raw = []
    for w in (name, short_name, code, *(aliases or [])):
        w = (w or "").strip()
        if not w:
            continue
        # 财联社很少用代码精确匹配，但港股 5 位代码（01810）有时直接出现
        # 太短的关键词容易误命中，要求 >= 2 字符
        if len(w) >= 2:
            raw.append(w)
    # 去重 + 长词优先
    seen: set[str] = set()
    out: list[str] = []
    for w in sorted(raw, key=len, reverse=True):
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def fetch_stock_news_relevance(
    market: str,
    code: str,
    name: str = "",
    short_name: str = "",
    aliases: list[str] | None = None,
    days: int = 7,
    pool_size: int = 100,
    keep_top: int = 5,
) -> dict:
    """抓取最近 N 天财联社电报里与本标的强相关的新闻。

    Parameters
    ----------
    market : "a" / "hk" / "us"
        标的市场（暂未据此差异化处理，留作扩展）
    code : str
        股票代码（如 "600519" / "01810"）
    name, short_name, aliases : str / list[str]
        匹配关键词；长词优先匹配，避免 "宁德" 误命中
    days : int
        时间窗口；默认近 7 天
    pool_size : int
        财联社电报抓取池大小；默认 100 条
    keep_top : int
        最终保留的命中条数（按时间倒序）

    Returns
    -------
    dict
        ``{related_count, important_count, items, fetched_at, error?}``
    """
    out: dict[str, Any] = {
        "related_count": 0,
        "important_count": 0,
        "items": [],
        "fetched_at": datetime.now(CN_TZ).isoformat(),
    }
    keywords = _build_keywords(name, short_name, code, aliases)
    if not keywords:
        out["error"] = "no_keywords"
        return out

    pool = _fetch_cls_pool(limit=pool_size)
    if not pool:
        out["error"] = "empty_pool"
        return out

    # 时间窗口过滤
    cutoff = datetime.now(CN_TZ) - timedelta(days=days)
    hits: list[dict] = []
    important = 0
    for it in pool:
        text = (it.get("title") or "") + " " + (it.get("content") or "")
        if not any(kw in text for kw in keywords):
            continue
        # 时间过滤
        pub_iso = it.get("pubDate")
        if pub_iso:
            try:
                pub_dt = datetime.fromisoformat(pub_iso)
                if pub_dt < cutoff:
                    continue
            except Exception:  # noqa: BLE001
                pass
        hits.append(it)
        if (it.get("level") or "").upper() == "A":
            important += 1

    # 按 pubDate 倒序
    def _sort_key(x: dict) -> str:
        return x.get("pubDate") or ""

    hits.sort(key=_sort_key, reverse=True)

    out["related_count"] = len(hits)
    out["important_count"] = important
    out["items"] = [
        {
            "title": h.get("title"),
            "source": h.get("source"),
            "link": h.get("link"),
            "pubDate": h.get("pubDate"),
            "level": h.get("level"),
        }
        for h in hits[:keep_top]
    ]
    return out


# ─── 2. 板块涨跌强弱（同板块/同行业 peers 对比） ─────────────────────


@cached(ttl=300, key_prefix="sector_quotes", schema_version=1)
def _quote_symbols(xq_symbols: tuple[str, ...]) -> list[dict]:
    """批量取雪球 quote（用于同板块对比，缓存 5 分钟）。

    用 tuple 而不是 list 是为了让 ``cached`` 能 hash 参数。
    """
    if not xq_symbols:
        return []
    cli = XueqiuClient()
    try:
        return cli.quotes(list(xq_symbols))
    except Exception as e:  # noqa: BLE001
        print(f"[sector] xueqiu quotes failed: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def fetch_sector_strength(
    market: str,
    code: str,
    self_xq_symbol: str,
    peer_xq_symbols: list[str] | None = None,
    self_change_pct: float | None = None,
) -> dict:
    """计算标的"个股 vs 板块"的相对强弱。

    依赖：调用方提供 ``peer_xq_symbols``（雪球 symbol 格式，如 "SZ300750"），
    通常来自 ``analyze_company`` 的 ``peers`` 字段。如果没有 peers 数据，
    本函数会优雅降级返回 ``error="no_peers"``，不强制要求调用方先做 peers。

    Parameters
    ----------
    market : "a" / "hk"
        港股 peers 通常缺失，本函数对港股的支持有限
    code : str
        本标的代码（仅用于日志）
    self_xq_symbol : str
        本标的雪球 symbol（如 "SZ300750"），用于从 peer 列表里剔除自身
    peer_xq_symbols : list[str]
        同板块 peer 的雪球 symbol 列表（来自 analyze_company.peers）
    self_change_pct : float
        本标的当日涨跌；调用方传入避免重复请求；为 None 时从 quotes 取

    Returns
    -------
    dict
        ``{sector_avg_pct, peer_count, stock_vs_sector_pct, label, peers_sample, fetched_at, error?}``

        ``label``: ``leader`` (个股 -板块 ≥ +1.5%)
                 / ``stronger`` (≥ +0.5%)
                 / ``inline`` (-0.5% ~ +0.5%)
                 / ``weaker`` (-1.5% ~ -0.5%)
                 / ``laggard`` (≤ -1.5%)
    """
    out: dict[str, Any] = {
        "fetched_at": datetime.now(CN_TZ).isoformat(),
    }
    if not peer_xq_symbols:
        out["error"] = "no_peers"
        return out

    # 剔除自身 + 限制最多 10 个 peer，避免请求过多
    candidates = tuple(s for s in dict.fromkeys(peer_xq_symbols) if s and s != self_xq_symbol)[:10]
    if not candidates:
        out["error"] = "no_peers"
        return out

    quotes = _quote_symbols(candidates)
    pcts: list[float] = []
    samples: list[dict] = []
    for q in quotes:
        try:
            p = float(q.get("percent"))
        except (TypeError, ValueError):
            continue
        pcts.append(p)
        samples.append({"symbol": q.get("symbol"), "percent": p})

    if not pcts:
        out["error"] = "no_quote_data"
        return out

    sector_avg = sum(pcts) / len(pcts)
    out["sector_avg_pct"] = round(sector_avg, 2)
    out["peer_count"] = len(pcts)
    out["peers_sample"] = samples[:5]

    if self_change_pct is None:
        out["error"] = "self_pct_missing"
        return out

    diff = self_change_pct - sector_avg
    out["stock_vs_sector_pct"] = round(diff, 2)
    if diff >= 1.5:
        label = "leader"
    elif diff >= 0.5:
        label = "stronger"
    elif diff > -0.5:
        label = "inline"
    elif diff > -1.5:
        label = "weaker"
    else:
        label = "laggard"
    out["label"] = label
    return out


# ─── 3. 雪球关注度（拥挤度信号） ─────────────────────────────────────


# 关注度绝对阈值（拥挤度判定用）
_FOLLOWERS_HOT = 500_000        # 50 万以上：散户热度高
_FOLLOWERS_VERY_HOT = 1_500_000  # 150 万以上：拥挤度极高


def _fetch_followers(market: str, xq_symbol: str) -> int | None:
    """取单只标的的雪球关注者数。

    实现策略：
      - 雪球的 ``/v5/stock/quote.json`` 需要登录态 cookie 才返回 followers，
        匿名访问拿到 OAuth 错误。
      - ``screener_by_symbols`` 是公开 API，按市值降序分页，能同时取到
        followers / market_capital / pe / roe 等 30+ 字段。大盘股在前几页就能
        命中，小盘股最坏要拉到底（≥20 页 ≈ 1800 条）但仍然可行。

    本函数限制 ``max_pages=20``（前 1800 大），覆盖绝大多数标的；找不到时
    返回 None（不阻塞决策）。
    """
    cli = XueqiuClient()
    # ⚠️ 雪球 screener 返回的 symbol 不带 "HK" 前缀（例 "01810" 而非 "HK01810"），
    #    因此港股需要剥掉前缀做匹配；A 股 screener 返回 "SH600519" 带前缀，原样匹配
    if market == "a":
        market_filter = "all_a"
        match_symbol = xq_symbol  # 例 "SH600519"
    elif market == "hk":
        market_filter = "hk"
        match_symbol = xq_symbol[2:] if xq_symbol.upper().startswith("HK") else xq_symbol
    else:
        return None
    try:
        items = cli.screener_by_symbols([match_symbol], market=market_filter, max_pages=40, page_size=90)
        if not items:
            return None
        followers = items[0].get("followers") or items[0].get("follow_count")
        if followers is None:
            return None
        return int(followers)
    except Exception as e:  # noqa: BLE001
        print(f"[attention] {xq_symbol} followers 抓取失败: {type(e).__name__}: {e}", file=sys.stderr)
        return None


@cached(
    ttl=1800, key_prefix="attention", schema_version=2,
    skip_if=lambda r: bool(r.get("error")),
)
def fetch_xueqiu_attention(
    market: str,
    code: str,
    xq_symbol: str,
) -> dict:
    """获取雪球关注度 + 拥挤度判定。

    注：雪球关注者数是散户热度指标，**不是机构观点**——
    高关注度 + 价格新高 = 拥挤交易顶部信号；
    高关注度 + 价格底部 = 散户抄底情绪（未必反转，仍需结合资金面）。
    决策时只能作为"辅助警告"，不能反向作为 buy 信号。

    Returns
    -------
    dict
        ``{followers, level, crowded, fetched_at, error?}``

        ``level``: ``low`` (< 5万) / ``moderate`` (5-50万)
                 / ``hot`` (50-150万) / ``very_hot`` (≥ 150万)
        ``crowded``: bool，level >= ``hot`` 即视为拥挤
    """
    out: dict[str, Any] = {
        "fetched_at": datetime.now(CN_TZ).isoformat(),
    }
    followers = _fetch_followers(market, xq_symbol)
    if followers is None:
        out["error"] = "no_followers_data"
        return out

    out["followers"] = followers
    if followers >= _FOLLOWERS_VERY_HOT:
        level = "very_hot"
    elif followers >= _FOLLOWERS_HOT:
        level = "hot"
    elif followers >= 50_000:
        level = "moderate"
    else:
        level = "low"
    out["level"] = level
    out["crowded"] = level in ("hot", "very_hot")
    return out

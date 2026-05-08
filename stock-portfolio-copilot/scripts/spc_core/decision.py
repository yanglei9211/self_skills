from __future__ import annotations

from decimal import Decimal

from spc_core.ledger import latest_snapshots, list_watch, save_analysis_run
from spc_core.market_bridge import StockMarketHubProvider
from spc_core.portfolio import sync_portfolio
from spc_core.settings import capital_settings, ensure_defaults
from spc_core.utils import decimal_str, normalize_code, normalize_market, q_money, to_decimal


RISK_KEYWORDS = ("立案", "处罚", "诉讼", "减持", "退市", "停牌", "风险", "问询", "亏损", "爆雷")
POSITIVE_KEYWORDS = ("回购", "增持", "中标", "分红", "合作", "增长", "预增", "扭亏", "新品")
LOW_REGIMES = {"NEW_YTD_LOW", "NEW_52W_LOW", "NEW_ALL_TIME_LOW"}
HIGH_REGIMES = {"NEW_YTD_HIGH", "NEW_52W_HIGH", "NEW_ALL_TIME_HIGH"}


def _select_targets(conn, scope: str, market: str | None, code: str | None) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []
    if market and code:
        return [(market, code, "single")]
    if scope in {"holdings", "all"}:
        for snap in latest_snapshots(conn):
            targets.append((snap["market"], snap["code"], "holdings"))
    if scope in {"watchlist", "all"}:
        existing = {(m, c) for m, c, _ in targets}
        for item in list_watch(conn):
            key = (item["market"], item["code"])
            if key not in existing:
                targets.append((item["market"], item["code"], "watchlist"))
    return targets


def _decision_from_analysis(snapshot: dict | None, analysis: dict, capital_total: Decimal, max_single_pct: Decimal) -> dict:
    quote = analysis.get("quote") or {}
    announcements = analysis.get("announcements") or []
    price_history = analysis.get("price_history") or {}
    current = quote.get("current")
    regime = price_history.get("regime")
    titles = [item.get("title", "") for item in announcements]
    risk_hits = sum(1 for title in titles if any(k in title for k in RISK_KEYWORDS))
    positive_hits = sum(1 for title in titles if any(k in title for k in POSITIVE_KEYWORDS))

    qty = Decimal(snapshot["qty"]) if snapshot else Decimal("0")
    avg_cost = to_decimal(snapshot["avg_cost_price"], "avg_cost_price") if snapshot else Decimal("0")
    position_value_cny = to_decimal(snapshot["position_value_cny"] or "0", "position_value_cny") if snapshot else Decimal("0")
    weight_pct = Decimal("0")
    if capital_total > 0 and position_value_cny > 0:
        weight_pct = q_money(position_value_cny / capital_total * Decimal("100"))

    action = "watch"
    confidence = Decimal("0.55")
    reasons = []
    risks = []

    if regime in LOW_REGIMES:
        risks.append("价格处于破位或创新低区间")
    if regime in HIGH_REGIMES:
        reasons.append("价格处于强势区间或突破状态")
    if risk_hits:
        risks.append(f"近期待公告标题中命中 {risk_hits} 个风险关键词")
    if positive_hits:
        reasons.append(f"近期待公告标题中命中 {positive_hits} 个正向关键词")

    if qty > 0:
        action = "hold"
        if risk_hits >= 2 or regime in LOW_REGIMES:
            action = "trim" if qty > 0 else "avoid"
            confidence = Decimal("0.72")
        if current is not None and avg_cost > 0:
            cur_price = to_decimal(current, "current")
            if cur_price < avg_cost * Decimal("0.92") and risk_hits:
                action = "sell"
                confidence = Decimal("0.78")
                reasons.append("现价明显低于持仓成本且伴随风险公告")
            elif weight_pct > max_single_pct and cur_price > avg_cost:
                action = "trim"
                confidence = Decimal("0.70")
                reasons.append("当前仓位超过单票上限且已有浮盈")
        if action == "hold" and not reasons:
            reasons.append("当前没有触发明显的减仓或卖出信号")
    else:
        action = "watch"
        if risk_hits >= 2 or regime in LOW_REGIMES:
            action = "avoid"
            confidence = Decimal("0.72")
        elif positive_hits > risk_hits and regime not in LOW_REGIMES:
            action = "buy"
            confidence = Decimal("0.64")
            reasons.append("正向信号多于风险信号，且价格结构未明显走坏")
        else:
            reasons.append("建议继续跟踪，等待更清晰的触发条件")

    if not reasons and current is not None:
        reasons.append(f"最新价为 {current}")

    sources = []
    for item in announcements[:3]:
        title = item.get("title") or "公告"
        pdf = item.get("pdf_url") or "-"
        sources.append(f"{item.get('date')}: {title} ({pdf})")
    if not sources:
        sources.append("analyze_company.quote")
    sources.append(f"price_history.regime={regime or '-'}")

    return {
        "action": action,
        "confidence": decimal_str(confidence),
        "reasoning": reasons,
        "risks": risks,
        "sources": sources,
        "weight_pct": decimal_str(weight_pct),
    }


def _digits_from_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol) if ch.isdigit())


def _discover_opportunities(results: list[dict], analysis_cache: dict[tuple[str, str], dict], provider) -> list[dict]:
    universe = {(item["market"], item["code"]) for item in results}
    opportunities: dict[tuple[str, str], dict] = {}

    for item in results:
        market = item["market"]
        code = item["code"]
        analysis = analysis_cache.get((market, code), {})
        peers = analysis.get("peers") or []
        concepts = analysis.get("concepts") or []
        if market != "a":
            continue
        for peer in peers:
            sym = peer.get("symbol", "")
            peer_code = _digits_from_symbol(sym)
            if not peer_code:
                continue
            key = ("a", peer_code)
            if key in universe:
                continue
            score = Decimal("0")
            percent = peer.get("percent")
            ytd = peer.get("ytd_pct")
            inflow = peer.get("main_inflow_yi")
            if isinstance(percent, (int, float)) and percent > 0:
                score += Decimal("1")
            if isinstance(ytd, (int, float)) and ytd > 0:
                score += Decimal("1")
            if isinstance(inflow, (int, float)) and inflow > 0:
                score += Decimal("1")
            if score <= 0:
                continue
            reason_parts = [f"来自你关注标的 {code} 的同概念/同板块 peer"]
            if concepts:
                reason_parts.append("概念：" + " / ".join(concepts[:3]))
            if isinstance(percent, (int, float)):
                reason_parts.append(f"当日涨跌幅 {percent:+.2f}%")
            opportunities[key] = {
                "market": "a",
                "code": peer_code,
                "name": peer.get("name") or sym,
                "kind": "peer",
                "score": decimal_str(score),
                "reasons": reason_parts,
            }

    boards_to_check = [("all_a", "gainers"), ("all_a", "main_inflow"), ("hk", "gainers")]
    for board_market, board_name in boards_to_check:
        try:
            board_data = provider.market_board(board_market, board_name, top=8)
        except Exception:  # noqa: BLE001
            continue
        for stock in board_data.get("items", []):
            sym = stock.get("symbol", "")
            name = stock.get("name") or sym
            if board_market == "hk":
                market = "hk"
                code = _digits_from_symbol(sym).zfill(5)
            else:
                market = "a"
                code = _digits_from_symbol(sym)
            if not code:
                continue
            key = (market, code)
            if key in universe or key in opportunities:
                continue
            market_cap_yi = stock.get("market_cap_yi")
            amount_yi = stock.get("amount_yi")
            if board_market == "hk":
                if isinstance(market_cap_yi, (int, float)) and market_cap_yi < 150:
                    continue
                if isinstance(amount_yi, (int, float)) and amount_yi < 3:
                    continue
            else:
                if isinstance(market_cap_yi, (int, float)) and market_cap_yi < 80:
                    continue
                if isinstance(amount_yi, (int, float)) and amount_yi < 5:
                    continue
            reason_parts = [f"全市场 {board_name} 榜靠前"]
            percent = stock.get("percent")
            if isinstance(percent, (int, float)):
                reason_parts.append(f"当日涨跌幅 {percent:+.2f}%")
            main_yi = stock.get("main_yi")
            if isinstance(main_yi, (int, float)) and main_yi > 0:
                reason_parts.append(f"主力净流入 {main_yi:+.2f} 亿")
            opportunities[key] = {
                "market": market,
                "code": code,
                "name": name,
                "kind": "market",
                "score": "1",
                "reasons": reason_parts,
            }

    ranked = sorted(
        opportunities.values(),
        key=lambda item: (
            1 if item["kind"] == "peer" else 0,
            Decimal(item["score"]),
            item["market"],
            item["code"],
        ),
        reverse=True,
    )
    return ranked[:8]


def analyze_now(conn, scope: str, market: str | None = None, code: str | None = None, analysis_provider=None) -> dict:
    ensure_defaults(conn)
    provider = analysis_provider or StockMarketHubProvider()
    if market:
        market = normalize_market(market)
    if market and code:
        code = normalize_code(market, code)
    sync_portfolio(conn, market=market, code=code, analysis_provider=provider)
    snapshots = {(item["market"], item["code"]): item for item in latest_snapshots(conn)}
    caps = capital_settings(conn)
    capital_total = to_decimal(caps["total_cny"] or "0", "capital.total_cny")
    if capital_total == 0:
        total_value = sum(
            (to_decimal(snap["position_value_cny"] or "0", "position_value_cny") for snap in snapshots.values()),
            Decimal("0"),
        )
        capital_total = q_money(total_value)
    max_single_pct = to_decimal(caps["max_single_position_pct"], "capital.max_single_position_pct")

    results = []
    analysis_cache: dict[tuple[str, str], dict] = {}
    targets = _select_targets(conn, scope, market, code)
    for tgt_market, tgt_code, tgt_scope in targets:
        analysis = provider.analyze(tgt_market, tgt_code, with_peers=(tgt_market == "a"))
        analysis_cache[(tgt_market, tgt_code)] = analysis
        snapshot = snapshots.get((tgt_market, tgt_code))
        decision = _decision_from_analysis(snapshot, analysis, capital_total, max_single_pct)
        results.append(
            {
                "market": tgt_market,
                "code": tgt_code,
                "scope": tgt_scope,
                "position": snapshot,
                "market_data": {
                    "last_price": analysis.get("quote", {}).get("current"),
                    "change_pct": analysis.get("quote", {}).get("percent"),
                    "as_of": analysis.get("fetched_at"),
                },
                "decision": decision,
            }
        )

    opportunities = _discover_opportunities(results, analysis_cache, provider)

    payload = {
        "scope": scope,
        "requested_market": market,
        "requested_code": code,
        "results": results,
        "opportunities": opportunities,
        "capital_total_cny": decimal_str(capital_total),
        "max_single_position_pct": decimal_str(max_single_pct),
    }
    save_analysis_run(conn, scope, market, code, payload)
    return payload


def render_analysis_text(payload: dict) -> str:
    lines = []
    for item in payload.get("results", []):
        market = item["market"]
        code = item["code"]
        position = item.get("position") or {}
        decision = item["decision"]
        lines.append(f"标的：{market.upper()} {code}")
        lines.append(f"建议：{decision['action']}")
        if item["market_data"].get("as_of"):
            lines.append(f"时间：截至 {item['market_data']['as_of']}")
        if position:
            lines.append(
                f"持仓：{position.get('qty')} 股，摊薄成本 {position.get('avg_cost_price')} {position.get('currency')}"
            )
        lines.append(f"置信度：{decision['confidence']}")
        lines.append("理由：")
        for reason in decision["reasoning"]:
            lines.append(f"- {reason}")
        if decision["risks"]:
            lines.append("风险：")
            for risk in decision["risks"]:
                lines.append(f"- {risk}")
        lines.append("数据来源：")
        for source in decision["sources"]:
            lines.append(f"- {source}")
        lines.append("")
    opportunities = payload.get("opportunities") or []
    if opportunities:
        lines.append("可额外关注的标的：")
        for item in opportunities:
            lines.append(f"- {item['market'].upper()} {item['code']} {item['name']}：{'；'.join(item['reasons'])}")
    return "\n".join(lines).strip()

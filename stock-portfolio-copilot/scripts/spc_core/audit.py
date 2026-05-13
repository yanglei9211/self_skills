"""决策审计 / 跨 session 复盘 渲染层。

支撑四个 CLI 子命令：

  spc explain  —— 展开单个标的的 confidence_trace（"为什么 0.78"）
  spc log      —— 列出最近 N 次 analyze_now 概览
  spc show     —— 按 analysis-id 显示某次完整负载
  spc diff     —— 对比同一只标的在两个时点的决策 + 期间公告增量

设计：
  - 所有"取数据"统一走 spc_core.ledger 的 list_analysis_runs / get_analysis_run_by_id
  - 这里只负责文本渲染，不直接 SQL
  - 把单条 decision 的渲染抽成 render_decision_block，让 explain / show / diff 共用
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from spc_core.ledger import (
    find_analysis_runs_covering_symbol,
    get_analysis_run_by_id,
    latest_analysis_run,
    list_analysis_runs,
)
from spc_core.utils import normalize_code, normalize_market, to_local_display


# ── 时间窗口解析 ─────────────────────────────────────────────────

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([hdwm])\s*$", re.IGNORECASE)


def parse_since(value: str | None) -> str | None:
    """把 "7d" / "12h" / "3w" / "1m" / "2026-05-06" / ISO 字符串 解析成 ISO UTC 字符串。

    Returns:
        ISO 8601 UTC 字符串（同 utc_now_iso 格式），caller 直接喂给 SQL 比较。
        None 输入 → None 输出。
    """
    if not value:
        return None
    text = value.strip()

    # 相对时长：3d / 12h / 2w / 1m
    m = _DURATION_RE.match(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "h":
            delta = timedelta(hours=n)
        elif unit == "d":
            delta = timedelta(days=n)
        elif unit == "w":
            delta = timedelta(weeks=n)
        else:  # 'm' → 30 天（粗略，避免引入 dateutil）
            delta = timedelta(days=30 * n)
        return (datetime.now(timezone.utc) - delta).replace(microsecond=0).isoformat()

    # YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()

    # 完整 ISO
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"无法识别的时间格式: {value!r}；支持 '7d' / '12h' / '2w' / "
            "'2026-05-06' / ISO 8601"
        ) from exc


# ── confidence_trace 渲染 ────────────────────────────────────────

def render_confidence_trace(decision: dict, indent: str = "  ") -> str:
    """把 decision['confidence_trace'] 渲染成阶梯式可读文本。"""
    trace = decision.get("confidence_trace") or []
    if not trace:
        return f"{indent}（无 confidence_trace，可能是旧版 analyze_run 数据）"
    lines = [f"{indent}置信度构成（从 0.55 起点逐步累加）："]
    for i, step in enumerate(trace, 1):
        value = step.get("value", 0.0)
        delta = step.get("delta", 0.0)
        if i == 1:
            delta_str = f"  起点  "
        else:
            sign = "+" if delta >= 0 else ""
            delta_str = f"  {sign}{delta:.2f}  "
        action = step.get("action", "?")
        rule = step.get("rule", "")
        lines.append(f"{indent}  [{i}] {value:.2f}  ({action:<6}) {delta_str}{rule}")
    return "\n".join(lines)


# ── 单个 decision / result 渲染（explain / show / diff 共用） ─────

def render_decision_block(item: dict, *, show_trace: bool = True) -> str:
    """渲染 results[i]：标的标识 + market_data + decision + 可选 confidence_trace。"""
    market = item.get("market", "?")
    code = item.get("code", "?")
    name = item.get("name") or ""
    position = item.get("position") or {}
    decision = item.get("decision") or {}
    md = item.get("market_data") or {}

    lines = [f"标的：{str(market).upper()} {code}" + (f"  {name}" if name else "")]
    action_label = decision.get("action_label") or decision.get("action") or "?"
    lines.append(f"建议：{action_label}（{decision.get('action')}）")
    if decision.get("description"):
        lines.append(f"说明：{decision['description']}")
    if md.get("last_price") is not None:
        chg = md.get("change_pct")
        chg_str = f"（{float(chg):+.2f}%）" if chg is not None else ""
        lines.append(f"最新价：{md['last_price']}{chg_str}")
    if md.get("as_of"):
        lines.append(f"时间：截至 {md['as_of']}")
    if position and position.get("qty"):
        lines.append(
            f"持仓：{position.get('qty')} 股，摊薄成本 {position.get('avg_cost_price')} "
            f"{position.get('currency')}"
        )
    lines.append(f"置信度：{decision.get('confidence')}")
    if decision.get("reasoning"):
        lines.append("理由：")
        for r in decision["reasoning"]:
            lines.append(f"  - {r}")
    if decision.get("risks"):
        lines.append("风险：")
        for r in decision["risks"]:
            lines.append(f"  - {r}")
    if decision.get("sources"):
        lines.append("数据来源：")
        for s in decision["sources"]:
            lines.append(f"  - {s}")
    if show_trace:
        lines.append("")
        lines.append(render_confidence_trace(decision))
    return "\n".join(lines)


# ── spc explain ──────────────────────────────────────────────────

def _resolve_run(conn, account_id: int, analysis_id: int | None) -> dict | None:
    if analysis_id is not None:
        return get_analysis_run_by_id(conn, account_id, analysis_id)
    return latest_analysis_run(conn, account_id)


def render_explain(
    conn, account_id: int, account_slug: str,
    analysis_id: int | None, market: str | None, code: str | None,
) -> str:
    """渲染单次 analyze_now 里指定标的（或全部）的 confidence_trace。"""
    run = _resolve_run(conn, account_id, analysis_id)
    if not run:
        return f"账户 {account_slug} 暂无 analyze_now 历史记录。先跑一次 `spc analyze now`。"
    payload = run.get("payload") or {}
    results = payload.get("results") or []
    if not results:
        return f"analysis_run id={run.get('id')} 的 payload 里没有 results"

    if market and code:
        norm_market = normalize_market(market)
        norm_code = normalize_code(norm_market, code)
        results = [r for r in results if r.get("market") == norm_market and r.get("code") == norm_code]
        if not results:
            return f"analysis_run id={run.get('id')} 里没有 {norm_market.upper()} {norm_code} 这只标的"

    header = (
        f"═══ analysis_run id={run.get('id')} "
        f"@ {to_local_display(run.get('run_time'))}  "
        f"(scope={payload.get('scope', '?')}) ═══"
    )
    blocks = [header]
    for item in results:
        blocks.append("")
        blocks.append(render_decision_block(item, show_trace=True))
    return "\n".join(blocks)


# ── spc log ──────────────────────────────────────────────────────

def render_log(
    conn, account_id: int, account_slug: str,
    market: str | None, code: str | None,
    since: str | None, until: str | None, limit: int,
) -> str:
    """列表渲染最近 N 次 analyze_now。"""
    since_iso = parse_since(since)
    until_iso = parse_since(until)
    if market and code:
        runs = find_analysis_runs_covering_symbol(
            conn, account_id, market, code, since=since_iso, until=until_iso, limit=limit,
        )
        # find_analysis_runs_covering_symbol 已经把 payload 解出来；
        # 这里只需要列表 metadata，所以裁掉 payload 节省渲染
        light_runs = []
        for r in runs:
            light_runs.append({
                "id": r["id"],
                "scope": r["scope"],
                "market": r["market"],
                "code": r["code"],
                "run_time": r["run_time"],
                "action_for_target": _extract_target_action(r["payload"], market, code),
            })
        runs = light_runs
    else:
        rows = list_analysis_runs(
            conn, account_id, market=market, code=code,
            since=since_iso, until=until_iso, limit=limit,
        )
        runs = [{**r, "action_for_target": None} for r in rows]

    if not runs:
        scope_desc = f"{market.upper()} {code}" if (market and code) else "该账户"
        return f"{scope_desc} 在指定时间范围内没有 analyze_now 记录"

    target_label = f"{market.upper()} {code} 的建议" if (market and code) else "scope/范围"
    lines = [
        f"账户 {account_slug} 最近 {len(runs)} 次 analyze_now"
        + (f"（含 {market.upper()} {code}）" if market and code else ""),
        "",
        f"{'ID':>5}  {'时间':<20}  {target_label}",
        "─" * 70,
    ]
    for r in runs:
        ts = to_local_display(r["run_time"])
        if market and code:
            note = r.get("action_for_target") or "-"
        else:
            scope = r.get("scope") or "-"
            mk = r.get("market") or "*"
            cd = r.get("code") or "*"
            note = f"{scope:<10}  market={mk}  code={cd}"
        lines.append(f"{r['id']:>5}  {ts:<20}  {note}")
    return "\n".join(lines)


def _extract_target_action(payload: dict, market: str, code: str) -> str | None:
    """从 payload['results'] 里找指定 symbol 的 (action, confidence) 摘要。"""
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    for r in payload.get("results", []):
        if r.get("market") == norm_market and r.get("code") == norm_code:
            d = r.get("decision") or {}
            return f"{d.get('action_label', d.get('action', '?'))} ({d.get('confidence', '?')})"
    return None


# ── spc show ─────────────────────────────────────────────────────

def render_show(conn, account_id: int, account_slug: str, analysis_id: int) -> str:
    """显示单次 analyze_now 的完整内容（含所有标的的 decision + trace）。"""
    run = get_analysis_run_by_id(conn, account_id, analysis_id)
    if not run:
        return f"账户 {account_slug} 没有 id={analysis_id} 的 analysis_run"
    payload = run.get("payload") or {}
    results = payload.get("results") or []
    blocks = [
        f"═══ analysis_run id={run['id']}  "
        f"@ {to_local_display(run['run_time'])}  scope={payload.get('scope', '?')} ═══",
        "",
        f"账户：{account_slug}  "
        f"资金上限 {payload.get('capital_total_cny', '-')} CNY  "
        f"单票上限 {payload.get('max_single_position_pct', '-')}%",
    ]

    # market_regime（如有）
    market_regime = payload.get("market_regime") or {}
    if market_regime:
        blocks.append("")
        blocks.append("== 大盘风险偏好 ==")
        for mkt_key, mkt_label in (("a", "A 股"), ("hk", "港股")):
            mr = market_regime.get(mkt_key)
            if mr:
                blocks.append(f"  {mkt_label}：{mr.get('regime', '-')}")

    for i, item in enumerate(results, 1):
        blocks.append("")
        blocks.append(f"── 标的 {i}/{len(results)} ──")
        blocks.append(render_decision_block(item, show_trace=True))

    opportunities = payload.get("opportunities") or []
    if opportunities:
        blocks.append("")
        blocks.append("── 可额外关注的标的 ──")
        for o in opportunities:
            blocks.append(
                f"  - {str(o.get('market', '?')).upper()} {o.get('code')} "
                f"{o.get('name', '')}：{'；'.join(o.get('reasons', []))}"
            )
    return "\n".join(blocks)


# ── spc diff ─────────────────────────────────────────────────────

def render_diff(
    conn, account_id: int, account_slug: str,
    market: str, code: str,
    since: str | None = None, until: str | None = None,
    between: tuple[str, str] | None = None,
) -> str:
    """对比同一只标的在不同时点的决策。

    两种用法：
      - --since 7d / --until: 取窗口内**最早**与**最晚**两次 run 对比
      - --between 2026-05-06 2026-05-13: 取最接近这两个日期的 run 对比

    如果窗口内只有 1 条记录，无法 diff，返回提示。
    """
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)

    if between:
        # 取 between[0] / between[1] 各自附近最近的 run
        a_iso = parse_since(between[0])
        b_iso = parse_since(between[1])
        runs_a = find_analysis_runs_covering_symbol(
            conn, account_id, norm_market, norm_code, since=a_iso, limit=1,
        )
        runs_b = find_analysis_runs_covering_symbol(
            conn, account_id, norm_market, norm_code, since=b_iso, limit=1,
        )
        if not runs_a or not runs_b:
            return f"{norm_market.upper()} {norm_code} 在 {between[0]} / {between[1]} 附近没有可用记录"
        old, new = runs_a[0], runs_b[0]
    else:
        since_iso = parse_since(since)
        until_iso = parse_since(until)
        runs = find_analysis_runs_covering_symbol(
            conn, account_id, norm_market, norm_code,
            since=since_iso, until=until_iso, limit=200,
        )
        if len(runs) < 2:
            return (
                f"{norm_market.upper()} {norm_code} 在指定窗口内只有 {len(runs)} 条记录，"
                "无法 diff（至少需要 2 条）"
            )
        # runs 按 id desc 排，第一个是最新、最后一个是最早
        new = runs[0]
        old = runs[-1]

    return _render_diff_pair(old, new, norm_market, norm_code, account_slug)


def _render_diff_pair(
    old: dict, new: dict, market: str, code: str, account_slug: str,
) -> str:
    old_payload = old.get("payload") or {}
    new_payload = new.get("payload") or {}
    old_item = _pick_item(old_payload, market, code)
    new_item = _pick_item(new_payload, market, code)
    if not old_item or not new_item:
        return f"在 analysis_run id={old['id']} / id={new['id']} 之一里找不到 {market.upper()} {code}"

    old_d = old_item.get("decision") or {}
    new_d = new_item.get("decision") or {}
    old_md = old_item.get("market_data") or {}
    new_md = new_item.get("market_data") or {}

    lines = [
        f"═══ {market.upper()} {code} 决策 diff（账户 {account_slug}）═══",
        "",
        f"  旧: analysis_run id={old['id']}  @ {to_local_display(old['run_time'])}",
        f"  新: analysis_run id={new['id']}  @ {to_local_display(new['run_time'])}",
        "",
        "── 决策变化 ──",
        f"  action       {_fmt_pair(old_d.get('action'), new_d.get('action'))}",
        f"  action_label {_fmt_pair(old_d.get('action_label'), new_d.get('action_label'))}",
        f"  confidence   {_fmt_pair(old_d.get('confidence'), new_d.get('confidence'))}",
    ]

    # 行情
    lines.extend([
        "",
        "── 行情变化 ──",
        f"  last_price   {_fmt_pair(old_md.get('last_price'), new_md.get('last_price'))}",
        f"  change_pct   {_fmt_pair(old_md.get('change_pct'), new_md.get('change_pct'))}",
    ])

    # reasoning / risks 集合 diff
    lines.extend(_diff_string_list("reasoning", old_d.get("reasoning"), new_d.get("reasoning")))
    lines.extend(_diff_string_list("risks", old_d.get("risks"), new_d.get("risks")))
    lines.extend(_diff_string_list("sources", old_d.get("sources"), new_d.get("sources")))

    # confidence_trace（精简）
    old_steps = [s.get("step") for s in (old_d.get("confidence_trace") or [])]
    new_steps = [s.get("step") for s in (new_d.get("confidence_trace") or [])]
    if old_steps != new_steps:
        lines.append("")
        lines.append("── confidence_trace 触发规则变化 ──")
        lines.append(f"  旧: {' → '.join(old_steps) or '(空)'}")
        lines.append(f"  新: {' → '.join(new_steps) or '(空)'}")

    return "\n".join(lines)


def _pick_item(payload: dict, market: str, code: str) -> dict | None:
    for r in payload.get("results", []):
        if r.get("market") == market and r.get("code") == code:
            return r
    return None


def _fmt_pair(old: Any, new: Any) -> str:
    if old == new:
        return f"{old}  (无变化)"
    return f"{old}  →  {new}"


def _diff_string_list(name: str, old: list | None, new: list | None) -> list[str]:
    old_set = set(old or [])
    new_set = set(new or [])
    added = new_set - old_set
    removed = old_set - new_set
    if not added and not removed:
        return []
    out = ["", f"── {name} diff ──"]
    for item in sorted(added):
        out.append(f"  + {item}")
    for item in sorted(removed):
        out.append(f"  - {item}")
    return out

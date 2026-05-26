"""LLM 复核：对敏感 action 进行 LLM 二次确认。

设计要点
========

**为什么要做这一层**
规则系统的优势是确定性 + 可审计；劣势是只能看预先建模的 features
（资金面 / 公告关键词 / 价格 regime 等）。LLM 复核负责的事正好是规则系统看不到的部分：
- 当下宏观 / 行业新闻是否与建议方向冲突
- 公告 / 财报里规则系统漏抓的隐含信号
- 同板块兄弟标的情况 / peer effect
- 用户隐含的 thesis（如"做 T 拉高成本"）和"价格停滞 vs 趋势走坏"的语义差异

**触发条件**
仅对 ``add / trim / sell / buy / probe`` 这类"仓位有创口"的 action 复核；
hold / watch / avoid / focus 不复核（节省 API 成本，这些动作本来就是"先观察"）。

**模型选择**
1. 首选 codex (gpt-5.4 + reasoning_effort=high) —— 默认配置就是高智能模式
2. fallback claude code (sonnet + effort=high) —— 配套 ~/.claude/skills 可调用
3. 都不可用 → 跳过复核，主决策维持原样，payload 加 ``llm_review_unavailable`` 警告

**输入数据**
按用户要求"给足数据"：把决策上下文打包成 JSON 一并放进 prompt，并显式告诉 LLM
可以用 stock-market-hub / stock-portfolio-copilot 等 skill 主动拉补充数据。

**输出 schema**
JSON 强制结构化，三种 verdict：``confirm`` / ``question`` / ``reject``。
此版本（L1 旁路集成）只把复核结果附在 ``decision.llm_review`` 字段里，
不否决主 action（L2/L3 留作未来增强）。

**失败处理**
fail-open：任何调用失败 / 超时 / JSON 解析错都不影响主决策，
只在结果里塞 ``status: failed`` + ``error``，由调用方在 UI 层提示用户。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from typing import Any


# 需要复核的 action（"仓位有创口"的动作）
REVIEW_ACTIONS = frozenset(["add", "trim", "sell", "buy", "probe"])

# LLM 调用超时（秒）。codex 实测一次 ~30-60s，留余量。
DEFAULT_TIMEOUT_SECS = 180

# 单次 prompt 上下文上限（保守值；codex 自己能截断更长上下文，但太长容易 OOM）
PROMPT_MAX_CHARS = 32000


# 输出 schema：codex --output-schema / claude --json-schema 都吃这个。
# 注意 OpenAI structured output 要求 ``required`` 列出 **所有** properties keys
# （不允许真正的可选字段）；想要"软可选"字段就让其值可以是空数组 / 空字符串。
LLM_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["confirm", "question", "reject"],
            "description": "对系统建议的整体判断",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "你对本次复核结论的置信度",
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "对系统建议的具体顾虑（如逻辑漏洞、宏观矛盾、时机问题）",
        },
        "missing_context": {
            "type": "array",
            "items": {"type": "string"},
            "description": "你认为决策需要但 features 里没给到的关键数据；没有则返回空数组",
        },
        "execution_hint": {
            "type": "string",
            "description": "如果按系统建议执行，具体怎么做（仓位 / 时机 / 风控）；没有则返回空串",
        },
    },
    "required": ["verdict", "confidence", "concerns", "missing_context", "execution_hint"],
    "additionalProperties": False,
}


def detect_llm_backend() -> str | None:
    """探测可用的 LLM 复核后端。

    Returns:
        "prompt" / "codex" / "claude" / None（都不可用）。

    默认返回 "prompt"——由当前会话的 agent 直接做复核，不再新开 subprocess。
    可被环境变量 ``SPC_LLM_BACKEND`` 强制覆盖（"prompt"/"codex"/"claude"/"none"）。
    """
    forced = os.environ.get("SPC_LLM_BACKEND")
    if forced == "none":
        return None
    if forced in ("prompt", "codex", "claude"):
        if forced == "prompt":
            return "prompt"
        if shutil.which(forced):
            return forced
        return "prompt"
    # 默认：当前 agent 直接复核（最稳定省时）
    return "prompt"


def should_review(action: str) -> bool:
    """是否对该 action 做 LLM 复核。"""
    return action in REVIEW_ACTIONS


def _build_user_prompt(
    *,
    result: dict,
    market_regime_payload: dict | None = None,
    analysis_excerpt: dict | None = None,
) -> str:
    """拼装喂给 LLM 的用户消息。

    设计原则：
      - 信息密度高：所有上下文一次性塞进去，避免 LLM 在 followup 里反复问数据
      - 结构清晰：分段标注（## 标的 / ## 系统建议 / ## 大盘背景），让 LLM 易解析
      - 显式邀请 skill 使用：告诉 LLM 可以调 stock-market-hub 等 skill 获取额外数据
    """
    dec = result.get("decision") or {}
    pos = result.get("position") or {}
    mkt_data = result.get("market_data") or {}
    parts: list[str] = []

    parts.append("# 复核任务")
    parts.append(
        "你是资深量化交易复核员。下面有一条由规则系统给出的交易建议。"
        "请基于提供的数据 + 你的判断（必要时调用 skill 拉补充信息），给出独立的二次判断。"
    )
    parts.append("")
    parts.append("## 输出要求")
    parts.append(
        "**严格按 JSON Schema 输出**，三种 verdict 含义："
    )
    parts.append("- `confirm`：你同意系统建议，confidence 越高表示越坚定")
    parts.append("- `question`：你不确定，concerns/missing_context 里说明顾虑")
    parts.append("- `reject`：你明确反对，concerns 里详细解释为何")
    parts.append("")
    parts.append("## 可用工具与 skill")
    parts.append(
        "你可以主动调用以下 skill 获取**额外**数据（如规则系统没给到的最新新闻 / 同板块 peer / 财报细节）："
    )
    parts.append(
        "- `stock-market-hub`：当日财经新闻、板块龙头扫描、个股深度尽调（基本面、财报、上下游、近期公告、研报）"
    )
    parts.append(
        "- `stock-portfolio-copilot`：账户持仓 / 自选 / 历史交易（一般无需调用，主决策已经包含上下文）"
    )
    parts.append("")
    parts.append("有 web search / finance 等内置工具的也可以用。")
    parts.append("")

    # ── 标的信息 ──
    parts.append(f"## 标的：{result.get('market', '?')}/{result.get('code', '?')} {result.get('name', '')}")
    parts.append(f"- scope: {result.get('scope', '?')}（holdings = 在持，watchlist = 自选）")
    if pos and Decimal(str(pos.get("qty", "0"))) > 0:
        parts.append(f"- 持仓：{pos.get('qty')} 股，均价 {pos.get('avg_cost_price')} {pos.get('currency')}")
        parts.append(f"- 当前价：{pos.get('last_price')}，浮动盈亏 {pos.get('unrealized_pnl_ccy')} {pos.get('currency')}")
        parts.append(f"- 持仓权重：{dec.get('weight_pct')}%（账户总资金的占比）")
    elif mkt_data.get("last_price"):
        parts.append(f"- 当前价：{mkt_data.get('last_price')}，当日涨跌幅 {mkt_data.get('change_pct')}%")

    # ── 大盘背景 ──
    if market_regime_payload:
        parts.append("")
        parts.append("## 大盘背景")
        for mkt_key, mkt_label in (("a", "A 股"), ("hk", "港股")):
            mr = market_regime_payload.get(mkt_key) or {}
            if not mr.get("regime"):
                continue
            parts.append(f"- {mkt_label}: **{mr.get('regime')}**")
            for r in (mr.get("reasons") or [])[:2]:
                parts.append(f"  - {r}")

    # ── 系统建议 ──
    parts.append("")
    parts.append("## 系统建议")
    parts.append(f"- **action**: `{dec.get('action')}`（{dec.get('action_label', '?')}）")
    parts.append(f"- **confidence**: {dec.get('confidence')}")
    parts.append(f"- description: {dec.get('description', '')}")
    if dec.get("reasoning"):
        parts.append("- reasoning:")
        for r in dec["reasoning"]:
            parts.append(f"  - {r}")
    if dec.get("risks"):
        parts.append("- risks:")
        for r in dec["risks"]:
            parts.append(f"  - {r}")
    if dec.get("sources"):
        parts.append("- sources (内部 feature 摘要):")
        for s in dec["sources"][:20]:
            parts.append(f"  - {s}")

    # ── 决策 trace ──
    trace = dec.get("confidence_trace") or []
    if trace:
        parts.append("")
        parts.append("## 决策置信度演变 trace")
        for s in trace:
            parts.append(
                f"- [{s.get('step', '?')}] action→{s.get('action_to', '?')} "
                f"value={s.get('value', '?')} / {s.get('rule', '')}"
            )

    # ── analysis 摘要（公告 / 资金流 / 价格 regime） ──
    if analysis_excerpt:
        parts.append("")
        parts.append("## 标的原始数据摘要")
        excerpt_json = json.dumps(analysis_excerpt, ensure_ascii=False, indent=2)
        if len(excerpt_json) > 8000:
            excerpt_json = excerpt_json[:8000] + "\n... (truncated)"
        parts.append("```json")
        parts.append(excerpt_json)
        parts.append("```")

    parts.append("")
    parts.append("## 重要约束")
    parts.append(
        "- 不要重复系统已经给出的判断，要补充独立视角（宏观 / 行业 / 时机 / 公告语义）"
    )
    parts.append(
        "- 涉及具体行情 / 公告 / 财报数据时，**优先调用 skill 或 web search 验证**，"
        "不要仅凭记忆给结论"
    )
    parts.append(
        "- `confidence` 表示你对**复核结论**的把握（不是对系统建议的赞同度）。"
        "`verdict=reject + confidence=0.9` = 强烈反对；`verdict=confirm + confidence=0.5` = 同意但拿不准"
    )

    prompt = "\n".join(parts)
    if len(prompt) > PROMPT_MAX_CHARS:
        prompt = prompt[:PROMPT_MAX_CHARS] + "\n\n... (prompt truncated due to size limits)"
    return prompt


def _build_analysis_excerpt(analysis: dict | None) -> dict | None:
    """从完整 analysis dict 提取适合喂给 LLM 的子集。

    保留：quote / fund_flow / price_history / announcements 头 10 条。
    剔除：原始 K 线 / 大段历史数据（LLM 一般用不到，徒增 token）。
    """
    if not analysis:
        return None
    out = {}
    if analysis.get("quote"):
        out["quote"] = analysis["quote"]
    if analysis.get("price_history"):
        ph = analysis["price_history"]
        out["price_history"] = {
            k: v for k, v in ph.items()
            if k not in ("history", "raw")  # 大段时序数据不放
        }
    if analysis.get("fund_flow"):
        ff = analysis["fund_flow"]
        out["fund_flow"] = {
            "regime": ff.get("regime"),
            "reversal": ff.get("reversal"),
            "cross_validation": ff.get("cross_validation"),
            "today": ff.get("today"),
            "rolling": ff.get("rolling"),
        }
    anns = analysis.get("announcements") or []
    if anns:
        out["announcements"] = [
            {k: a.get(k) for k in ("title", "publish_date", "category", "url")}
            for a in anns[:10]
        ]
    if analysis.get("info"):
        info = analysis["info"]
        # 公司基本信息：截取关键字段
        out["company_info"] = {
            k: info.get(k) for k in (
                "name", "short_name", "industry", "concept", "main_business",
                "market_capital", "pe_ttm", "pb", "dividend_yield",
            ) if info.get(k) is not None
        }
    return out


def _invoke_codex(prompt: str, schema_path: str, timeout: int) -> str:
    """调 codex exec，返回 stdout（含答案 JSON）。"""
    proc = subprocess.run(
        [
            "codex", "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--output-schema", schema_path,
            "--color", "never",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "NO_COLOR": "1"},
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"codex exit={proc.returncode}; stderr={proc.stderr[:500]}"
        )
    return proc.stdout


def _invoke_claude(prompt: str, schema_json: str, timeout: int) -> str:
    """调 claude code 非交互模式，返回 stdout。"""
    proc = subprocess.run(
        [
            "claude", "-p",
            "--model", "sonnet",
            "--effort", "high",
            "--output-format", "json",
            "--json-schema", schema_json,
            "--allow-dangerously-skip-permissions",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exit={proc.returncode}; stderr={proc.stderr[:500]}"
        )
    return proc.stdout


def _parse_llm_json(stdout: str) -> dict:
    """从 codex/claude 输出里提取 JSON 答案。

    两个 CLI 的输出都含 banner + 工具调用日志 + 最后一段 JSON。策略：
      1. 尝试整体 json.loads（claude --output-format json 的常见形态）
      2. 失败则倒序找最后一行以 ``{`` 开头的 JSON 块
    """
    text = stdout.strip()

    def _unwrap(obj):
        """如果是 claude 的 {"type":"result","result":"<inner_json>"} 包装，剥一层。"""
        if (isinstance(obj, dict) and obj.get("type") == "result"
                and isinstance(obj.get("result"), str)):
            try:
                inner = json.loads(obj["result"])
                if isinstance(inner, dict) and "verdict" in inner:
                    return inner
            except Exception:  # noqa: BLE001
                pass
        return obj

    # 尝试整体解析
    try:
        obj = json.loads(text)
        obj = _unwrap(obj)
        if isinstance(obj, dict) and "verdict" in obj:
            return obj
    except Exception:  # noqa: BLE001
        pass
    # claude --output-format json 通常 wrap 在 {"result": "...", "type": "result"} 里
    # 但 --json-schema 模式应该直接 result 是 JSON 字符串
    # 倒序找最后一个 { 起始的有效 JSON 块
    lines = text.split("\n")
    candidates: list[str] = []
    buf: list[str] = []
    depth = 0
    in_block = False
    for line in lines:
        if not in_block:
            if line.lstrip().startswith("{"):
                in_block = True
                buf = [line]
                depth = line.count("{") - line.count("}")
                if depth == 0:
                    candidates.append("\n".join(buf))
                    in_block = False
                    buf = []
            continue
        buf.append(line)
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            candidates.append("\n".join(buf))
            in_block = False
            buf = []
            depth = 0
    # 优先尝试最后一个 candidate（通常是模型最终答案）
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
            obj = _unwrap(obj)
            if isinstance(obj, dict) and "verdict" in obj:
                return obj
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(f"failed to parse LLM JSON from stdout (len={len(text)})")


def review_decision(
    *,
    result: dict,
    market_regime_payload: dict | None = None,
    analysis: dict | None = None,
    backend: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECS,
) -> dict:
    """对单个决策结果做 LLM 复核。

    Args:
        result: ``analyze_now`` 单个 ``results[i]`` 字典
        market_regime_payload: 顶层 ``payload["market_regime"]`` 给 LLM 看大盘
        analysis: 标的原始 analysis dict（来自 ``analysis_cache``）
        backend: 强制使用某后端（None = 自动探测）
        timeout: 单次调用超时秒数

    Returns:
        dict 含：
          - ``status``: "ok" / "prompted" / "failed" / "skipped" / "unavailable"
          - ``backend``: 实际使用的后端
          - ``brief``: 复核上下文（仅 status="prompted" 时，供 agent 消费）
          - ``verdict`` / ``confidence`` / ``concerns`` / ``missing_context`` /
            ``execution_hint``（仅 status="ok" 时有）
          - ``error``: 错误信息（仅 status="failed"）
          - ``elapsed_ms``: 调用耗时

        如果 action 不在 REVIEW_ACTIONS 集合，返回 ``{"status": "skipped"}``。
        如果 backend 不可用，返回 ``{"status": "unavailable"}``。
    """
    import time

    action = (result.get("decision") or {}).get("action")
    if not should_review(action):
        return {"status": "skipped", "reason": f"action={action} 不在复核列表"}

    chosen = backend or detect_llm_backend()
    if chosen is None:
        return {
            "status": "unavailable",
            "message": "未检测到可用的 LLM 后端，跳过复核；请仅依据规则系统信号处理",
        }

    excerpt = _build_analysis_excerpt(analysis)
    prompt = _build_user_prompt(
        result=result,
        market_regime_payload=market_regime_payload,
        analysis_excerpt=excerpt,
    )

    start = time.time()
    try:
        if chosen == "prompt":
            # 由当前会话的 agent 直接复核，不新开 subprocess。
            # 返回 review brief 供 agent 消费。
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                "status": "prompted",
                "backend": "prompt",
                "brief": prompt,
                "elapsed_ms": elapsed_ms,
            }
        elif chosen == "codex":
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8",
            ) as fp:
                json.dump(LLM_OUTPUT_SCHEMA, fp)
                schema_path = fp.name
            try:
                stdout = _invoke_codex(prompt, schema_path, timeout)
            finally:
                try:
                    os.unlink(schema_path)
                except Exception:  # noqa: BLE001
                    pass
        elif chosen == "claude":
            schema_str = json.dumps(LLM_OUTPUT_SCHEMA)
            stdout = _invoke_claude(prompt, schema_str, timeout)
        else:
            return {"status": "unavailable", "message": f"unknown backend: {chosen}（可用：prompt / codex / claude）"}

        elapsed_ms = int((time.time() - start) * 1000)
        parsed = _parse_llm_json(stdout)
        return {
            "status": "ok",
            "backend": chosen,
            "verdict": parsed.get("verdict"),
            "confidence": parsed.get("confidence"),
            "concerns": parsed.get("concerns") or [],
            "missing_context": parsed.get("missing_context") or [],
            "execution_hint": parsed.get("execution_hint") or "",
            "elapsed_ms": elapsed_ms,
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "status": "failed",
            "backend": chosen,
            "error": f"timeout after {timeout}s",
            "elapsed_ms": elapsed_ms,
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "status": "failed",
            "backend": chosen,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
            "elapsed_ms": elapsed_ms,
        }


def attach_review_to_results(
    payload: dict,
    *,
    analysis_cache: dict | None = None,
    backend: str | None = None,
    enabled: bool = True,
    timeout: int = DEFAULT_TIMEOUT_SECS,
    progress: Any = None,
) -> dict:
    """对 payload 里所有 results 顺序跑 LLM 复核，把结果挂在 result["decision"]["llm_review"]。

    Args:
        payload: ``analyze_now`` 的返回
        analysis_cache: ``{(market, code): analysis_dict}``，可选
        backend: 强制后端
        enabled: 是否启用（False 时 results 完全不挂 llm_review 字段，节省 IO）
        timeout: 单次调用超时
        progress: 可选 callable，每个标的复核完调一下，签名 ``(idx, total, result, review)``

    Returns:
        装饰后的 payload（原地修改 + 返回）。同时在 payload 顶层加：
          - ``llm_review_meta``: {"backend": str, "enabled": bool, "reviewed": N, "prompted": N, "failed": N, "skipped": N}
    """
    results = payload.get("results") or []
    market_regime_payload = payload.get("market_regime") or {}
    meta = {
        "enabled": enabled,
        "backend": None,
        "reviewed": 0,
        "prompted": 0,
        "skipped": 0,
        "failed": 0,
        "unavailable": False,
    }
    if not enabled:
        meta["disabled_reason"] = "explicitly disabled via flag/setting"
        payload["llm_review_meta"] = meta
        return payload

    chosen = backend or detect_llm_backend()
    meta["backend"] = chosen
    if chosen is None:
        meta["unavailable"] = True
        meta["message"] = "未检测到可用后端，全部结果跳过 LLM 复核"
        for r in results:
            dec = r.get("decision") or {}
            if should_review(dec.get("action")):
                dec["llm_review"] = {
                    "status": "unavailable",
                    "message": "未检测到 LLM 后端，按规则系统信号处理",
                }
        payload["llm_review_meta"] = meta
        return payload

    total = len(results)
    for i, r in enumerate(results):
        dec = r.get("decision") or {}
        action = dec.get("action")
        if not should_review(action):
            meta["skipped"] += 1
            continue
        key = (r.get("market"), r.get("code"))
        analysis = (analysis_cache or {}).get(key)
        review = review_decision(
            result=r,
            market_regime_payload=market_regime_payload,
            analysis=analysis,
            backend=chosen,
            timeout=timeout,
        )
        dec["llm_review"] = review
        status = review.get("status")
        if status == "ok":
            meta["reviewed"] += 1
        elif status == "prompted":
            meta["prompted"] += 1
        elif status == "failed":
            meta["failed"] += 1
        else:
            meta["skipped"] += 1
        if progress is not None:
            try:
                progress(i + 1, total, r, review)
            except Exception:  # noqa: BLE001
                pass

    payload["llm_review_meta"] = meta
    return payload

"""
轻量增量缓存层。

设计原则：
  - 通过装饰器使用：@cached(ttl=3600) 自动按函数+参数 hash key 缓存到磁盘
  - JSON 序列化（pickle 不安全且不跨版本）
  - 默认目录 ~/.cache/stock-market-hub/data/
  - 支持环境变量 STOCK_HUB_CACHE_DISABLE=1 关闭缓存
  - TTL=0 表示永久缓存
  - 失败的请求不缓存（return None / [] / {}）

用法：
    from core.cache import cached

    @cached(ttl=3600, key_prefix="quote")
    def get_quote(symbol: str) -> dict:
        ...

预设 TTL（建议）：
  - 行情类（盘中变）：60s
  - 行情类（盘后）：4h（自己判断）
  - 板块成分：4h
  - 公告列表：1h（公告新发频率高时缩短）
  - 公司基本信息：24h
  - 财报 / 高管 / 股东：24h
  - K 线日 K：4h（盘后）/ 60s（盘中）
  - PDF 文件：永久（用 ETag/file size 判定）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path(os.environ.get(
    "STOCK_HUB_CACHE_DIR",
    str(Path.home() / ".cache" / "stock-market-hub" / "data"),
))
DISABLED = os.environ.get("STOCK_HUB_CACHE_DISABLE") == "1"


def _make_key(func_name: str, args: tuple, kwargs: dict, key_prefix: str) -> str:
    """对 (函数名, 参数) 做稳定 hash 作为缓存 key。"""
    sig = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
    h = hashlib.sha256(sig.encode()).hexdigest()[:16]
    prefix = f"{key_prefix}_" if key_prefix else ""
    return f"{prefix}{func_name}_{h}"


def _path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def cached(
    ttl: float = 3600,
    key_prefix: str = "",
    skip_if: Callable[[Any], bool] | None = None,
):
    """缓存装饰器。

    参数：
        ttl: 过期秒数，0 = 永久缓存
        key_prefix: 缓存 key 前缀（便于按业务清理）
        skip_if: 函数 (result) -> bool；为 True 时不写缓存（如返回空 list 时不缓存）

    默认 skip_if：返回 None / [] / {} / "" 时不写缓存。
    """
    if skip_if is None:
        skip_if = lambda r: r is None or r == [] or r == {} or r == ""

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if DISABLED:
                return func(*args, **kwargs)

            key = _make_key(func.__name__, args, kwargs, key_prefix)
            p = _path(key)

            # 尝试读缓存
            if p.exists():
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                    cached_at = payload.get("_cached_at", 0)
                    age = time.time() - cached_at
                    if ttl == 0 or age < ttl:
                        return payload.get("data")
                except Exception:
                    pass  # 损坏的缓存忽略

            # 调用原函数
            result = func(*args, **kwargs)

            # 写缓存（除非 skip_if 命中）
            if not skip_if(result):
                try:
                    p.write_text(
                        json.dumps(
                            {"_cached_at": time.time(), "_func": func.__name__, "data": result},
                            ensure_ascii=False, default=str,
                        ),
                        encoding="utf-8",
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"[cache] write failed for {key}: {e}", file=sys.stderr)

            return result

        wrapper._cache_func_name = func.__name__  # type: ignore
        wrapper._cache_ttl = ttl  # type: ignore
        return wrapper

    return decorator


def clear_cache(prefix: str = "") -> int:
    """清理指定前缀的缓存。返回清理数量。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in CACHE_DIR.glob(f"{prefix}*.json"):
        try:
            p.unlink()
            n += 1
        except Exception:
            pass
    return n


def cache_stats() -> dict:
    """返回缓存目录统计。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = list(CACHE_DIR.glob("*.json"))
    total_size = sum(f.stat().st_size for f in files)
    by_prefix: dict = {}
    for f in files:
        prefix = f.stem.split("_")[0]
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
    return {
        "dir": str(CACHE_DIR),
        "total_files": len(files),
        "total_size_kb": round(total_size / 1024, 1),
        "by_prefix": by_prefix,
    }


if __name__ == "__main__":
    # CLI 工具：smh-cache stats / clear
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p_clear = sub.add_parser("clear")
    p_clear.add_argument("--prefix", default="")
    args = ap.parse_args()
    if args.cmd == "stats":
        print(json.dumps(cache_stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "clear":
        n = clear_cache(args.prefix)
        print(f"清理 {n} 个缓存文件（前缀 '{args.prefix}'）")

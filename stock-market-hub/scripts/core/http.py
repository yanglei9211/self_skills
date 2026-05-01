"""
共享 HTTP 客户端：基于 curl_cffi 的 Chrome 指纹模拟，绕过新浪/腾讯/同花顺/巨潮的反爬。

所有 stock-market-hub 脚本应通过 fetch() / fetch_json() / fetch_text() 发起请求，
不要直接使用 requests 或 urllib —— 普通客户端在大量源上会被反爬识别（HTTP 000）。
"""
from __future__ import annotations

import sys
import time
import random
from typing import Any

try:
    from curl_cffi import requests as _cffi
except ImportError as e:
    print(
        "ERROR: curl_cffi not installed. Run: "
        ".venv/bin/pip install curl_cffi",
        file=sys.stderr,
    )
    raise

DEFAULT_TIMEOUT = 10
DEFAULT_IMPERSONATE = "chrome"

_DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


def fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: Any = None,
    json_body: Any = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    impersonate: str = DEFAULT_IMPERSONATE,
    encoding: str | None = None,
    retries: int = 2,
    retry_backoff: float = 1.5,
) -> "_cffi.Response":
    """
    单次 HTTP 请求，自带：
      - Chrome TLS 指纹（impersonate=chrome）
      - 通用 Header（Accept-Language: zh-CN）
      - 简单重试（默认 2 次，针对 ConnectionError / 5xx / 429）

    encoding: 强制指定响应编码（腾讯/同花顺需要 'gbk'）
    """
    final_headers = {**_DEFAULT_HEADERS, **(headers or {})}
    last_err: Exception | None = None

    for attempt in range(retries + 1):
        try:
            r = _cffi.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=final_headers,
                timeout=timeout,
                impersonate=impersonate,
            )
            if encoding:
                r.encoding = encoding
            if r.status_code in (429, 500, 502, 503, 504):
                raise _cffi.RequestsError(f"HTTP {r.status_code}")
            return r
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                sleep_s = retry_backoff ** attempt + random.uniform(0, 0.5)
                print(
                    f"[http] {method} {url} attempt {attempt+1} failed: "
                    f"{type(e).__name__}: {str(e)[:80]} → retry in {sleep_s:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_s)
            else:
                print(
                    f"[http] {method} {url} FAILED after {retries+1} attempts: "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
    assert last_err is not None
    raise last_err


def fetch_json(url: str, **kwargs) -> Any:
    """同 fetch()，但解析 JSON。"""
    r = fetch(url, **kwargs)
    return r.json()


def fetch_text(url: str, **kwargs) -> str:
    """同 fetch()，但返回文本。"""
    r = fetch(url, **kwargs)
    return r.text


def polite_sleep(min_s: float = 0.3, max_s: float = 0.8) -> None:
    """节流：在批量调用同一个源之间插入随机短暂停。"""
    time.sleep(random.uniform(min_s, max_s))

"""Side-effect import：把 ``<repo>/shared`` 和当前 ``scripts/`` 加入 ``sys.path``。

被 stock-market-hub/scripts 下的脚本在顶部一行 ``import _path_setup``
统一引入，替代以前各自抄写 4 行的样板：

    _SHARED = Path(__file__).resolve().parents[2] / "shared"
    if str(_SHARED) not in sys.path:
        sys.path.insert(0, str(_SHARED))

被 import 时自动执行；多次 import 幂等。

附带把脚本同目录也加进 ``sys.path``——脚本互相 import（例如 supply_chain 引
scan_sector / event_timeline 引 fetch_announcements）时无论入口形态都能解析。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_SHARED = _SCRIPTS.parent.parent / "shared"
for _p in (_SHARED, _SCRIPTS):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

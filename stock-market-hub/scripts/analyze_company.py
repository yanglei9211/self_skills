#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_shared_path() -> None:
    current = Path(__file__).resolve()
    shared_dir = current.parents[2] / "shared"
    if str(shared_dir) not in sys.path:
        sys.path.insert(0, str(shared_dir))


_ensure_shared_path()

from stock_core.company_analysis import main  # noqa: E402


if __name__ == "__main__":
    main()

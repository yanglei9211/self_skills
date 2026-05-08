from __future__ import annotations

import sqlite3

from spc_core.utils import db_path


SCHEMA = """
CREATE TABLE IF NOT EXISTS position_seed (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  qty TEXT NOT NULL,
  cost_price TEXT NOT NULL,
  currency TEXT NOT NULL,
  seed_time TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(market, code)
);

CREATE TABLE IF NOT EXISTS trade_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  side TEXT NOT NULL,
  qty TEXT NOT NULL,
  price TEXT NOT NULL,
  currency TEXT NOT NULL,
  trade_time TEXT NOT NULL,
  fee_commission TEXT DEFAULT '0',
  fee_platform TEXT DEFAULT '0',
  fee_transfer TEXT DEFAULT '0',
  tax_stamp TEXT DEFAULT '0',
  fx_rate TEXT,
  note TEXT DEFAULT '',
  is_deleted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  qty TEXT NOT NULL,
  avg_cost_price TEXT NOT NULL,
  currency TEXT NOT NULL,
  gross_cost_ccy TEXT NOT NULL,
  total_fees_ccy TEXT NOT NULL,
  realized_pnl_ccy TEXT NOT NULL,
  last_price TEXT,
  last_price_time TEXT,
  unrealized_pnl_ccy TEXT,
  fx_rate_to_cny TEXT,
  position_value_cny TEXT,
  snapshot_time TEXT NOT NULL,
  source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(market, code)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  market TEXT,
  code TEXT,
  run_time TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    target = path or str(db_path())
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()

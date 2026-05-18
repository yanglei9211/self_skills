from __future__ import annotations

import sqlite3
import sys

from spc_core.utils import db_path, utc_now_iso


# v2→v3 新增的三张表 + 索引；同时被 SCHEMA_V3（fresh install）
# 和 _migrate_v2_to_v3（增量迁移）复用，避免两边 DDL 漂移。
_DDL_V3_NEW_TABLES = """
CREATE TABLE IF NOT EXISTS execution_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  side TEXT NOT NULL,
  action_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned',
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_ref_id INTEGER,
  source_action TEXT DEFAULT '',
  thesis TEXT NOT NULL,
  invalidation TEXT DEFAULT '',
  target_qty TEXT,
  target_cash_cny TEXT,
  target_position_pct TEXT,
  price_limit_low TEXT,
  price_limit_high TEXT,
  stop_loss_price TEXT,
  take_profit_price TEXT,
  add_condition TEXT DEFAULT '',
  reduce_condition TEXT DEFAULT '',
  time_window_start TEXT,
  time_window_end TEXT,
  confidence TEXT,
  risk_level TEXT DEFAULT '',
  tags TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_execution_plan_account_symbol
  ON execution_plan(account_id, market, code, created_at, id);

CREATE TABLE IF NOT EXISTS trade_execution_link (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  plan_id INTEGER NOT NULL,
  trade_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'fill',
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(account_id, plan_id, trade_id),
  FOREIGN KEY(account_id) REFERENCES accounts(id),
  FOREIGN KEY(plan_id) REFERENCES execution_plan(id),
  FOREIGN KEY(trade_id) REFERENCES trade_ledger(id)
);

CREATE INDEX IF NOT EXISTS idx_trade_execution_link_account_plan
  ON trade_execution_link(account_id, plan_id, trade_id);

CREATE INDEX IF NOT EXISTS idx_trade_execution_link_trade
  ON trade_execution_link(trade_id);

CREATE TABLE IF NOT EXISTS execution_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  plan_id INTEGER,
  trade_id INTEGER,
  review_time TEXT NOT NULL,
  horizon TEXT NOT NULL DEFAULT 'manual',
  outcome TEXT NOT NULL,
  discipline_score INTEGER,
  execution_score INTEGER,
  thesis_score INTEGER,
  plan_followed INTEGER,
  mistake_tags TEXT DEFAULT '',
  good_tags TEXT DEFAULT '',
  pnl_snapshot_cny TEXT,
  max_favorable_excursion_pct TEXT,
  max_adverse_excursion_pct TEXT,
  lesson TEXT NOT NULL,
  next_rule TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(account_id) REFERENCES accounts(id),
  FOREIGN KEY(plan_id) REFERENCES execution_plan(id),
  FOREIGN KEY(trade_id) REFERENCES trade_ledger(id)
);

CREATE INDEX IF NOT EXISTS idx_execution_review_account_plan
  ON execution_review(account_id, plan_id, trade_id, review_time, id);
"""


# v3→v4 新增：position_peak 表用于 trailing stop。
# 设计选择：一行 (account, market, code) 一条记录，而不是写到 portfolio_snapshot：
#   - peak 只关心"当前持仓阶段"的最高价，不需要历史
#   - 清仓时直接 DELETE，下次再买入会重新初始化，逻辑天然干净
#   - 与 snapshot 解耦，避免 sync 频率变化牵连风控逻辑
_DDL_V4_NEW_TABLES = """
CREATE TABLE IF NOT EXISTS position_peak (
  account_id INTEGER NOT NULL,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  peak_price TEXT NOT NULL,
  peak_time TEXT NOT NULL,
  position_open_time TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  -- v5 字段：P0a 分档幂等性。fresh install 时直接带上，避免迁移
  -- 与 fresh 两边的 DDL 漂移。NULL 表示尚未触发过任何 tier。
  last_trim_tier TEXT,                  -- 'T1' / 'T2' / NULL
  last_trim_price TEXT,                 -- 上次 trim 触发时的现价（用于 reason 提示）
  last_trim_time TEXT,                  -- 上次 trim 触发的时间（用于 reason 提示）
  PRIMARY KEY(account_id, market, code),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);
"""


SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  broker TEXT DEFAULT '',
  base_currency TEXT DEFAULT 'CNY',
  note TEXT DEFAULT '',
  is_default INTEGER NOT NULL DEFAULT 0,
  is_archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_settings (
  account_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(account_id, key),
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS position_seed (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  qty TEXT NOT NULL,
  cost_price TEXT NOT NULL,
  currency TEXT NOT NULL,
  seed_time TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, market, code)
);

CREATE TABLE IF NOT EXISTS trade_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_trade_ledger_account_symbol
  ON trade_ledger(account_id, market, code, trade_time, id);

CREATE TABLE IF NOT EXISTS portfolio_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_snapshot_account_symbol
  ON portfolio_snapshot(account_id, market, code, id);

CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  market TEXT NOT NULL,
  code TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, market, code)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  scope TEXT NOT NULL,
  market TEXT,
  code TEXT,
  run_time TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);
""" + _DDL_V3_NEW_TABLES + _DDL_V4_NEW_TABLES


def _get_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {version}")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate from v1 (global) to v2 (multi-account).

    All existing data is moved to a default account with slug='default'.
    """
    now = utc_now_iso()

    # 1. Create new tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY,
          slug TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          broker TEXT DEFAULT '',
          base_currency TEXT DEFAULT 'CNY',
          note TEXT DEFAULT '',
          is_default INTEGER NOT NULL DEFAULT 0,
          is_archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_settings (
          account_id INTEGER NOT NULL,
          key TEXT NOT NULL,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(account_id, key),
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)

    # 2. Create default account
    conn.execute(
        """
        INSERT INTO accounts(slug, display_name, broker, base_currency, note, is_default, is_archived, created_at, updated_at)
        VALUES('default', '默认账户', '', 'CNY', '', 1, 0, ?, ?)
        """,
        (now, now),
    )
    default_id = conn.execute("SELECT id FROM accounts WHERE slug = 'default'").fetchone()[0]

    # 3. Migrate capital settings
    capital_keys = ["capital.total_cny", "capital.max_single_position_pct", "capital.max_sector_position_pct"]
    for key in capital_keys:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row:
            conn.execute(
                "INSERT INTO account_settings(account_id, key, value, updated_at) VALUES(?, ?, ?, ?)",
                (default_id, key, row["value"], now),
            )

    # 4. Rebuild business tables with account_id

    # position_seed
    conn.execute("""
        CREATE TABLE position_seed_v2 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL,
          market TEXT NOT NULL,
          code TEXT NOT NULL,
          qty TEXT NOT NULL,
          cost_price TEXT NOT NULL,
          currency TEXT NOT NULL,
          seed_time TEXT NOT NULL,
          note TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(account_id, market, code)
        )
    """)
    conn.execute("""
        INSERT INTO position_seed_v2(account_id, market, code, qty, cost_price, currency, seed_time, note, created_at, updated_at)
        SELECT ?, market, code, qty, cost_price, currency, seed_time, note, created_at, updated_at
          FROM position_seed
    """, (default_id,))
    conn.execute("DROP TABLE position_seed")
    conn.execute("ALTER TABLE position_seed_v2 RENAME TO position_seed")

    # trade_ledger
    conn.execute("""
        CREATE TABLE trade_ledger_v2 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL,
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
        )
    """)
    conn.execute("""
        INSERT INTO trade_ledger_v2(account_id, market, code, side, qty, price, currency, trade_time,
                                     fee_commission, fee_platform, fee_transfer, tax_stamp,
                                     fx_rate, note, is_deleted, created_at, updated_at)
        SELECT ?, market, code, side, qty, price, currency, trade_time,
               fee_commission, fee_platform, fee_transfer, tax_stamp,
               fx_rate, note, is_deleted, created_at, updated_at
          FROM trade_ledger
    """, (default_id,))
    conn.execute("DROP TABLE trade_ledger")
    conn.execute("ALTER TABLE trade_ledger_v2 RENAME TO trade_ledger")
    conn.execute("""
        CREATE INDEX idx_trade_ledger_account_symbol
          ON trade_ledger(account_id, market, code, trade_time, id)
    """)

    # portfolio_snapshot
    conn.execute("""
        CREATE TABLE portfolio_snapshot_v2 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL,
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
        )
    """)
    conn.execute("""
        INSERT INTO portfolio_snapshot_v2(account_id, market, code, qty, avg_cost_price, currency,
                                           gross_cost_ccy, total_fees_ccy, realized_pnl_ccy,
                                           last_price, last_price_time, unrealized_pnl_ccy,
                                           fx_rate_to_cny, position_value_cny, snapshot_time, source)
        SELECT ?, market, code, qty, avg_cost_price, currency,
               gross_cost_ccy, total_fees_ccy, realized_pnl_ccy,
               last_price, last_price_time, unrealized_pnl_ccy,
               fx_rate_to_cny, position_value_cny, snapshot_time, source
          FROM portfolio_snapshot
    """, (default_id,))
    conn.execute("DROP TABLE portfolio_snapshot")
    conn.execute("ALTER TABLE portfolio_snapshot_v2 RENAME TO portfolio_snapshot")
    conn.execute("""
        CREATE INDEX idx_snapshot_account_symbol
          ON portfolio_snapshot(account_id, market, code, id)
    """)

    # watchlist
    conn.execute("""
        CREATE TABLE watchlist_v2 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL,
          market TEXT NOT NULL,
          code TEXT NOT NULL,
          note TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(account_id, market, code)
        )
    """)
    conn.execute("""
        INSERT INTO watchlist_v2(account_id, market, code, note, created_at, updated_at)
        SELECT ?, market, code, note, created_at, updated_at
          FROM watchlist
    """, (default_id,))
    conn.execute("DROP TABLE watchlist")
    conn.execute("ALTER TABLE watchlist_v2 RENAME TO watchlist")

    # analysis_run
    conn.execute("""
        CREATE TABLE analysis_run_v2 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL,
          scope TEXT NOT NULL,
          market TEXT,
          code TEXT,
          run_time TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)
    conn.execute("""
        INSERT INTO analysis_run_v2(account_id, scope, market, code, run_time, payload_json)
        SELECT ?, scope, market, code, run_time, payload_json
          FROM analysis_run
    """, (default_id,))
    conn.execute("DROP TABLE analysis_run")
    conn.execute("ALTER TABLE analysis_run_v2 RENAME TO analysis_run")

    # 5. Set version
    _set_user_version(conn, 2)
    conn.commit()


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_V3_NEW_TABLES)
    _set_user_version(conn, 3)
    conn.commit()


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """新增 position_peak 表，trailing stop 用。"""
    conn.executescript(_DDL_V4_NEW_TABLES)
    _set_user_version(conn, 4)
    conn.commit()


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """给 position_peak 加 3 个字段，支持 P0a 分档幂等性。

    设计：用 IF NOT EXISTS 风格的语义自检，避免重复迁移；SQLite 没有原生支持，
    所以通过 PRAGMA table_info 判断列是否已存在。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(position_peak)").fetchall()}
    if "last_trim_tier" not in cols:
        conn.execute("ALTER TABLE position_peak ADD COLUMN last_trim_tier TEXT")
    if "last_trim_price" not in cols:
        conn.execute("ALTER TABLE position_peak ADD COLUMN last_trim_price TEXT")
    if "last_trim_time" not in cols:
        conn.execute("ALTER TABLE position_peak ADD COLUMN last_trim_time TEXT")
    _set_user_version(conn, 5)
    conn.commit()


def connect(path: str | None = None) -> sqlite3.Connection:
    target = path or str(db_path())
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    version = _get_user_version(conn)

    if version >= 5:
        return

    if version == 0:
        # version=0 could be truly fresh OR a pre-versioning v1 DB
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if not existing:
            # Truly fresh database
            conn.executescript(SCHEMA_V3)
            _set_user_version(conn, 5)
            conn.commit()
            return
        # Has tables but no version stamp — treat as v1

    if version <= 1:
        print("检测到旧版本数据库，正在迁移到多账户结构...", file=sys.stderr)
        try:
            _migrate_v1_to_v2(conn)
        except Exception:
            conn.rollback()
            print("迁移失败，数据库未变更。请检查备份后重试。", file=sys.stderr)
            raise
        print("迁移完成。所有数据已迁移到默认账户 (slug='default')。", file=sys.stderr)
        version = 2

    if version == 2:
        print("检测到 v2 数据库，正在迁移执行记录增强结构...", file=sys.stderr)
        try:
            _migrate_v2_to_v3(conn)
        except Exception:
            conn.rollback()
            print("迁移失败，数据库未变更。请检查备份后重试。", file=sys.stderr)
            raise
        print("迁移完成。execution_plan / trade_execution_link / execution_review 已启用。", file=sys.stderr)
        version = 3

    if version == 3:
        print("检测到 v3 数据库，正在升级 trailing stop 支持（position_peak）...", file=sys.stderr)
        try:
            _migrate_v3_to_v4(conn)
        except Exception:
            conn.rollback()
            print("迁移失败，数据库未变更。请检查备份后重试。", file=sys.stderr)
            raise
        print("迁移完成。position_peak 已启用，trailing stop 现可用。", file=sys.stderr)
        version = 4

    if version == 4:
        print("检测到 v4 数据库，正在升级 P0a 分档幂等性支持（last_trim_tier）...", file=sys.stderr)
        try:
            _migrate_v4_to_v5(conn)
        except Exception:
            conn.rollback()
            print("迁移失败，数据库未变更。请检查备份后重试。", file=sys.stderr)
            raise
        print("迁移完成。P0a 分档触发后不再重复建议同档减仓。", file=sys.stderr)

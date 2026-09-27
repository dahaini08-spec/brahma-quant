-- init_db.sql — 梵天signals库建表脚本（自动生成，禁手改）
-- 来源: data/brahma_signals.db 活库dump (dump_schema.py)
-- 变更方式: 新增 config/migrations/V00X_<desc>.sql，禁止手改本文件
-- 生成时间: 
-- 部署: 部署脚本只跑本文件 + migrations目录
-- GENERATED_AT: 2026-09-27T12:11:17.672113Z

-- table: signals
CREATE TABLE signals (
    signal_id    TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    ts_day       TEXT GENERATED ALWAYS AS (date(ts, 'unixepoch')) STORED,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    regime       TEXT,
    score        REAL,
    grade        REAL,
    entry_lo     REAL,
    entry_hi     REAL,
    stop         REAL,
    target       REAL,
    action       TEXT,
    source       TEXT DEFAULT 'analyze',
    -- 结算字段
    outcome      TEXT,
    pnl_pct      REAL,
    settled_ts   REAL,
    -- 扩展JSON
    extras       TEXT,
    created_at   REAL DEFAULT (unixepoch())
);

-- index: idx_outcome
CREATE INDEX idx_outcome ON signals(outcome);

-- index: idx_regime
CREATE INDEX idx_regime ON signals(regime);

-- index: idx_score
CREATE INDEX idx_score ON signals(score);

-- index: idx_signals_outcome
CREATE INDEX idx_signals_outcome ON signals(outcome);

-- index: idx_signals_regime
CREATE INDEX idx_signals_regime ON signals(regime);

-- index: idx_signals_score
CREATE INDEX idx_signals_score ON signals(score);

-- index: idx_signals_symbol
CREATE INDEX idx_signals_symbol ON signals(symbol);

-- index: idx_signals_ts
CREATE INDEX idx_signals_ts ON signals(ts);

-- index: idx_sym
CREATE INDEX idx_sym ON signals(symbol);

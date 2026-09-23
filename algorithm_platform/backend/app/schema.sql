-- 算法迭代平台 SQLite Schema
-- events.payload 存完整脱敏事件 JSON（含骨骼序列）；可筛选/聚合字段反规范化成列 + 索引

CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT NOT NULL UNIQUE,   -- 脱敏后的事件ID（algo_ + hash），确定性，用于去重
  source_id    TEXT NOT NULL,          -- 脱敏后的来源站点ID（src_ + hash），非设备序列号
  created_at   TEXT NOT NULL,          -- 原始 ISO 字符串（展示用）
  created_ts   REAL NOT NULL,          -- epoch 秒(UTC)（排序/过滤/趋势）—— 归档混用时区必须归一化
  ingested_at  TEXT NOT NULL,          -- 平台入库时间 ISO8601
  risk_level   TEXT,                   -- I / II / III
  status       TEXT,                   -- archived / voice_cancelled / false_alarm ...
  is_fall      INTEGER,                -- 1=真实跌倒 0=误报（由反馈/状态派生）
  has_skeleton INTEGER DEFAULT 0,      -- 是否带骨骼关键点序列
  payload      TEXT NOT NULL           -- 完整脱敏事件 JSON（见 services/anonymize.py）
);

CREATE INDEX IF NOT EXISTS idx_events_created_ts ON events(created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_source     ON events(source_id);
CREATE INDEX IF NOT EXISTS idx_events_risk       ON events(risk_level);
CREATE INDEX IF NOT EXISTS idx_events_status     ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_is_fall    ON events(is_fall);

-- 批量接收日志（含去重/错误计数）
CREATE TABLE IF NOT EXISTS ingest_log (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  ingested_at         TEXT NOT NULL,
  batch_id            TEXT,
  source_id           TEXT,
  total               INTEGER DEFAULT 0,
  inserted            INTEGER DEFAULT 0,
  skipped_duplicates  INTEGER DEFAULT 0,
  errors              INTEGER DEFAULT 0
);

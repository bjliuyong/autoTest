-- Gate AutoTest Studio 共享后端 PostgreSQL 建表脚本
-- 使用前请先在 PostgreSQL 中创建数据库: CREATE DATABASE gate_autotest;

CREATE TABLE IF NOT EXISTS sys_roles (
  id SERIAL PRIMARY KEY,
  code VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(64) NOT NULL,
  description VARCHAR(255) NOT NULL DEFAULT '',
  permissions_json TEXT NOT NULL,
  is_system SMALLINT NOT NULL DEFAULT 0,
  created_at VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS sys_users (
  id SERIAL PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  nickname VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role_code VARCHAR(64) NOT NULL DEFAULT 'tester',
  department VARCHAR(128) NOT NULL DEFAULT '质量保障部',
  email VARCHAR(128) NOT NULL DEFAULT '',
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  last_login_at VARCHAR(64) NULL,
  created_at VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_role ON sys_users (role_code);

CREATE TABLE IF NOT EXISTS sys_audit_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER NULL,
  username VARCHAR(64) NOT NULL,
  module VARCHAR(64) NOT NULL,
  action VARCHAR(128) NOT NULL,
  detail TEXT NOT NULL,
  ip VARCHAR(64) NOT NULL DEFAULT '',
  created_at VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON sys_audit_logs (created_at);

CREATE TABLE IF NOT EXISTS test_batches (
  id BIGSERIAL PRIMARY KEY,
  run_code VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  gate_mode VARCHAR(32) NOT NULL DEFAULT 'both',
  log_source VARCHAR(32) NOT NULL DEFAULT 'ws',
  total_cases INTEGER NOT NULL DEFAULT 0,
  passed_cases INTEGER NOT NULL DEFAULT 0,
  failed_cases INTEGER NOT NULL DEFAULT 0,
  timeout_cases INTEGER NOT NULL DEFAULT 0,
  pass_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_by VARCHAR(64) NOT NULL DEFAULT 'admin',
  started_at VARCHAR(64) NOT NULL,
  ended_at VARCHAR(64) NULL,
  duration_ms BIGINT NOT NULL DEFAULT 0,
  config_snapshot_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_cases (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES test_batches(id) ON DELETE CASCADE,
  case_id VARCHAR(128) NOT NULL,
  serial VARCHAR(128) NULL,
  round_key VARCHAR(128) NULL,
  passenger_name VARCHAR(128) NOT NULL DEFAULT '',
  cert_type VARCHAR(64) NOT NULL DEFAULT '',
  cert_no VARCHAR(128) NOT NULL DEFAULT '',
  gate_id VARCHAR(64) NOT NULL DEFAULT '',
  photo_data TEXT NULL,
  expected_allow SMALLINT NOT NULL,
  expected_node VARCHAR(128) NULL,
  actual_allow SMALLINT NULL,
  actual_node VARCHAR(128) NULL,
  actual_nodes_json TEXT NOT NULL DEFAULT '[]',
  verdict_status VARCHAR(32) NOT NULL,
  passed SMALLINT NOT NULL DEFAULT 0,
  timed_out SMALLINT NOT NULL DEFAULT 0,
  contradictory SMALLINT NOT NULL DEFAULT 0,
  session_id VARCHAR(128) NULL,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  resolution_note TEXT NOT NULL DEFAULT '',
  capture_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}',
  gate_messages_json TEXT NOT NULL DEFAULT '[]',
  log_slice TEXT NOT NULL DEFAULT '',
  analysis_status VARCHAR(32) NOT NULL DEFAULT 'none',
  analysis_json TEXT NULL,
  created_by VARCHAR(64) NOT NULL DEFAULT 'admin',
  captured_at VARCHAR(64) NOT NULL,
  judged_at VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_batch ON test_cases (batch_id);
CREATE INDEX IF NOT EXISTS idx_cases_verdict ON test_cases (verdict_status);
CREATE INDEX IF NOT EXISTS idx_cases_case_id ON test_cases (case_id);

CREATE TABLE IF NOT EXISTS system_logs (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NULL,
  level VARCHAR(16) NOT NULL DEFAULT 'INFO',
  source VARCHAR(64) NOT NULL DEFAULT 'engine',
  message TEXT NOT NULL,
  operator VARCHAR(64) NOT NULL DEFAULT 'system',
  created_at VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_syslogs_batch ON system_logs (batch_id);

CREATE TABLE IF NOT EXISTS app_configs (
  config_key VARCHAR(64) PRIMARY KEY,
  config_json TEXT NOT NULL,
  updated_by VARCHAR(64) NOT NULL DEFAULT 'admin',
  updated_at VARCHAR(64) NOT NULL
);

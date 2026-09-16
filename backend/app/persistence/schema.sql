-- 索引等增量变更走 app/persistence/migrations.py（版本化，启动自动应用）
-- AgentInsight MySQL DDL（契约 §9，scripts/init_db.py 执行）
CREATE TABLE IF NOT EXISTS datasets (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  path VARCHAR(512) NOT NULL,
  rows_estimate BIGINT NOT NULL DEFAULT 0,
  size_bytes BIGINT NOT NULL DEFAULT 0,
  schema_json JSON NULL,
  user_id VARCHAR(36) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_datasets_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tasks (
  id VARCHAR(36) PRIMARY KEY,
  dataset_id VARCHAR(36) NULL,
  query TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  engine VARCHAR(16) NULL,
  current_step INT NOT NULL DEFAULT 0,
  final_result JSON NULL,
  error TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  user_id VARCHAR(36) NULL,
  KEY idx_tasks_status (status),
  KEY idx_tasks_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_steps (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL,
  step INT NOT NULL,
  agent_name VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  latency_ms INT NULL,
  retry_count INT NOT NULL DEFAULT 0,
  detail JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_steps_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL,
  agent_name VARCHAR(64) NOT NULL,
  model VARCHAR(64) NULL,
  input_tokens INT NULL,
  output_tokens INT NULL,
  latency_ms INT NULL,
  tool_name VARCHAR(64) NULL,
  error_type VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_runs_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS jobs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  company VARCHAR(255) NULL,
  location VARCHAR(255) NULL,
  skills TEXT NULL,
  description MEDIUMTEXT NULL,
  source_url VARCHAR(512) NOT NULL,
  crawled_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_job (title, company, source_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== 阶段2 增量（CONTRACTS2 §4.1）=====

CREATE TABLE IF NOT EXISTS resumes (
  id VARCHAR(36) PRIMARY KEY,
  filename VARCHAR(255),
  path VARCHAR(512),
  profile_json JSON,
  user_id VARCHAR(36) NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_resumes_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS matches (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id VARCHAR(36),
  resume_id VARCHAR(36),
  job_ids_json JSON,
  score INT,
  detail_json JSON,
  user_id VARCHAR(36) NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_matches_task (task_id),
  KEY idx_matches_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS model_configs (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(64),
  provider VARCHAR(32),
  base_url VARCHAR(255),
  api_key_enc VARCHAR(1024),
  model VARCHAR(64),
  temperature FLOAT DEFAULT 0,
  is_active TINYINT(1) DEFAULT 0,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS app_settings (
  `key` VARCHAR(64) PRIMARY KEY,
  value TEXT,
  is_secret TINYINT(1) DEFAULT 0,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===== 阶段3 增量（CONTRACTS3 §3.1）：用户与数据隔离 =====
-- user_id 为 NULL 的存量数据视为"公共遗留"，任何已登录用户可见；新写入必带 user_id。

CREATE TABLE IF NOT EXISTS users (
  id VARCHAR(36) PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 内置默认设置（INSERT IGNORE：只在缺行时插入，不覆盖已有值）
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('match_llm_enabled', 'true', 0);
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('report_llm_enabled', 'false', 0);
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('llm_fallback_mock', 'auto', 0);
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('parser_backend', 'mineru_api', 0);
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('sql_timeout', '30', 0);
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('mineru_api_token', '', 1);
INSERT IGNORE INTO app_settings (`key`, value, is_secret) VALUES
  ('tavily_api_key', '', 1);

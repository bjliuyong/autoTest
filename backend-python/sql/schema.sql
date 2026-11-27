-- Gate AutoTest Studio 共享后端 MySQL 8 建表脚本
CREATE DATABASE IF NOT EXISTS `gate_autotest` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `gate_autotest`;

CREATE TABLE IF NOT EXISTS `sys_roles` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `code` VARCHAR(64) NOT NULL UNIQUE,
  `name` VARCHAR(64) NOT NULL,
  `description` VARCHAR(255) NOT NULL DEFAULT '',
  `permissions_json` TEXT NOT NULL,
  `is_system` TINYINT(1) NOT NULL DEFAULT 0,
  `created_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `sys_users` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(64) NOT NULL UNIQUE,
  `nickname` VARCHAR(64) NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `role_code` VARCHAR(64) NOT NULL DEFAULT 'tester',
  `department` VARCHAR(128) NOT NULL DEFAULT '质量保障部',
  `email` VARCHAR(128) NOT NULL DEFAULT '',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active',
  `last_login_at` VARCHAR(64) NULL,
  `created_at` VARCHAR(64) NOT NULL,
  INDEX `idx_users_role` (`role_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `sys_audit_logs` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NULL,
  `username` VARCHAR(64) NOT NULL,
  `module` VARCHAR(64) NOT NULL,
  `action` VARCHAR(128) NOT NULL,
  `detail` TEXT NOT NULL,
  `ip` VARCHAR(64) NOT NULL DEFAULT '',
  `created_at` VARCHAR(64) NOT NULL,
  INDEX `idx_audit_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `test_batches` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `run_code` VARCHAR(64) NOT NULL UNIQUE,
  `name` VARCHAR(255) NOT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'running',
  `gate_mode` VARCHAR(32) NOT NULL DEFAULT 'both',
  `log_source` VARCHAR(32) NOT NULL DEFAULT 'ws',
  `total_cases` INT NOT NULL DEFAULT 0,
  `passed_cases` INT NOT NULL DEFAULT 0,
  `failed_cases` INT NOT NULL DEFAULT 0,
  `timeout_cases` INT NOT NULL DEFAULT 0,
  `pass_rate` DOUBLE NOT NULL DEFAULT 0,
  `created_by` VARCHAR(64) NOT NULL DEFAULT 'admin',
  `started_at` VARCHAR(64) NOT NULL,
  `ended_at` VARCHAR(64) NULL,
  `duration_ms` BIGINT NOT NULL DEFAULT 0,
  `config_snapshot_json` LONGTEXT NOT NULL,
  `notes` TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `test_cases` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `batch_id` BIGINT NOT NULL,
  `case_id` VARCHAR(128) NOT NULL,
  `serial` VARCHAR(128) NULL,
  `round_key` VARCHAR(128) NULL,
  `passenger_name` VARCHAR(128) NOT NULL DEFAULT '',
  `cert_type` VARCHAR(64) NOT NULL DEFAULT '',
  `cert_no` VARCHAR(128) NOT NULL DEFAULT '',
  `gate_id` VARCHAR(64) NOT NULL DEFAULT '',
  `photo_data` LONGTEXT NULL,
  `expected_allow` TINYINT(1) NOT NULL,
  `expected_node` VARCHAR(128) NULL,
  `actual_allow` TINYINT(1) NULL,
  `actual_node` VARCHAR(128) NULL,
  `actual_nodes_json` TEXT NOT NULL,
  `verdict_status` VARCHAR(32) NOT NULL,
  `passed` TINYINT(1) NOT NULL DEFAULT 0,
  `timed_out` TINYINT(1) NOT NULL DEFAULT 0,
  `contradictory` TINYINT(1) NOT NULL DEFAULT 0,
  `session_id` VARCHAR(128) NULL,
  `reasons_json` TEXT NOT NULL,
  `resolution_note` TEXT NOT NULL,
  `capture_json` LONGTEXT NOT NULL,
  `raw_json` LONGTEXT NOT NULL,
  `gate_messages_json` LONGTEXT NOT NULL,
  `log_slice` LONGTEXT NOT NULL,
  `analysis_status` VARCHAR(32) NOT NULL DEFAULT 'none',
  `analysis_json` LONGTEXT NULL,
  `created_by` VARCHAR(64) NOT NULL DEFAULT 'admin',
  `captured_at` VARCHAR(64) NOT NULL,
  `judged_at` VARCHAR(64) NOT NULL,
  INDEX `idx_cases_batch` (`batch_id`),
  INDEX `idx_cases_verdict` (`verdict_status`),
  INDEX `idx_cases_case_id` (`case_id`),
  CONSTRAINT `fk_cases_batch` FOREIGN KEY (`batch_id`) REFERENCES `test_batches`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `system_logs` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `batch_id` BIGINT NULL,
  `level` VARCHAR(16) NOT NULL DEFAULT 'INFO',
  `source` VARCHAR(64) NOT NULL DEFAULT 'engine',
  `message` TEXT NOT NULL,
  `operator` VARCHAR(64) NOT NULL DEFAULT 'system',
  `created_at` VARCHAR(64) NOT NULL,
  INDEX `idx_syslogs_batch` (`batch_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `app_configs` (
  `config_key` VARCHAR(64) PRIMARY KEY,
  `config_json` LONGTEXT NOT NULL,
  `updated_by` VARCHAR(64) NOT NULL DEFAULT 'admin',
  `updated_at` VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

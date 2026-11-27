-- -------------------------------------------------------------
-- 自动化测试系统数据库扩展脚本（持久化存储 + 菜单配置）
-- -------------------------------------------------------------
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- 1. 测试批次表
DROP TABLE IF EXISTS `auto_test_batch`;
CREATE TABLE `auto_test_batch` (
  `batch_id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '批次ID',
  `batch_no` varchar(64) NOT NULL COMMENT '批次号(唯一编码)',
  `batch_name` varchar(128) DEFAULT '' COMMENT '批次名称',
  `status` varchar(32) NOT NULL DEFAULT 'IDLE' COMMENT '状态(IDLE/RUNNING/COMPLETED/STOPPED/FAILED)',
  `gate_ws_url` varchar(255) DEFAULT '' COMMENT '闸机WS连接地址',
  `log_ws_url` varchar(255) DEFAULT '' COMMENT '待测日志WS连接地址',
  `total_cases` int(11) DEFAULT 0 COMMENT '总用例数',
  `pass_cases` int(11) DEFAULT 0 COMMENT '通过用例数',
  `fail_cases` int(11) DEFAULT 0 COMMENT '失败用例数',
  `timeout_cases` int(11) DEFAULT 0 COMMENT '超时用例数',
  `pass_rate` decimal(5,2) DEFAULT 0.00 COMMENT '通过率百分比',
  `start_time` datetime DEFAULT NULL COMMENT '启动时间',
  `end_time` datetime DEFAULT NULL COMMENT '结束时间',
  `duration_seconds` bigint(20) DEFAULT 0 COMMENT '执行耗时(秒)',
  `remark` varchar(500) DEFAULT '' COMMENT '备注说明',
  `create_by` varchar(64) DEFAULT '' COMMENT '创建者',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_by` varchar(64) DEFAULT '' COMMENT '更新者',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`batch_id`),
  UNIQUE KEY `uk_batch_no` (`batch_no`),
  KEY `idx_batch_status` (`status`),
  KEY `idx_batch_create_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自动化测试批次表';

-- 2. 测试用例记录表（持久化追溯：包含旅客信息、比对详情、照片、日志切片）
DROP TABLE IF EXISTS `auto_test_case`;
CREATE TABLE `auto_test_case` (
  `case_record_id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '用例记录主键',
  `batch_id` bigint(20) NOT NULL COMMENT '归属测试批次ID',
  `batch_no` varchar(64) NOT NULL COMMENT '批次号',
  `case_id` varchar(64) NOT NULL COMMENT '用例编号',
  `serial` varchar(128) DEFAULT '' COMMENT '单次采集唯一序号',
  `gate_id` varchar(64) DEFAULT '' COMMENT '闸机编号',
  `passenger_name` varchar(64) DEFAULT '' COMMENT '旅客姓名',
  `cert_type` varchar(32) DEFAULT 'ID_CARD' COMMENT '证件类型',
  `cert_no` varchar(64) DEFAULT '' COMMENT '证件号码',
  `photo_url` longtext COMMENT '采集照片(Base64或路径/URL)',
  `session_id` varchar(64) DEFAULT '' COMMENT '关联的待测系统会话ID',
  `expected_allow_pass` tinyint(1) NOT NULL COMMENT '预期是否放行(1:放行, 0:拦截)',
  `expected_failed_node` varchar(128) DEFAULT '' COMMENT '预期失败节点名称',
  `actual_allow_pass` tinyint(1) DEFAULT NULL COMMENT '实际是否放行(1:放行, 0:拦截)',
  `actual_failed_node` varchar(128) DEFAULT '' COMMENT '实际失败节点名称',
  `verdict` varchar(32) NOT NULL COMMENT '判定结果(PASS:通过, FAIL:不通过, TIMEOUT:超时, ERROR:异常)',
  `reasons` text COMMENT '判定结论与原因详情',
  `raw_capture` longtext COMMENT '闸机原始推送采集报文(JSON)',
  `log_slice` longtext COMMENT '该旅客在待测系统中的全量日志切片',
  `duration_ms` bigint(20) DEFAULT 0 COMMENT '从采集到判定耗时(毫秒)',
  `received_time` datetime DEFAULT NULL COMMENT '用例接收时间',
  `finish_time` datetime DEFAULT NULL COMMENT '判定完成时间',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (`case_record_id`),
  KEY `idx_case_batch_id` (`batch_id`),
  KEY `idx_case_id` (`case_id`),
  KEY `idx_verdict` (`verdict`),
  KEY `idx_passenger_cert` (`cert_no`),
  KEY `idx_passenger_name` (`passenger_name`),
  KEY `idx_received_time` (`received_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自动化测试用例记录表';

-- 3. 测试配置参数表
DROP TABLE IF EXISTS `auto_test_config`;
CREATE TABLE `auto_test_config` (
  `config_id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '配置主键',
  `config_name` varchar(128) NOT NULL DEFAULT '默认配置' COMMENT '配置方案名称',
  `gate_ws_url` varchar(255) DEFAULT 'ws://127.0.0.1:8765/gate' COMMENT '闸机WS地址',
  `log_ws_url` varchar(255) DEFAULT 'ws://127.0.0.1:8766/logs' COMMENT '日志WS地址',
  `is_gate_server` tinyint(1) DEFAULT 0 COMMENT '是否作为闸机服务端监听(1:是, 0:否)',
  `gate_server_port` int(11) DEFAULT 8765 COMMENT '作为服务端时的监听端口',
  `timeout_seconds` int(11) DEFAULT 15 COMMENT '用例日志等待超时时间(秒)',
  `session_create_pattern` text COMMENT '会话创建正则',
  `node_failure_pattern` text COMMENT '节点失败正则',
  `passenger_exit_pattern` text COMMENT '旅客放行/离开正则',
  `node_aliases` text COMMENT '节点别名映射(JSON)',
  `is_default` tinyint(1) DEFAULT 1 COMMENT '是否默认配置(1:是, 0:否)',
  `remark` varchar(500) DEFAULT '' COMMENT '说明',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`config_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自动化测试参数配置表';

-- 插入默认测试配置
INSERT INTO `auto_test_config` (
  `config_id`, `config_name`, `gate_ws_url`, `log_ws_url`, `is_gate_server`, `gate_server_port`,
  `timeout_seconds`, `session_create_pattern`, `node_failure_pattern`, `passenger_exit_pattern`,
  `node_aliases`, `is_default`, `remark`
) VALUES (
  1,
  '默认闸机测试配置',
  'ws://127.0.0.1:8765/gate',
  'ws://127.0.0.1:8766/logs',
  0,
  8765,
  15,
  '\\[流程引擎\\]\\s*创建新会话[：:\\s]*\\[?(?P<sid>[^\\s\\[\\]，,。;；]+)\\]?',
  '会话ID\\[(?P<sid>[^\\]]+)\\]\\s*[-—－–:：]\\s*节点\\s*(?P<node>.+?)\\s*执行失败',
  '旅客\\[(?P<key>[^\\]]+)\\][^\\n]{0,16}?离开通道\n旅客\\[(?P<key>[^\\]]+)\\][^\\n]{0,16}?(?:已放行|开闸成功|闸门已开)\n离开通道\n(?:已放行|开闸成功|闸门已开)',
  '{"人脸比对": "人脸识别", "证件核验": "人证核验", "行程校验": "客票核验"}',
  1,
  '兼容真实闸机通道与流程引擎日志解析'
) ON DUPLICATE KEY UPDATE `config_name` = VALUES(`config_name`);

-- 4. 插入自动化测试系统菜单
DELETE FROM `sys_menu` WHERE `menu_id` IN (2000, 2001, 2002, 2003, 2004);

INSERT INTO `sys_menu` (
  `menu_id`, `menu_name`, `parent_id`, `order_num`, `path`, `component`,
  `query`, `route_name`, `is_frame`, `is_cache`, `menu_type`, `visible`,
  `status`, `perms`, `icon`, `create_by`, `create_time`, `remark`
) VALUES 
(2000, '自动化测试', 0, 4, 'autotest', NULL, '', '', 1, 0, 'M', '0', '0', '', 'example', 'admin', sysdate(), '自动化测试根目录'),
(2001, '测试大盘', 2000, 1, 'dashboard', 'autotest/dashboard/index', '', '', 1, 0, 'C', '0', '0', 'autotest:dashboard:view', 'dashboard', 'admin', sysdate(), '测试监控大盘'),
(2002, '测试批次', 2000, 2, 'batch', 'autotest/batch/index', '', '', 1, 0, 'C', '0', '0', 'autotest:batch:list', 'nested', 'admin', sysdate(), '测试批次历史'),
(2003, '用例追溯', 2000, 3, 'case', 'autotest/case/index', '', '', 1, 0, 'C', '0', '0', 'autotest:case:list', 'search', 'admin', sysdate(), '用例追溯与详情'),
(2004, '测试配置', 2000, 4, 'config', 'autotest/config/index', '', '', 1, 0, 'C', '0', '0', 'autotest:config:list', 'build', 'admin', sysdate(), '测试配置参数');

-- 赋予超级管理员权限 (role_id = 1)
DELETE FROM `sys_role_menu` WHERE `menu_id` IN (2000, 2001, 2002, 2003, 2004);
INSERT INTO `sys_role_menu` (`role_id`, `menu_id`) VALUES
(1, 2000), (1, 2001), (1, 2002), (1, 2003), (1, 2004);

SET FOREIGN_KEY_CHECKS = 1;

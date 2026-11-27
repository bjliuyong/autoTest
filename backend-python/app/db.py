"""关系型数据库持久层：支持 SQLite (WAL 并发模式，零依赖开箱即用) 与 MySQL 8 (pymysql)。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth import DEFAULT_ROLES, hash_password

DEFAULT_CONFIG: dict[str, Any] = {
    "run": {
        "caseTimeoutSeconds": 30,
        "maxBufferLines": 200000,
        "captureToleranceLines": 5,
        "sliceIncludePassengerLines": True,
        "maxSliceLines": 1500,
        "sliceLeadLines": 60,
        "singleSessionFallback": True,
        "httpApiEnabled": True,
        "httpApiHost": "127.0.0.1",
        "httpApiPort": 8767,
    },
    "gate": {
        "mode": "both",
        "url": "ws://127.0.0.1:1880",
        "listenHost": "0.0.0.0",
        "listenPort": 8765,
        "listenPath": "/gate",
        "reconnectSeconds": 2,
        "maxReconnectSeconds": 10,
        "sendAck": False,
        "subscription": None,
        "fieldMap": {},
        "roundKeys": [
            "serial",
            "Serial",
            "serialNo",
            "serial_no",
            "requestId",
            "request_id",
            "capture.serial",
            "capture.cert_no",
            "cert_no",
        ],
        "roundSplit": "_",
        "roundTtlSeconds": 600,
    },
    "logs": {
        "source": "ws",
        "url": "ws://127.0.0.1:8766/logs",
        "messageFormat": "auto",
        "textFields": ["line", "text", "message", "msg", "content", "log"],
        "subscription": None,
        "filePath": "",
        "filePrefix": "",
        "fileDateFormat": "YYYY-MM-DD",
        "fileExtension": ".log",
        "fileEncoding": "auto",
        "pollIntervalMs": 500,
        "fromBeginning": False,
        "followRotation": True,
        "maxFieldChars": 100,
        "dropUndecodable": True,
    },
    "patterns": {
        "sessionCreate": [r"\[流程引擎\]\s*创建新会话[：:\s]*\[?(?<sid>[^\s\[\]，,。;；]+)\]?"],
        "nodeFailure": [r"会话ID\[(?<sid>[^\]]+)\]\s*[-—－–:：]\s*节点\s*(?<node>.+?)\s*执行失败"],
        "passengerExit": [
            r"旅客\[(?<key>[^\]]+)\][^\n]{0,16}?离开通道",
            r"旅客\[(?<key>[^\]]+)\][^\n]{0,16}?(?:已放行|开闸成功|闸门已开)",
            r"离开通道",
            r"(?:已放行|开闸成功|闸门已开)",
        ],
        "passengerMarker": r"旅客\[(?<key>[^\]]+)\]",
    },
    "judge": {
        "nodeAliases": {},
        "passengerKeys": ["cert_no", "case_id", "passenger_name", "id_no", "name", "passengerName", "certNo"],
        "contradictionAsFailure": True,
    },
    "dsh": {
        "enabled": False,
        "command": "dsh",
        "profile": "headless",
        "timeoutSeconds": 300,
        "codeAnalysis": True,
        "codePath": "",
        "codeSubdir": "",
        "allowCodeFix": False,
        "branchTemplate": "{time}-修复{node}error",
    },
}

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sys_roles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  permissions_json TEXT NOT NULL,
  is_system INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sys_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  nickname TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role_code TEXT NOT NULL DEFAULT 'tester',
  department TEXT NOT NULL DEFAULT '质量保障部',
  email TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  last_login_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sys_audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  username TEXT NOT NULL,
  module TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  gate_mode TEXT NOT NULL DEFAULT 'both',
  log_source TEXT NOT NULL DEFAULT 'ws',
  total_cases INTEGER NOT NULL DEFAULT 0,
  passed_cases INTEGER NOT NULL DEFAULT 0,
  failed_cases INTEGER NOT NULL DEFAULT 0,
  timeout_cases INTEGER NOT NULL DEFAULT 0,
  pass_rate REAL NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL DEFAULT 'admin',
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  config_snapshot_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL,
  case_id TEXT NOT NULL,
  serial TEXT,
  round_key TEXT,
  passenger_name TEXT NOT NULL DEFAULT '',
  cert_type TEXT NOT NULL DEFAULT '',
  cert_no TEXT NOT NULL DEFAULT '',
  gate_id TEXT NOT NULL DEFAULT '',
  photo_data TEXT,
  expected_allow INTEGER NOT NULL,
  expected_node TEXT,
  actual_allow INTEGER,
  actual_node TEXT,
  actual_nodes_json TEXT NOT NULL DEFAULT '[]',
  verdict_status TEXT NOT NULL,
  passed INTEGER NOT NULL DEFAULT 0,
  timed_out INTEGER NOT NULL DEFAULT 0,
  contradictory INTEGER NOT NULL DEFAULT 0,
  session_id TEXT,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  resolution_note TEXT NOT NULL DEFAULT '',
  capture_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL DEFAULT '{}',
  gate_messages_json TEXT NOT NULL DEFAULT '[]',
  log_slice TEXT NOT NULL DEFAULT '',
  analysis_status TEXT NOT NULL DEFAULT 'none',
  analysis_json TEXT,
  created_by TEXT NOT NULL DEFAULT 'admin',
  captured_at TEXT NOT NULL,
  judged_at TEXT NOT NULL,
  FOREIGN KEY (batch_id) REFERENCES test_batches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cases_batch ON test_cases(batch_id);
CREATE INDEX IF NOT EXISTS idx_cases_verdict ON test_cases(verdict_status);
CREATE INDEX IF NOT EXISTS idx_cases_case_id ON test_cases(case_id);

CREATE TABLE IF NOT EXISTS system_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER,
  level TEXT NOT NULL DEFAULT 'INFO',
  source TEXT NOT NULL DEFAULT 'engine',
  message TEXT NOT NULL,
  operator TEXT NOT NULL DEFAULT 'system',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_configs (
  config_key TEXT PRIMARY KEY,
  config_json TEXT NOT NULL,
  updated_by TEXT NOT NULL DEFAULT 'admin',
  updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_json(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return fallback


class DatabaseRepository:
    """线程安全的服务端数据库访问层，支持 PostgreSQL (pg8000，离线自带) / SQLite / MySQL。"""

    def __init__(self) -> None:
        raw_type = os.environ.get("DB_TYPE", "postgres").strip().lower()
        if raw_type in ("pg", "postgresql", "postgres"):
            self.db_type = "postgres"
        elif raw_type == "mysql":
            self.db_type = "mysql"
        else:
            self.db_type = "sqlite"
        self._lock = threading.RLock()
        self._sqlite_path = Path(
            os.environ.get(
                "SQLITE_PATH",
                str(Path(__file__).resolve().parent.parent / "data" / "autotest_shared.db"),
            )
        )
        self._conn: Any = None
        self.data_version = 1

    def _bump(self) -> None:
        self.data_version += 1

    def _placeholder(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.db_type in ("postgres", "mysql") else sql

    def _get_conn(self) -> Any:
        if self.db_type == "postgres":
            if self._conn is not None:
                try:
                    cur = self._conn.cursor()
                    cur.execute("SELECT 1")
                    cur.close()
                    return self._conn
                except Exception:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
                    self._conn = None

            import pg8000.dbapi

            conn = pg8000.dbapi.connect(
                host=os.environ.get("PG_HOST", "127.0.0.1"),
                port=int(os.environ.get("PG_PORT", "5432")),
                user=os.environ.get("PG_USER", "postgres"),
                password=os.environ.get("PG_PASSWORD", "postgres"),
                database=os.environ.get("PG_DATABASE", "gate_autotest"),
            )
            conn.autocommit = True
            self._conn = conn
            return self._conn

        if self.db_type == "mysql":
            import pymysql
            import pymysql.cursors

            if self._conn is None or not self._conn.open:
                self._conn = pymysql.connect(
                    host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                    port=int(os.environ.get("MYSQL_PORT", "3306")),
                    user=os.environ.get("MYSQL_USER", "root"),
                    password=os.environ.get("MYSQL_PASSWORD", "autotest123"),
                    database=os.environ.get("MYSQL_DATABASE", "gate_autotest"),
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True,
                )
            else:
                self._conn.ping(reconnect=True)
            return self._conn

        if self._conn is None:
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._sqlite_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._conn = conn
        return self._conn

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(self._placeholder(sql), params)
            rows = cur.fetchall()
            if self.db_type == "postgres":
                cols = [desc[0] for desc in (cur.description or ())]
                result = [dict(zip(cols, row)) for row in rows]
            else:
                result = [dict(r) for r in rows]
            cur.close()
            return result

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._fetchall(sql, params)
        return rows[0] if rows else None

    def _execute(self, sql: str, params: tuple[Any, ...] = (), *, returning_id: bool = False) -> int:
        with self._lock:
            conn = self._get_conn()
            cur = conn.cursor()
            exec_sql = self._placeholder(sql)
            if self.db_type == "postgres" and returning_id and "RETURNING" not in exec_sql.upper():
                exec_sql = f"{exec_sql.rstrip().rstrip(';')} RETURNING id"
            cur.execute(exec_sql, params)
            if self.db_type == "postgres" and returning_id:
                row = cur.fetchone()
                last_id = int(row[0]) if row else 0
            else:
                last_id = int(getattr(cur, "lastrowid", 0) or 0)
            if self.db_type == "sqlite":
                conn.commit()
            cur.close()
            return last_id

    def init(self) -> None:
        with self._lock:
            conn = self._get_conn()
            if self.db_type == "postgres":
                schema_file = Path(__file__).resolve().parent.parent / "sql" / "schema_pg.sql"
                if schema_file.exists():
                    cur = conn.cursor()
                    for stmt in schema_file.read_text(encoding="utf-8").split(";"):
                        s = "\n".join(line for line in stmt.splitlines() if not line.strip().startswith("--")).strip()
                        if s:
                            cur.execute(s)
                    cur.close()
            elif self.db_type == "mysql":
                schema_file = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
                if schema_file.exists():
                    cur = conn.cursor()
                    for stmt in schema_file.read_text(encoding="utf-8").split(";"):
                        s = stmt.strip()
                        if s and not s.upper().startswith(("CREATE DATABASE", "USE ")):
                            cur.execute(s)
                    cur.close()
            else:
                conn.executescript(SQLITE_SCHEMA)
                conn.commit()

            now = now_iso()
            for role in DEFAULT_ROLES:
                existing = self._fetchone("SELECT id FROM sys_roles WHERE code = ?", (role["code"],))
                if not existing:
                    self._execute(
                        "INSERT INTO sys_roles (code, name, description, permissions_json, is_system, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            role["code"],
                            role["name"],
                            role["description"],
                            json.dumps(role["permissions"], ensure_ascii=False),
                            1 if role["isSystem"] else 0,
                            now,
                        ),
                    )

            cnt = self._fetchone("SELECT COUNT(*) AS cnt FROM sys_users")
            if not cnt or int(cnt["cnt"]) == 0:
                self._execute(
                    "INSERT INTO sys_users (username, nickname, password_hash, role_code, department, email, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
                    ("admin", "系统管理员", hash_password("admin123"), "admin", "平台管理部", "admin@autotest.local", now),
                )
                self._execute(
                    "INSERT INTO sys_users (username, nickname, password_hash, role_code, department, email, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
                    ("tester", "测试工程师", hash_password("tester123"), "tester", "质量保障部", "tester@autotest.local", now),
                )

            cfg = self._fetchone("SELECT config_key FROM app_configs WHERE config_key = 'default'")
            if not cfg:
                self._execute(
                    "INSERT INTO app_configs (config_key, config_json, updated_by, updated_at) VALUES ('default', ?, 'admin', ?)",
                    (json.dumps(DEFAULT_CONFIG, ensure_ascii=False), now),
                )

    # ---------------- Users ----------------
    def get_user_raw_by_username(self, username: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM sys_users WHERE username = ?", (username,))

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT u.*, r.name AS role_name, r.permissions_json
               FROM sys_users u LEFT JOIN sys_roles r ON r.code = u.role_code
               WHERE u.id = ?""",
            (user_id,),
        )
        return self._map_user(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            """SELECT u.*, r.name AS role_name, r.permissions_json
               FROM sys_users u LEFT JOIN sys_roles r ON r.code = u.role_code
               ORDER BY u.id ASC"""
        )
        return [self._map_user(r) for r in rows]

    def _map_user(self, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "username": r["username"],
            "nickname": r["nickname"],
            "roleCode": r["role_code"],
            "roleName": r.get("role_name") or r["role_code"],
            "permissions": safe_json(r.get("permissions_json"), []),
            "department": r.get("department") or "",
            "email": r.get("email") or "",
            "status": r.get("status") or "active",
            "lastLoginAt": r.get("last_login_at"),
            "createdAt": r.get("created_at") or "",
        }

    def create_user(self, data: dict[str, Any]) -> dict[str, Any]:
        username = str(data.get("username") or "").strip()
        if not username:
            raise ValueError("用户名不能为空")
        if self.get_user_raw_by_username(username):
            raise ValueError(f"用户名 {username} 已存在")
        uid = self._execute(
            """INSERT INTO sys_users (username, nickname, password_hash, role_code, department, email, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                username,
                str(data.get("nickname") or username).strip(),
                hash_password(str(data.get("password") or "123456")),
                str(data.get("roleCode") or "tester"),
                str(data.get("department") or "质量保障部"),
                str(data.get("email") or ""),
                str(data.get("status") or "active"),
                now_iso(),
            ),
            returning_id=True,
        )
        self._bump()
        return self.get_user_by_id(uid)  # type: ignore[return-value]

    def update_user(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_user_by_id(user_id)
        if not existing:
            raise ValueError("用户不存在")
        role_code = "admin" if existing["username"] == "admin" else str(data.get("roleCode", existing["roleCode"]))
        status = "active" if existing["username"] == "admin" else str(data.get("status", existing["status"]))
        self._execute(
            "UPDATE sys_users SET nickname=?, role_code=?, department=?, email=?, status=? WHERE id=?",
            (
                str(data.get("nickname", existing["nickname"])),
                role_code,
                str(data.get("department", existing["department"])),
                str(data.get("email", existing["email"])),
                status,
                user_id,
            ),
        )
        self._bump()
        return self.get_user_by_id(user_id)  # type: ignore[return-value]

    def reset_password(self, user_id: int, password: str) -> None:
        self._execute("UPDATE sys_users SET password_hash=? WHERE id=?", (hash_password(password), user_id))

    def delete_user(self, user_id: int) -> None:
        existing = self.get_user_by_id(user_id)
        if not existing:
            return
        if existing["username"] == "admin":
            raise ValueError("不能删除内置超级管理员 admin")
        self._execute("DELETE FROM sys_users WHERE id=?", (user_id,))
        self._bump()

    def touch_login(self, user_id: int) -> None:
        self._execute("UPDATE sys_users SET last_login_at=? WHERE id=?", (now_iso(), user_id))

    # ---------------- Roles ----------------
    def list_roles(self) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM sys_roles ORDER BY id ASC")
        return [
            {
                "id": int(r["id"]),
                "code": r["code"],
                "name": r["name"],
                "description": r.get("description") or "",
                "permissions": safe_json(r.get("permissions_json"), []),
                "isSystem": bool(r.get("is_system")),
                "createdAt": r.get("created_at") or "",
            }
            for r in rows
        ]

    def get_role(self, code: str) -> dict[str, Any] | None:
        for r in self.list_roles():
            if r["code"] == code:
                return r
        return None

    def create_role(self, data: dict[str, Any]) -> dict[str, Any]:
        code = str(data.get("code") or "").strip()
        name = str(data.get("name") or "").strip()
        if not code or not name:
            raise ValueError("角色标识和名称不能为空")
        if self.get_role(code):
            raise ValueError(f"角色标识 {code} 已存在")
        self._execute(
            "INSERT INTO sys_roles (code, name, description, permissions_json, is_system, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (code, name, str(data.get("description") or ""), json.dumps(data.get("permissions") or [], ensure_ascii=False), now_iso()),
        )
        self._bump()
        return self.get_role(code)  # type: ignore[return-value]

    def update_role(self, code: str, data: dict[str, Any]) -> dict[str, Any]:
        role = self.get_role(code)
        if not role:
            raise ValueError("角色不存在")
        perms = role["permissions"] if code == "admin" else data.get("permissions", role["permissions"])
        self._execute(
            "UPDATE sys_roles SET name=?, description=?, permissions_json=? WHERE code=?",
            (str(data.get("name", role["name"])), str(data.get("description", role["description"])), json.dumps(perms, ensure_ascii=False), code),
        )
        self._bump()
        return self.get_role(code)  # type: ignore[return-value]

    def delete_role(self, code: str) -> None:
        role = self.get_role(code)
        if not role:
            return
        if role["isSystem"]:
            raise ValueError("不能删除系统内置角色")
        self._execute("DELETE FROM sys_roles WHERE code=?", (code,))
        self._bump()

    # ---------------- Audit Logs ----------------
    def add_audit_log(self, user_id: int | None, username: str, module: str, action: str, detail: str, ip: str) -> None:
        self._execute(
            "INSERT INTO sys_audit_logs (user_id, username, module, action, detail, ip, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, module, action, detail, ip, now_iso()),
        )
        self._bump()

    def list_audit_logs(self, limit: int = 150) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM sys_audit_logs ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "id": int(r["id"]),
                "userId": r.get("user_id"),
                "username": r["username"],
                "module": r["module"],
                "action": r["action"],
                "detail": r["detail"],
                "ip": r.get("ip") or "",
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    # ---------------- Config ----------------
    def get_config(self) -> dict[str, Any]:
        row = self._fetchone("SELECT config_json FROM app_configs WHERE config_key='default'")
        if not row:
            return json.loads(json.dumps(DEFAULT_CONFIG))
        saved = safe_json(row["config_json"], {})
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged

    def save_config(self, cfg: dict[str, Any], updated_by: str) -> dict[str, Any]:
        now = now_iso()
        exists = self._fetchone("SELECT config_key FROM app_configs WHERE config_key='default'")
        payload = json.dumps(cfg, ensure_ascii=False)
        if exists:
            self._execute("UPDATE app_configs SET config_json=?, updated_by=?, updated_at=? WHERE config_key='default'", (payload, updated_by, now))
        else:
            self._execute("INSERT INTO app_configs (config_key, config_json, updated_by, updated_at) VALUES ('default', ?, ?, ?)", (payload, updated_by, now))
        self._bump()
        return self.get_config()

    def reset_config(self, updated_by: str) -> dict[str, Any]:
        return self.save_config(DEFAULT_CONFIG, updated_by)

    # ---------------- Batches ----------------
    def _map_batch(self, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "runCode": r["run_code"],
            "name": r["name"],
            "status": r["status"],
            "gateMode": r["gate_mode"],
            "logSource": r["log_source"],
            "totalCases": int(r.get("total_cases") or 0),
            "passedCases": int(r.get("passed_cases") or 0),
            "failedCases": int(r.get("failed_cases") or 0),
            "timeoutCases": int(r.get("timeout_cases") or 0),
            "passRate": float(r.get("pass_rate") or 0),
            "createdBy": r.get("created_by") or "admin",
            "startedAt": r["started_at"],
            "endedAt": r.get("ended_at"),
            "durationMs": int(r.get("duration_ms") or 0),
            "notes": r.get("notes") or "",
        }

    def create_batch(self, data: dict[str, Any], created_by: str) -> dict[str, Any]:
        bid = self._execute(
            """INSERT INTO test_batches (run_code, name, status, gate_mode, log_source, created_by, started_at, config_snapshot_json, notes)
               VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)""",
            (
                data["runCode"],
                data["name"],
                data.get("gateMode") or "both",
                data.get("logSource") or "ws",
                created_by,
                now_iso(),
                json.dumps(data.get("configSnapshot") or {}, ensure_ascii=False),
                data.get("notes") or "",
            ),
            returning_id=True,
        )
        self._bump()
        return self.get_batch(bid)  # type: ignore[return-value]

    def finish_batch(self, batch_id: int, status: str = "completed") -> dict[str, Any] | None:
        batch = self.get_batch(batch_id)
        if not batch:
            return None
        ended = now_iso()
        try:
            t1 = datetime.fromisoformat(batch["startedAt"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(ended.replace("Z", "+00:00"))
            dur = max(0, int((t2 - t1).total_seconds() * 1000))
        except Exception:
            dur = 0
        self._execute("UPDATE test_batches SET status=?, ended_at=?, duration_ms=? WHERE id=?", (status, ended, dur, batch_id))
        self._bump()
        return self.get_batch(batch_id)

    def _recompute_batch(self, batch_id: int) -> dict[str, Any] | None:
        agg = self._fetchone(
            """SELECT COUNT(*) AS total,
               SUM(CASE WHEN verdict_status='PASS' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN verdict_status IN ('FAIL','ERROR') THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN verdict_status='TIMEOUT' THEN 1 ELSE 0 END) AS timed_out
               FROM test_cases WHERE batch_id=?""",
            (batch_id,),
        )
        total = int((agg or {}).get("total") or 0)
        passed = int((agg or {}).get("passed") or 0)
        failed = int((agg or {}).get("failed") or 0)
        timeout = int((agg or {}).get("timed_out") or 0)
        rate = round((passed / total) * 100, 1) if total > 0 else 0.0
        self._execute(
            "UPDATE test_batches SET total_cases=?, passed_cases=?, failed_cases=?, timeout_cases=?, pass_rate=? WHERE id=?",
            (total, passed, failed, timeout, rate, batch_id),
        )
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM test_batches WHERE id=?", (batch_id,))
        return self._map_batch(row) if row else None

    def list_batches(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM test_batches ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return [self._map_batch(r) for r in rows]

    def delete_batch(self, batch_id: int) -> None:
        self._execute("DELETE FROM system_logs WHERE batch_id=?", (batch_id,))
        self._execute("DELETE FROM test_cases WHERE batch_id=?", (batch_id,))
        self._execute("DELETE FROM test_batches WHERE id=?", (batch_id,))
        self._bump()

    # ---------------- Cases ----------------
    def _map_case(self, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "batchId": int(r["batch_id"]),
            "batchRunCode": r.get("batch_run_code"),
            "caseId": r["case_id"],
            "serial": r.get("serial"),
            "roundKey": r.get("round_key"),
            "passengerName": r.get("passenger_name") or "",
            "certType": r.get("cert_type") or "",
            "certNo": r.get("cert_no") or "",
            "gateId": r.get("gate_id") or "",
            "photoData": r.get("photo_data"),
            "expectedAllow": bool(r["expected_allow"]),
            "expectedNode": r.get("expected_node"),
            "actualAllow": None if r.get("actual_allow") is None else bool(r["actual_allow"]),
            "actualNode": r.get("actual_node"),
            "actualNodes": safe_json(r.get("actual_nodes_json"), []),
            "verdictStatus": r["verdict_status"],
            "passed": bool(r["passed"]),
            "timedOut": bool(r["timed_out"]),
            "contradictory": bool(r["contradictory"]),
            "sessionId": r.get("session_id"),
            "reasons": safe_json(r.get("reasons_json"), []),
            "resolutionNote": r.get("resolution_note") or "",
            "capture": safe_json(r.get("capture_json"), {}),
            "raw": safe_json(r.get("raw_json"), {}),
            "gateMessages": safe_json(r.get("gate_messages_json"), []),
            "logSlice": r.get("log_slice") or "",
            "analysisStatus": r.get("analysis_status") or "none",
            "analysis": safe_json(r.get("analysis_json"), None),
            "createdBy": r.get("created_by") or "admin",
            "capturedAt": r["captured_at"],
            "judgedAt": r["judged_at"],
        }

    def insert_case(self, data: dict[str, Any], created_by: str) -> dict[str, Any]:
        cid = self._execute(
            """INSERT INTO test_cases (
                 batch_id, case_id, serial, round_key, passenger_name, cert_type, cert_no, gate_id, photo_data,
                 expected_allow, expected_node, actual_allow, actual_node, actual_nodes_json,
                 verdict_status, passed, timed_out, contradictory, session_id, reasons_json, resolution_note,
                 capture_json, raw_json, gate_messages_json, log_slice, analysis_status, created_by, captured_at, judged_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(data["batchId"]),
                data["caseId"],
                data.get("serial"),
                data.get("roundKey"),
                data.get("passengerName") or "",
                data.get("certType") or "",
                data.get("certNo") or "",
                data.get("gateId") or "",
                data.get("photoData"),
                1 if data.get("expectedAllow") else 0,
                data.get("expectedNode"),
                None if data.get("actualAllow") is None else (1 if data.get("actualAllow") else 0),
                data.get("actualNode"),
                json.dumps(data.get("actualNodes") or [], ensure_ascii=False),
                data["verdictStatus"],
                1 if data.get("passed") else 0,
                1 if data.get("timedOut") else 0,
                1 if data.get("contradictory") else 0,
                data.get("sessionId"),
                json.dumps(data.get("reasons") or [], ensure_ascii=False),
                data.get("resolutionNote") or "",
                json.dumps(data.get("capture") or {}, ensure_ascii=False),
                json.dumps(data.get("raw") or {}, ensure_ascii=False),
                json.dumps(data.get("gateMessages") or [], ensure_ascii=False),
                data.get("logSlice") or "",
                data.get("analysisStatus") or "none",
                created_by,
                data.get("capturedAt") or now_iso(),
                data.get("judgedAt") or now_iso(),
            ),
            returning_id=True,
        )
        batch = self._recompute_batch(int(data["batchId"]))
        self._bump()
        return {"record": self.get_case(cid), "batch": batch}

    def update_case_analysis(self, case_id: int, status: str, analysis: Any) -> dict[str, Any] | None:
        self._execute(
            "UPDATE test_cases SET analysis_status=?, analysis_json=? WHERE id=?",
            (status, json.dumps(analysis, ensure_ascii=False) if analysis is not None else None, case_id),
        )
        self._bump()
        return self.get_case(case_id)

    def get_case(self, case_id: int) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT c.*, b.run_code AS batch_run_code
               FROM test_cases c LEFT JOIN test_batches b ON b.id = c.batch_id
               WHERE c.id = ?""",
            (case_id,),
        )
        return self._map_case(row) if row else None

    def list_cases(
        self,
        *,
        batch_id: int | None = None,
        status: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if batch_id:
            where.append("c.batch_id = ?")
            params.append(batch_id)
        if status and status != "ALL":
            where.append("c.verdict_status = ?")
            params.append(status)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            where.append(
                "(c.case_id LIKE ? OR c.passenger_name LIKE ? OR c.cert_no LIKE ? OR c.actual_node LIKE ? OR c.session_id LIKE ? OR c.created_by LIKE ?)"
            )
            params.extend([kw, kw, kw, kw, kw, kw])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cnt = self._fetchone(f"SELECT COUNT(*) AS total FROM test_cases c {where_sql}", tuple(params))
        rows = self._fetchall(
            f"""SELECT c.*, b.run_code AS batch_run_code
                FROM test_cases c LEFT JOIN test_batches b ON b.id = c.batch_id
                {where_sql}
                ORDER BY c.id DESC LIMIT ? OFFSET ?""",
            tuple([*params, limit, offset]),
        )
        return {"items": [self._map_case(r) for r in rows], "total": int((cnt or {}).get("total") or 0)}

    def delete_case(self, case_id: int) -> None:
        rec = self.get_case(case_id)
        if not rec:
            return
        self._execute("DELETE FROM test_cases WHERE id=?", (case_id,))
        self._recompute_batch(rec["batchId"])
        self._bump()

    # ---------------- System Logs ----------------
    def insert_system_log(self, data: dict[str, Any], operator: str) -> dict[str, Any]:
        created_at = now_iso()
        lid = self._execute(
            "INSERT INTO system_logs (batch_id, level, source, message, operator, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (data.get("batchId"), data.get("level") or "INFO", data.get("source") or "engine", data.get("message") or "", operator, created_at),
            returning_id=True,
        )
        self._bump()
        return {
            "id": lid,
            "batchId": data.get("batchId"),
            "level": data.get("level") or "INFO",
            "source": data.get("source") or "engine",
            "message": data.get("message") or "",
            "operator": operator,
            "createdAt": created_at,
        }

    def list_system_logs(self, batch_id: int | None = None, limit: int = 150) -> list[dict[str, Any]]:
        if batch_id:
            rows = self._fetchall("SELECT * FROM system_logs WHERE batch_id=? ORDER BY id DESC LIMIT ?", (batch_id, limit))
        else:
            rows = self._fetchall("SELECT * FROM system_logs ORDER BY id DESC LIMIT ?", (limit,))
        rows.reverse()
        return [
            {
                "id": int(r["id"]),
                "batchId": r.get("batch_id"),
                "level": r["level"],
                "source": r["source"],
                "message": r["message"],
                "operator": r.get("operator") or "system",
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    # ---------------- Dashboard & Storage Info ----------------
    def get_dashboard_stats(self) -> dict[str, Any]:
        b_cnt = self._fetchone("SELECT COUNT(*) AS cnt FROM test_batches")
        c_agg = self._fetchone(
            """SELECT COUNT(*) AS total,
               SUM(CASE WHEN verdict_status='PASS' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN verdict_status IN ('FAIL','ERROR') THEN 1 ELSE 0 END) AS failed,
               SUM(CASE WHEN verdict_status='TIMEOUT' THEN 1 ELSE 0 END) AS timed_out
               FROM test_cases"""
        )
        total = int((c_agg or {}).get("total") or 0)
        passed = int((c_agg or {}).get("passed") or 0)
        failed = int((c_agg or {}).get("failed") or 0)
        timeout = int((c_agg or {}).get("timed_out") or 0)
        pass_rate = round((passed / total) * 100, 1) if total > 0 else 0.0

        node_rows = self._fetchall(
            """SELECT COALESCE(NULLIF(actual_node, ''), CASE WHEN verdict_status='TIMEOUT' THEN '日志超时' ELSE '通行结论不符' END) AS node,
                      COUNT(*) AS cnt
               FROM test_cases WHERE verdict_status != 'PASS'
               GROUP BY node ORDER BY cnt DESC LIMIT 8"""
        )
        return {
            "totalBatches": int((b_cnt or {}).get("cnt") or 0),
            "totalCases": total,
            "passedCases": passed,
            "failedCases": failed,
            "timeoutCases": timeout,
            "passRate": pass_rate,
            "failedNodeStats": [{"node": r["node"], "count": int(r["cnt"])} for r in node_rows],
            "recentBatches": self.list_batches(8, 0),
            "recentCases": self.list_cases(limit=15, offset=0)["items"],
        }

    def get_storage_info(self) -> dict[str, Any]:
        b = self._fetchone("SELECT COUNT(*) AS cnt FROM test_batches")
        c = self._fetchone("SELECT COUNT(*) AS cnt FROM test_cases")
        l = self._fetchone("SELECT COUNT(*) AS cnt FROM system_logs")
        u = self._fetchone("SELECT COUNT(*) AS cnt FROM sys_users")
        if self.db_type == "postgres":
            location = f"{os.environ.get('PG_HOST', '127.0.0.1')}:{os.environ.get('PG_PORT', '5432')}/{os.environ.get('PG_DATABASE', 'gate_autotest')}"
            size_bytes = 0
        elif self.db_type == "mysql":
            location = f"{os.environ.get('MYSQL_HOST', '127.0.0.1')}:{os.environ.get('MYSQL_PORT', '3306')}/{os.environ.get('MYSQL_DATABASE', 'gate_autotest')}"
            size_bytes = 0
        else:
            location = str(self._sqlite_path)
            size_bytes = self._sqlite_path.stat().st_size if self._sqlite_path.exists() else 0
        return {
            "driver": f"python-{self.db_type}",
            "location": location,
            "sizeBytes": size_bytes,
            "batchCount": int((b or {}).get("cnt") or 0),
            "caseCount": int((c or {}).get("cnt") or 0),
            "logCount": int((l or {}).get("cnt") or 0),
            "userCount": int((u or {}).get("cnt") or 0),
        }

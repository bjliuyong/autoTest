"""Python HTTP 共享后端服务（纯标准库 ThreadingHTTPServer + JWT 鉴权 + RBAC + 团队在线状态）。"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .auth import ALL_PERMISSIONS, has_permission, sign_token, verify_password, verify_token
from .db import DatabaseRepository, now_iso

repo = DatabaseRepository()

# 在线成员心跳表：user_id -> { userId, username, nickname, roleCode, roleName, connectedAt, lastSeen }
_presence_lock = threading.Lock()
_online_users: dict[int, dict[str, Any]] = {}
PRESENCE_TTL_SECONDS = 30.0


def touch_presence(user: dict[str, Any]) -> None:
    now = time.monotonic()
    with _presence_lock:
        uid = int(user["id"])
        existing = _online_users.get(uid)
        _online_users[uid] = {
            "userId": uid,
            "username": user["username"],
            "nickname": user["nickname"],
            "roleCode": user["roleCode"],
            "roleName": user.get("roleName") or user["roleCode"],
            "connectedAt": existing["connectedAt"] if existing else now_iso(),
            "_lastSeen": now,
        }


def get_online_users() -> list[dict[str, Any]]:
    now = time.monotonic()
    with _presence_lock:
        expired = [uid for uid, info in _online_users.items() if now - info["_lastSeen"] > PRESENCE_TTL_SECONDS]
        for uid in expired:
            _online_users.pop(uid, None)
        return [{k: v for k, v in info.items() if k != "_lastSeen"} for info in _online_users.values()]


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # 精简访问日志，跳过高频心跳轮询
        if args and "/api/sync/poll" in str(args[0]):
            return
        print(f"[HTTP] {self.address_string()} - {format % args}")

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def _authenticate(self) -> dict[str, Any] | None:
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        payload = verify_token(token)
        if not payload or not payload.get("userId"):
            return None
        user = repo.get_user_by_id(int(payload["userId"]))
        if not user or user.get("status") != "active":
            return None
        touch_presence(user)
        return user

    def _client_ip(self) -> str:
        fwd = self.headers.get("X-Forwarded-For")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        p = parsed.path
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

        try:
            # 1) Public Health Check
            if method == "GET" and p == "/api/health":
                storage = repo.get_storage_info()
                return self._send_json(
                    200,
                    {
                        "ok": True,
                        "service": "Gate AutoTest Python HTTP Backend",
                        "dbDriver": storage["driver"],
                        "onlineCount": len(get_online_users()),
                        "time": now_iso(),
                    },
                )

            # 2) Public Login
            if method == "POST" and p == "/api/auth/login":
                body = self._read_json()
                username = str(body.get("username") or "").strip()
                password = str(body.get("password") or "")
                user_raw = repo.get_user_raw_by_username(username)
                if not user_raw or not verify_password(password, user_raw["password_hash"]):
                    return self._send_json(401, {"error": "用户名或密码错误"})
                if user_raw.get("status") != "active":
                    return self._send_json(403, {"error": "该账号已被禁用，请联系管理员"})
                repo.touch_login(int(user_raw["id"]))
                user = repo.get_user_by_id(int(user_raw["id"]))
                assert user is not None
                touch_presence(user)
                token = sign_token({"userId": user["id"], "username": user["username"], "roleCode": user["roleCode"]})
                repo.add_audit_log(
                    user["id"],
                    user["username"],
                    "auth",
                    "登录系统",
                    f"{user['nickname']} ({user['username']}) 登录共享平台",
                    self._client_ip(),
                )
                return self._send_json(
                    200,
                    {"token": token, "user": user, "allPermissions": ALL_PERMISSIONS, "onlineUsers": get_online_users()},
                )

            # 3) All routes below require JWT auth
            user = self._authenticate()
            if not user:
                return self._send_json(401, {"error": "未登录或登录已过期，请重新登录"})

            if method == "GET" and p == "/api/auth/me":
                return self._send_json(
                    200,
                    {"user": user, "allPermissions": ALL_PERMISSIONS, "onlineUsers": get_online_users()},
                )

            # HTTP Sync Poll (replaces WebSocket for pure HTTP deployments)
            if method == "GET" and p == "/api/sync/poll":
                return self._send_json(
                    200,
                    {"version": repo.data_version, "onlineUsers": get_online_users()},
                )

            # ---- Dashboard & Storage ----
            if method == "GET" and p == "/api/dashboard":
                return self._send_json(200, repo.get_dashboard_stats())

            if method == "GET" and p == "/api/storage-info":
                info = repo.get_storage_info()
                info["onlineUsers"] = get_online_users()
                return self._send_json(200, info)

            # ---- Config ----
            if method == "GET" and p == "/api/config":
                return self._send_json(200, repo.get_config())

            if method == "PUT" and p == "/api/config":
                if not has_permission(user, "config:edit"):
                    return self._send_json(403, {"error": "无权修改测试配置"})
                body = self._read_json()
                saved = repo.save_config(body, user["username"])
                repo.add_audit_log(
                    user["id"],
                    user["username"],
                    "config",
                    "修改全局配置",
                    f"更新通道/判定配置（gate={saved.get('gate', {}).get('mode')}, logs={saved.get('logs', {}).get('source')}）",
                    self._client_ip(),
                )
                return self._send_json(200, saved)

            if method == "POST" and p == "/api/config/reset":
                if not has_permission(user, "config:edit"):
                    return self._send_json(403, {"error": "无权重置测试配置"})
                return self._send_json(200, repo.reset_config(user["username"]))

            # ---- Batches ----
            if method == "GET" and p == "/api/batches":
                return self._send_json(200, repo.list_batches(int(qs.get("limit", "50")), int(qs.get("offset", "0"))))

            if method == "POST" and p == "/api/batches":
                if not has_permission(user, "engine:control"):
                    return self._send_json(403, {"error": "无权创建测试批次"})
                body = self._read_json()
                batch = repo.create_batch(body, user["username"])
                repo.add_audit_log(
                    user["id"],
                    user["username"],
                    "autotest",
                    "启动测试批次",
                    f"批次 {batch['runCode']}（{batch['name']}）",
                    self._client_ip(),
                )
                return self._send_json(200, batch)

            m_batch = re.match(r"^/api/batches/(\d+)(/finish)?$", p)
            if m_batch:
                bid = int(m_batch.group(1))
                sub = m_batch.group(2)
                if method == "GET" and not sub:
                    b = repo.get_batch(bid)
                    return self._send_json(200, b) if b else self._send_json(404, {"error": "批次不存在"})
                if method == "POST" and sub == "/finish":
                    body = self._read_json()
                    return self._send_json(200, repo.finish_batch(bid, str(body.get("status") or "completed")))
                if method == "DELETE" and not sub:
                    if not has_permission(user, "batch:manage"):
                        return self._send_json(403, {"error": "无权删除批次"})
                    repo.delete_batch(bid)
                    repo.add_audit_log(user["id"], user["username"], "autotest", "删除测试批次", f"删除批次 ID={bid}", self._client_ip())
                    return self._send_json(200, {"ok": True})

            # ---- Cases ----
            if method == "GET" and p == "/api/cases":
                return self._send_json(
                    200,
                    repo.list_cases(
                        batch_id=int(qs["batchId"]) if qs.get("batchId") else None,
                        status=qs.get("status", "ALL"),
                        keyword=qs.get("keyword"),
                        limit=int(qs.get("limit", "100")),
                        offset=int(qs.get("offset", "0")),
                    ),
                )

            if method == "POST" and p == "/api/cases":
                body = self._read_json()
                return self._send_json(200, repo.insert_case(body, user["username"]))

            m_case = re.match(r"^/api/cases/(\d+)(/analysis)?$", p)
            if m_case:
                cid = int(m_case.group(1))
                sub = m_case.group(2)
                if method == "GET" and not sub:
                    c = repo.get_case(cid)
                    return self._send_json(200, c) if c else self._send_json(404, {"error": "用例不存在"})
                if method == "PUT" and sub == "/analysis":
                    body = self._read_json()
                    return self._send_json(200, repo.update_case_analysis(cid, str(body.get("status") or "done"), body.get("analysis")))
                if method == "DELETE" and not sub:
                    if not has_permission(user, "case:manage"):
                        return self._send_json(403, {"error": "无权删除用例"})
                    repo.delete_case(cid)
                    return self._send_json(200, {"ok": True})

            # ---- System Logs ----
            if method == "GET" and p == "/api/system-logs":
                bid = int(qs["batchId"]) if qs.get("batchId") else None
                return self._send_json(200, repo.list_system_logs(bid, int(qs.get("limit", "150"))))

            if method == "POST" and p == "/api/system-logs":
                body = self._read_json()
                return self._send_json(200, repo.insert_system_log(body, user["username"]))

            # ---- System Management: Users ----
            if method == "GET" and p == "/api/system/users":
                if not has_permission(user, "system:user"):
                    return self._send_json(403, {"error": "无权查看用户列表"})
                return self._send_json(200, repo.list_users())

            if method == "POST" and p == "/api/system/users":
                if not has_permission(user, "system:user"):
                    return self._send_json(403, {"error": "无权创建用户"})
                body = self._read_json()
                created = repo.create_user(body)
                repo.add_audit_log(
                    user["id"],
                    user["username"],
                    "user",
                    "新增用户",
                    f"创建用户 {created['username']}（角色={created['roleCode']}）",
                    self._client_ip(),
                )
                return self._send_json(200, created)

            m_user = re.match(r"^/api/system/users/(\d+)(/reset-password)?$", p)
            if m_user:
                if not has_permission(user, "system:user"):
                    return self._send_json(403, {"error": "无权管理用户"})
                uid = int(m_user.group(1))
                sub = m_user.group(2)
                if method == "PUT" and not sub:
                    body = self._read_json()
                    updated = repo.update_user(uid, body)
                    repo.add_audit_log(
                        user["id"],
                        user["username"],
                        "user",
                        "编辑用户",
                        f"修改用户 {updated['username']}（状态={updated['status']}，角色={updated['roleCode']}）",
                        self._client_ip(),
                    )
                    return self._send_json(200, updated)
                if method == "POST" and sub == "/reset-password":
                    body = self._read_json()
                    repo.reset_password(uid, str(body.get("password") or "123456"))
                    repo.add_audit_log(user["id"], user["username"], "user", "重置密码", f"重置用户 ID={uid} 的登录密码", self._client_ip())
                    return self._send_json(200, {"ok": True})
                if method == "DELETE" and not sub:
                    repo.delete_user(uid)
                    repo.add_audit_log(user["id"], user["username"], "user", "删除用户", f"删除用户 ID={uid}", self._client_ip())
                    return self._send_json(200, {"ok": True})

            # ---- System Management: Roles ----
            if method == "GET" and p == "/api/system/roles":
                return self._send_json(200, {"roles": repo.list_roles(), "allPermissions": ALL_PERMISSIONS})

            if method == "POST" and p == "/api/system/roles":
                if not has_permission(user, "system:role"):
                    return self._send_json(403, {"error": "无权创建角色"})
                body = self._read_json()
                created = repo.create_role(body)
                repo.add_audit_log(user["id"], user["username"], "role", "新增角色", f"创建角色 {created['name']} ({created['code']})", self._client_ip())
                return self._send_json(200, created)

            m_role = re.match(r"^/api/system/roles/([a-zA-Z0-9_-]+)$", p)
            if m_role:
                if not has_permission(user, "system:role"):
                    return self._send_json(403, {"error": "无权管理角色"})
                code = m_role.group(1)
                if method == "PUT":
                    body = self._read_json()
                    updated = repo.update_role(code, body)
                    repo.add_audit_log(
                        user["id"],
                        user["username"],
                        "role",
                        "更新角色权限",
                        f"更新角色 {code} 权限（共 {len(updated['permissions'])} 项）",
                        self._client_ip(),
                    )
                    return self._send_json(200, updated)
                if method == "DELETE":
                    repo.delete_role(code)
                    repo.add_audit_log(user["id"], user["username"], "role", "删除角色", f"删除角色 {code}", self._client_ip())
                    return self._send_json(200, {"ok": True})

            # ---- System Management: Audit Logs ----
            if method == "GET" and p == "/api/system/audit-logs":
                if not has_permission(user, "system:audit"):
                    return self._send_json(403, {"error": "无权查看审计日志"})
                return self._send_json(200, repo.list_audit_logs(int(qs.get("limit", "150"))))

            return self._send_json(404, {"error": f"Not Found: {method} {p}"})
        except ValueError as exc:
            return self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            return self._send_json(500, {"error": f"服务端异常: {exc}"})


def run_server() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "3100"))
    repo.init()
    try:
        server = ThreadingHTTPServer((host, port), ApiHandler)
    except OSError as exc:
        print(f"[错误] 端口 {host}:{port} 已被占用（后端可能已在运行）：{exc}")
        raise SystemExit(1)
    print(f"[Gate AutoTest Python Backend] HTTP 服务已启动: http://{host}:{port} (DB_TYPE={repo.db_type})")
    print("[Gate AutoTest Python Backend] 默认账号: admin / admin123, tester / tester123")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Gate AutoTest Python Backend] 服务已停止")
        server.server_close()

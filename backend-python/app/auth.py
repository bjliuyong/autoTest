"""JWT 签发校验、scrypt 密码哈希与 RBAC 权限定义（纯 Python 标准库实现）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

JWT_SECRET = os.environ.get("JWT_SECRET", "gate-autotest-shared-secret-2026").encode("utf-8")

ALL_PERMISSIONS: list[dict[str, str]] = [
    {"code": "dashboard:view", "label": "查看监控大盘"},
    {"code": "engine:control", "label": "启停测试与自测"},
    {"code": "batch:manage", "label": "管理/删除测试批次"},
    {"code": "case:manage", "label": "分析/删除测试用例"},
    {"code": "config:edit", "label": "修改通道与判定配置"},
    {"code": "system:user", "label": "用户管理"},
    {"code": "system:role", "label": "角色与权限管理"},
    {"code": "system:audit", "label": "查看操作审计日志"},
]

DEFAULT_ROLES: list[dict[str, Any]] = [
    {
        "code": "admin",
        "name": "超级管理员",
        "description": "拥有系统全部管理权限与测试控制权限",
        "permissions": [p["code"] for p in ALL_PERMISSIONS],
        "isSystem": True,
    },
    {
        "code": "tester",
        "name": "测试工程师",
        "description": "可执行测试、查看与分析用例、管理测试批次和配置",
        "permissions": [
            "dashboard:view",
            "engine:control",
            "batch:manage",
            "case:manage",
            "config:edit",
        ],
        "isSystem": True,
    },
    {
        "code": "viewer",
        "name": "只读访客",
        "description": "仅可查看共享大盘、历史批次与用例追溯记录",
        "permissions": ["dashboard:view"],
        "isSystem": True,
    },
]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"{salt.hex()}:{derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, key_hex = str(stored_hash or "").split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(expected, derived)
    except Exception:
        return False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign_token(payload: dict[str, Any], expires_in: int = 86400 * 7) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    body_obj = {**payload, "iat": now, "exp": now + expires_in}
    body = _b64url(json.dumps(body_obj, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(JWT_SECRET, f"{header}.{body}".encode("ascii"), hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        parts = str(token or "").split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected = _b64url(
            hmac.new(JWT_SECRET, f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        if payload.get("exp") and int(payload["exp"]) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def has_permission(user: dict[str, Any] | None, perm: str) -> bool:
    if not user:
        return False
    if user.get("roleCode") == "admin":
        return True
    perms = user.get("permissions") or []
    return perm in perms

"""闸机 WebSocket 协议（本脚本定义）。

角色约定：Python 端是 WebSocket **客户端**，闸机程序是服务端，闸机每采集到
一位旅客就主动推送一条 JSON 文本帧，里面同时带上"采集信息"（待测系统的输入）
和"预期结果"。

一条用例消息（JSON 文本帧）::

    {
      "type": "gate.capture",
      "case_id": "C-000123",                            # 可省略，缺省自动生成
      "timestamp": "2026-02-14T10:00:00+08:00",         # 可省略
      "gate_id": "G01",                                 # 可省略，原样透传
      "expected": {                                     # 必填：预期结果
        "allow_pass": false,                            #   是否允许通行
        "failed_node": "证件核验"                        #   不允许通行时的失败节点名称
      },
      "capture": {                                      # 必填：闸机采集的证件信息
        "passenger_name": "张三",
        "cert_type": "ID_CARD",
        "cert_no": "110101199003071234",
        "photo_base64": "data:image/jpeg;base64,/9j/4AAQ..."   # 照片，也支持 url / 本地路径
      }
    }

兼容写法（容错解析，方便老闸机程序快速接入）：

* ``expected`` 可以省略，直接把 ``allow_pass`` / ``pass`` / ``allow`` 和
  ``failed_node`` / ``node`` 放在消息顶层；
* ``capture`` 可以省略，此时消息顶层的其余字段全部当作采集信息；
* ``allow_pass`` 支持 ``true/false``、``"true"/"false"``、``1/0``、
  ``"允许"/"不允许"``、``"pass"/"deny"``。

本脚本回给闸机的确认消息（可选，``gate.send_ack`` 控制）::

    {"type": "gate.ack", "case_id": "C-000123",
     "status": "accepted" | "rejected", "reason": "..."}
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

_TRUE_TOKENS = {"true", "1", "yes", "y", "on", "pass", "allowed", "allow", "允许", "允许通行", "通行"}
_FALSE_TOKENS = {"false", "0", "no", "n", "off", "deny", "denied", "block", "禁止", "不允许", "不通行", "拦截"}


class ProtocolError(ValueError):
    """闸机消息不符合协议。"""


class NotACaseMessage(ProtocolError):
    """这条消息根本不是"用例"（例如同一轮里的设备/命令应答）。

    现场：一轮测试会发十几条消息，只有一条带预期结果。其余消息应当**安静地忽略**，
    既不能当成用例、也不该计入"协议错误"。
    """


@dataclass(slots=True)
class Expected:
    """预期结果。"""

    allow_pass: bool
    failed_node: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"allow_pass": self.allow_pass, "failed_node": self.failed_node}


@dataclass(slots=True)
class GateCapture:
    """一条待测用例：闸机采集信息 + 预期结果。"""

    case_id: str
    captured_at: datetime
    expected: Expected
    capture: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)
    # 可选：由测试程序直接指定会话ID（不给就从日志里的"创建新会话"自动推断）
    session_id_hint: str | None = None
    # 收到消息时日志缓冲区里的行下标，用于把用例和后续日志关联起来
    log_marker: int = 0
    # 这一轮"第一条消息"在闸机报文留档里的下标（用于把整轮报文附到用例上）
    frame_start: int = 0
    # 这一轮的标识（serial 等），便于排查
    round_key: str | None = None
    # 唯一序号（完整 serial，不切 "_"）：用例号可能被重复测多次、证号也可能重复，
    # 所以产物目录、队列任务都用它来区分
    serial: str | None = None

    @property
    def unique_key(self) -> str:
        """本次采集的唯一标识（没有 serial 时退化成用例号）。"""
        return self.serial or self.case_id
    received_monotonic: float = field(default_factory=time.monotonic)

    def passenger_candidates(self, keys: tuple[str, ...]) -> list[str]:
        """按配置的字段顺序，取出可用于匹配日志中 ``旅客[xxx]`` 的候选值。"""
        values: list[str] = []
        for key in keys:
            value = _dig(self.capture, key)
            if value is None:
                value = _dig(self.raw, key)
            if isinstance(value, (str, int)) and str(value).strip():
                text = str(value).strip()
                if text not in values:
                    values.append(text)
        return values


def _dig(data: Mapping[str, Any], dotted: str) -> Any:
    """支持 ``a.b.c`` 形式的多层取值。"""
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    raise ProtocolError(f"字段 {field_name} 无法解析成「是否允许通行」：{value!r}")


def _first_present(data: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def apply_field_map(payload: Mapping[str, Any], mapping: Mapping[str, str] | None) -> dict[str, Any]:
    """按配置把"对方的字段"映射成本程序认识的字段。

    用途：对方（例如 Node-RED 的流程）发的是它自己的 JSON 结构，又**不能改**，
    那就在这边声明式地翻译一下，不用改代码::

        gate:
          field_map:
            case_id: msg.payload.testId          # 点路径，支持 a.b.c
            allow_pass: msg.payload.expect.allow
            failed_node: msg.payload.expect.node
            cert_no: msg.payload.idCard

    映射结果会**覆盖**同名的顶层字段，其余字段原样保留（所以原来的 capture 兜底仍然有效）。
    """
    if not mapping:
        return dict(payload)
    result = dict(payload)
    for target, source in mapping.items():
        if not isinstance(source, str) or not source.strip():
            continue
        value = _dig(payload, source.strip())
        if value is not None:
            result[str(target)] = value
    return result


def parse_gate_message(payload: Mapping[str, Any]) -> GateCapture:
    """把闸机推送的 JSON 解析成 :class:`GateCapture`。"""
    if not isinstance(payload, Mapping):
        raise ProtocolError(f"闸机消息必须是 JSON 对象，当前：{type(payload).__name__}")

    msg_type = str(payload.get("type") or "").strip()
    known_types = {"", "gate.capture", "capture", "case", "test.case", "gate", "gate.case"}
    if msg_type and msg_type.lower() not in known_types:
        # 很多测试程序会给消息加自己的 type 名。只要它长得像"用例"就照收，
        # 免得因为 type 名字对不上而整条消息被丢掉（这种问题很难排查）。
        looks_like_case = any(key in payload for key in ("expected", "allow_pass", "capture"))
        if not looks_like_case:
            raise NotACaseMessage(f"不是用例报文（type={msg_type}）")

    expected_raw = payload.get("expected")
    expected_map: Mapping[str, Any] = expected_raw if isinstance(expected_raw, Mapping) else payload

    allow_value = _first_present(
        expected_map, ("allow_pass", "allow", "allowed", "pass", "expect_allow", "是否允许通行")
    )
    if allow_value is None:
        raise NotACaseMessage("没有 expected.allow_pass，不是用例报文")
    allow_pass = _parse_bool(allow_value, "expected.allow_pass")

    node_value = _first_present(
        expected_map, ("failed_node", "fail_node", "node", "failedNode", "失败节点", "节点")
    )
    failed_node = None
    if node_value is not None and str(node_value).strip():
        failed_node = str(node_value).strip()

    capture_raw = payload.get("capture")
    if isinstance(capture_raw, Mapping):
        capture = dict(capture_raw)
    else:
        excluded = {"type", "case_id", "timestamp", "expected", "capture"}
        capture = {k: v for k, v in payload.items() if k not in excluded}

    case_id = str(payload.get("case_id") or payload.get("caseId") or "").strip()
    if not case_id:
        case_id = f"C-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"

    session_hint = _first_present(
        payload, ("session_id", "sessionId", "conversation_id", "trace_id", "会话ID")
    )
    if session_hint is None and isinstance(capture_raw, Mapping):
        session_hint = _first_present(
            capture_raw, ("session_id", "sessionId", "conversation_id", "trace_id", "会话ID")
        )
    session_text = str(session_hint).strip() if session_hint is not None else ""

    return GateCapture(
        case_id=case_id,
        captured_at=_parse_timestamp(payload.get("timestamp")),
        expected=Expected(allow_pass=allow_pass, failed_node=failed_node),
        capture=capture,
        raw=dict(payload),
        session_id_hint=session_text or None,
    )


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return datetime.now()
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
    return datetime.now()


def build_ack(case_id: str, status: str, reason: str = "") -> dict[str, Any]:
    """构造回给闸机的确认报文。"""
    return {"type": "gate.ack", "case_id": case_id, "status": status, "reason": reason}

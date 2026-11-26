"""端到端验证：用本程序生成的容量 patch 去跑真实 DSH，并抓取它发出的 max_tokens。

DSH 检出目录存在时才会跑；否则跳过（CI/离线机上没有检出也能过）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_ROOT = Path(__file__).resolve().parent.parent
_DSH_ROOT = Path("D:/Study/Deepseek Harness/deepseek-harness")
_DSH_CLI = _DSH_ROOT / "apps" / "cli" / "lib" / "bin.js"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeEndpoint(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, *args):  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except json.JSONDecodeError:
            data = {}
        type(self).requests.append(
            {"max_tokens": data.get("max_tokens"), "model": data.get("model")}
        )
        chunk = {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": data.get("model") or "fake",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}],
        }
        body = (
            f"data: {json.dumps(chunk)}\n\n"
            'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"fake",'
            '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}\n\n'
            "data: [DONE]\n\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"data": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RealDshCapacityTest(unittest.TestCase):
    """真跑一次 DSH（打到本地假端点），确认它发出的 max_tokens 是我们 patch 的值。"""

    def test_patch_controls_request_max_tokens(self) -> None:
        if not _DSH_CLI.is_file():
            self.skipTest("本机没有 DSH 检出，跳过真跑验证")
        import subprocess

        tmp = _ROOT / ".tmp-tests" / f"dshw-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        port = _free_port()
        _FakeEndpoint.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", port), _FakeEndpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        home = tmp / "home"
        home.mkdir()
        try:
            patch = tmp / "capacity.yml"
            # 与程序生成的 patch 结构一致：llm-deepseek 的容量键
            patch.write_text(
                "- id: llm-deepseek\n"
                "  config:\n"
                f"    baseURL: http://127.0.0.1:{port}/v1\n"
                "    apiKeyEnv: DEEPSEEK_API_KEY\n"
                "    maxTokens: 24000\n"
                "    defaultContextWindow: 103424\n",
                encoding="utf-8",
            )
            import os

            env = os.environ.copy()
            env.update({"DSH_HOME": str(home), "DEEPSEEK_API_KEY": "sk-fake"})
            completed = subprocess.run(
                ["node", str(_DSH_CLI), "--profile", "headless", "--patch", str(patch), "say hi"],
                cwd=str(_DSH_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            deadline = time.time() + 10
            while not _FakeEndpoint.requests and time.time() < deadline:
                time.sleep(0.2)
            self.assertTrue(
                _FakeEndpoint.requests,
                "DSH 没有发出请求（可能启动失败）：" + (completed.stderr or completed.stdout)[-400:],
            )
            caps = [item["max_tokens"] for item in _FakeEndpoint.requests if item["max_tokens"]]
            self.assertIn(24000, caps, f"patch 里的 maxTokens 没生效：{caps}")
            self.assertTrue(
                all(cap <= 24000 for cap in caps),
                f"不能出现超过 patch 值的输出上限：{caps}",
            )
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

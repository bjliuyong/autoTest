"""内建 HTTP 服务：直接用 ``http://<ip>:<端口>`` 打开 BI 面板。

不依赖任何第三方库（只用标准库 ``http.server``），所以离线机也能跑。

路由：

* ``/``                  大屏面板（自动轮询 ``/api/state`` 局部刷新，不整页闪烁）
* ``/api/state``         当前状态 JSON
* ``POST /api/case``     **HTTP 直接推送用例**（不用 WebSocket，任何语言一行 curl 就能推）
* ``/cases/<用例>/report.html``   单条用例报告
* ``/cases/<用例>/photo.*``       采集照片
* ``/health``            健康检查（给负载均衡/监控用）

服务端只读**快照**：由主循环在 ``_refresh_dashboard()`` 里更新，避免跨线程访问
协程状态。``POST /api/case`` 通过 ``case_sink`` 回调把消息交回主循环处理。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

PayloadProvider = Callable[[], Mapping[str, Any]]
# 收到 HTTP 推送的用例：返回 (是否接受, 说明)
CaseSink = Callable[[Mapping[str, Any]], "tuple[bool, str]"]


class _Server(ThreadingHTTPServer):
    """Windows 上关掉地址复用：否则两个进程能同时绑同一端口，谁都发现不了冲突。"""

    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


class DashboardServer:
    """把面板和用例产物挂到 HTTP 上。"""

    def __init__(
        self,
        *,
        provider: PayloadProvider,
        cases_dir: Path,
        host: str = "0.0.0.0",
        port: int = 8767,
        logger: logging.Logger | None = None,
        refresh_seconds: float = 2.0,
        title: str = "闸机自动化测试",
        case_sink: CaseSink | None = None,
    ) -> None:
        self.provider = provider
        self.cases_dir = Path(cases_dir)
        self.host = host
        self.port = port
        self.logger = logger or logging.getLogger("autotest.web")
        self.refresh_seconds = refresh_seconds
        self.title = title
        self.case_sink = case_sink
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # -- 生命周期 ------------------------------------------------------------ #
    def start(self) -> str | None:
        handler = _make_handler(self)
        try:
            httpd = _Server((self.host, self.port), handler)
        except OSError as exc:
            self.logger.error(
                f"BI 面板端口 {self.host}:{self.port} 无法监听（{exc.strerror or exc}）；"
                "可在配置里改 report.port"
            )
            return None
        httpd.daemon_threads = True
        self._httpd = httpd
        # 允许 port=0：由系统挑一个空闲端口（避免和别的程序抢端口）
        try:
            actual_port = int(httpd.server_address[1])
        except (IndexError, TypeError, ValueError):
            actual_port = self.port
        self.port = actual_port
        self._thread = threading.Thread(target=httpd.serve_forever, name="dashboard-http", daemon=True)
        self._thread.start()
        shown = "127.0.0.1" if self.host in {"0.0.0.0", ""} else self.host
        self.url = f"http://{shown}:{self.port}/"
        self.logger.info(f"BI 面板已启动：{self.url}（监听 {self.host}:{self.port}）")
        return self.url

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        self._httpd = None
        self.logger.info("BI 面板已停止")

    # -- 内容 ---------------------------------------------------------------- #
    def state(self) -> dict[str, Any]:
        try:
            payload = dict(self.provider() or {})
        except Exception as exc:  # noqa: BLE001 - 面板出错不能影响测试
            self.logger.debug(f"取面板数据失败：{exc}")
            payload = {}
        payload.setdefault("updated_at", "")
        if self.url:
            payload["server_url"] = self.url
        payload["refresh_seconds"] = self.refresh_seconds
        payload["title"] = self.title
        return payload

    def page(self) -> str:
        from .dashboard import render_bigscreen

        return render_bigscreen(self.state())

    def resolve_case_file(self, relative: str) -> Path | None:
        """把 /cases/... 映射到 cases 目录内的真实文件（防目录穿越）。"""
        target = (self.cases_dir / relative).resolve()
        try:
            target.relative_to(self.cases_dir.resolve())
        except ValueError:
            return None
        return target if target.is_file() else None


def _make_handler(server: DashboardServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoTestPanel/1.0"

        # -- 输出工具 ---------------------------------------------------------- #
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if getattr(self, "_cors", False):
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, data: object) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

        def _text(self, status: int, text: str) -> None:
            self._send(status, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8")

        # -- 路由 -------------------------------------------------------------- #
        def do_GET(self) -> None:  # noqa: N802 - 标准库要求
            path = self.path.split("?", 1)[0]
            try:
                if path in {"/", "/index.html"}:
                    self._send(
                        HTTPStatus.OK,
                        server.page().encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                    return
                if path == "/api/state":
                    self._json(server.state())
                    return
                if path == "/health":
                    self._json({"ok": True})
                    return
                if path == "/favicon.ico":
                    self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                    return
                if path.startswith("/cases/"):
                    relative = path[len("/cases/") :]
                    target = server.resolve_case_file(relative)
                    if target is None:
                        self._text(HTTPStatus.NOT_FOUND, f"找不到文件：{relative}")
                        return
                    body = target.read_bytes()
                    content_type = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
                    self._send(HTTPStatus.OK, body, content_type)
                    return
                self._text(HTTPStatus.NOT_FOUND, f"未知路径：{path}")
            except Exception as exc:  # noqa: BLE001 - 别让面板报错拖垮服务
                server.logger.debug(f"面板请求 {path} 失败：{exc}")
                try:
                    self._text(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务内部错误：{exc}")
                except Exception:  # noqa: BLE001
                    pass

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 静音访问日志
            return

        # -- HTTP 推用例 -------------------------------------------------------- #
        _CASE_PATHS = {"/api/case", "/api/cases", "/case", "/gate"}

        def do_POST(self) -> None:  # noqa: N802 - 标准库要求
            self._cors = True
            path = self.path.split("?", 1)[0]
            try:
                if path not in self._CASE_PATHS:
                    self._text(HTTPStatus.NOT_FOUND, f"未知路径：{path}（推用例用 POST /api/case）")
                    return
                if server.case_sink is None:
                    self._json({"accepted": False, "error": "本程序未启用 HTTP 推用例"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b""
                text = raw.decode("utf-8-sig", "replace").strip()
                if not text:
                    self._json({"accepted": False, "error": "请求体为空"})
                    return
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    self._json({"accepted": False, "error": f"不是合法 JSON：{exc}"})
                    return
                if isinstance(data, list):
                    results = [server.case_sink(item) for item in data if isinstance(item, Mapping)]
                    accepted = sum(1 for ok, _ in results if ok)
                    self._json(
                        {
                            "accepted": accepted > 0,
                            "count": accepted,
                            "results": [msg for _ok, msg in results],
                        }
                    )
                    return
                if not isinstance(data, Mapping):
                    self._json({"accepted": False, "error": "JSON 顶层必须是对象或用例数组"})
                    return
                ok, message = server.case_sink(data)
                self._json({"accepted": bool(ok), "message": message})
            except Exception as exc:  # noqa: BLE001 - 推送出错不能拖垮面板
                server.logger.debug(f"POST {path} 失败：{exc}")
                try:
                    self._json({"accepted": False, "error": f"{type(exc).__name__}: {exc}"})
                except Exception:  # noqa: BLE001
                    pass

        def do_OPTIONS(self) -> None:  # noqa: N802 - 允许浏览器端跨域推用例
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler

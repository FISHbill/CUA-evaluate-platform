"""本地 OpenAI 兼容 mock：记录图片是否进请求、工具清单里有没有 shell。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def payload_has_image(payload: object) -> bool:
    if isinstance(payload, dict):
        kind = payload.get("type")
        if kind in {"image_url", "image", "input_image"}:
            return True
        if "image_url" in payload:
            return True
        url = payload.get("url")
        if isinstance(url, str) and url.startswith("data:image"):
            return True
        return any(payload_has_image(value) for value in payload.values())
    if isinstance(payload, list):
        return any(payload_has_image(item) for item in payload)
    return False


def tool_names_from_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    tools = payload.get("tools") or []
    names: list[str] = []
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str):
                names.append(name)
    return names


class OpenAICompatMock:
    """最小 chat.completions mock。按请求序号返回固定 tool_call / 文本。"""

    def __init__(self, *, vision: bool) -> None:
        self.vision = vision
        self.requests: list[dict[str, Any]] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("mock server is not running")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> None:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def do_GET(self) -> None:
                if self.path.rstrip("/").endswith("/models"):
                    body = json.dumps(
                        {
                            "object": "list",
                            "data": [
                                {"id": "openai-compat-vlm", "object": "model"},
                                {"id": "openai-compat-text", "object": "model"},
                            ],
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    payload = {}
                if isinstance(payload, dict):
                    mock.requests.append(payload)
                index = len(mock.requests)
                names = tool_names_from_payload(payload)
                reply = mock._completion(index, names, payload_has_image(payload))
                stream = bool(payload.get("stream")) if isinstance(payload, dict) else False
                if stream:
                    body = mock._sse(reply)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                encoded = json.dumps(reply).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _completion(self, index: int, names: list[str], saw_image: bool) -> dict[str, Any]:
        screenshot = next(
            (n for n in names if n.endswith("__screenshot") or n == "screenshot"),
            None,
        )
        shell = next((n for n in names if n.endswith("__shell") or n == "shell"), None)
        terminate = next((n for n in names if n.endswith("__terminate") or n == "terminate"), None)
        if self.vision and screenshot and (index == 1 or (index == 2 and not saw_image)):
            return _tool_call_message(screenshot, {})
        if (not self.vision) and shell and index == 1:
            return _tool_call_message(shell, {"command": "hostname"})
        if terminate and (saw_image if self.vision else index >= 2):
            return _tool_call_message(terminate, {"status": "success"})
        return {
            "id": f"chatcmpl-mock-{index}",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9},
        }

    def _sse(self, completion: dict[str, Any]) -> bytes:
        choice = completion["choices"][0]
        message = choice["message"]
        chunks: list[dict[str, Any]] = []
        if message.get("tool_calls"):
            call = message["tool_calls"][0]
            chunks.append(
                {
                    "id": completion["id"],
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": call["function"]["name"],
                                            "arguments": "",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            chunks.append(
                {
                    "id": completion["id"],
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": call["function"]["arguments"]},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            chunks.append(
                {
                    "id": completion["id"],
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }
            )
        else:
            chunks.append(
                {
                    "id": completion["id"],
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": message.get("content") or ""},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            chunks.append(
                {
                    "id": completion["id"],
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
        parts = [f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks]
        parts.append(b"data: [DONE]\n\n")
        return b"".join(parts)


def _tool_call_message(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-tool",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_mock",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
    }

"""A from-scratch Model Context Protocol client (stdio transport, JSON-RPC 2.0).

No SDK, no framework: ~200 lines that speak the MCP handshake (``initialize`` ->
``notifications/initialized``), discovery (``tools/list``, ``resources/list``, ``prompts/list``)
and invocation (``tools/call``, ``resources/read``, ``prompts/get``) over newline-delimited
JSON on a subprocess' stdin/stdout.

It is synchronous and thread-safe (a reader thread correlates responses by id), which makes it
easy to share one server process across Streamlit sessions.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from goodfoods import __version__, config

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-11-25"  # latest revision that uses the initialize handshake
SERVER_COMMAND = [sys.executable, "-m", "goodfoods.mcp_server"]


class MCPError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP error {code}: {message}")
        self.code, self.message, self.data = code, message, data


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    title: str | None = None
    annotations: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @property
    def policy(self) -> dict:
        return self.meta.get("goodfoods/policy", {})

    def to_openai(self) -> dict:
        """MCP tool -> OpenAI-style function schema (what chat-completions APIs expect)."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": _slim_schema(self.input_schema)}}


@dataclass
class ToolResult:
    is_error: bool
    text: str
    structured: dict | None
    latency_ms: float


def _slim_schema(schema: dict) -> dict:
    """Drop JSON-schema noise (titles, ``anyOf: [T, null]``) that costs tokens and confuses small models."""
    def slim(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: slim(v) for k, v in node.items() if k not in ("title", "default") or not isinstance(v, (str, int, float, bool, type(None)))}
            if "anyOf" in node:
                options = [o for o in node["anyOf"] if o.get("type") != "null"]
                if len(options) == 1:
                    merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **options[0]}
                    return merged
            return node
        if isinstance(node, list):
            return [slim(x) for x in node]
        return node

    return slim(schema)


class MCPStdioClient:
    def __init__(self, command: list[str] | None = None, *, env: dict[str, str] | None = None,
                 cwd: Path | str | None = None, timeout: float = 60.0, log_path: Path | None = None) -> None:
        self.command = command or SERVER_COMMAND
        self.env = {**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})}
        self.cwd = str(cwd or config.ROOT)
        self.timeout = timeout
        self.log_path = Path(log_path or config.DATA_DIR / "mcp_server.log")
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.instructions: str | None = None
        self._proc: subprocess.Popen | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        self._write_lock = threading.Lock()
        self._connect_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> MCPStdioClient:
        with self._connect_lock:
            if self.alive:
                return self
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            stderr = open(self.log_path, "ab")  # noqa: SIM115 - lives as long as the process
            self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                          cwd=self.cwd, env=self.env, bufsize=0)
            threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True, name="mcp-reader").start()
            result = self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "goodfoods-concierge", "version": __version__},
            })
            self.server_info = result.get("serverInfo", {})
            self.capabilities = result.get("capabilities", {})
            self.instructions = result.get("instructions")
            self.protocol_version = result.get("protocolVersion")
            self.notify("notifications/initialized")
            return self

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                proc.kill()
        for event, slot in self._pending.values():
            slot["error"] = {"code": -32000, "message": "connection closed"}
            event.set()

    def __enter__(self) -> MCPStdioClient:
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ JSON-RPC plumbing
    def _send(self, message: dict) -> None:
        if not self.alive:
            raise MCPError(-32000, "MCP server process is not running")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode()
        with self._write_lock:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        if method != "initialize" and not self.alive:
            log.warning("MCP server not running; reconnecting")
            self.connect()
        req_id = next(self._ids)
        event, slot = threading.Event(), {}
        self._pending[req_id] = (event, slot)
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
            if not event.wait(timeout or self.timeout):
                raise MCPError(-32001, f"Timed out waiting for '{method}'")
        finally:
            self._pending.pop(req_id, None)
        if "error" in slot:
            err = slot["error"]
            raise MCPError(err.get("code", -32603), err.get("message", "unknown error"), err.get("data"))
        return slot.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("non-JSON line from server: %s", line[:200])
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                pending = self._pending.get(msg["id"])
                if pending:
                    event, slot = pending
                    slot.update({k: msg[k] for k in ("result", "error") if k in msg})
                    event.set()
            elif "method" in msg and "id" in msg:  # server -> client request
                self._answer_server_request(msg)
            elif "method" in msg:
                log.debug("server notification: %s", msg["method"])
        for event, slot in list(self._pending.values()):  # process exited
            slot.setdefault("error", {"code": -32000, "message": "MCP server exited"})
            event.set()

    def _answer_server_request(self, msg: dict) -> None:
        if msg["method"] == "ping":
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        else:  # we advertise no client capabilities (sampling, roots, elicitation)
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": f"Method not supported: {msg['method']}"}})

    # ------------------------------------------------------------------ MCP methods
    def _paginate(self, method: str, key: str) -> list[dict]:
        items, cursor = [], None
        while True:
            result = self.request(method, {"cursor": cursor} if cursor else {})
            items.extend(result.get(key, []))
            cursor = result.get("nextCursor")
            if not cursor:
                return items

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name=t["name"], description=t.get("description", ""), input_schema=t.get("inputSchema", {}),
                         title=t.get("title"), annotations=t.get("annotations") or {}, meta=t.get("_meta") or {})
                for t in self._paginate("tools/list", "tools")]

    def call_tool(self, name: str, arguments: dict, *, meta: dict | None = None) -> ToolResult:
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if meta:
            params["_meta"] = meta
        t0 = time.perf_counter()
        result = self.request("tools/call", params)
        latency = (time.perf_counter() - t0) * 1000
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        return ToolResult(is_error=bool(result.get("isError")), text=text,
                          structured=result.get("structuredContent"), latency_ms=latency)

    def list_resources(self) -> list[dict]:
        return self._paginate("resources/list", "resources")

    def list_resource_templates(self) -> list[dict]:
        return self._paginate("resources/templates/list", "resourceTemplates")

    def read_resource(self, uri: str) -> str:
        contents = self.request("resources/read", {"uri": uri}).get("contents", [])
        return "\n".join(c.get("text", "") for c in contents)

    def list_prompts(self) -> list[dict]:
        return self._paginate("prompts/list", "prompts")

    def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        result = self.request("prompts/get", {"name": name, "arguments": arguments or {}})
        parts = []
        for message in result.get("messages", []):
            content = message.get("content", {})
            parts.append(content.get("text", "") if isinstance(content, dict) else str(content))
        return "\n".join(parts)

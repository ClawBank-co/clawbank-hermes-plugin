"""Shared fixtures: plugin loading and a configurable mock MCP endpoint."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "clawbank_plugin"

TEST_TOKEN = "test-token-123"

SAMPLE_TOOLS = [
    {
        "name": "get_balance",
        "description": "Primary dashboard wallet USDC balance",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    },
    {
        "name": "send_usdc_on_base",
        "description": (
            "Sends USDC on Base mainnet from the user's self-custody wallet "
            "to a destination 0x address. MOVES FUNDS OUT — always confirm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to_address": {"type": "string"},
                "amount": {"type": "string"},
            },
            "required": ["to_address", "amount"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    },
]


def _load_plugin_package():
    """Import the repo root as the package Hermes would create.

    Mirrors Hermes's loader: spec_from_file_location on ``__init__.py`` with
    ``submodule_search_locations`` so relative imports (``from .client``)
    resolve exactly as they do in production.
    """
    if PACKAGE_NAME in sys.modules:
        return sys.modules[PACKAGE_NAME]
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PACKAGE_NAME
    module.__path__ = [str(REPO_ROOT)]
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    return _load_plugin_package()


@pytest.fixture(scope="session")
def client_mod(plugin):
    return sys.modules[f"{PACKAGE_NAME}.client"]


class MockState:
    """Mutable knobs for the mock MCP endpoint."""

    def __init__(self):
        self.tools = list(SAMPLE_TOOLS)
        self.response_mode = "json"  # "json" | "sse"
        self.required_token = TEST_TOKEN
        self.calls = []  # recorded tools/call params
        self.call_result = {
            "content": [{"type": "text", "text": json.dumps({"balance_usdc": "12.34"})}]
        }
        self.requests = []  # every request: {method, path, authorization}
        self.redirect_to = None  # if set, answer every POST with a 302 there
        # None | "cycle" (same cursor forever) | "endless" (fresh cursor forever)
        self.pagination_mode = None
        self._page_counter = 0
        # if not None, tools/list returns exactly this as the JSON-RPC result
        self.list_result_override = None
        # if not None, tools/list returns these pages in order with cursors
        self.list_pages = None


class _Handler(BaseHTTPRequestHandler):
    state: MockState  # injected per-fixture

    def log_message(self, *args):  # keep test output clean
        pass

    def _record(self):
        self.state.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )

    def do_GET(self):
        self._record()
        self._send_json(200, {"service": "clawbank-mcp", "transport": "streamable-http"})

    def do_POST(self):
        self._record()
        if self.state.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.state.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {self.state.required_token}":
            self._send_json(401, {"error": "missing or invalid API token"})
            return

        length = int(self.headers.get("Content-Length", 0))
        message = json.loads(self.rfile.read(length))
        method = message.get("method")
        rpc_id = message.get("id")

        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-clawbank", "version": "0"},
            }
        elif method == "tools/list":
            result: dict = {"tools": self.state.tools}
            if self.state.list_result_override is not None:
                result = self.state.list_result_override
            elif self.state.list_pages is not None:
                index = self.state._page_counter
                self.state._page_counter += 1
                result = {"tools": self.state.list_pages[index]}
                if self.state._page_counter < len(self.state.list_pages):
                    result["nextCursor"] = f"page-{self.state._page_counter}"
            elif self.state.pagination_mode == "cycle":
                result = {"tools": [], "nextCursor": "same-cursor-forever"}
            elif self.state.pagination_mode == "endless":
                self.state._page_counter += 1
                result = {"tools": [], "nextCursor": f"page-{self.state._page_counter}"}
        elif method == "tools/call":
            params = message.get("params") or {}
            self.state.calls.append(params)
            result = self.state.call_result
        else:
            self._send_json(
                200,
                {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"unknown method {method}"}},
            )
            return

        envelope = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        if self.state.response_mode == "sse":
            body = f"event: message\ndata: {json.dumps(envelope)}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json(200, envelope)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockMCP:
    def __init__(self):
        self.state = MockState()
        handler = type("BoundHandler", (_Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/mcp"

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def mock_mcp():
    server = MockMCP()
    yield server
    server.shutdown()


@pytest.fixture()
def unreachable_url():
    """A URL nothing is listening on."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/mcp"


class FakeCtx:
    """Stand-in for Hermes's PluginContext, recording registrations."""

    def __init__(self):
        self.tools = {}
        self.skills = {}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            **kwargs,
        }

    def register_skill(self, name, path, description=""):
        self.skills[name] = {"path": path, "description": description}


@pytest.fixture()
def fake_ctx():
    return FakeCtx()

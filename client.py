"""JSON-RPC 2.0 client for the ClawBank MCP endpoint.

This module is the entire transport layer of the plugin. It speaks
MCP-over-streamable-HTTP to ``https://app.clawbank.co/mcp``:

* ``tools_list()``  — fetch the live, per-account tool catalog
* ``tools_call()``  — forward a tool invocation and return the result

There is deliberately no business logic here. All tool names, schemas,
descriptions, gating, and safety language live server-side and are
inherited by every client automatically.

Standard library only — no third-party dependencies.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_VERSION = "0.1.0"

DEFAULT_MCP_URL = "https://app.clawbank.co/mcp"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "clawbank-hermes-plugin", "version": PLUGIN_VERSION}

_ACCEPT = "application/json, text/event-stream"

# Catalog pagination guards: a well-behaved server returns the full catalog in
# a handful of pages. These caps turn a malicious or broken server into a
# clean startup error (which falls back to the cache) instead of a hang.
MAX_CATALOG_PAGES = 50


class ClawbankError(Exception):
    """Transport or protocol failure talking to the ClawBank MCP endpoint."""


class AuthError(ClawbankError):
    """The API token is missing, invalid, or revoked (HTTP 401/403)."""


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow redirects.

    Python's default redirect handler forwards the ``Authorization`` header to
    the redirect target — including a *different origin* — which would leak
    the full-access API token to whatever a 3xx points at. The endpoint never
    legitimately redirects, so any 3xx is refused outright (surfaced as an
    ``HTTPError`` by the default error handler and mapped to ``ClawbankError``
    in ``_post``).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects())

_LOOPBACK_HOSTS = {"localhost", "::1"}


def validate_mcp_url(url: str) -> str:
    """Enforce that the MCP endpoint cannot leak the bearer token in transit.

    Rules:

    * ``https://`` — always allowed.
    * ``http://``  — allowed only for loopback hosts (local development and
      tests), or when ``CLAWBANK_ALLOW_INSECURE_URL=1`` is explicitly set.
      That flag is a development escape hatch; never use it with a real token.
    * anything else — rejected.

    Raises ``ClawbankError`` on rejection.
    """
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return url
    if parts.scheme == "http":
        host = (parts.hostname or "").lower()
        if host in _LOOPBACK_HOSTS or host.startswith("127."):
            return url
        if os.environ.get("CLAWBANK_ALLOW_INSECURE_URL", "").strip() == "1":
            return url
        raise ClawbankError(
            f"refusing insecure MCP URL {url!r}: the API token would be sent in "
            "cleartext. Use an https:// endpoint, or set "
            "CLAWBANK_ALLOW_INSECURE_URL=1 for development only."
        )
    raise ClawbankError(f"invalid MCP URL {url!r}: must be an https:// URL")


def _parse_sse(body: str, want_id: Any) -> dict:
    """Extract the JSON-RPC response from a ``text/event-stream`` body.

    Streamable-HTTP servers may answer a POST with an SSE stream containing
    one or more JSON-RPC messages. We collect every ``data:`` payload and
    return the message whose ``id`` matches the request, falling back to the
    last parseable message.
    """
    messages = []
    data_lines: list = []
    for raw_line in body.splitlines() + [""]:
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                try:
                    messages.append(json.loads("\n".join(data_lines)))
                except ValueError:
                    pass
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    for message in reversed(messages):
        if isinstance(message, dict) and message.get("id") == want_id:
            return message
    if messages:
        return messages[-1]
    raise ClawbankError("SSE response contained no JSON-RPC message")


class ClawbankClient:
    """Minimal MCP client: Bearer auth, JSON or SSE responses, session echo."""

    def __init__(
        self,
        mcp_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.mcp_url = validate_mcp_url(
            mcp_url or os.environ.get("CLAWBANK_MCP_URL") or DEFAULT_MCP_URL
        )
        self.token = token
        self.timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        self._initialized = False

    # -- HTTP -----------------------------------------------------------------

    def _post(self, payload: dict) -> tuple[str, str]:
        """POST one JSON-RPC message. Returns ``(body, content_type)``."""
        headers = {
            "Content-Type": "application/json",
            "Accept": _ACCEPT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        request = urllib.request.Request(
            self.mcp_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with _OPENER.open(request, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                body = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("Content-Type", "")
                return body, content_type
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                location = (exc.headers.get("Location", "") if exc.headers else "").strip()
                raise ClawbankError(
                    f"refusing HTTP {exc.code} redirect from {self.mcp_url}"
                    + (f" to {location}" if location else "")
                    + " — credentials are never forwarded across redirects"
                ) from exc
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in (401, 403):
                raise AuthError(_error_detail(body) or f"HTTP {exc.code}") from exc
            raise ClawbankError(
                f"HTTP {exc.code} from {self.mcp_url}: {_error_detail(body) or body[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ClawbankError(f"cannot reach {self.mcp_url}: {exc.reason}") from exc
        except OSError as exc:
            raise ClawbankError(f"cannot reach {self.mcp_url}: {exc}") from exc

    # -- JSON-RPC ---------------------------------------------------------------

    def _rpc(self, method: str, params: dict | None = None, *, notification: bool = False):
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            self._next_id += 1
            payload["id"] = self._next_id

        body, content_type = self._post(payload)
        if notification or not body.strip():
            return None

        if content_type.split(";")[0].strip() == "text/event-stream":
            message = _parse_sse(body, payload.get("id"))
        else:
            try:
                message = json.loads(body)
            except ValueError as exc:
                raise ClawbankError(f"non-JSON response to {method}: {body[:200]}") from exc

        if isinstance(message, dict) and "error" in message:
            error = message["error"] or {}
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise ClawbankError(f"{method} failed: {detail or error}")
        return message.get("result") if isinstance(message, dict) else message

    # -- MCP methods ------------------------------------------------------------

    def initialize(self) -> None:
        """Best-effort MCP handshake.

        The ClawBank endpoint is stateless per-request, but a spec-compliant
        handshake keeps us compatible if that ever changes. Auth failures
        propagate; anything else is tolerated.
        """
        if self._initialized:
            return
        try:
            self._rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            )
            self._rpc("notifications/initialized", notification=True)
        except AuthError:
            raise
        except ClawbankError:
            pass
        self._initialized = True

    def tools_list(self) -> list:
        """Fetch the full per-account tool catalog (follows pagination).

        Bounded: a repeated cursor or more than ``MAX_CATALOG_PAGES`` pages is
        treated as a broken/hostile server and raises ``ClawbankError`` (the
        caller then falls back to the cached catalog or the setup tool).
        """
        self.initialize()
        tools: list = []
        cursor = None
        seen_cursors: set = set()
        for _ in range(MAX_CATALOG_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params) or {}
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise ClawbankError("tools/list pagination cursor cycle detected")
            seen_cursors.add(cursor)
        raise ClawbankError(
            f"tools/list did not terminate within {MAX_CATALOG_PAGES} pages"
        )

    def tools_call(self, name: str, arguments: dict | None = None) -> dict:
        """Forward one tool invocation. Returns the raw MCP tool result."""
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return result if isinstance(result, dict) else {"content": [], "raw": result}


def result_to_text(result: Any) -> str:
    """Flatten an MCP tool result into the string Hermes handlers must return.

    Preference order: ``structuredContent`` (machine-readable JSON) over
    joined ``text`` content blocks over a dump of the raw result. Server-side
    tool errors (``isError``) are wrapped in an ``{"error": ...}`` envelope.
    """
    if not isinstance(result, dict):
        return json.dumps(result)

    texts = [
        block.get("text", "")
        for block in (result.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(part for part in texts if part)

    if result.get("isError"):
        return json.dumps({"error": text or "tool call failed"})

    structured = result.get("structuredContent")
    if structured is not None:
        return json.dumps(structured)
    if text:
        return text
    return json.dumps(result)


# -- catalog cache ---------------------------------------------------------------
#
# ``tools/list`` runs once per Hermes launch. We keep the last successful
# catalog on disk so a flaky network or brief outage at startup still yields
# a working toolset. New server-side tools appear on the next successful
# launch — the same freshness model as every other MCP client.


def load_cached_catalog(path: Path) -> list:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tools = data.get("tools") if isinstance(data, dict) else None
    return tools if isinstance(tools, list) else []


def save_catalog(path: Path, tools: list) -> None:
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "tools": tools,
    }
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".catalog-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError:
        pass  # cache is an optimization; never fail a launch over it


def _error_detail(body: str) -> str:
    """Pull a human-readable message out of a JSON error body, if any."""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "")
    return ""

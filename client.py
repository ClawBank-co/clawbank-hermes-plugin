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

import hashlib
import ipaddress
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PLUGIN_VERSION = "0.1.0"

DEFAULT_MCP_URL = "https://app.clawbank.co/mcp"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "clawbank-hermes-plugin", "version": PLUGIN_VERSION}

_ACCEPT = "application/json, text/event-stream"

# Resource bounds: a well-behaved server never approaches these. They turn a
# malicious or broken server into a clean startup error (which falls back to
# the cache or the setup tool) instead of a hang, memory blow-up, or a
# poisoned registration pass.
MAX_CATALOG_PAGES = 50
MAX_CATALOG_TOOLS = 512
MAX_TOOL_NAME_LENGTH = 200
MAX_CURSOR_LENGTH = 4096
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # 8 MiB per HTTP response body
MAX_CATALOG_BYTES = 8 * 1024 * 1024  # 8 MiB across all paginated tool metadata
MAX_CACHE_FILE_BYTES = MAX_CATALOG_BYTES + 64 * 1024  # catalog plus cache envelope

# Cached catalogs are a fallback for flaky starts, not a source of truth:
# they expire, and they are bound to the endpoint + token identity that
# produced them so one account's catalog is never served to another.
CACHE_TTL = timedelta(days=7)


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


def _is_loopback_host(host: str) -> bool:
    """True only for ``localhost`` or a *literal* loopback IP address.

    The host must parse as an IP address — a DNS name is never loopback, no
    matter what it looks like. (A prefix check like ``startswith("127.")``
    would accept attacker-controlled public names such as
    ``127.evil.example``.)
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_mcp_url(url: str) -> str:
    """Enforce that the MCP endpoint cannot leak the bearer token in transit.

    Rules:

    * ``https://`` — allowed (must have a host).
    * ``http://``  — allowed only for ``localhost`` / literal loopback IPs
      (local development and tests), or when ``CLAWBANK_ALLOW_INSECURE_URL=1``
      is explicitly set. That flag is a development escape hatch; never use
      it with a real token.
    * anything else — rejected.

    Raises ``ClawbankError`` on rejection.
    """
    url = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise ClawbankError(f"invalid MCP URL {url!r}: {exc}") from exc
    if parts.scheme == "https":
        if not host:
            raise ClawbankError(f"invalid MCP URL {url!r}: missing host")
        return url
    if parts.scheme == "http":
        if _is_loopback_host(host):
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
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ClawbankError(
                        f"response from {self.mcp_url} exceeded "
                        f"{MAX_RESPONSE_BYTES} bytes; refusing to parse it"
                    )
                body = raw.decode("utf-8", errors="replace")
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
            body = exc.read(65536).decode("utf-8", errors="replace")
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

        Bounded and validated: malformed result shapes, repeated cursors,
        oversized catalogs, and runaway pagination all raise ``ClawbankError``
        (the caller then falls back to the cached catalog or the setup tool)
        instead of crashing registration or hanging.
        """
        self.initialize()
        tools: list = []
        catalog_bytes = 0
        cursor = None
        seen_cursors: set = set()
        for _ in range(MAX_CATALOG_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params)
            if result is None:
                result = {}
            if not isinstance(result, dict):
                raise ClawbankError("tools/list returned a non-object result")
            page = result.get("tools") or []
            if not isinstance(page, list):
                raise ClawbankError("tools/list 'tools' field is not a list")
            catalog_bytes += len(
                json.dumps(page, separators=(",", ":")).encode("utf-8")
            )
            if catalog_bytes > MAX_CATALOG_BYTES:
                raise ClawbankError(
                    f"catalog metadata exceeded {MAX_CATALOG_BYTES} bytes; refusing it"
                )
            tools.extend(page)
            if len(tools) > MAX_CATALOG_TOOLS:
                raise ClawbankError(
                    f"catalog exceeded {MAX_CATALOG_TOOLS} tools; refusing it"
                )
            cursor = result.get("nextCursor")
            if not cursor:
                return sanitize_tools(tools)
            if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_LENGTH:
                raise ClawbankError("tools/list returned an invalid pagination cursor")
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


def sanitize_tools(raw: Any) -> list:
    """Reduce a raw tool catalog to well-formed, deduplicated descriptors.

    Runs on every catalog before it is registered or cached — fetched *or*
    loaded from disk. Structural garbage (not a list, oversized) raises
    ``ClawbankError``; individually malformed descriptors are dropped rather
    than poisoning registration. Only known keys survive, with the shapes
    downstream code assumes:

    * ``name`` — required non-empty string, capped length, first occurrence
      wins (a duplicate can never shadow an earlier tool's handler)
    * ``description`` — string, else ``""``
    * ``inputSchema`` — dict, else an empty object schema
    * ``annotations`` — dict, preserved for risk metadata (``readOnlyHint``,
      ``destructiveHint``) if/when the server ships it
    """
    if not isinstance(raw, list):
        raise ClawbankError("tool catalog is not a list")
    if len(raw) > MAX_CATALOG_TOOLS:
        raise ClawbankError(f"catalog exceeded {MAX_CATALOG_TOOLS} tools; refusing it")
    try:
        catalog_bytes = len(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ClawbankError("tool catalog metadata is not JSON-serializable") from exc
    if catalog_bytes > MAX_CATALOG_BYTES:
        raise ClawbankError(
            f"catalog metadata exceeded {MAX_CATALOG_BYTES} bytes; refusing it"
        )
    tools: list = []
    seen_names: set = set()
    for tool in raw:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > MAX_TOOL_NAME_LENGTH
            or name in seen_names
        ):
            continue
        description = tool.get("description")
        schema = tool.get("inputSchema")
        clean = {
            "name": name,
            "description": description if isinstance(description, str) else "",
            "inputSchema": schema
            if isinstance(schema, dict)
            else {"type": "object", "properties": {}},
        }
        annotations = tool.get("annotations")
        if isinstance(annotations, dict):
            clean["annotations"] = annotations
        seen_names.add(name)
        tools.append(clean)
    return tools


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
# a working toolset. The cache is strictly a fallback: it expires after
# ``CACHE_TTL``, is bound to the (endpoint, token) identity that produced it,
# and is re-validated with ``sanitize_tools`` before use.


def catalog_cache_identity(mcp_url: str, token: str | None) -> str:
    """Non-secret identity a cache entry is bound to.

    Endpoint plus a short token fingerprint (a SHA-256 prefix — not
    reversible, never the token itself). Rotating the token, switching
    accounts, or pointing at a different endpoint all invalidate the cache.
    """
    fingerprint = hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:12]
    return f"{mcp_url}#{fingerprint}"


def load_cached_catalog(path: Path, identity: str) -> list:
    """Load a cached catalog if it matches ``identity`` and is fresh.

    Anything unexpected — unreadable file, wrong identity, missing or
    unparseable ``saved_at``, expired TTL, malformed tools — returns ``[]``
    (fail closed to the setup tool; never register a stale or foreign
    catalog).
    """
    try:
        cache_path = Path(path)
        if cache_path.stat().st_size > MAX_CACHE_FILE_BYTES:
            return []
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("identity") != identity:
        return []
    try:
        saved_at = datetime.fromisoformat(data.get("saved_at") or "")
    except (TypeError, ValueError):
        return []
    if saved_at.tzinfo is None:
        return []
    if datetime.now(timezone.utc) - saved_at > CACHE_TTL:
        return []
    try:
        return sanitize_tools(data.get("tools"))
    except ClawbankError:
        return []


def save_catalog(path: Path, tools: list, identity: str) -> None:
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity,
        "tools": tools,
    }
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".catalog-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
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

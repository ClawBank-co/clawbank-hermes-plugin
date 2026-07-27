"""ClawBank plugin for Hermes — a dynamic catalog proxy.

At startup this plugin fetches the live tool catalog from the ClawBank MCP
endpoint (``tools/list``) and registers every tool with Hermes. Invocations
are forwarded verbatim to ``tools/call``. Nothing is hardcoded:

* **No tool list in this repo.** The catalog is discovered at load time and
  is per-account — each user's Hermes sees exactly the tools their ClawBank
  account can call.
* **No business logic.** Schemas, descriptions, gating, and safety language
  all live server-side and are inherited automatically. When ClawBank ships
  a new capability, it appears here on the next Hermes launch with zero
  plugin changes.

If the catalog cannot be fetched (no token, revoked token, network down with
a cold cache), the plugin degrades to a single ``clawbank_setup`` tool that
explains how to connect — it never fails the Hermes launch.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .client import (
    DEFAULT_MCP_URL,
    PLUGIN_VERSION,
    AuthError,
    ClawbankClient,
    ClawbankError,
    catalog_cache_identity,
    load_cached_catalog,
    result_to_text,
    save_catalog,
)

__version__ = PLUGIN_VERSION

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
_CATALOG_CACHE = _PLUGIN_DIR / ".catalog.json"
_SKILL_PATH = _PLUGIN_DIR / "skills" / "clawbank" / "SKILL.md"

TOOLSET = "clawbank"
EMOJI = "🦞"

_REGISTER_URL = "https://app.clawbank.co/users/register"
_SETTINGS_URL = "https://app.clawbank.co/users/settings"

_SETUP_SCHEMA = {
    "name": "clawbank_setup",
    "description": (
        "The ClawBank plugin is installed but could not load its tool catalog "
        "(missing/invalid API token, or the API was unreachable). Call this "
        "tool to get exact instructions for connecting a ClawBank account — "
        "signup, token minting, and configuration."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_SETUP_REASONS = {
    "no_token": "No CLAWBANK_API_TOKEN is set in the environment.",
    "invalid_token": "The configured ClawBank API token was rejected (revoked or mistyped).",
    "unreachable": "The ClawBank API was unreachable at startup and no cached catalog exists yet.",
    "insecure_url": (
        "The configured CLAWBANK_MCP_URL was rejected: it must be an https:// "
        "endpoint (plain http:// is allowed only for localhost, or with "
        "CLAWBANK_ALLOW_INSECURE_URL=1 in development). Unset the override to "
        "use the default endpoint."
    ),
}


def _make_setup_handler(reason: str):
    def clawbank_setup(args: dict, **kwargs) -> str:
        return json.dumps(
            {
                "status": "not_connected",
                "reason": reason,
                "detail": _SETUP_REASONS.get(reason, reason),
                "how_to_connect": [
                    f"1. Create a free ClawBank account: {_REGISTER_URL}",
                    f"2. Mint an API token: {_SETTINGS_URL} → API tokens",
                    "3. Set it in the environment: export CLAWBANK_API_TOKEN=\"<token>\" "
                    "(Hermes also saves it to .env when prompted during "
                    "`hermes plugins install`)",
                    "4. Restart Hermes — the full ClawBank tool catalog loads automatically.",
                ],
                "agent_bootstrap": (
                    "Agents can mint a token without a browser (base URL "
                    "https://app.clawbank.co): "
                    '1) POST /api/v1/auth/request_code with JSON body {"email": "<email>"} '
                    "— emails a login code (codes expire quickly). "
                    '2) POST /api/v1/auth/verify_code with JSON body {"email": "<email>", '
                    '"code": "<code>"} — BOTH fields are required; returns a short-lived '
                    "bootstrap token. "
                    "3) POST /api/v1/auth/bootstrap/api_tokens with header "
                    "'Authorization: Bearer *** token>' and JSON body "
                    "{'name': 'hermes-default', 'scopes': ['read', 'send'], "
                    "'daily_cap_usd': '10'} — returns a bounded long-lived API token. "
                    "Omitting scopes intentionally creates a full-access token including "
                    "raw transaction signing; the plugin does not use that as its silent "
                    "default."
                ),
                "recommended_token": {
                    "name": "hermes-default",
                    "scopes": ["read", "send"],
                    "daily_cap_usd": "10",
                },
                "docs": "https://app.clawbank.co/docs",
                "security_note": (
                    "The recommended default is scoped and capped. Omitting scopes "
                    "intentionally creates a full-access token including raw signing; "
                    "that can be appropriate for deliberate fresh-account exploration, "
                    "but should not happen silently. Never display, echo, or log a token."
                ),
            }
        )

    return clawbank_setup


def _is_destructive(tool: dict) -> bool:
    """Fail closed unless MCP annotations explicitly classify a tool read-only."""
    annotations = tool.get("annotations")
    return not (
        isinstance(annotations, dict)
        and annotations.get("readOnlyHint") is True
        and annotations.get("destructiveHint") is False
    )


def _make_handler(
    client: ClawbankClient,
    tool_name: str,
    *,
    destructive: bool = False,
    allow_destructive: bool = False,
    cached_catalog: bool = False,
):
    """One generic handler per tool: serialize args → tools/call → text.

    Per Hermes handler rules: always return a JSON-safe string, never raise.
    """

    def handler(args: dict, **kwargs) -> str:
        if destructive and not allow_destructive:
            reason = (
                "This tool came from a cached catalog, whose annotations cannot "
                "authorize execution while ClawBank is unreachable."
                if cached_catalog
                else "This tool was not explicitly classified read-only by the server."
            )
            return json.dumps(
                {
                    "error": "destructive_tool_blocked",
                    "tool": tool_name,
                    "hint": (
                        f"{reason} It is disabled by default. Use a scoped, "
                        "spend-capped token and set "
                        "CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS=1 before starting Hermes "
                        "only when destructive operations are intentionally required."
                    ),
                }
            )
        try:
            result = client.tools_call(tool_name, args or {})
            return result_to_text(result)
        except AuthError as exc:
            return json.dumps(
                {
                    "error": f"ClawBank rejected the API token: {exc}",
                    "hint": f"Mint a new token at {_SETTINGS_URL} and update "
                    "CLAWBANK_API_TOKEN, then restart Hermes.",
                }
            )
        except Exception as exc:  # noqa: BLE001 — handlers must never raise
            return json.dumps({"error": f"ClawBank call failed: {exc}"})

    handler.__name__ = tool_name
    return handler


def _to_hermes_schema(tool: dict) -> dict:
    """MCP tool descriptor → Hermes (OpenAI function-style) schema."""
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
    }


def _short_description(text: str, limit: int = 140) -> str:
    """First line of a server description, trimmed for banner display."""
    first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    if len(first_line) <= limit:
        return first_line
    return first_line[: limit - 1].rstrip() + "…"


def register(ctx) -> None:
    token = (os.environ.get("CLAWBANK_API_TOKEN") or os.environ.get("CLAWBANK_TOKEN") or "").strip()
    mcp_url = os.environ.get("CLAWBANK_MCP_URL", DEFAULT_MCP_URL)
    allow_destructive = (
        os.environ.get("CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS", "").strip() == "1"
    )

    tools: list = []
    catalog_from_cache = False
    setup_reason = ""
    client = None
    try:
        client = ClawbankClient(mcp_url=mcp_url, token=token or None)
    except ClawbankError as exc:
        # URL failed validation (non-HTTPS, non-loopback). Never fail the
        # launch — degrade to the setup tool, which explains the rejection.
        setup_reason = "insecure_url"
        logger.warning("ClawBank MCP URL rejected: %s", exc)

    if client is not None and client.mcp_url != DEFAULT_MCP_URL:
        logger.warning(
            "ClawBank: using custom MCP endpoint %s — it will receive the "
            "API token; only point CLAWBANK_MCP_URL at endpoints "
            "you control",
            client.mcp_url,
        )

    if client is None:
        pass
    elif not token:
        setup_reason = "no_token"
    else:
        cache_identity = catalog_cache_identity(client.mcp_url, token)
        try:
            tools = client.tools_list()
            save_catalog(_CATALOG_CACHE, tools, cache_identity)
        except AuthError as exc:
            setup_reason = "invalid_token"
            logger.warning("ClawBank API token rejected: %s", exc)
        except ClawbankError as exc:
            tools = load_cached_catalog(_CATALOG_CACHE, cache_identity)
            if tools:
                catalog_from_cache = True
                logger.warning(
                    "ClawBank catalog fetch failed (%s); using cached catalog "
                    "with %d tools", exc, len(tools),
                )
            else:
                setup_reason = "unreachable"
                logger.warning("ClawBank catalog fetch failed and no cache exists: %s", exc)

    if not tools:
        ctx.register_tool(
            name="clawbank_setup",
            toolset=TOOLSET,
            schema=_SETUP_SCHEMA,
            handler=_make_setup_handler(setup_reason),
            description="Connect a ClawBank account (signup + API token instructions).",
            emoji=EMOJI,
        )
    else:
        assert client is not None
        for tool in tools:
            name = tool.get("name")
            if not name:
                continue
            ctx.register_tool(
                name=name,
                toolset=TOOLSET,
                schema=_to_hermes_schema(tool),
                handler=_make_handler(
                    client,
                    name,
                    destructive=catalog_from_cache or _is_destructive(tool),
                    cached_catalog=catalog_from_cache,
                    allow_destructive=allow_destructive,
                ),
                description=_short_description(tool.get("description", "")),
                emoji=EMOJI,
            )
        logger.info("ClawBank: registered %d tools from the live catalog", len(tools))

    if _SKILL_PATH.exists():
        ctx.register_skill(
            "clawbank",
            _SKILL_PATH,
            description=(
                "When and how to use ClawBank: capability map, confirmation "
                "rules for fund-moving tools, and safety invariants."
            ),
        )

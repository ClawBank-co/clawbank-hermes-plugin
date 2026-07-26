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
                    "Agents can mint a token without a browser: "
                    "POST /api/v1/auth/request_code (emails a login code), "
                    "POST /api/v1/auth/verify_code (returns a bootstrap token), "
                    "POST /api/v1/auth/bootstrap/api_tokens (returns the long-lived API token). "
                    "Base URL: https://app.clawbank.co"
                ),
                "docs": "https://app.clawbank.co/docs",
                "security_note": (
                    "The token grants full account access. Never display, echo, or "
                    "log its value."
                ),
            }
        )

    return clawbank_setup


def _make_handler(client: ClawbankClient, tool_name: str):
    """One generic handler per tool: serialize args → tools/call → text.

    Per Hermes handler rules: always return a JSON-safe string, never raise.
    """

    def handler(args: dict, **kwargs) -> str:
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
    client = ClawbankClient(mcp_url=mcp_url, token=token or None)

    tools: list = []
    setup_reason = ""
    if not token:
        setup_reason = "no_token"
    else:
        try:
            tools = client.tools_list()
            save_catalog(_CATALOG_CACHE, tools)
        except AuthError as exc:
            setup_reason = "invalid_token"
            logger.warning("ClawBank API token rejected: %s", exc)
        except ClawbankError as exc:
            tools = load_cached_catalog(_CATALOG_CACHE)
            if tools:
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
        for tool in tools:
            name = tool.get("name")
            if not name:
                continue
            ctx.register_tool(
                name=name,
                toolset=TOOLSET,
                schema=_to_hermes_schema(tool),
                handler=_make_handler(client, name),
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

#!/usr/bin/env python3
"""Drift alarm against the live ClawBank MCP surface.

Always checked (no credentials needed):
  1. ``GET /mcp`` answers 200 with the expected service identity.
  2. Unauthenticated ``POST /mcp`` is rejected with 401.

If ``CLAWBANK_TEST_TOKEN`` is set, additionally loads the real catalog
through the plugin's own client — the same code path Hermes runs at startup.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_URL = os.environ.get("CLAWBANK_MCP_URL", "https://app.clawbank.co/mcp")


def load_plugin_client():
    spec = importlib.util.spec_from_file_location(
        "clawbank_plugin",
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "clawbank_plugin"
    module.__path__ = [str(REPO_ROOT)]
    sys.modules["clawbank_plugin"] = module
    spec.loader.exec_module(module)
    return sys.modules["clawbank_plugin.client"]


def check_health() -> None:
    with urllib.request.urlopen(MCP_URL, timeout=15) as response:
        assert response.status == 200, f"health check returned {response.status}"
        payload = json.loads(response.read())
    assert payload.get("service") == "clawbank-mcp", f"unexpected identity: {payload}"
    print(f"ok: GET {MCP_URL} → 200, service={payload['service']}")


def check_unauthenticated_rejection() -> None:
    request = urllib.request.Request(
        MCP_URL,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401, f"expected 401 without a token, got {exc.code}"
        print("ok: unauthenticated tools/list → 401")
        return
    raise AssertionError("unauthenticated tools/list unexpectedly succeeded")


def check_catalog(client_mod) -> None:
    token = os.environ.get("CLAWBANK_TEST_TOKEN", "").strip()
    if not token:
        print("skip: CLAWBANK_TEST_TOKEN not set — catalog load not exercised")
        return
    client = client_mod.ClawbankClient(mcp_url=MCP_URL, token=token)
    tools = client.tools_list()
    assert tools, "authenticated tools/list returned an empty catalog"
    missing = [t["name"] for t in tools if not t.get("inputSchema")]
    assert not missing, f"tools missing inputSchema: {missing[:5]}"
    print(f"ok: authenticated catalog loaded — {len(tools)} tools")


def main() -> int:
    client_mod = load_plugin_client()
    check_health()
    check_unauthenticated_rejection()
    check_catalog(client_mod)
    print("live surface: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

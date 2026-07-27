"""Real-Hermes integration tests — no fake context anywhere.

These install the plugin into an isolated ``$HERMES_HOME`` exactly as
``hermes plugins install`` lays it out, then drive **Hermes's own** plugin
discovery, loading, tool registry, and dispatch in a fresh subprocess (the
same lifecycle as a real ``hermes`` launch).

Skipped automatically when ``hermes-agent`` is not installed; CI runs them in
a dedicated job against the pinned minimum Hermes release (see
``.github/workflows/ci.yml``). To run locally::

    pip install "hermes-agent>=0.19.0"
    pytest tests/test_hermes_integration.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SAMPLE_TOOLS, TEST_TOKEN, MockMCP

pytest.importorskip("hermes_cli", reason="hermes-agent is not installed")

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).resolve().parent / "_hermes_driver.py"

# What `hermes plugins install` would not ship: VCS/tool caches, stale
# catalog caches, tests, and repo assets irrelevant to loading.
_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "*.pyc",
    ".catalog.json",
    ".DS_Store",
    "tests",
    "*.png",
)


@pytest.fixture()
def hermes_home(tmp_path):
    """An isolated Hermes profile with this repo installed and enabled."""
    home = tmp_path / "hermes-home"
    shutil.copytree(REPO_ROOT, home / "plugins" / "clawbank", ignore=_COPY_IGNORE)
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - clawbank\n")
    return home


def _run_driver(home: Path, extra_env: dict | None = None) -> dict:
    """Run one real-Hermes discovery pass in a fresh interpreter."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("CLAWBANK", "HERMES"))
    }
    env["HERMES_HOME"] = str(home)
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, str(DRIVER)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(home),
    )
    assert proc.returncode == 0, f"driver failed:\n{proc.stdout}\n{proc.stderr}"
    lines = [
        line for line in proc.stdout.splitlines() if line.startswith("HERMES_SMOKE_JSON:")
    ]
    assert lines, f"driver produced no facts:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(lines[-1].split("HERMES_SMOKE_JSON:", 1)[1])


def _plugin_entry(facts: dict) -> dict:
    entries = facts["plugins"]
    assert len(entries) == 1, f"expected exactly one clawbank plugin: {entries}"
    return entries[0]


class TestRealHermes:
    def test_no_token_loads_and_degrades_to_setup_tool(self, hermes_home):
        facts = _run_driver(hermes_home, {"HERMES_SMOKE_DISPATCH": "clawbank_setup"})

        entry = _plugin_entry(facts)
        assert entry["enabled"] is True
        assert entry["error"] is None
        assert entry["version"] == "0.1.0"
        assert entry["source"] == "user"

        assert facts["clawbank_tools"] == ["clawbank_setup"]
        payload = json.loads(facts["dispatch"])
        assert payload["reason"] == "no_token"
        assert any("register" in step for step in payload["how_to_connect"])

    def test_skill_registered_and_file_served(self, hermes_home):
        facts = _run_driver(hermes_home)
        skill_path = facts["skill_path"]
        assert skill_path and skill_path.endswith("SKILL.md")
        # The safety-critical content must be inside the file Hermes serves
        # (CBH-004: supporting files are not reliably loadable in-session).
        content = Path(skill_path).read_text(encoding="utf-8")
        for required in (
            "confirmation contract",
            "Confirmation matrix by area",
            "Failure patterns to avoid",
            "Known limits",
        ):
            assert required in content, f"SKILL.md is missing: {required}"

    def test_live_catalog_registers_and_dispatches(self, hermes_home):
        server = MockMCP()
        try:
            facts = _run_driver(
                hermes_home,
                {
                    "CLAWBANK_API_TOKEN": TEST_TOKEN,
                    "CLAWBANK_MCP_URL": server.url,
                    "HERMES_SMOKE_DISPATCH": "get_balance",
                    "HERMES_SMOKE_DISPATCH_ARGS": "{}",
                },
            )

            entry = _plugin_entry(facts)
            assert entry["enabled"] is True and entry["error"] is None
            assert facts["clawbank_tools"] == sorted(t["name"] for t in SAMPLE_TOOLS)
            assert entry["tools"] == len(SAMPLE_TOOLS)

            # Server schemas survive the trip into Hermes's registry intact.
            send_schema = facts["schemas"]["send_usdc_on_base"]
            assert send_schema["parameters"]["required"] == ["to_address", "amount"]
            assert "MOVES FUNDS OUT" in send_schema["description"]

            # A read-only dispatch through Hermes's own registry reaches the
            # endpoint (get_balance is annotated readOnlyHint: true).
            assert json.loads(facts["dispatch"]) == {"balance_usdc": "12.34"}
            assert server.state.calls[-1] == {"name": "get_balance", "arguments": {}}
        finally:
            server.shutdown()

    def test_destructive_tool_blocked_through_real_hermes(self, hermes_home):
        """The annotation gate holds inside a real Hermes registry: a
        destructive tool dispatch is refused locally and never reaches the
        endpoint unless CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS=1 is set."""
        server = MockMCP()
        try:
            facts = _run_driver(
                hermes_home,
                {
                    "CLAWBANK_API_TOKEN": TEST_TOKEN,
                    "CLAWBANK_MCP_URL": server.url,
                    "HERMES_SMOKE_DISPATCH": "send_usdc_on_base",
                    "HERMES_SMOKE_DISPATCH_ARGS": json.dumps(
                        {"to_address": "0xdef", "amount": "1"}
                    ),
                },
            )

            payload = json.loads(facts["dispatch"])
            assert payload["error"] == "destructive_tool_blocked"
            # The endpoint never saw a tools/call for the blocked tool.
            assert all(c["name"] != "send_usdc_on_base" for c in server.state.calls)
        finally:
            server.shutdown()

    def test_rejected_token_degrades_without_failing_launch(self, hermes_home):
        server = MockMCP()
        try:
            facts = _run_driver(
                hermes_home,
                {
                    "CLAWBANK_API_TOKEN": "revoked-token",
                    "CLAWBANK_MCP_URL": server.url,
                    "HERMES_SMOKE_DISPATCH": "clawbank_setup",
                },
            )

            entry = _plugin_entry(facts)
            assert entry["enabled"] is True
            assert entry["error"] is None  # plugin load itself never fails
            assert facts["clawbank_tools"] == ["clawbank_setup"]
            payload = json.loads(facts["dispatch"])
            assert payload["reason"] == "invalid_token"
        finally:
            server.shutdown()

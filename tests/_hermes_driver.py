"""Subprocess driver for the real-Hermes integration tests.

Executed by ``test_hermes_integration.py`` in a fresh interpreter — the same
way a real ``hermes`` launch loads plugins. It runs Hermes's own plugin
discovery against ``$HERMES_HOME`` (no fake context anywhere) and prints a
single line of JSON facts prefixed with ``HERMES_SMOKE_JSON:`` for the parent
test to assert on.

Optional env knobs:

* ``HERMES_SMOKE_DISPATCH``       — tool name to dispatch through the real
                                    tool registry after discovery
* ``HERMES_SMOKE_DISPATCH_ARGS``  — JSON arguments for that dispatch
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    from hermes_cli.plugins import get_plugin_manager
    from tools.registry import registry

    manager = get_plugin_manager()
    manager.discover_and_load()

    clawbank_tools = sorted(registry.get_tool_names_for_toolset("clawbank"))
    skill_path = manager.find_plugin_skill("clawbank:clawbank")

    facts = {
        "plugins": [p for p in manager.list_plugins() if p["key"] == "clawbank"],
        "clawbank_tools": clawbank_tools,
        "schemas": {name: registry.get_schema(name) for name in clawbank_tools},
        "skill_path": str(skill_path) if skill_path else None,
        "dispatch": None,
    }

    tool = os.environ.get("HERMES_SMOKE_DISPATCH")
    if tool:
        args = json.loads(os.environ.get("HERMES_SMOKE_DISPATCH_ARGS", "{}"))
        facts["dispatch"] = registry.dispatch(tool, args)

    print("HERMES_SMOKE_JSON:" + json.dumps(facts))
    return 0


if __name__ == "__main__":
    sys.exit(main())

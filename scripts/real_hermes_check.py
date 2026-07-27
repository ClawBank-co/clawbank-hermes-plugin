#!/usr/bin/env python3
"""Fail unless a real Hermes runtime can discover and load this plugin."""

from __future__ import annotations

import os
from pathlib import Path

from hermes_cli.plugins import PluginManager


def main() -> None:
    # This smoke test intentionally exercises the no-token setup fallback.
    os.environ.pop("CLAWBANK_API_TOKEN", None)
    os.environ.pop("CLAWBANK_TOKEN", None)

    manager = PluginManager()
    manager.discover_and_load()
    plugin = manager._plugins.get("clawbank")
    if plugin is None:
        raise SystemExit("ClawBank plugin was not discovered")
    if not plugin.enabled or plugin.error:
        raise SystemExit(f"ClawBank plugin failed to load: {plugin.error}")
    expected_path = (Path(os.environ["HERMES_HOME"]) / "plugins" / "clawbank").resolve()
    loaded_path = Path(plugin.manifest.path or "").resolve()
    if loaded_path != expected_path:
        raise SystemExit(
            f"ClawBank loaded from {loaded_path}, not isolated install {expected_path}"
        )
    if "clawbank_setup" not in manager._plugin_tool_names:
        raise SystemExit("ClawBank setup fallback was not registered")
    print("Real Hermes discovery passed: clawbank_setup registered")


if __name__ == "__main__":
    main()

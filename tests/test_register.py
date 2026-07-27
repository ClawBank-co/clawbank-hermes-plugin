"""register(ctx) behavior: dynamic catalog, fallbacks, and tool dispatch."""

from __future__ import annotations

import json

from conftest import SAMPLE_TOOLS, TEST_TOKEN


def _wire_env(monkeypatch, plugin, tmp_path, url, token=TEST_TOKEN):
    monkeypatch.setenv("CLAWBANK_MCP_URL", url)
    if token is None:
        monkeypatch.delenv("CLAWBANK_API_TOKEN", raising=False)
        monkeypatch.delenv("CLAWBANK_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CLAWBANK_API_TOKEN", token)
    monkeypatch.setattr(plugin, "_CATALOG_CACHE", tmp_path / "catalog.json")


class TestDynamicRegistration:
    def test_registers_every_catalog_tool(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {t["name"] for t in SAMPLE_TOOLS}
        entry = fake_ctx.tools["send_usdc_on_base"]
        assert entry["toolset"] == "clawbank"
        # MCP inputSchema is remapped to the Hermes "parameters" key
        assert entry["schema"]["parameters"]["required"] == ["to_address", "amount"]
        # Server-side safety language is inherited verbatim
        assert "MOVES FUNDS OUT" in entry["schema"]["description"]

    def test_registers_bundled_skill(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        plugin.register(fake_ctx)
        assert "clawbank" in fake_ctx.skills
        assert fake_ctx.skills["clawbank"]["path"].name == "SKILL.md"

    def test_successful_fetch_writes_cache(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        plugin.register(fake_ctx)
        cached = json.loads((tmp_path / "catalog.json").read_text())
        assert [t["name"] for t in cached["tools"]] == [t["name"] for t in SAMPLE_TOOLS]


class TestFallbacks:
    def test_no_token_registers_setup_tool(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url, token=None)
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {"clawbank_setup"}
        payload = json.loads(fake_ctx.tools["clawbank_setup"]["handler"]({}))
        assert payload["reason"] == "no_token"
        assert any("register" in step for step in payload["how_to_connect"])
        assert payload["recommended_token"] == {
            "name": "hermes-default",
            "scopes": ["read", "send"],
            "daily_cap_usd": "10",
        }

    def test_rejected_token_registers_setup_tool(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url, token="revoked-token")
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {"clawbank_setup"}
        payload = json.loads(fake_ctx.tools["clawbank_setup"]["handler"]({}))
        assert payload["reason"] == "invalid_token"

    def test_offline_start_uses_cached_catalog(self, plugin, fake_ctx, client_mod, unreachable_url, tmp_path, monkeypatch):
        cache = tmp_path / "catalog.json"
        identity = client_mod.catalog_cache_identity(unreachable_url, TEST_TOKEN)
        client_mod.save_catalog(cache, SAMPLE_TOOLS, identity)
        _wire_env(monkeypatch, plugin, tmp_path, unreachable_url)
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {t["name"] for t in SAMPLE_TOOLS}

    def test_offline_cached_annotations_cannot_authorize_calls(
        self, plugin, fake_ctx, client_mod, unreachable_url, tmp_path, monkeypatch
    ):
        cache = tmp_path / "catalog.json"
        identity = client_mod.catalog_cache_identity(unreachable_url, TEST_TOKEN)
        client_mod.save_catalog(cache, SAMPLE_TOOLS, identity)
        _wire_env(monkeypatch, plugin, tmp_path, unreachable_url)
        plugin.register(fake_ctx)

        payload = json.loads(fake_ctx.tools["get_balance"]["handler"]({}))

        assert payload["error"] == "destructive_tool_blocked"
        assert "cached catalog" in payload["hint"]

    def test_offline_start_ignores_cache_from_other_token(self, plugin, fake_ctx, client_mod, unreachable_url, tmp_path, monkeypatch):
        """A cache written under one token identity is never served to
        another (account switch / token rotation)."""
        cache = tmp_path / "catalog.json"
        other = client_mod.catalog_cache_identity(unreachable_url, "someone-elses-token")
        client_mod.save_catalog(cache, SAMPLE_TOOLS, other)
        _wire_env(monkeypatch, plugin, tmp_path, unreachable_url)
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {"clawbank_setup"}
        payload = json.loads(fake_ctx.tools["clawbank_setup"]["handler"]({}))
        assert payload["reason"] == "unreachable"

    def test_offline_start_with_cold_cache_registers_setup(self, plugin, fake_ctx, unreachable_url, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, unreachable_url)
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {"clawbank_setup"}
        payload = json.loads(fake_ctx.tools["clawbank_setup"]["handler"]({}))
        assert payload["reason"] == "unreachable"

    def test_insecure_url_registers_setup_tool_not_crash(self, plugin, fake_ctx, tmp_path, monkeypatch):
        """CBH-001: a cleartext non-loopback endpoint must never receive the
        token — and a bad URL must degrade to the setup tool, not fail the
        Hermes launch."""
        _wire_env(monkeypatch, plugin, tmp_path, "http://evil.example.com/mcp")
        monkeypatch.delenv("CLAWBANK_ALLOW_INSECURE_URL", raising=False)
        plugin.register(fake_ctx)

        assert set(fake_ctx.tools) == {"clawbank_setup"}
        payload = json.loads(fake_ctx.tools["clawbank_setup"]["handler"]({}))
        assert payload["reason"] == "insecure_url"
        assert "https" in payload["detail"]


class TestDispatch:
    def test_destructive_annotation_blocks_call_by_default(
        self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch
    ):
        mock_mcp.state.tools[1] = {
            **mock_mcp.state.tools[1],
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        }
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        monkeypatch.delenv("CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS", raising=False)
        plugin.register(fake_ctx)

        payload = json.loads(
            fake_ctx.tools["send_usdc_on_base"]["handler"](
                {"to_address": "0xdef", "amount": "1"}
            )
        )

        assert payload["error"] == "destructive_tool_blocked"
        assert mock_mcp.state.calls == []

    def test_explicit_opt_in_allows_destructive_call(
        self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch
    ):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        monkeypatch.setenv("CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS", "1")
        plugin.register(fake_ctx)

        output = fake_ctx.tools["send_usdc_on_base"]["handler"](
            {"to_address": "0xdef", "amount": "1"}
        )

        assert json.loads(output) == {"balance_usdc": "12.34"}
        assert mock_mcp.state.calls[-1]["name"] == "send_usdc_on_base"

    def test_handler_forwards_to_tools_call(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        plugin.register(fake_ctx)

        handler = fake_ctx.tools["get_balance"]["handler"]
        output = handler({})
        assert isinstance(output, str)
        assert json.loads(output) == {"balance_usdc": "12.34"}
        assert mock_mcp.state.calls[-1] == {"name": "get_balance", "arguments": {}}

    def test_handler_passes_arguments_through(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        monkeypatch.setenv("CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS", "1")
        plugin.register(fake_ctx)

        fake_ctx.tools["send_usdc_on_base"]["handler"]({"to_address": "0xdef", "amount": "1"})
        assert mock_mcp.state.calls[-1]["arguments"] == {"to_address": "0xdef", "amount": "1"}

    def test_handler_never_raises(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        plugin.register(fake_ctx)
        handler = fake_ctx.tools["get_balance"]["handler"]

        mock_mcp.shutdown()  # server dies mid-session
        output = handler({})
        assert "error" in json.loads(output)

    def test_handler_reports_auth_failure_with_hint(self, plugin, fake_ctx, mock_mcp, tmp_path, monkeypatch):
        _wire_env(monkeypatch, plugin, tmp_path, mock_mcp.url)
        plugin.register(fake_ctx)

        mock_mcp.state.required_token = "rotated-away"  # token revoked mid-session
        payload = json.loads(fake_ctx.tools["get_balance"]["handler"]({}))
        assert "hint" in payload and "error" in payload


class TestSchemaShaping:
    def test_missing_or_malformed_annotations_fail_closed(self, plugin):
        assert plugin._is_destructive({"name": "unclassified"}) is True
        assert plugin._is_destructive(
            {
                "name": "malformed",
                "annotations": {
                    "readOnlyHint": "true",
                    "destructiveHint": False,
                },
            }
        ) is True

    def test_short_description_truncates_first_line(self, plugin):
        long = "x" * 200 + "\nsecond line"
        short = plugin._short_description(long)
        assert len(short) <= 140
        assert short.endswith("…")

    def test_missing_input_schema_gets_empty_object(self, plugin):
        schema = plugin._to_hermes_schema({"name": "t"})
        assert schema["parameters"] == {"type": "object", "properties": {}}

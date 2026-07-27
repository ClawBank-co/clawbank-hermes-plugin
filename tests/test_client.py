"""Transport tests: JSON/SSE parsing, auth, pagination-safe catalog, cache,
URL validation, and redirect refusal."""

from __future__ import annotations

import json

import pytest
from conftest import SAMPLE_TOOLS, TEST_TOKEN, MockMCP


def _client(client_mod, url, token=TEST_TOKEN):
    return client_mod.ClawbankClient(mcp_url=url, token=token, timeout=5)


class TestToolsList:
    def test_json_response(self, client_mod, mock_mcp):
        tools = _client(client_mod, mock_mcp.url).tools_list()
        assert [t["name"] for t in tools] == [t["name"] for t in SAMPLE_TOOLS]

    def test_sse_response(self, client_mod, mock_mcp):
        mock_mcp.state.response_mode = "sse"
        tools = _client(client_mod, mock_mcp.url).tools_list()
        assert [t["name"] for t in tools] == [t["name"] for t in SAMPLE_TOOLS]

    def test_bad_token_raises_auth_error(self, client_mod, mock_mcp):
        client = _client(client_mod, mock_mcp.url, token="wrong-token")
        with pytest.raises(client_mod.AuthError):
            client.tools_list()

    def test_missing_token_raises_auth_error(self, client_mod, mock_mcp):
        client = _client(client_mod, mock_mcp.url, token=None)
        with pytest.raises(client_mod.AuthError):
            client.tools_list()

    def test_unreachable_raises_clawbank_error(self, client_mod, unreachable_url):
        client = _client(client_mod, unreachable_url)
        with pytest.raises(client_mod.ClawbankError):
            client.tools_list()

    def test_pagination_cursor_cycle_raises(self, client_mod, mock_mcp):
        mock_mcp.state.pagination_mode = "cycle"
        with pytest.raises(client_mod.ClawbankError, match="cursor cycle"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_pagination_page_cap_raises(self, client_mod, mock_mcp):
        mock_mcp.state.pagination_mode = "endless"
        with pytest.raises(client_mod.ClawbankError, match="did not terminate"):
            _client(client_mod, mock_mcp.url).tools_list()


class TestCatalogValidation:
    """Malformed or oversized server responses become ClawbankError (which
    register() turns into the cache/setup fallback) — never a crash."""

    def test_non_dict_result_raises(self, client_mod, mock_mcp):
        mock_mcp.state.list_result_override = ["not", "an", "object"]
        with pytest.raises(client_mod.ClawbankError, match="non-object"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_tools_not_a_list_raises(self, client_mod, mock_mcp):
        mock_mcp.state.list_result_override = {"tools": "not-a-list"}
        with pytest.raises(client_mod.ClawbankError, match="not a list"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_non_string_cursor_raises(self, client_mod, mock_mcp):
        mock_mcp.state.list_result_override = {"tools": [], "nextCursor": {"evil": 1}}
        with pytest.raises(client_mod.ClawbankError, match="invalid pagination cursor"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_too_many_tools_raises(self, client_mod, mock_mcp):
        flood = [{"name": f"tool_{i}", "inputSchema": {}} for i in range(client_mod.MAX_CATALOG_TOOLS + 1)]
        mock_mcp.state.list_result_override = {"tools": flood}
        with pytest.raises(client_mod.ClawbankError, match="exceeded"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_oversized_response_body_raises(self, client_mod, mock_mcp, monkeypatch):
        monkeypatch.setattr(client_mod, "MAX_RESPONSE_BYTES", 1024)
        mock_mcp.state.list_result_override = {"tools": [{"name": "x" * 2048}]}
        with pytest.raises(client_mod.ClawbankError, match="exceeded"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_aggregate_catalog_metadata_is_bounded(
        self, client_mod, mock_mcp, monkeypatch
    ):
        monkeypatch.setattr(client_mod, "MAX_CATALOG_BYTES", 256)
        mock_mcp.state.list_result_override = {
            "tools": [
                {
                    "name": "large_schema",
                    "inputSchema": {
                        "type": "object",
                        "description": "x" * 512,
                    },
                }
            ]
        }
        with pytest.raises(client_mod.ClawbankError, match="catalog metadata exceeded"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_aggregate_bound_applies_across_pages(
        self, client_mod, mock_mcp, monkeypatch
    ):
        pages = [
            [{"name": "page_one", "description": "x" * 180}],
            [{"name": "page_two", "description": "y" * 180}],
        ]
        page_sizes = [
            len(json.dumps(page, separators=(",", ":")).encode("utf-8"))
            for page in pages
        ]
        limit = max(page_sizes) + 1
        assert sum(page_sizes) > limit
        monkeypatch.setattr(client_mod, "MAX_CATALOG_BYTES", limit)
        mock_mcp.state.list_pages = pages

        with pytest.raises(client_mod.ClawbankError, match="catalog metadata exceeded"):
            _client(client_mod, mock_mcp.url).tools_list()

    def test_malformed_descriptors_dropped_and_duplicates_deduped(self, client_mod):
        raw = [
            {"name": "good", "description": "ok", "inputSchema": {"type": "object"}},
            {"name": "good", "description": "duplicate must not shadow the first"},
            "not-a-dict",
            {"description": "no name"},
            {"name": ""},
            {"name": 42},
            {"name": "x" * (client_mod.MAX_TOOL_NAME_LENGTH + 1)},
            {"name": "odd_shapes", "description": 7, "inputSchema": "nope"},
        ]
        clean = client_mod.sanitize_tools(raw)
        assert [t["name"] for t in clean] == ["good", "odd_shapes"]
        assert clean[0]["description"] == "ok"
        assert clean[1]["description"] == ""
        assert clean[1]["inputSchema"] == {"type": "object", "properties": {}}

    def test_sanitize_preserves_annotations(self, client_mod):
        raw = [{"name": "t", "annotations": {"readOnlyHint": True}}]
        assert client_mod.sanitize_tools(raw)[0]["annotations"] == {"readOnlyHint": True}

    def test_sanitize_rejects_non_list(self, client_mod):
        with pytest.raises(client_mod.ClawbankError):
            client_mod.sanitize_tools({"tools": []})

    def test_sanitize_rejects_oversized_metadata(self, client_mod, monkeypatch):
        monkeypatch.setattr(client_mod, "MAX_CATALOG_BYTES", 128)
        raw = [{"name": "large", "description": "x" * 256}]
        with pytest.raises(client_mod.ClawbankError, match="catalog metadata exceeded"):
            client_mod.sanitize_tools(raw)


class TestUrlValidation:
    """CBH-001: the endpoint must never be able to receive the token over
    cleartext HTTP (loopback excepted for development and tests)."""

    def test_https_allowed(self, client_mod):
        client = client_mod.ClawbankClient(mcp_url="https://app.clawbank.co/mcp")
        assert client.mcp_url == "https://app.clawbank.co/mcp"

    def test_http_loopback_allowed(self, client_mod):
        for host in ("localhost", "127.0.0.1", "127.9.9.9", "[::1]"):
            client_mod.ClawbankClient(mcp_url=f"http://{host}:8080/mcp")

    def test_http_public_host_rejected(self, client_mod, monkeypatch):
        monkeypatch.delenv("CLAWBANK_ALLOW_INSECURE_URL", raising=False)
        with pytest.raises(client_mod.ClawbankError, match="insecure"):
            client_mod.ClawbankClient(mcp_url="http://evil.example.com/mcp")

    def test_dns_names_that_look_like_loopback_rejected(self, client_mod, monkeypatch):
        """A hostname is loopback only if it *parses as* a loopback IP — DNS
        names dressed up as one (127.evil.example) must be rejected."""
        monkeypatch.delenv("CLAWBANK_ALLOW_INSECURE_URL", raising=False)
        for host in (
            "127.evil.example",
            "127.attacker.com",
            "localhost.evil.example",
            "127.0.0.1.nip.io",
        ):
            with pytest.raises(client_mod.ClawbankError, match="insecure"):
                client_mod.ClawbankClient(mcp_url=f"http://{host}/mcp")

    def test_alternate_ipv4_representations_rejected(self, client_mod, monkeypatch):
        """Decimal/hex/zero-padded IPv4 forms don't parse as literal IPs —
        they must fail closed, not be resolved leniently."""
        monkeypatch.delenv("CLAWBANK_ALLOW_INSECURE_URL", raising=False)
        for host in ("2130706433", "0x7f000001", "017700000001", "127.1"):
            with pytest.raises(client_mod.ClawbankError):
                client_mod.ClawbankClient(mcp_url=f"http://{host}/mcp")

    def test_non_loopback_ipv6_rejected(self, client_mod, monkeypatch):
        monkeypatch.delenv("CLAWBANK_ALLOW_INSECURE_URL", raising=False)
        with pytest.raises(client_mod.ClawbankError, match="insecure"):
            client_mod.ClawbankClient(mcp_url="http://[2001:db8::1]/mcp")

    def test_https_requires_host(self, client_mod):
        for url in ("https:///mcp", "https://"):
            with pytest.raises(client_mod.ClawbankError, match="missing host"):
                client_mod.ClawbankClient(mcp_url=url)

    def test_non_http_scheme_rejected(self, client_mod):
        for url in ("ftp://app.clawbank.co/mcp", "file:///etc/passwd", "not a url"):
            with pytest.raises(client_mod.ClawbankError):
                client_mod.ClawbankClient(mcp_url=url)

    def test_insecure_flag_allows_http_for_development(self, client_mod, monkeypatch):
        monkeypatch.setenv("CLAWBANK_ALLOW_INSECURE_URL", "1")
        client_mod.ClawbankClient(mcp_url="http://dev-box.internal/mcp")

    def test_default_url_used_when_env_unset(self, client_mod, monkeypatch):
        monkeypatch.delenv("CLAWBANK_MCP_URL", raising=False)
        client = client_mod.ClawbankClient()
        assert client.mcp_url == client_mod.DEFAULT_MCP_URL


class TestRedirectRefusal:
    """CBH-001: redirects are refused outright — the Authorization header must
    never reach a redirect target (covers cross-origin 302 and, because *all*
    redirects are refused, HTTPS→HTTP downgrade as well)."""

    def test_redirect_raises_and_token_never_reaches_destination(self, client_mod, mock_mcp):
        destination = MockMCP()
        try:
            mock_mcp.state.redirect_to = destination.url
            client = _client(client_mod, mock_mcp.url)
            with pytest.raises(client_mod.ClawbankError, match="redirect"):
                client.tools_list()
            assert destination.state.requests == []
        finally:
            destination.shutdown()

    def test_redirect_error_names_the_destination(self, client_mod, mock_mcp):
        mock_mcp.state.redirect_to = "http://attacker.example.com/steal"
        client = _client(client_mod, mock_mcp.url)
        with pytest.raises(client_mod.ClawbankError, match="attacker.example.com"):
            client.tools_list()


class TestToolsCall:
    def test_forwards_name_and_arguments(self, client_mod, mock_mcp):
        client = _client(client_mod, mock_mcp.url)
        client.tools_call("send_usdc_on_base", {"to_address": "0xabc", "amount": "5"})
        assert mock_mcp.state.calls == [
            {"name": "send_usdc_on_base", "arguments": {"to_address": "0xabc", "amount": "5"}}
        ]

    def test_returns_result_dict(self, client_mod, mock_mcp):
        client = _client(client_mod, mock_mcp.url)
        result = client.tools_call("get_balance", {})
        text = result["content"][0]["text"]
        assert json.loads(text) == {"balance_usdc": "12.34"}

    def test_sse_call(self, client_mod, mock_mcp):
        mock_mcp.state.response_mode = "sse"
        client = _client(client_mod, mock_mcp.url)
        result = client.tools_call("get_balance", {})
        assert result["content"][0]["type"] == "text"


class TestSseParser:
    def test_picks_message_matching_id(self, client_mod):
        body = (
            'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n'
            "\n"
            'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n'
            "\n"
        )
        message = client_mod._parse_sse(body, want_id=7)
        assert message["result"] == {"ok": True}

    def test_multiline_data_joined(self, client_mod):
        body = 'data: {"id": 1,\ndata:  "result": {"a": 2}}\n\n'
        message = client_mod._parse_sse(body, want_id=1)
        assert message["result"] == {"a": 2}

    def test_empty_stream_raises(self, client_mod):
        with pytest.raises(client_mod.ClawbankError):
            client_mod._parse_sse(": keep-alive\n\n", want_id=1)


class TestResultToText:
    def test_prefers_structured_content(self, client_mod):
        result = {
            "content": [{"type": "text", "text": "human text"}],
            "structuredContent": {"balance": "1.00"},
        }
        assert json.loads(client_mod.result_to_text(result)) == {"balance": "1.00"}

    def test_joins_text_blocks(self, client_mod):
        result = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        assert client_mod.result_to_text(result) == "a\nb"

    def test_wraps_is_error(self, client_mod):
        result = {"isError": True, "content": [{"type": "text", "text": "insufficient funds"}]}
        assert json.loads(client_mod.result_to_text(result)) == {"error": "insufficient funds"}

    def test_empty_result_dumps_raw(self, client_mod):
        assert client_mod.result_to_text({}) == "{}"

    def test_non_dict_dumps_json(self, client_mod):
        assert client_mod.result_to_text([1, 2]) == "[1, 2]"


class TestCatalogCache:
    IDENTITY = "https://app.clawbank.co/mcp#0123456789ab"

    def _write(self, path, **overrides):
        from datetime import datetime, timezone

        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "identity": self.IDENTITY,
            "tools": SAMPLE_TOOLS,
        }
        payload.update(overrides)
        path.write_text(json.dumps(payload))

    def test_roundtrip(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        client_mod.save_catalog(path, SAMPLE_TOOLS, self.IDENTITY)
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == SAMPLE_TOOLS
        assert '": "' not in path.read_text()

    def test_wrong_identity_returns_empty(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        client_mod.save_catalog(path, SAMPLE_TOOLS, self.IDENTITY)
        assert client_mod.load_cached_catalog(path, "https://other/mcp#fingerprint") == []

    def test_expired_cache_returns_empty(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        self._write(path, saved_at="2000-01-01T00:00:00+00:00")
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == []

    def test_missing_or_naive_timestamp_returns_empty(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        self._write(path, saved_at=None)
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == []
        self._write(path, saved_at="2026-07-27T00:00:00")  # no timezone
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == []

    def test_malformed_tools_returns_empty(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        self._write(path, tools="not-a-list")
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == []

    def test_missing_file_returns_empty(self, client_mod, tmp_path):
        assert client_mod.load_cached_catalog(tmp_path / "nope.json", self.IDENTITY) == []

    def test_corrupt_file_returns_empty(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        path.write_text("{not json")
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == []

    def test_oversized_cache_is_rejected_before_read(
        self, client_mod, tmp_path, monkeypatch
    ):
        path = tmp_path / "catalog.json"
        path.write_text("x" * 129)
        monkeypatch.setattr(client_mod, "MAX_CACHE_FILE_BYTES", 128)

        def unexpected_read(*args, **kwargs):
            pytest.fail("oversized cache should not be read into memory")

        monkeypatch.setattr(client_mod.Path, "read_text", unexpected_read)
        assert client_mod.load_cached_catalog(path, self.IDENTITY) == []

    def test_identity_binds_endpoint_and_token_without_leaking_it(self, client_mod):
        url = "https://app.clawbank.co/mcp"
        a = client_mod.catalog_cache_identity(url, "token-one")
        b = client_mod.catalog_cache_identity(url, "token-two")
        c = client_mod.catalog_cache_identity("https://staging.example/mcp", "token-one")
        assert len({a, b, c}) == 3
        assert "token-one" not in a and "token-one" not in c

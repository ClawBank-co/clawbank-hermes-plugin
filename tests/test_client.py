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


class TestUrlValidation:
    """CBH-001: the endpoint must never be able to receive the token over
    cleartext HTTP (loopback excepted for development and tests)."""

    def test_https_allowed(self, client_mod):
        client = client_mod.ClawbankClient(mcp_url="https://app.clawbank.co/mcp")
        assert client.mcp_url == "https://app.clawbank.co/mcp"

    def test_http_loopback_allowed(self, client_mod):
        for host in ("localhost", "127.0.0.1", "127.9.9.9"):
            client_mod.ClawbankClient(mcp_url=f"http://{host}:8080/mcp")

    def test_http_public_host_rejected(self, client_mod, monkeypatch):
        monkeypatch.delenv("CLAWBANK_ALLOW_INSECURE_URL", raising=False)
        with pytest.raises(client_mod.ClawbankError, match="insecure"):
            client_mod.ClawbankClient(mcp_url="http://evil.example.com/mcp")

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
    def test_roundtrip(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        client_mod.save_catalog(path, SAMPLE_TOOLS)
        assert client_mod.load_cached_catalog(path) == SAMPLE_TOOLS

    def test_missing_file_returns_empty(self, client_mod, tmp_path):
        assert client_mod.load_cached_catalog(tmp_path / "nope.json") == []

    def test_corrupt_file_returns_empty(self, client_mod, tmp_path):
        path = tmp_path / "catalog.json"
        path.write_text("{not json")
        assert client_mod.load_cached_catalog(path) == []

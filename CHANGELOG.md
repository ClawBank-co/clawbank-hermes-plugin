# Changelog

All notable changes to this plugin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

Note that the *tool catalog* is not versioned here — tools are discovered
live from the ClawBank API and change server-side without plugin releases.

## [0.1.0] — 2026-07-26

### Security

- `CLAWBANK_MCP_URL` must be HTTPS (with a host). Plain HTTP is allowed only
  for `localhost` or *literal* loopback IP addresses — parsed with
  `ipaddress`, so DNS names dressed up as loopback (`127.evil.example`) are
  rejected — or behind the explicit `CLAWBANK_ALLOW_INSECURE_URL=1`
  development flag. A rejected URL degrades to the `clawbank_setup` tool
  (reason `insecure_url`) — the token is never sent to an unvalidated
  endpoint, and the Hermes launch never fails. A warning is logged whenever
  a non-default endpoint is in use, since any override receives the token.
- Catalogs are validated and bounded before registration: response bodies
  are capped at 8 MiB, catalogs at 512 tools, cursors must be bounded
  strings, malformed result shapes raise a clean startup error instead of
  crashing, and individual descriptors are sanitized (name/description/
  schema type-checked, duplicates dropped, server `annotations` preserved).
- The catalog cache now expires after 7 days and is bound to the endpoint
  plus a non-secret token fingerprint — a cache from another account,
  endpoint, or rotated token is never served; cached tools are re-validated
  on load.
- HTTP redirects (3xx) are refused outright: the `Authorization` bearer
  header is never forwarded to a redirect target, closing a cross-origin
  token-leak path in Python's default `urllib` redirect handling.
- Catalog pagination is bounded: a repeated `nextCursor` or more than 50
  pages aborts the fetch (falling back to the cached catalog) instead of
  looping forever against a broken or hostile server.
- The bundled skill is now self-contained: the confirmation contract, the
  per-area confirmation matrix, failure patterns, and known limits are
  inlined in `SKILL.md` so they load through `skill_view` even where Hermes
  does not serve a skill's supporting files.
- Deterministic destructive-tool gate: every tool's standard MCP annotations
  (served from the same server-side scope table that gates `tools/call`) are
  checked at dispatch. The plugin fails closed — a tool must be explicitly
  annotated read-only to execute; destructive or unclassified tools return a
  clear `destructive_tool_blocked` error unless
  `CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS=1` was deliberately set before launch.
  Annotations from a *cached* catalog never authorize execution while the
  live catalog is unavailable.
- Scoped, spend-capped tokens are the recommended default (`read` + `send`,
  $10/day). The setup tool, README, and setup reference document the scoped
  mint body; unscoped full-access keys remain available but are never the
  silent default.
- Aggregate catalog metadata is byte-capped across pagination (8 MiB) in
  both live fetches and `sanitize_tools`, and oversized cache files are
  rejected by size before being read into memory.
- GitHub Actions are pinned to commit SHAs in CI.

### Added

- Dynamic catalog proxy: fetches `tools/list` from the ClawBank MCP endpoint
  at Hermes startup and registers every tool with a single generic
  `tools/call` passthrough handler.
- On-disk catalog cache so offline or flaky starts still register the
  last-known toolset.
- `clawbank_setup` fallback tool when no token is configured, the token is
  rejected, or the API is unreachable with a cold cache.
- Bundled `clawbank` skill (confirmation contract, capability map, setup,
  safety matrix, worked examples), registered as `clawbank:clawbank`.
- JSON and SSE (`text/event-stream`) response handling, MCP session-id echo,
  and catalog pagination support — standard library only, zero dependencies.
- Mock-endpoint test suite and CI (Python 3.10–3.13 on Linux, macOS, and
  Windows), plus a scheduled live-surface drift check.
- Real-Hermes integration tests: a dedicated CI job installs the actual
  `hermes-agent` release (declared minimum: 0.19.0), installs the plugin
  into an isolated `HERMES_HOME`, and drives Hermes's own plugin discovery,
  loading, tool registry, and dispatch in a fresh process — covering the
  no-token fallback, the authenticated catalog, schema fidelity, the
  rejected-token degradation, and in-session availability of the
  safety-critical skill content.
- Headless bootstrap payloads (`request_code`, `verify_code`,
  `bootstrap/api_tokens`) documented with exact request bodies in the
  `clawbank_setup` tool output and the setup reference.

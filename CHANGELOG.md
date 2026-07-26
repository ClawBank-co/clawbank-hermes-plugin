# Changelog

All notable changes to this plugin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

Note that the *tool catalog* is not versioned here — tools are discovered
live from the ClawBank API and change server-side without plugin releases.

## [0.1.0] — 2026-07-26

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
- Mock-endpoint test suite and CI (Python 3.10–3.13), plus a scheduled
  live-surface drift check.

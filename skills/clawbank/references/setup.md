# Connecting a ClawBank account

Everything runs against one base URL: `https://app.clawbank.co`.

## 1. Create an account (free)

- Web: <https://app.clawbank.co/users/register>
- Signup is free; no funding is required to mint a token and browse the
  read-only tools.

## 2. Mint an API token

**In the app (humans):** Settings → API tokens at
<https://app.clawbank.co/users/settings> (requires login).

**Headless (agents), no browser needed** — three REST calls. Exact payloads
matter; codes expire quickly, so don't burn one guessing shapes:

1. `POST /api/v1/auth/request_code`
   Body: `{"email": "<email>"}`
   → 200 with `status: code_sent`; a login code is emailed.
2. `POST /api/v1/auth/verify_code`
   Body: `{"email": "<email>", "code": "<code>"}` — **both fields are
   required** (code alone returns 400 `invalid_request`)
   → returns a short-lived bootstrap token.
3. `POST /api/v1/auth/bootstrap/api_tokens`
   Headers: `Authorization: Bearer <bootstrap token>`
   Recommended body for a normal Hermes install:
   `{"name": "hermes-default", "scopes": ["read", "send"], "daily_cap_usd": "10"}`
   → returns a long-lived API token limited to read/send and $10 per day.

Available scopes are `read`, `trade`, `send`, `admin`, and `raw_sign`.
`per_tx_cap_usd` and `daily_cap_usd` are optional decimal-string spending
caps enforced server-side. Use the narrowest scopes and lowest caps needed.
Omitting `scopes` intentionally creates a full-access token that includes raw
transaction signing; the plugin does not use that as its silent default.
Unscoped keys remain appropriate for deliberate fresh-account exploration.
Tokens are long-lived, independently revocable, and additional
purpose-specific keys can be minted by repeating the email bootstrap flow.
Never display, echo, or log a token; store it only in the environment or a
secrets manager.

The complete request/response contract and curl examples are maintained at
<https://app.clawbank.co/docs>.

## 3. Configure the plugin

```bash
export CLAWBANK_API_TOKEN="<token>"
```

`hermes plugins install` prompts for this automatically and saves it to the
active Hermes profile's `$HERMES_HOME/.env` (default `~/.hermes/.env`).
Optional overrides (matching the ClawBank CLI's conventions):

| Variable | Purpose | Default |
| --- | --- | --- |
| `CLAWBANK_API_TOKEN` | API token (primary) | — |
| `CLAWBANK_TOKEN` | API token (fallback, CLI compatibility) | — |
| `CLAWBANK_MCP_URL` | MCP endpoint override | `https://app.clawbank.co/mcp` |
| `CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS` | Set to `1` to arm handlers not explicitly classified read-only by a fresh catalog, including destructive, unclassified, and cached tools | unset |

## 4. Verify

Restart Hermes. The banner's tool list should show the `clawbank` toolset
with the scope-aware catalog for that token. A quick smoke test: *"Show me my
ClawBank balances."* A read-only token registers no fund-moving tools.

If only `clawbank_setup` appears, the token is missing or was rejected —
re-mint at Settings → API tokens and restart.

## Rotating or revoking

Revoke tokens any time at Settings → API tokens. After rotating, update
`CLAWBANK_API_TOKEN` and restart Hermes; the catalog reloads on launch.

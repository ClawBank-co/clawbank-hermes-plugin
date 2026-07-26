# Connecting a ClawBank account

Everything runs against one base URL: `https://app.clawbank.co`.

## 1. Create an account (free)

- Web: <https://app.clawbank.co/users/register>
- Signup is free; no funding is required to mint a token and browse the
  read-only tools.

## 2. Mint an API token

**In the app (humans):** Settings → API tokens at
<https://app.clawbank.co/users/settings> (requires login).

**Headless (agents), no browser needed** — three REST calls:

1. `POST /api/v1/auth/request_code` — sends a login code to an email address
2. `POST /api/v1/auth/verify_code` — exchanges the code for a short-lived
   bootstrap token
3. `POST /api/v1/auth/bootstrap/api_tokens` — with the bootstrap token as
   Bearer, returns the long-lived API token

Tokens are long-lived and revocable. There are **no scopes and no spending
limits** — a token is full account access. Treat it like a bank password:
never display, echo, or log it, and store it only in the environment or a
secrets manager.

## 3. Configure the plugin

```bash
export CLAWBANK_API_TOKEN="<token>"
```

`hermes plugins install` prompts for this automatically and saves it to
`.env`. Optional overrides (matching the ClawBank CLI's conventions):

| Variable | Purpose | Default |
| --- | --- | --- |
| `CLAWBANK_API_TOKEN` | API token (primary) | — |
| `CLAWBANK_TOKEN` | API token (fallback, CLI compatibility) | — |
| `CLAWBANK_MCP_URL` | MCP endpoint override | `https://app.clawbank.co/mcp` |

## 4. Verify

Restart Hermes. The banner's tool list should show the `clawbank` toolset
with the full catalog. A quick smoke test: *"Show me my ClawBank balances."*

If only `clawbank_setup` appears, the token is missing or was rejected —
re-mint at Settings → API tokens and restart.

## Rotating or revoking

Revoke tokens any time at Settings → API tokens. After rotating, update
`CLAWBANK_API_TOKEN` and restart Hermes; the catalog reloads on launch.

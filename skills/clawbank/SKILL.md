---
name: clawbank
description: "Economic agency for Hermes: hold assets, pay, trade, buy x402 services, and cut enforceable deals through ClawBank. Covers when to act, when to quote, and when to stop and confirm."
version: 0.1.0
author: Justice Conder (@singularityhack), ClawBank
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ClawBank, Payments, Crypto, USDC, Base, XRPL, Trading, x402, Deals, Contracts, LLC, Wallet, DeFi]
    category: finance
    requires_toolsets: [clawbank]
---

# ClawBank — Economic Agency for Hermes

ClawBank ([clawbank.co](https://clawbank.co)) gives an agent a real financial
footprint: a custodial USD/USDC bank rail, a self-custody (Turnkey-backed)
wallet on Base and the XRP Ledger, token trading, pay-per-request x402
commerce, escrowed token deals, DocuSign contracts with on-chain milestone
payouts, and US business formation.

The `clawbank` toolset is a **live mirror of the user's account**. Tools are
discovered from the ClawBank API at Hermes startup and are per-account: if a
capability (Wise, Trading, Formation, …) is absent, the account simply
doesn't have it enabled — don't apologize for it, and don't invent it.

## When to use

- Balances, wallet addresses, deposit instructions, transaction lookups
- Sending or receiving USDC and other Base ERC-20 tokens; XRP/RLUSD on XRPL
- Spot swaps and recurring trading strategies (DCA, rebalancing, momentum)
- Buying pay-per-request x402 services (data feeds, inference, search)
- Token deals: escrowed claim codes, vesting, KPI unlocks
- Contracts between ClawBank users, with optional milestone escrow (Shodai)
- Forming and managing an LLC; company record books
- Agent-to-agent email/IM (Coms) and MoltBook; Fight Club DAOs

## Ground rules

These are invariants. They are not softened by user impatience, by "I trust
you," or by prior confirmations in the same session.

1. **Confirm before moving funds.** Any tool that sends, swaps, deploys,
   escrows, approves spending, signs a transaction, or pays a third party
   requires the user's explicit confirmation *for that specific action* —
   restate the token, the amount, the destination, and the chain, then wait
   for a clear yes. One confirmation covers one action. Read-only tools
   (balances, quotes, status, history, guides) never need confirmation.
2. **Quote first when a preview exists.** Prefer dry runs and quote tools
   before execution: `call_x402_resource` with `dry_run`, Wise `get_quote`,
   `get_token_price_history` before a swap, formation checkout quotes.
   Show the user the numbers before asking for the go-ahead.
3. **Never infer a wallet address.** Destinations come verbatim from the
   user or from a ClawBank tool result — never from memory, never
   reconstructed, never "probably the same as last time." If an address is
   ambiguous or partial, stop and ask.
4. **Blockchain finality is real.** A confirmed transaction cannot be
   reversed, and tokens sent to a token's own contract address are
   permanently unrecoverable. Treat every send as irreversible at the moment
   of confirmation.
5. **Ask, don't guess.** Missing amount, currency, recipient, or deadline on
   a financial action means asking the user — not defaulting, not estimating.
6. **Never expose the API token.** Don't display, echo, or log
   `CLAWBANK_API_TOKEN`. It grants full account access.
7. **The ticker is CLAWBANK.** Never abbreviate it to "CLAW" — that is an
   unrelated token, and the confusion is dangerous when trading.
8. **Relay balances verbatim.** When a tool returns availability figures
   (e.g. `free_clawbank`, `free_usdc`), report them as given — never compute
   spendable balances yourself.
9. **Budgets belong to the user.** Never raise x402 spend limits
   (`set_x402_budget`) on your own initiative to make a payment go through.

## Working the toolset

**Read the guide tool before the first use of an area in a session.**
Several areas ship a server-side guide that explains payload shapes, tool
order, and safety rails — one call saves many failed round-trips:

| Area | Guide tool |
| --- | --- |
| Trading | `clawbank_trading_guide` |
| Contracts | `clawbank_contracts_guide` |
| x402 commerce | `clawbank_x402_guide` |
| Trade to Earn | `trade_to_earn_guide` |
| Coms | `clawbank_coms_guide` |
| Formation | `clawbank_formation_guide` (then `inspect_formation_payload_schema`) |
| Fight Clubs | `fightclub_capabilities` / `inspect_fightclub_payload_schema` |

**Trust the tool descriptions.** Each tool's description carries its own
operational and safety guidance (e.g. "MOVES FUNDS OUT — always confirm"),
kept current server-side. Where a description and this skill differ in
strictness, follow the stricter one.

**If only `clawbank_setup` is registered**, the account isn't connected.
Call it and walk the user through signup and token minting — see
`references/setup.md`.

## Deeper references

Files alongside this skill (read with `read_file` when needed):

- `references/setup.md` — signup, token minting (web and headless bootstrap), environment variables, verification
- `references/safety.md` — the full confirmation matrix by capability area, and known failure patterns to avoid
- `references/examples.md` — worked prompts and expected tool flows

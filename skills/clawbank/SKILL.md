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
   see [The confirmation contract](#the-confirmation-contract) below.
   Read-only tools (balances, quotes, status, history, guides) never need
   confirmation.
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

## The confirmation contract

For any action that moves value, the confirmation must:

1. Restate the **asset** (full ticker — CLAWBANK, never "CLAW"), the
   **amount** (with a USD estimate when a quote is available), the
   **destination** (full address or recipient), and the **network**.
2. Be answered with a clear, affirmative reply *to that restatement*. A
   prior "yes" in the session, a general "go ahead with the plan," or
   silence does not carry over.
3. Cover exactly one action. Batching ("okay to do all three transfers?")
   is not a substitute when amounts or destinations differ.

## Confirmation matrix by area

| Area | Free to call (read-only) | Requires explicit confirmation |
| --- | --- | --- |
| Accounts & money | `get_me`, `get_balance`, `list_wallets`, `get_deposit_instructions`, `get_offramp_status` | `create_usdc_transfer`, `link_offramp_bank_account`, `unlink_offramp_bank_account`, `create_offramp_address` |
| Self-custody wallet | address/balance reads, `trace_transaction`, `check_address_balance`, bridge status | `send_usdc_on_base`, `send_token`, `send_xrp`, `send_rlusd`, `bridge_to_xrpl`, `bridge_to_base`, `swap_xrp_rlusd`, `sign_transaction`, `sign_raw_payload`, `setup_rlusd_trustline` (locks reserve) |
| Wise | rates, quotes, balances, history, recipients list | `send_money`, `convert_balance`, `save_recipient`, `delete_recipient` |
| Trading | guides, token lists, price history, positions, PnL, logs | `execute_spot_swap`, `create_strategy`, `approve_token_for_trading` (unlimited allowance), strategy start/stop/destroy per user intent |
| Trade to Earn | status, leaderboard | `deploy_trade_to_earn`, `top_up_trade_to_earn` (both execute real swaps), `claim_trade_to_earn_rewards` is safe but tell the user |
| Deals | `deal_status`, `my_deals` | `create_deal` (escrows funds), `claim_deal`, `reclaim_deal`, `cancel_stream` (irreversible for the stream) |
| Contracts | guide, inbox/sent/read/status | `clawbank_contracts_create`, `clawbank_contracts_sign`, milestone approve/reject (approval pays USDC on-chain), terminate |
| x402 | guide, `discover_x402_resources`, budget/history reads, `call_x402_resource` with `dry_run` | `call_x402_resource` (paid), `set_x402_budget` (only on explicit user request) |
| Formation | guides, schema inspection, jurisdiction/package lists, order reads | `start_formation_checkout` + the payment send, `upload_signed_formation_filing`, `cancel_formation_order` |
| Coms | status, threads, message reads | outbound email/IM the user hasn't reviewed; MoltBook registration |
| Fight Clubs | every `[READ]` tool | every `[SIGNS TX]` tool — each signs and broadcasts with the user's wallet keys |

When in doubt: if the tool's description says it moves funds, signs, or is
irreversible, it's in the right column.

## Failure patterns to avoid

**Sizing a send from a swap quote.** After a swap, the settled amount is
smaller than the quote once fees are skimmed — a send sized from the quote
reverts. Use `amount: "all"` or re-read the live balance first.

**Sending tokens to a token's contract address.** Users paste the CLAWBANK
contract address from a listing page as a "deposit address." Tokens sent to
a token's own contract are burned forever. When sharing a deposit address,
relay the tool's `deposit_warnings` verbatim.

**Answering "what's in address X?" with the user's own balance.** Use
`check_address_balance` for third-party addresses, never the user's wallet
reads.

**Guessing formation payloads.** Call `inspect_formation_payload_schema`
before `start_formation_checkout`; if a submission returns `missing`, fix
those exact fields — don't loop and guess.

**Treating deal codes casually.** A deal code is a bearer instrument shown
exactly once at creation. Deliver it to the sender immediately, never log it,
and never ask for recipient identity — the code *is* the claim.

**Computing availability.** Trade to Earn returns `free_clawbank` /
`free_usdc` precisely because wallet balance ≠ spendable balance. Relay
those figures verbatim.

**Missing deposits.** When a user reports a missing deposit with a tx hash,
run `trace_transaction` first — it usually explains the mystery (wrong
token, wrong destination, wrong network, still pending) without escalation.

## Known limits (be candid about them)

- API tokens are scope-aware (`read`, `send`, `trade`, `admin`, `raw_sign`) and
  can carry server-enforced per-transaction and daily USD caps. Use separate,
  least-privilege keys for monitoring, normal operation, and raw signing.
- The plugin blocks every MCP tool marked `destructiveHint: true` unless
  `CLAWBANK_ALLOW_DESTRUCTIVE_TOOLS=1` was set before Hermes started. Enabling
  that flag does not replace the per-action confirmation contract above; it
  only arms the registered destructive handlers.
- There is no sandbox mode. Suggest small test amounts for first-time flows.
- New server-side tools appear at the next Hermes restart, not mid-session.

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
Call it — its output contains the full signup, token-minting (web and
headless bootstrap), and configuration instructions. Walk the user through
them.

---

*This skill is self-contained: every safety-critical rule lives in this
file. The `references/` directory alongside it (setup, worked examples)
exists for human readers browsing the repository and is not required
in-session.*

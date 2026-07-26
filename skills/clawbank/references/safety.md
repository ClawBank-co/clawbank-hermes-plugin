# Safety reference — confirmation matrix and failure patterns

The plugin inherits every tool's safety language from the server, so the
descriptions themselves say when a tool moves funds. This reference is the
judgment layer on top: what to confirm, how to confirm it, and the mistakes
that actually burn users.

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

- Tokens have no scopes or spending caps; the account-level x402 budget is
  the only server-side spend limit. The confirmation contract above is the
  compensating control.
- There is no sandbox mode. Suggest small test amounts for first-time flows.
- New server-side tools appear at the next Hermes restart, not mid-session.

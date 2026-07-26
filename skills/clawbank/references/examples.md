# Worked examples

Prompts a user might give, and the tool flow a well-behaved agent follows.
These assume a connected account; if only `clawbank_setup` is registered,
run setup first (`references/setup.md`).

## Balances and addresses

> "Show me my ClawBank balances."

`get_balance` (custodial USDC) and/or `get_self_custody_tracked_balances`
(self-custody portfolio). Report both if the user didn't specify which
wallet — they are different wallets with different purposes.

> "What's my ClawBank wallet address on Base?"

`get_self_custody_wallet_address`, then relay the returned
`deposit_warnings` verbatim — especially "never send to a token's contract
address" and "start with a small test amount."

## A quote without execution

> "Get me a quote to swap $50 of USDC for CLAWBANK — don't execute."

`clawbank_trading_guide` (first trading use of the session) →
`list_tradeable_tokens` (get the CLAWBANK contract address — always pass
addresses, not symbols) → `get_token_price_history` for the current price.
Present the numbers. **No swap tool is called.**

## A confirmed payment

> "Send 25 USDC to 0xAB…CD on Base."

1. `get_self_custody_token_balance` — verify the balance covers it.
2. Restate: *"Sending **25 USDC** on **Base** from your self-custody wallet
   to **0xAB…CD** (full address shown). Confirm?"*
3. On a clear yes: `send_usdc_on_base`.
4. Report the transaction hash from the result.

If the user pasted only part of an address, stop and ask for the full one —
never complete it from history.

## An x402 purchase

> "Find me a crypto market data feed I can pay per request, and try it."

`clawbank_x402_guide` → `discover_x402_resources` (respect the user's
favorites via `list_x402_service_marks`) → `call_x402_resource` with
`dry_run: true` to preview price and budget verdict → present the price →
on a clear yes, the real call → tell the user what was paid (the `payment`
field).

If the call exceeds the budget caps, report the limits and stop. Only the
user can decide to change them via `set_x402_budget`.

## An escrowed token deal

> "Set up 1,000 CLAWBANK for my designer, vesting over 90 days."

`create_deal` with `vest_days: 90` — after confirming amount, token, and
vesting terms. The response includes a claim **link** (safe to share) and a
one-time **code** (the bearer claim). Deliver the code to the user
immediately and remind them to guard it like cash and send it via a second
channel for high-value deals. Never ask who the recipient is.

## Checking on things (no confirmation needed)

- "List my open ClawBank deals." → `my_deals`
- "Did my deposit arrive? Here's the hash: 0x…" → `trace_transaction`
- "How are my strategies doing?" → `list_strategies` → `get_pnl` /
  `get_trading_report`
- "Where's my LLC filing?" → `list_formation_orders` /
  `get_formation_order`

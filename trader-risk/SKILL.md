---
name: trader-risk
description: >-
  Risk Manager. Checks every proposed trade against your own limits and flags
  anything over them, in rupees and IST, for NSE cash equities. For a day
  trade: a stop is required, the loss at the stop must fit your max loss per
  trade (measured from the higher of the plan's entry and a fresh price),
  reward:risk must meet your minimum, the day's total risk must fit your daily
  loss limit, no more than your trades per day, and no new entries outside
  your entry window or when the NSE is closed. It also keeps the basics:
  blocked symbols, order size, position cap, cash buffer, margin, short sales,
  F&O and crypto, malformed orders (qty, MIS or CNC), duplicates and a daily
  order limit. Getting out is never blocked by a day limit. Prints PASS or
  BLOCK with the reason and records the verdicts for trader-ticket. A PASS is
  a rules check, not a recommendation. Use when the user says "check my
  order", "is this inside my limits", "risk check", "does this break my
  rules", "how much have I risked today", or before /trader-ticket, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager compares a proposal with the limits the user set. Every order
written by trader-budget or trader-ticket's flatten passes through it. Most
people who day trade lose money. These limits keep each loss and each day's
losses small. They do not make a trade win, and a PASS does not mean the trade
is a good one. The user makes every decision.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-19 09:50"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-ticket's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/ticket.json` | what the user recorded placing today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, qty not a positive whole number, exchange not NSE, product not MIS or CNC, type not market or limit, a limit order with no price in `entry` |
| asset | an F&O contract (`RELIANCE26NOVFUT`, `RELIANCE26NOV1300CE`) while `allow_options` is false; crypto or anything that is not an NSE symbol |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin |
| stop | (entries) no stop while `require_stop` is true; a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the market is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already recorded as placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the order's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). A plan written at 9:50 cannot
  understate the risk at 10:10 after the price ran up. An older price is not
  used, and it says so.
- **Daily loss** counts the full max loss of every entry the user recorded
  today, win or lose. Three full-size trades use up a ₹1,500 day. It only knows
  what trader-ticket recorded: a trade placed in Groww and not recorded is not
  counted.
- **The window** is IST, worked out in the tool (fixed +5:30), never from the
  computer's clock zone. The session is 9:15 to 15:30. Weekends and NSE holidays
  are closed. The holiday list covers 2026 (from NSE's holiday API); outside it
  there is a warning to check nseindia.com. The Muhurat session is not modelled
  and counts as closed. Set both `no_entries_*` to null to switch the window off.

Warnings do not block, but say them to the user: a price too old to use, a date
outside the holiday list, a rule an exit broke.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, and how much of today's limits it uses. For a
   BLOCK, give the reason and the options (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-ticket matches
   each order to its verdict by fingerprint, so an edited order shows as not
   checked until it runs again, and a verdict from yesterday does not count today.

## Example: the reel

The RELIANCE order from trader-budget at 9:50 AM on Monday 2026-10-19, on a
fresh ₹1,00,000 account with the example config:

```
PASS  #1 buy RELIANCE 40 shares, entry
          entry 1227.80 (the plan's entry), stop 1215.35, target 1240.25: risk 12.45 a share
          max loss ₹498 (limit ₹500), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss

1 passed, 0 blocked at 2026-10-19 09:50 IST. Cash after the passed orders: ₹50,888.
```

One more share and it is blocked:

```
BLOCK #1 buy RELIANCE 41 shares, entry
          entry 1227.80 (the plan's entry), stop 1215.35, target 1240.25: risk 12.45 a share
          max loss ₹510.45 (limit ₹500), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss
        - loss: 41 shares x 12.45 risk a share = ₹510.45 at the stop, over your ₹500 max loss per trade
```

The same 40 shares checked with a fresh quote of 1235.48 in `trader/prices.json`
are blocked twice: 40 x 20.13 = ₹805.20 at the stop, and reward:risk 0.24. At
9:25 they are blocked by the window (no new entries before 9:30). The 15:10 sell
that closes the position passes, even though it is after the 15:00 entry
cutoff: it is an exit.

## Rules

- A PASS only means the order is inside the user's limits. It is not advice to place it.
- Every number comes from the config, the order, the portfolio, the prices or the ticket log.
- Free Yahoo prices are unofficial and can lag; the broker's price is the one that fills, and a
  stop can fill worse than its price in a fast market.

## What it never does

It never places an order, never edits an order to make it pass, never loosens a
limit, never blocks a sell that only gets the user out, and never tells the user
to take a trade.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

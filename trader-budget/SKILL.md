---
name: trader-budget
description: >-
  Budget Manager. Sizes every position to your budget and your max loss per
  trade, in rupees. Reads the trade plan from trader-exit (entry, stop,
  target), finds the whole shares that fit inside four limits (max loss per
  trade, position cap, max order, cash after your buffer), says which limit
  decided it, and writes ONE proposed order (symbol, NSE, side, qty, MIS or
  CNC, entry, stop, target) to trader/orders.json. A stop too far away for
  your max loss is a plain refusal, not a smaller order. Nothing is placed,
  and it never says whether to take the trade. Use when the user says "how
  many shares", "size this trade", "how much can I buy", "I have 1 lakh",
  "I only want to risk 500 a trade", "set my rules", "keep 10% in cash", or
  after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("RELIANCE at 1227.80, stop 1215.35") into an
exact size ("40 shares"), inside rules the user sets once. The stop decides
the size: the further away the stop, the fewer shares. It writes a proposal
only. trader-risk checks it, trader-ticket prints it, and the user decides
whether to place it in Groww. Day trading loses most people money. Sizing to a
max loss keeps each loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                         # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                 # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write         # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1225.00 --json  # a limit order at 1225.00
python3 "${CLAUDE_SKILL_DIR}/size.py" --product CNC --write   # delivery instead of intraday (default MIS)
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=60,ITC=40 --config split.json   # weights mode, below
```

`--now "2026-10-19 09:50"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-portfolio writes it, trader-ticket keeps it) |
| `trader/orders.json` | the one proposed order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default 500).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (₹75,000) and the smallest order (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (1500), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "15:00") and **flat_by** ("15:10"), all IST. `flat_by` is a knob: check the user's
  broker for its own intraday square-off time and keep this before it.
- **blocked**: symbols they never want to trade.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only: NSE cash equities do not trade in fractions. It sizes on the
user's own cash and never counts MIS leverage as buying power. The size is the
smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the ticker is blocked, or the plan is a short. Say
the reason to the user in one line.

**Product.** MIS is intraday: the broker can square it off for you near the
close. CNC is delivery. The default is MIS because this pack plans day trades.

**Charges.** The sizing leaves out brokerage, STT, stamp duty, exchange charges
and GST. On small intraday trades they can matter, so tell the user to check
Groww's current charges page or its order screen.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees and
the max loss at the stop. Say plainly that a stop is an order, not a promise: in
a fast market it can fill below the stop. Then hand over to `/trader-risk`.
Do not say whether the trade is a good one: that is the user's call.

## Example: the reel

RELIANCE on Monday 2026-10-19. Opening-range breakout: entry 1227.80 at 9:50 AM,
stop 1215.35 (the opening-range midpoint), target 1240.25 (1R). Risk 12.45 a
share. The example config: ₹1,00,000 budget, 10% cash buffer, ₹500 max loss.

| limit | the math | shares |
| --- | --- | --- |
| max loss | ₹500 / 12.45 | **40 (decides it)** |
| position cap | 75% of ₹1,00,000 = ₹75,000 / 1227.80 | 61 |
| max order | ₹75,000 / 1227.80 | 61 |
| cash | ₹1,00,000 minus ₹10,000 = ₹90,000 / 1227.80 | 73 |

40 shares = ₹49,112. Max loss at the stop: 40 x 12.45 = ₹498. It writes:

```json
{"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": 40, "product": "MIS", "type": "market",
 "entry": 1227.8, "stop": 1215.35, "target": 1240.25,
 "why": "opening-range breakout on RELIANCE: 40 shares, bound by your ₹500 max loss per trade (40 x 12.45 = ₹498 at the stop)",
 "plan": {"risk_per_share": 12.45, "max_loss": 498.0, "setup": "orb", "entry_time": "9:50 AM", "flat_by": "15:10"}}
```

`why` and `plan` are notes for trader-risk and trader-ticket. With the stop at
700.00 instead, one share would risk 527.80, more than the ₹500 max loss: no
order. With a fresh quote of 1235.48, the same plan sizes to 24 shares at 20.13
a share, and reward:risk falls to 0.24: refused, because the move it was waiting
for has mostly happened.

## Weights mode

`--weights RELIANCE=60,ITC=40` or `--targets FILE` (`targets.example.json` here
shows the format; its stocks are arbitrary, not a suggestion) splits a budget
across a list the user chose: the cash buffer comes off the top, weights are
renormalised to 100, any stock over `max_position_pct` is pinned at the cap and
its excess shared out, slices under the minimum order are dropped. Whole shares
only, so it needs a price for each (`--prices trader/prices.json`). With a 25%
cap, ₹1,00,000 and that file's weights, a RELIANCE slice is capped at ₹25,000.
These are CNC buys with no stop: trader-risk blocks them while `require_stop` is
true and counts each one against `max_trades_per_day`. Use a separate config
(`--config`) for a long-term split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- Never pick the stocks. The user brings the plan or the list.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. The broker's price is the one that fills.

## What it never does

It never places or schedules an order, never recommends a trade, never raises a
limit on its own, never shrinks a stop to fit more shares, and never sizes F&O,
margin or short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

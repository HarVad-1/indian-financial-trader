---
name: trader-ticket
description: >-
  Order Ticket. Prints what you would enter in Groww for a proposed trade: the
  symbol, quantity, Intraday (MIS) or Delivery (CNC), market or limit, the
  stop and target to set, and the max loss at the stop, with trader-risk's
  verdict on that exact order. It never places, changes or cancels an order
  and never calls a broker tool: you decide, and you place it yourself. Then
  it records what you say you did, so trader-risk can hold the daily limits:
  placed, skipped, fills. Builds the sell ticket that closes a day trade by
  your flat-by time. Use when the user says "show me the order", "what do I
  enter in Groww", "ticket", "I placed it", "I bought it at", "my stop hit",
  "I skipped it", "close my position", "flatten", "get me out", or after
  /trader-risk. If the user asks Claude to place an order, say it is theirs to
  place in Groww and show the ticket.
argument-hint: "[order number]"
---

# trader-ticket

The ticket is the end of the pack's road. Nothing here reaches the market. The
user reads the ticket, decides, and places the order in Groww with their own
hands, or does not.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, with trader-risk's verdict
python3 "${CLAUDE_SKILL_DIR}/ticket.py" placed --index 1 --filled-qty 40 --filled-price 1227.85
python3 "${CLAUDE_SKILL_DIR}/ticket.py" skipped --index 1 --note "spread too wide"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 40 --price 1240.25
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 40
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, builds the flatten
ticket, and keeps the log in `trader/state/ticket.json`. `--now "2026-10-19 09:50"`
pins the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts, matched to each exact order by fingerprint |
| `trader/state/ticket.json` | every placed, skipped and filled the user reported, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `placed --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Steps: the entry

1. `ticket.py show`. Show the ticket as it is, with its trader-risk line. A BLOCK
   or NOT CHECKED goes back to `/trader-risk`; explain it, and the user decides.
2. Do not nudge. No "go ahead", no "looks good". Whether to place it is their call.
3. The user places it in Groww themselves, or does not. The stop and the target
   only exist if they set them there. Ask them to check which stop order types
   their Groww account offers, and that prices fit the stock's tick size (5 paise
   on many stocks), rounding if Groww rejects one.
4. When the user says what they did, record that and only that:
   - `ticket.py placed --index N --filled-qty Q --filled-price P`
   - `ticket.py skipped --index N`
   Never record a trade the user has not told you about. The log keeps the entry's
   max loss for trader-risk's daily limit. An order trader-risk blocked is still
   recorded if the user placed it, because the limits must count what happened.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). It rings; it does not trade.
- When a stop or target fills, record it:
  `ticket.py fill --symbol RELIANCE --side sell --qty 40 --price 1215.35`.
- By `flat_by` (default 15:10 IST), for anything still open that the user wants out of:
  1. Ask how many shares Groww shows.
  2. `ticket.py flatten --symbol RELIANCE --qty 40` writes a market sell to
     `trader/orders.json`. `--product CNC` for delivery.
  3. `/trader-risk`. A sell that only closes what the user holds is never blocked
     by the daily limits or the entry window.
  4. `ticket.py show`. The user cancels any open stop or target for those shares in
     Groww first, places the sell, and says so. Record it with `placed`.
- MIS positions can be squared off by the broker near the close, and brokers can
  charge for that. `flat_by` is a knob: the user should check Groww's current
  square-off time and keep `flat_by` before it.

## Charges

Tickets leave out brokerage, STT, stamp duty, exchange charges and GST. Tell the
user to check Groww's current charges; its order screen shows what applies.

## Example: the reel

```
ORDER 1 of 1, to enter yourself in Groww. Nothing here places it.
  BUY 40 RELIANCE on NSE, Intraday (MIS), market
  stop-loss 1215.35
  target 1240.25
  max loss about ₹498 if the stop fills at 1215.35 (it can fill worse in a fast market)
  why: opening-range breakout on RELIANCE: 40 shares, bound by your ₹500 max loss per trade (40 x 12.45 = ₹498 at the stop)
  trader-risk PASS: inside your limits. That is a rules check, not a recommendation.
  The stop and target only exist if you set them in Groww yourself.

Whether to place it is your call. Education and tooling, not financial advice.
```

The user says "bought 40 at 1227.85". Claude runs
`ticket.py placed --index 1 --filled-qty 40 --filled-price 1227.85`, and cash goes
from ₹1,00,000 to ₹50,886.

## A Groww MCP server

This skill does not need one. If the user connects one to read holdings (see
`/trader-portfolio`), its order tools stay off, or on approval, and Claude does not
call them. Claude does not place, change or cancel orders in this pack, even when asked.

## What it never does

- Never places, changes or cancels an order, and never calls a broker's order tool.
- Never places anything from a scheduled or background task.
- Never asks for, reads, stores or repeats tokens or passwords.
- Never records a trade the user did not report, and never promises a fill price.
- Never says whether to take a trade.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

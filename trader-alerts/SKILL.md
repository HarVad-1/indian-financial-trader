---
name: trader-alerts
description: >-
  Price Alerts for NSE stocks. Rings when a price, a stop or the opening bell
  hits. Turns the trade plan into three alerts in one go (the stop, the target
  and "flat by close" at your flat-by time), plus above / below / stop / target
  levels, percent moves from the previous close, any time of day, and the NSE
  opening bell, each ringing once so you are not spammed. Knows NSE hours (9:15
  to 15:30 IST) and the 2026 holidays, fetches free delayed quotes (Yahoo chart
  JSON) one symbol at a time, and writes trader/prices.json for sizing and the
  risk check. An alert never sells anything. Use when the user says "alert me
  when", "tell me if RELIANCE hits", "watch my stop", "watch my trade", "remind
  me to close by 3:10", "is the market open", "when does the market open", "is
  NSE open today", "get me a price", or right after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill, and nothing in this pack places an order. A stop
only exists at the broker if the user set it in Groww themselves. The alerts say
what the price did and what time it is; what to do about it is the user's call.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1215.35
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1240.25
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by close"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol "M&M" --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-19 15:10"` pins the clock (IST); `--offline DIR` reads saved
quote files instead of the network. Symbols are plain NSE symbols (RELIANCE, M&M,
BAJAJ-AUTO); a typed `.NS` is stripped. Quote an `&` in the shell.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/config.json` | `flat_by`, when the plan has none (default 15:10) |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:10") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules carry
the plan's date, so yesterday's stop never rings today.

The default flat-by time, 15:10, is a config knob (`flat_by`). Brokers square off
intraday positions on their own schedule: check Groww's current intraday
square-off time and set `flat_by` before it.

## Quotes

- `query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`, an unofficial free
  JSON endpoint, one request per symbol, half a second apart. On a 429 it waits
  and tries once more; on a 429 or a 5xx after that it tries `query1`. It
  identifies itself as `indian-financial-trader/1.0`.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. Groww's price is the one
  that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price in Groww, or from a connected Groww MCP
  server's read-only tools, and write it into `trader/prices.json` as
  `{"as_of": "...Z", "prices": {"RELIANCE": 1221.10}}`.

## The NSE clock

Regular session 9:15 a.m. to 3:30 p.m. IST, closed on weekends and on these
capital-market holidays, from nseindia.com (read 2026-11-02):

- 2026 weekdays: Jan 15, Jan 26, Mar 3, Mar 26, Mar 31, Apr 3, Apr 14, May 1,
  May 28, Jun 26, Sep 14, Oct 2, Oct 20, Nov 10, Nov 24, Dec 25.

India has no daylight time, so the tool needs no time zone files and ignores the
computer's own zone. For any other year it warns that the holiday list is unknown
and points to nseindia.com. The pre-open session (9:00 to 9:15) and the Muhurat
session are not modelled: Muhurat day counts as closed here, so a bell or an
alert does not ring for it.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while a position is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each, as a fact. Whether to sell, hold or
   do anything is their call, and they place any order themselves in Groww.
   `/trader-ticket` can print the ticket if they ask. Never act for them.

## Example: the reel

RELIANCE on Monday 2026-10-19: entry 1227.80 at 9:50 AM, stop 1215.35, target
1240.25, flat by 15:10. `add --from-plan` writes `reliance-plan-stop`,
`reliance-plan-target` and `reliance-flat-by` for that day. A `check` at 10:57
reads the 10:50 bar's close, 1221.10, between the stop and the target:

```
NSE OPEN (2026-10-19 10:57 IST, regular session).
Nothing hit. 3 rule(s) watched today, RELIANCE ₹1221.10.
```

At 3:10 PM:

```
ALERT Flat by close: it is 15:10 IST (set for 15:10), the market closes at 15:30. If you still hold RELIANCE
from today, closing it is your call: check Groww, and trader-ticket flatten --symbol RELIANCE prints what you would enter.
```

It rings once. The next trading day the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at the first check after 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, never predicts where a price is going, and never tells the user to buy or sell.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

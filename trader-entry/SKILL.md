---
name: trader-entry
description: >-
  Entry Timer. Watches a setup on an NSE stock bar by bar and tells you the
  moment it triggers. Reads free Yahoo 1-minute or 5-minute bars, builds the
  opening range (the first 15 minutes by default, 9:15 to 9:30 IST) and calls
  the trigger on the first completed bar that CLOSES above the range high, or
  above a level you give it (like a 20-day high from the scanner), with an
  optional volume check. Says waiting and how far, triggered with the time and
  the price, expired, or not open yet, and writes trader/entry.json for
  /trader-exit. Long side only. It never places an order: whether to buy is
  your call. Use when the user says "tell me when it triggers", "watch
  RELIANCE", "has it broken out", "did it break the opening range", "entry
  signal", "time my entry", "check my watchlist", or after /trader-scanner and
  /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not a trigger. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything, and the decision stays
with you. Day trading loses most people money, and a clean signal does not
change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE              # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write      # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1230.50
python3 "${CLAUDE_SKILL_DIR}/entry.py" check "M&M" --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 15:00  # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in trader/watchlist.json
```

Symbols are plain NSE symbols: RELIANCE, M&M (quote it in the shell),
BAJAJ-AUTO. A typed `.NS` is fine. Other flags: `--or-minutes N` (default 15),
`--json`, `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`, or
a folder of `<SYMBOL>.json`), `--now "2026-10-19 09:50"` (IST) to replay a day.
`watch` with `--now` or `--offline` runs on a simulated clock and replays the
day bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (15:00), IST. Optional |
| Yahoo chart API | today's bars for `<SYMBOL>.NS`, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range |

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The level
  to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:45 bar closes at 9:50 AM).
  The price is its close, to the paisa.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment (it waits a
  minute at most). Yahoo's live tick, like 10:34:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the average of the range bars that report any
  volume. Yahoo usually sends the NSE 9:15 bar with zero volume, so that bar
  is left out of the average.
- **Time limits.** No entries before 9:30 or after 15:00 (config). The NSE
  session is 9:15 to 15:30 IST with no early closes.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming. Weekends and NSE holidays: the market is closed. The
  holiday list covers 2026; for other years it says to check nseindia.com. The
  Diwali Muhurat session is not modelled and counts as closed.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 15:00` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches: a sleeping
   laptop pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, that nothing was placed, and
   that buying or not is the user's call. If the trigger is more than a bar
   old, say that too: a late entry is a different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: the reel

RELIANCE, Monday 2026-10-19, 5-minute bars, real Yahoo data. The opening range
is 1207.30 to 1223.40. At 9:47 the 9:45 bar is still open, so it waits:

```
WAITING  last close ₹1221.80 (9:40 bar), ₹1.60 under the range high ₹1223.40 (-0.13%).
The 9:45 bar is still forming (₹1227.80 so far, closes 9:50 AM); it does not count until it closes.
```

At 9:50 the bar has closed:

```
RELIANCE  Mon 2026-10-19 09:50 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹1223.40, low ₹1207.30 (3 of 3 bars).
TRIGGERED 9:50 AM at ₹1227.80: the 9:45 bar (bar 6) closed above the range high ₹1223.40 (+0.36%).
The setup has triggered. Whether to buy is your call. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
```

With `--min-volume-x 2` the 9:45 bar is too thin (1.52x the range's volume) and
no later bar in the first hour clears 2x, so it keeps waiting.

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars. They are unofficial
  and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money. It never tells the user to buy.
- Long side only. v2 plans longs only. If the user wants a short, say that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call a trigger early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

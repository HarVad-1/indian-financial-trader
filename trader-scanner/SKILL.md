---
name: trader-scanner
description: >-
  Chart Scanner for NSE stocks. Scans thousands of Indian charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the list of NSE
  series EQ stocks from NSE's own equity list, runs a pre-market scan of daily
  closes (20-day closing-high breakout, bull flag, reclaimed the 20-day
  average, oversold, oversold bounce) and an intraday opening-range breakout
  scan on 5-minute bars, then writes trader/watchlist.json for trader-news and
  trader-entry. Free Yahoo data, bar by bar. Use when the user says "scan the
  market", "scan NSE", "find me setups", "what is breaking out", "opening range
  breakouts", "scan for bull flags", "oversold stocks", "pre-market scan",
  "build my watchlist", "what should I look at today", or at the start of a
  trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of looking at a day trade. It reads
thousands of free charts and lists the ones that match a rule. That is all it
does. A scan is a list of charts that match a rule, not a list of stocks to
buy, and a setup on a chart is not a prediction. Most people who day trade lose
money; the scanner does not change that. It only saves you from flipping through
2,000 charts by hand. The decision stays with the user.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols "RELIANCE,M&M,TCS"  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:00                 # cut the clock at 10:00 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Quote a symbol with an `&` in the shell, as above. Symbols are plain NSE symbols
(RELIANCE, M&M, BAJAJ-AUTO); a typed `.NS` is stripped.

Flags: `--min-price 50` (rupees) and `--min-volume` (shares; daily 100,000, orb
0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:00"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the scan
found something, 1 when it found nothing or could not run (the reason is one
plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 15:00) |

## The universe

`universe` downloads `EQUITY_L.csv` from nsearchives.nseindia.com and keeps
series EQ, the normal rolling-settlement board. Series BE and BZ are
trade-for-trade boards with no intraday squaring off, so they are out. ETFs are
not in this file. On Nov 2 2026 that is 2,620 rows: 2,357 kept, 230 BE and 33 BZ
dropped.

NSE drops requests that do not look like a browser, so this one request sends a
browser User-Agent and a Referer. Yahoo gets the tool's own name.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 2,357 symbols
take 118 requests. On Nov 2 2026 that took 68 seconds with the polite pause
(0.3 s). Liquidity comes from the spark meta: last price at least `--min-price`
and last session's volume at least `--min-volume`. That morning 1,037 passed,
1,011 had enough history and 254 matched a setup. Run it before the open or
after the close: during the session the volume is the day's so far, and today's
daily bar is left out until 3:30 PM.

The floors are in rupees and shares because a US-style "a million shares" drops
real large caps: MARUTI traded 280,448 shares on Oct 30 2026 at ₹11,397.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar to
CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. Spark leaves bars out of NSE charts
   that the v8 chart has (on Oct 30 2026 AXISBANK had no 9:20 or 9:25 bar there),
   so this stage goes by bar time and does not need every bar. A breakout whose
   only close above the level is a bar spark left out is missed until a later
   bar closes above it.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. Stage 2 checks at most `--max-confirm` (200) of them, the most
   traded first, and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A chart is usable only when its v8 chart has every 5-minute bar from 9:15 to the
cutoff; a missing bar means a halt or a thin stock. The exception is 15:15
onwards: Yahoo's NSE charts usually have no close for the 15:20 bar and often
none for 15:15 or 15:25, so those may be missing. Null closes are dropped, a bar
off the 5-minute grid (the live tick) is dropped, and a bar counts only once it
is complete (start + 5 minutes). Before 9:35 AM, on a weekend or holiday, after
the close, or after `no_entries_after` (an expired setup) it prints one plain
line and writes nothing.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial: "Fetched 2,300 of 2,357 charts (57
  failed or had no data)". Never round a partial scan up to "the whole market".
- NSE holidays for 2026 only (nseindia.com, read 2026-11-02). In any other year
  it warns and points to nseindia.com. The Muhurat session is not modelled: that
  day counts as closed.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST: `scan.py orb`. Tell the user how many usable charts broke
   their range and when, and that a later hit is closer to the price now.
4. The user picks one chart, or none. Hand it on: `/trader-news SYMBOL` (what is
   moving it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for
   them.

## Example: the reel

Monday Oct 19 2026, scan at 10:00 AM over 33 liquid and penny names
(`tests/fixtures/trader-scanner/wall5m-2026-10-19.json.gz`, real Yahoo charts):

```
python3 scan.py orb --offline wall5m-2026-10-19.json.gz --now "2026-10-19 10:00" --min-price 0
Fetched 32 of 33 charts (1 failed or had no data); 32 usable; 15 candidates, 15 confirmed with real highs and lows.
8 of 32 charts closed above their opening-range high by 10:00 AM:
   9:35 AM  SBIN           ₹978.95  range 960.90 to 977.70  +0.13% over
  ...
   9:50 AM  RELIANCE     ₹1,227.80  range 1,207.30 to 1,223.40  +0.36% over
```

RELIANCE's range is the 9:15 to 9:25 bars, high 1223.40, low 1207.30; the 9:45
bar closed at 1227.80, 0.36 % over the high, at 9:50 AM. If the user wants to
look closer, Claude hands it to `/trader-news RELIANCE`, then
`/trader-entry RELIANCE`.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before relying on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

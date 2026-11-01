---
name: trader-portfolio
description: >-
  Portfolio view. Reads your holdings from a CSV or JSON file (a Groww export,
  or the answer of a read-only Groww MCP tool saved to a file) and prints
  value, P&L, weights and concentration flags in rupees, then writes
  trader/portfolio.json for trader-budget, trader-risk and trader-ticket. It
  reports facts about what you hold and never says what to buy or sell. Covers
  connecting a Groww MCP server read-only: order tools off, tokens out of the
  chat. Use when the user says "how is my portfolio", "show my holdings", "what
  is my P&L", "am I too concentrated", "connect Groww", "import my Groww
  holdings", or before /trader-budget when real cash should cap the size.
argument-hint: "[path to holdings file]"
---

# trader-portfolio

Shows what the user holds and how it stands. Facts only: if they ask what to buy
or sell, say that is their decision (and a SEBI-registered adviser's question if
they want personal advice), then offer the numbers.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" holdings.csv
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" holdings.json --cash 25000 --write   # -> trader/portfolio.json
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" holdings.json --json
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" holdings.csv --map symbol=Scrip,qty=Held,avg=Cost
python3 "${CLAUDE_SKILL_DIR}/portfolio.py" holdings.json --max-weight 20 --max-top3 50
```

## Getting the holdings

**From a file.** Export holdings from Groww, save as CSV (a spreadsheet must be
saved as CSV first), and pass the path. Title lines above the header and Indian
commas (`1,00,000`) are fine.

**From a Groww MCP server, read-only.** If one is connected, call its read-only
holdings tool, save the answer to a file in `trader/`, and pass that path.
What to tell the user before they connect one:

1. **Check for an official server first** in Groww's own developer docs. The
   servers found when this skill was written were community projects, not
   confirmed by Groww. They would hold access to a real-money account, so read the
   code of any one before installing it.
2. **Order tools off.** List the server's tools. Turn off every tool that places,
   changes or cancels anything, or at the very least keep it on approval. In Claude
   Code, deny them in `.claude/settings.json`, using the tool names that server
   actually lists, for example `"permissions": {"deny": ["mcp__groww__place_order"]}`.
   In Claude Desktop, use the connector's tool permissions.
3. **Tokens stay out of the chat.** The token goes in an environment variable or the
   MCP config, typed by the user. Never ask for it, read that file back or repeat it.
   If one is pasted anyway, tell them to delete the message and regenerate it.
4. **Tokens expire.** One community listing says Groww tokens expire daily around
   6:00 AM IST, so a failed read in the morning may just mean a new token. Check
   Groww's docs for the current rule.

## Field names

Exports and servers differ, and this skill was written without a live Groww account,
so common names are mapped: symbol (`symbol`, `trading_symbol`, `tradingsymbol`,
`ticker`, ...), name (`stock_name`, `company_name`, ...), quantity (`quantity`, `qty`,
`shares`, ...), average price (`average_price`, `avg_price`, `average_buy_price`, ...),
last price (`ltp`, `last_price`, `closing_price`, ...), cash (`cash`, `available_cash`,
...). JSON may be wrapped under `holdings`, `payload` or `data`. If it cannot map a file
it stops and names the columns it saw; use `--map`. It never guesses a number.

**Last price.** From the file, else `trader/prices.json`
(`alerts.py quote RELIANCE ITC --write`), else the holding is valued at cost, its P&L
is left out, and it says so. The P&L line says how many holdings it covers.

**Names instead of symbols.** An export with only company names still prints, with a
warning, but trader-risk cannot match those to an order. Use a file with NSE symbols
for `--write`.

## What it prints

Each holding's quantity, average, last price, value, P&L and weight; totals; and flags:

- a holding over 25% of the holdings (`--max-weight`)
- a top 3 over 60% of the holdings (`--max-top3`), with more than three holdings

Weights leave out cash. The flags are arithmetic against thresholds the user can
change. They are not advice to trim anything. It has no sector data, so it says
nothing about sectors.

## What it writes

`--write` replaces `trader/portfolio.json`, in the shape the other skills read:

```json
{"cash": 25000.0, "source": "trader-portfolio", "as_of": "2026-11-02", "positions": [
  {"ticker": "RELIANCE", "shares": 10, "lots": [{"id": "RELIANCE-1", "date": "2026-11-02", "shares": 10, "cost": 11000.0}]}]}
```

It needs cash: `--cash`, a cash field in the file, or the cash already in the file it
replaces. It refuses to guess, because trader-budget and trader-risk size against
it. Ask the user for the number. Lots hold the average cost as one lot dated today,
so they are no record of tax lots or purchase dates.

## What it never does

It never calls a broker's order tool, never recommends a buy or a sell, never asks for
a token, and never invents a price, a quantity or a cash figure.

- Education and tooling, not financial advice.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

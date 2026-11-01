---
name: trader-deals
description: >-
  The Deals Tracker. Reads what NSE publishes about large trades and company
  insiders: the last session's bulk deals and block deals (who, stock, buy or
  sell, quantity, price, value) and insider filings under SEBI's insider
  trading rules (person, category, buy, sell, gift or pledge, dates). Filters
  by stock or by client name, alerts once per new row, tags a client who bought
  and sold the same stock the same day, and marks rows in stocks you hold. Use
  when the user says "bulk deals", "block deals", "who bought X", "did any
  mutual fund buy X", "insider trading in X", "promoter selling", "FII big
  trades", "track big investors", or "run the deals skill".
argument-hint: "[symbol or client name]"
---

# trader-deals

NSE publishes who made large trades. This skill reads those lists, notices what
is new since the last check, and marks anything in a stock the user holds.

It helps the user see what happened. It does not say what to do next. Every
output is a report of past trades, and the decision stays with the user.

Tool in this folder (standard library Python):

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" show                               # the last session's bulk and block deals
python3 "${CLAUDE_SKILL_DIR}/deals.py" show --kind block --client goldman # one list, one client
python3 "${CLAUDE_SKILL_DIR}/deals.py" show --symbol SWIGGY --json
python3 "${CLAUDE_SKILL_DIR}/deals.py" insiders --symbol RELIANCE         # insider filings for one stock
python3 "${CLAUDE_SKILL_DIR}/deals.py" insiders --from 2026-01-01 --to 2026-03-01   # every stock, by date
python3 "${CLAUDE_SKILL_DIR}/deals.py" new --symbol "M&M" --client "mutual fund"    # only what is new
```

Quote symbols like `M&M` in the shell. A typed `.NS` is dropped.

## What it reads and writes

| file or page | what |
| --- | --- |
| `nsearchives.nseindia.com/content/equities/bulk.csv` | bulk deals, one session |
| `nsearchives.nseindia.com/content/equities/block.csv` | block deals, one session |
| `www.nseindia.com/api/corporates-pit` | insider filings (`symbol`, or `from_date` and `to_date` as DD-MM-YYYY) |
| `trader/portfolio.json` | optional: its positions are marked HELD, and `new` reads their insider filings |
| `trader/state/deals.json` | the rows `new` has already shown |

## How to read each list

- **Bulk deal.** A client traded over 0.5% of a company's equity in one day. It
  is reported after the session ends, so the trade is already over.
- **In and out.** A large share of bulk deals are the same client buying and
  selling the same stock the same day, often a trading firm. The tool tags these
  `(in and out)`, because they say nothing about a long-term view. In the
  October 30 2026 list, 33 of the 53 client and stock pairs were like this.
- **Block deal.** A large trade made in the exchange's separate block window,
  often a pre-arranged sale from one holder to others. The client who sold and
  the clients who bought are usually all in the list.
- **Insider filing.** A promoter, director, employee or relative reporting a
  change in holding. Only `Market Purchase` and `Market Sale` happened on the
  exchange. Gifts, pledges and off-market transfers are tagged
  `(not a market trade)`, and a pledge is not a sale.

## Steps

1. Run `show`, `insiders` or `new`, depending on what the user asked.
2. Show the rows as they are, with the date, the side and the value. Group by
   stock when there are many.
3. Say plainly what each row is, using the notes above, and mention the delay.
4. If a row is marked HELD, point it out and stop there. The user decides what,
   if anything, it means.
5. For a recurring check, run `new` on a schedule. Each row alerts once. The
   first run shows only the last 30 days, and a source that fails is reported
   while the others still run.

## Known limits

- **The insider feed looks behind.** On 2026-11-02, NSE's API returned no
  market-wide filings after 2 May 2026 (Jun, Jul, Aug, Sep and Oct were empty)
  and the reason is unknown. The tool prints a note when the newest filing is
  over 30 days old. Treat an empty answer as "not known", never as "no insider
  traded", and check nseindia.com.
- `new` reads insider filings only for stocks given with `--symbol` or held in
  the portfolio. The all-stocks list needs `insiders --from --to`.
- NSE's pages are unofficial for this use and can block a request. Say so if one
  fails, and do not retry in a loop.
- BSE deals are not covered, since BSE's API refused automated requests when
  tested. A stock's BSE trades will not appear here.

## Example

```
2026-10-30  block    SWIGGY       BUY  23,740,000 shares at ₹180.65 (₹4,288,631,000.00)  SOCIETE GENERALE
2026-10-30  bulk     AEROFLEX     BUY  670,611 shares at ₹540.12 (₹362,210,413.32)  QE SECURITIES LLP  (in and out)
```

The first is one of several buyers on the other side of a large block sale. The
second is a client that also sold 677,056 AEROFLEX shares the same day. Neither
says the stock will rise or fall.

## What it never does

- Never says a buyer knows something, or that copying a big client beats the market.
- Never turns a row into a buy or sell suggestion.
- Never treats a gift, a pledge or an in-and-out trade as a view on the stock.
- Never treats an empty insider list as proof that nobody traded.
- Never places an order.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. Day trading loses most people money.
- You make every buy and sell decision. Nothing in this pack places an order: trader-ticket prints what you would enter, and you place it yourself in Groww.
- No F&O, margin (MTF) or short selling unless the config allows it and you confirm.
- Broker tokens live in environment variables or the MCP config, never in the chat.
- Free data can lag or be wrong. Check anything that matters in Groww before you act on it.

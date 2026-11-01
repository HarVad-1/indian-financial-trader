---
name: trader-news
description: >-
  News Monitor. Tracks breaking news on every Indian stock you watch and flags
  what moves it: Google News headlines per company, each company's NSE
  corporate announcements (results, board outcomes, deals, orders, resignations)
  and how far each stock is from yesterday's close. Shows only what is new since
  the last check, newest first, in IST, tags each item (earnings, analyst, deal,
  halt, regulatory, macro...) and never repeats one. Use when the user says "any
  news on RELIANCE", "why is this moving", "what's the news on my watchlist",
  "morning briefing", "pre-market news", "monitor the news", "did anything
  break", "check the NSE announcements", or after /trader-scanner finds setups.
argument-hint: "[NSE symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on results, a block deal or
a regulator's order. Before a setup means anything, this skill reads the two
feeds that carry those first, for every stock on the watchlist, and tells you
only what is new. It also prints how far each stock has moved today, so a
headline and a move sit side by side. What to do about it is the user's call.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE "M&M" --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-filings --json
```

Plain NSE symbols. Quote any with an `&`, because the shell treats it as a
command separator.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the setup you are studying (trader-exit) |
| `trader/portfolio.json` | what you hold |
| Google News RSS | searched on the company's name, en-IN. Yahoo's headline feed is empty for NSE stocks. Links go through Google's redirect |
| NSE corporate announcements | `nseindia.com/api/corporate-announcements`, per symbol. Unofficial: if NSE refuses, the tool says so and headlines still run |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| NSE `EQUITY_L.csv` | the company's name, read once per new ticker |
| `trader/state/news.json` | what has been shown (kept 30 days) and the names |

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an NSE announcement and a headline disagree,
  the announcement is what the company told the exchange.
- **Headlines are filtered by title.** Google's search also returns stories
  that only mention the company in passing, so a headline must name it: first
  word of the name, the symbol or the initials (RIL). The tool counts what it
  hid; `--loose` shows them. "Kotak Mahindra" still passes for M&M, and a story
  using an unusual nickname can be hidden.
- **Some times are dates.** Google gives many stories a date with no time. They
  print `time n/a` and count as published some time that day, so one can really
  be from after the moment being studied.
- **Say what it touches.** Every item lists the tickers it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro. Announcements marked `!` are the kinds that often matter:
  financial results, board outcomes, orders passed, acquisitions, schemes,
  buybacks, management changes, resignations, fraud, insolvency, default,
  delisting, credit ratings, news clarifications. NSE's labels are broad: a
  "General Updates" can matter too, so read the text.
- **Noise is dropped.** Lost share certificates, newspaper clippings and
  trading-window notices never print.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next. Many Indian
  headlines carry broker calls ("top 3 stocks to buy"): report them as someone's
  opinion, never as this skill's.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: every `!` filing, anything tagged earnings,
   halt, legal, regulatory or risk, and every MOVING ticker first.
3. Write a short brief, per ticker: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results are
   this week: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: results inside the trade, a large IPO in the group, an order
   from a regulator.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 9:15 open.

## Example: the reel

Monday 2026-10-19, RELIANCE closed above its opening range in the bar ending
9:50 AM IST. The check at 9:50 leads with the day's real stories, all dated
that day by Google with no time: "RIL stock sees unusually large F&O bets ahead
of Jio IPO: Report" (Moneycontrol, tagged capital), "RIL nears technical
breakout amid Jio IPO buzz: Anand James shares the outlook" (The Economic
Times). NSE's announcements that week are about the same IPO ("General Updates:
Proposed Initial Public Offer of Jio Platforms Limited - filing of the Red Herring
Prospectus", Oct 14). The brief: "The Jio Platforms IPO is the story around the
stock today, and one report says options activity is unusually large. No results
or order from the company at NSE in the last few days." At
that bar's close the stock was at ₹1,227.80, +1.37 % against Friday's close of
₹1,211.20. When a ticker has no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per ticker and a pause between
  calls.

## Safety

Education and tooling, not financial advice. This skill only reads and reports.
You make every buy and sell decision. Nothing in this pack places an order.

---
name: trader-analyst
description: >-
  The Earnings Analyst. Reads an Indian company's recent quarters (revenue,
  gross, operating and net margins, net income, diluted EPS, in ₹ crore) from
  Yahoo Finance, finds its results PDFs on NSE, and sums up what changed against
  the same quarter a year earlier. Says plainly where the free data has gaps: a
  missing quarter, no year-ago number, results NSE has that Yahoo does not yet.
  Reads an earnings call transcript when you paste one. Use when the user says
  "how did Reliance's results look", "analyze the earnings", "what changed this
  quarter", "is revenue growing", "check the results", "compare margins", "sum
  up the concall", "does it report this week", or before studying a stock around
  its results.
argument-hint: "[NSE symbols]"
---

# trader-analyst

Results move stocks more than anything else on the calendar, and the press
release is written to put the best number first. This skill lines the numbers
up quarter by quarter, links the filing, and says plainly what changed and what
it could not see. For a day trader it answers two questions: what did the last
results say, and are the next ones inside this trade?

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

## What it reads and writes

- Numbers: Yahoo Finance's unofficial `fundamentals-timeseries` endpoint. It
  keeps about five quarters, can skip some, and can run months behind. For banks
  it can have almost nothing. It is not the company's filing, and Yahoo does not
  say whether a figure is consolidated or standalone.
- Filings: NSE's corporate announcements, for the results PDFs and the period
  each covers (read from the announcement's text), and NSE's
  `corporates-financial-results` list for XBRL files. That list can stop far
  behind: on 2026-11-02 it ended at December 2024 for RELIANCE, HDFCBANK and M&M.
  The PDFs are the current source.
- Writes `trader/metrics.json` with `--metrics`: one row per company, for notes
  or a spreadsheet. TTM fields are blank when Yahoo lacks four consecutive quarters.
- Earnings calls: the tool does not fetch transcripts. Companies post them to
  NSE (`/trader-news` shows them as "Analysts/Institutional Investor Meet/Con.
  Call Updates"). If the user pastes one or you can open it, sum up what
  management said changed: guidance, margins, demand, anything they declined to answer.
- The next results date is not in this tool. Look for a "Board Meeting
  Intimation" in `/trader-news`, and say where it came from.

## The rules

- **Read the gaps first.** They print under the table and are part of the answer:
  - `GAP`: a quarter between two others that Yahoo does not have.
  - `No year-on-year`: the year-ago quarter is missing. Growth is blank, never estimated.
  - `Last 4 quarters not shown`: no four consecutive quarters to add up.
  - `NOTE`: NSE has results for a later period than Yahoo. Read that PDF before
    summarising. Never present Yahoo's latest quarter as the latest results when
    this note is there.
- **Compare like with like.** Growth is against the same quarter a year earlier.
  Quarter-on-quarter is shown only between true neighbours, and many businesses
  are seasonal.
- **Check against the filing.** Before relying on a number, compare it with the
  results PDF, and say which numbers came from where. The tool does not read PDFs.
- **Flag one-offs.** When net income is above operating income, gains outside
  the business are inside it. The tool says so. Banks have no gross margin:
  blank, not zero.
- **Fiscal labels assume April to March.** Q1 FY27 ends June 2026. A company
  with another year-end gets the wrong label; dates are always right.

## Steps

1. Run it for the companies in question (or the tickers on
   `trader/watchlist.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE "M&M" --metrics trader/metrics.json
   ```

   `--quarters 8` asks for more (Yahoo usually has about five), `--json` gives
   everything machine-readable, `--no-filings` skips NSE.
2. Open the newest results PDF, always when there is a `NOTE`, and read it.
3. Write the summary: one line on the quarter (revenue, EPS, growth), one on
   margins, one on anything unusual, one on the gaps, and the links. Plain words,
   real numbers, no adjectives the numbers do not support.
4. If the next results fall inside the planned trade (an overnight hold, or
   results before the open), say it before `/trader-exit` sets the stop: a stop
   does not protect against a gap, the stock can open past it.

## Example: the reel's stock

`facts.py RELIANCE` (run 2026-11-02): quarter ending 2026-06-30 (Q1 FY27), revenue
₹3,09,468 crore, +27.0% against a year earlier, net income ₹20,946 crore (-22.4%),
diluted EPS ₹15.48 (-22.4%); gross margin 26.8%, operating margin 10.5%. Yahoo has
no September 2025 quarter, so there is no last-four-quarters line and no
year-on-year for December 2025. And the note matters most: NSE shows results for
the period ending 2026-09-30, filed 2026-10-30, which Yahoo does not have yet, so
the summary reads that PDF instead of calling June the latest quarter.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports the numbers.
- Never invents a number, estimates a missing quarter or computes growth against
  one that is not there.
- Never hammers a server: a pause between calls, one company at a time.

## Safety

Education and tooling, not financial advice. This skill only reads numbers and
filings. You make every buy and sell decision. Nothing in this pack places an order.

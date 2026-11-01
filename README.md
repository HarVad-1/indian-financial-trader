# Indian Financial Trader

12 Claude skills for understanding Indian stocks: scan NSE charts, follow company news and filings, time entries and exits, replay outcomes, and review your portfolio.

This is a learning and analysis tool. **You make every buy and sell decision, and nothing in this pack places an order.** The tools show facts and arithmetic, and `/trader-ticket` prints what you would enter. You place any order yourself in Groww.

Adapted from Jake Schincariol's [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT), which covers US markets.

## Install

```bash
git clone https://github.com/HarVad-1/indian-financial-trader.git
mkdir -p .claude/skills
cp -r indian-financial-trader/trader-* .claude/skills/
mkdir -p trader
cp indian-financial-trader/trader-budget/config.example.json trader/config.json
```

Run this from your project folder, and start a new Claude Code session there. To install for all projects, copy the folders to `~/.claude/skills/` instead. The repo is private, so the clone needs your GitHub login.

The tools need Python 3.8 or newer and nothing from pip. There is no signup or API key. Run them from your project folder, because they read and write `trader/` relative to where you start them.

Edit `trader/config.json` before relying on the numbers. The defaults (a ₹1,00,000 budget and ₹500 max loss per trade) are examples, not suggestions.

## The skills

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans NSE stocks for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Company headlines, NSE announcements and how far the stock has moved today. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and says when its rule triggered. |
| `/trader-exit` | Exit Planner | Works out a stop and a target before you decide on an entry. |
| `/trader-timemachine` | Time Machine | Replays the rest of the session 10,000 times, plus every past time the setup fired. |
| `/trader-budget` | Budget Manager | Sizes a position to your budget and your max loss per trade. |
| `/trader-risk` | Risk Manager | Checks a proposed trade against your own limits: PASS or BLOCK, with the reason. |
| `/trader-ticket` | Order Ticket | Prints what you would enter in Groww and logs what you tell it you did. It places nothing. |
| `/trader-portfolio` | Portfolio Review | Value, gain or loss, weights and concentration flags from your holdings. |
| `/trader-alerts` | Price Alerts | Rings when a price, your stop or the opening bell hits. |
| `/trader-analyst` | Earnings Analyst | Sums up quarterly results, and labels any gaps in the data. |
| `/trader-deals` | Deals Tracker | NSE bulk and block deals and insider-trade filings, with your holdings flagged. |

Everything lands in a `trader/` folder in your project, and each skill reads what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> ticket   (one idea)
alerts: stop, target, flat by the close                                       (while it is open)
portfolio / news / analyst / deals                                            (any time)
```

## Worked example

Reliance Industries, Monday 19 October 2026, real data. It shows how the tools work and is not a suggestion.

1. **`/trader-entry`**: the 15-minute opening range was 1,207.30 to 1,223.40. The 9:45 bar closed at 1,227.80, the first close above the top, so the rule triggered at 9:50 AM.
2. **`/trader-exit`**: a stop at the range midpoint, 1,215.35, puts ₹12.45 a share at risk. A target of one times that risk is 1,240.25.
3. **`/trader-timemachine`**: across 10,000 replays built from Reliance's own bars, the target came first 17.8 % of the time, the stop 17.3 %, and neither by 3:15 PM 64.8 %. The same rule fired on 23 of the previous 50 sessions: 11 hit the target, 10 the stop and 2 neither. That is close to a coin flip.
4. **`/trader-budget`**: a ₹500 max loss gives 40 shares (₹49,112), with a ₹498 loss if the stop fills at its price.
5. **`/trader-risk`**: checks that against your limits.
6. **`/trader-ticket`**: prints the details. Whether to enter them in Groww is your decision.

## The tools

```bash
python3 trader-scanner/scan.py universe                             # NSE EQ-series stocks
python3 trader-scanner/scan.py daily                                # setups on daily closes
python3 trader-scanner/scan.py orb --at 09:45                       # opening-range breakouts so far
python3 trader-news/news.py --tickers RELIANCE --hours 12           # headlines, announcements, today's move
python3 trader-entry/entry.py check RELIANCE --setup orb            # waiting / triggered / expired
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write  # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write               # 10,000 replays + the history
python3 trader-budget/size.py --write                               # -> trader/orders.json
python3 trader-risk/check.py                                        # PASS / BLOCK per order
python3 trader-ticket/ticket.py show                                # what you would enter in Groww
python3 trader-portfolio/portfolio.py --help                        # review an exported holdings file
python3 trader-alerts/alerts.py check                               # rings what hit, once
python3 trader-analyst/facts.py RELIANCE                            # quarters, growth, EPS
python3 trader-deals/deals.py show --symbol RELIANCE                # bulk and block deals
```

```bash
cd tests && python3 -m unittest discover -s .
```

The tests run offline on fixtures cut from real NSE and Yahoo data.

## Groww

`/trader-portfolio` can read holdings saved from a Groww MCP server or from a file you export. Only community-built Groww servers turned up when this was written, none confirmed as official, so check Groww's developer docs and read the code of any server first. Connect it read-only, and turn off its order tools or set them to need approval. Keep tokens out of the chat.

The Groww field names in `/trader-portfolio` have not been tested against a live account. It stops on unknown columns, and `--map` lets you match them yourself.

## Known limits

- **Not financial advice.** This software reads public data and does arithmetic. It does not know your situation or the future. Most people who day trade lose money. Talk to a SEBI-registered adviser if you want personal advice.
- **Yahoo's data is unofficial.** It can lag, change or rate-limit you. NSE's 5-minute bars are often empty from 3:15 PM, so the Time Machine stops there. The 9:15 bar usually reports zero volume.
- **NSE's endpoints are unofficial** and can refuse requests. BSE is not covered.
- **Insider filings looked stale.** On 2 November 2026 the feed returned nothing after 2 May 2026, so an empty result does not mean nobody traded.
- **Earnings data is thin.** Yahoo gives about five quarters, some missing, and its figures have not been checked against the results PDFs. The report labels gaps and does not fill them.
- **The holiday list covers 2026 only.** Other years warn you to check nseindia.com. The Muhurat session is treated as closed.
- **Charges are not included.** Brokerage, STT and other costs can change a small intraday result, so check Groww's charges. Also check Groww's own intraday square-off time, because `flat_by` (15:10) is only a default.
- **The Time Machine is a simulation, not a prediction.** It uses about 60 days of bars, and the future can do things those days never did.
- **A stop is a plan, not a guarantee.** It can fill past its price in a fast market or a gap.
- **Long side only.** No F&O, margin or short selling unless you turn them on in the config.
- **Taxes.** Nothing here is tax advice.

MIT. See [LICENSE](LICENSE).

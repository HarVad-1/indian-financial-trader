"""trader-news tests. Fixtures in tests/fixtures/trader-news/, all real:

news-RELIANCE.xml   Google News search, "Reliance Industries" share, Oct 18-20 2026
news-M&M.xml        Google News search, first 10 items, fetched Nov 2 2026
nse-RELIANCE.json   NSE announcements Oct 1-19 2026, plus the Oct 30 results and one
                    share-certificate notice taken from the full history
nse-M&M.json        NSE announcements Oct 20 - Nov 1 2026
chart-RELIANCE.json Yahoo range=1d, last session Oct 30 2026
EQUITY_L.csv        NSE's equity list, four rows
"""

import datetime
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
REEL = "2026-10-19T09:50"   # no offset: IST
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_google_news_rss(self):
        items = news.parse_rss(raw("news-RELIANCE.xml"), "RELIANCE")
        self.assertEqual(len(items), len(re.findall(rb"<item>", raw("news-RELIANCE.xml"))))
        bets = next(i for i in items if i["title"].startswith("RIL stock sees"))
        # the " - Moneycontrol.com" tail moves to the source
        self.assertEqual(bets["title"], "RIL stock sees unusually large F&O bets ahead of Jio IPO: Report")
        self.assertEqual(bets["source"], "Moneycontrol.com")
        self.assertTrue(bets["link"].startswith("https://news.google.com/"))
        self.assertTrue(bets["id"].startswith("rss:"))
        self.assertEqual(bets["time"], datetime.datetime(2026, 10, 19, 7, 0, tzinfo=UTC))
        self.assertTrue(bets["date_only"])
        timed = next(i for i in items if i["source"] == "Univest")
        self.assertFalse(timed["date_only"])       # 10:18:00 GMT is a real time

    def test_nse_announcements(self):
        items = news.parse_nse(raw("nse-RELIANCE.json"), "RELIANCE")
        self.assertEqual(len(items), 6)     # 9 rows less one share-certificate and two newspaper notices
        self.assertFalse(any("Share Certificate" in i["form"] or "Newspaper" in i["form"] for i in items))
        results = items[0]
        self.assertEqual(results["form"], "Outcome of Board Meeting")
        self.assertTrue(results["important"])
        self.assertEqual(results["id"], "nse:106837394")
        self.assertIn("financial results", results["summary"].lower())
        self.assertTrue(results["link"].endswith("_Results.pdf"))
        # NSE's 19:40:11 is IST, so 14:10:11 UTC
        self.assertEqual(results["time"], datetime.datetime(2026, 10, 30, 14, 10, 11, tzinfo=UTC))
        order = next(i for i in items if i["form"].startswith("Action(s)"))
        self.assertTrue(order["important"])
        update = next(i for i in items if i["form"] == "General Updates")
        self.assertFalse(update["important"])
        self.assertIn("Jio Platforms", update["title"])

    def test_nse_refusals_are_plain_errors(self):
        with self.assertRaises(news.NewsError):
            news.parse_nse(b"<html>Access Denied</html>", "RELIANCE")
        with self.assertRaises(news.NewsError):
            news.parse_nse(b'{"msg": "no"}', "RELIANCE")
        self.assertEqual(news.parse_nse(b"[]", "ZZZZ"), [])

    def test_times(self):
        self.assertEqual(news.parse_time("Tue, 13 Oct 2026 20:02:03 +0000").hour, 20)
        self.assertEqual(news.parse_time("2026-10-19T09:50").hour, 4)       # IST with no offset: 04:20 UTC
        self.assertEqual(news.parse_time("2026-10-19T09:50").minute, 20)
        self.assertEqual(news.parse_time("2026-10-19T09:50:00Z").hour, 9)
        self.assertIsNone(news.parse_time("soon"))
        self.assertIsNone(news.parse_nse_time("soon"))
        # India has one offset all year
        for month in (1, 7):
            utc = datetime.datetime(2026, month, 15, 3, 45, tzinfo=UTC)
            self.assertEqual(news.to_ist(utc), datetime.datetime(2026, month, 15, 9, 15))

    def test_symbols(self):
        self.assertEqual(news.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("m&m"), "M%26M.NS")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(news.clean_ticker("reliance.ns"), "RELIANCE")
        for bad in ("", "A B", "../x", "A?B=1"):
            with self.assertRaises(news.NewsError):
                news.clean_ticker(bad)

    def test_urls_escape_the_ampersand(self):
        seen = []

        class Spy(news.Fetcher):
            def get(self, url, name):
                seen.append((url, name))
                return b"[]" if name.endswith(".json") else b"<rss></rss>"
        now = news.parse_time(REEL)
        news.collect(Spy(), {"M&M": "Mahindra & Mahindra Limited"}, now, hours=48)
        self.assertIn("q=%22Mahindra+%26+Mahindra%22+share+when%3A2d&hl=en-IN", seen[0][0])
        self.assertEqual(seen[0][1], "news-M&M.xml")
        self.assertIn("symbol=M%26M&from_date=17-10-2026&to_date=19-10-2026", seen[1][0])
        self.assertEqual(seen[1][1], "nse-M&M.json")


class Tagging(unittest.TestCase):
    def test_touches_and_tags(self):
        items = [{"id": "x", "source": "Mint", "kind": "headline", "ticker": "TCS", "summary": "", "link": "",
                  "time": None, "title": "Reliance Industries Q2 net profit rises; SEBI approval for Jio IPO"}]
        news.tag(items, {"TCS": "Tata Consultancy Services Limited", "RELIANCE": "Reliance Industries Limited",
                         "INFY": "Infosys Limited"})
        self.assertEqual(items[0]["touches"], ["RELIANCE", "TCS"])
        for t in ("earnings", "regulatory", "capital"):
            self.assertIn(t, items[0]["tags"])

    def test_results_filing_is_tagged_earnings(self):
        items = news.tag(news.parse_nse(raw("nse-RELIANCE.json"), "RELIANCE"), {"RELIANCE": None})
        self.assertIn("earnings", items[0]["tags"])

    def test_india_tags(self):
        items = [{"id": "x", "source": "Mint", "kind": "headline", "ticker": "TCS", "summary": "", "link": "",
                  "time": None, "title": "Stock hits lower circuit as RBI holds repo rate; FIIs sell, USFDA nod"}]
        tags = news.tag(items, {"TCS": None})[0]["tags"]
        for t in ("halt", "regulatory", "macro"):
            self.assertIn(t, tags)

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("news-M&M.xml"), "M&M")
        b = news.parse_rss(raw("news-M&M.xml"), "TCS")
        items = news.dedupe(news.tag(a + b, {"M&M": None, "TCS": None}))
        self.assertEqual(len(items), 10)
        self.assertEqual(items[0]["touches"], ["M&M", "TCS"])

    def test_headlines_that_never_name_the_company_go(self):
        ok = news.about_company
        name = "Reliance Industries Limited"
        self.assertTrue(ok("RIL stock sees large bets", "RELIANCE", name))    # initials
        self.assertTrue(ok("Reliance shares climb", "RELIANCE", name))
        self.assertFalse(ok("OPEC+ keeps December oil output unchanged", "RELIANCE", name))
        self.assertFalse(ok("April sales rise", "RELIANCE", name))            # "ril" inside a word
        self.assertTrue(ok("M&M Financial Services", "M&M", "Mahindra & Mahindra Limited"))
        self.assertTrue(ok("SBI cuts rates", "SBIN", "State Bank of India"))  # "of" is skipped
        self.assertTrue(ok("anything", "ZZZZ", None))                         # no name: keep it


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", REEL,
                     "--portfolio", os.path.join(self.tmp.name, "none.json"),
                     "--watchlist", os.path.join(self.tmp.name, "none.json"),
                     "--plan", os.path.join(self.tmp.name, "none.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "120", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "120"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "120", "--all"] + self.base)
        self.assertEqual(code, 1)                                # shown again, but none are new
        self.assertIn("Jio IPO", out)

    def test_the_reel_reliance_morning(self):
        # Monday Oct 19 2026, 9:50 AM IST, the moment the opening range broke
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--no-moves"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-19 time n/a    RELIANCE", out)   # Google gave a date, not a time
        self.assertIn("RIL stock sees unusually large F&O bets ahead of Jio IPO: Report", out)
        self.assertIn("Checked 2026-10-19 09:50 IST", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn("Share Price Prediction", out)          # 15:48 IST, after the clock
        self.assertNotIn("biggest issue opens", out)             # dated Oct 20
        self.assertNotIn(chr(0x2014), out)
        lines = out.splitlines()
        bets = next(i for i, l in enumerate(lines) if "unusually large F&O" in l)
        self.assertIn("capital", lines[bets + 1])                # IPO

    def test_filings_show_in_a_wider_window(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "300", "--no-moves"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("! 2026-10-07 22:36 IST  RELIANCE    NSE", out)     # marked important
        self.assertIn("  2026-10-14 20:12 IST  RELIANCE    NSE", out)     # a routine update is not
        self.assertNotIn("Copy of Newspaper Publication", out)            # routine noise goes
        self.assertNotIn("2026-10-30", out)                               # results after the clock

    def test_no_filings(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "300", "--no-filings", "--no-moves"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertNotIn(" NSE  ", out)

    def test_off_topic_headlines_are_hidden_and_counted(self):
        args = ["--tickers", "M&M", "--hours", "48", "--no-moves", "--dry-run"]
        later = self.base + ["--now", "2026-11-02T09:00"]
        code, out, err = run(args + later)
        self.assertEqual(code, 0, err)
        self.assertNotIn("Binance", out)
        self.assertIn("Mahindra Farm Equipment", out)
        self.assertIn("M&M Financial Services", out)
        # Binance, Samco's EV list, Alaska, Jio tariffs; "Kotak Mahindra" is the known false friend
        self.assertIn("note: 4 headlines for M&M hidden", out)
        code, out, err = run(args + ["--loose"] + later)
        self.assertIn("Binance", out)

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "M&M", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "RELIANCE"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["M&M", "RELIANCE"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        code, out, err = run(["--json", "--dry-run", "--hours", "24", "--no-moves"] + args
                             + ["--now", "2026-11-02T09:00"])
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"M&M"})
        self.assertFalse(os.path.exists(self.state))

    def test_bad_input_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)
        code, _, err = run(["--tickers", "A?B"] + self.base)
        self.assertEqual(code, 2)
        self.assertIn("not an NSE symbol", err)
        args = list(self.base)
        args[args.index("--now") + 1] = "soon"
        code, _, err = run(["--tickers", "RELIANCE"] + args)
        self.assertEqual(code, 2)
        self.assertIn("--now", err)

    def test_names_come_from_nse_and_are_cached(self):
        state, errors = {}, []
        watch = {"M&M": None, "ZZZZ": None}
        news.fill_names(news.Fetcher(offline=DIR), watch, state, errors)
        self.assertEqual(watch["M&M"], "Mahindra & Mahindra Limited")
        self.assertIsNone(watch["ZZZZ"])
        self.assertEqual(state["names"], {"M&M": "Mahindra & Mahindra Limited"})
        self.assertEqual(news.short_name(watch["M&M"]), "Mahindra & Mahindra")
        # the list is not read again for a cached name
        watch = {"M&M": None}
        news.fill_names(news.Fetcher(offline="/nonexistent"), watch, state, errors)
        self.assertEqual(watch["M&M"], "Mahindra & Mahindra Limited")
        self.assertEqual(errors, [])
        # a list NSE refuses: search on the symbol and say so
        news.fill_names(news.Fetcher(offline="/nonexistent"), {"TCS": None}, state, errors)
        self.assertIn("searching on the symbol", errors[0])

    def test_nse_down_still_gives_headlines(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "news-RELIANCE.xml"), "wb") as fh:
                fh.write(raw("news-RELIANCE.xml"))
            args = list(self.base)
            args[args.index("--offline") + 1] = d
            code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--no-moves", "--dry-run"] + args)
        self.assertEqual(code, 0, err)
        self.assertIn("note: NSE announcements for RELIANCE", out)
        self.assertIn("Jio IPO", out)

    def test_moves(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        self.assertEqual((m["price"], m["prev_close"]), (1164.9, 1167.4))
        self.assertEqual(m["pct"], -0.21)                         # (1164.90 - 1167.40) / 1167.40
        self.assertEqual(news.to_ist(m["time"]).strftime("%Y-%m-%d %H:%M"), "2026-10-30 15:28")
        line = news.render_moves([m])[0]
        self.assertIn("₹1,164.90 -0.21% vs yesterday's close ₹1,167.40 at 15:28 IST", line)
        self.assertNotIn("MOVING", line)
        self.assertIn("MOVING", news.render_moves([m], move_pct=0.2)[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        # no chart file offline: no move line, no error note about it
        code, out, _ = run(["--tickers", "M&M", "--hours", "24", "--dry-run", "--now", "2026-11-02T09:00"]
                           + self.base[:4])
        self.assertNotIn("vs yesterday's close", out)
        self.assertNotIn("price for", out)

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_time(REEL)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


if __name__ == "__main__":
    unittest.main()

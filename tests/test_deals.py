"""trader-deals tests. Fixtures in tests/fixtures/trader-deals/, saved from NSE on 2026-11-02:
bulk.csv and block.csv (session of 2026-10-30), pit-RELIANCE.json (corporates-pit, 20 rows)."""

import csv
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

deals = load("trader-deals", "deals")
DIR = os.path.join(FIX, "trader-deals")


def raw_csv(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def raw_pit():
    with open(os.path.join(DIR, "pit-RELIANCE.json"), encoding="utf-8") as fh:
        return json.load(fh)["data"]


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = deals.main(args)
    return code, out.getvalue(), err.getvalue()


class Parsing(unittest.TestCase):
    def test_every_csv_row_is_read(self):
        for kind in ("bulk", "block"):
            self.assertEqual(len(deals.load_deals(kind, DIR)), len(raw_csv(kind + ".csv")))

    def test_values_and_dates(self):
        r = [x for x in deals.load_deals("bulk", DIR) if x["symbol"] == "AEROFLEX" and x["side"] == "BUY"][0]
        self.assertEqual(r["date"], "2026-10-30")
        self.assertEqual(r["client"], "QE SECURITIES LLP")
        self.assertEqual(r["value_inr"], round(670611 * 540.12, 2))

    def test_in_and_out_tag_matches_the_raw_list(self):
        pairs = {}
        for x in raw_csv("bulk.csv"):
            pairs.setdefault((x["Symbol"], x["Client Name"]), set()).add(x["Buy/Sell"])
        want = sum(1 for v in pairs.values() if len(v) == 2)
        got = {(r["symbol"], r["client"]) for r in deals.load_deals("bulk", DIR) if r["in_and_out"]}
        self.assertEqual(len(got), want)
        self.assertGreater(want, 0)
        self.assertIn(("AEROFLEX", "QE SECURITIES LLP"), got)
        self.assertNotIn(("LOTUSDEV", "SMALLCAP WORLD FUND INC"), got)

    def test_a_quiet_day_and_a_blocked_page(self):
        head = "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n"
        self.assertEqual(deals.parse_deals(head + "NO RECORDS,,,,,,\n", "block"), [])
        with self.assertRaises(deals.DealsError):
            deals.parse_deals("<html>Access Denied</html>", "bulk")

    def test_insider_rows(self):
        rows = deals.load_insiders("RELIANCE", offline=DIR)
        raw = raw_pit()
        self.assertEqual(len(rows), len(raw))
        self.assertEqual(rows[0]["date"], "2026-02-18")
        self.assertEqual(rows[0]["qty"], 2320)
        self.assertEqual(rows[0]["value_inr"], 3294168)
        self.assertEqual(rows[0]["price"], round(3294168 / 2320, 2))
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_only_market_trades_count_as_trades(self):
        rows = deals.load_insiders("RELIANCE", offline=DIR)
        want = sum(1 for x in raw_pit() if x["acqMode"] in ("Market Sale", "Market Purchase"))
        self.assertEqual(sum(1 for r in rows if r["market_trade"]), want)
        gift = [r for r in rows if r["mode"] == "Gift"][0]
        self.assertFalse(gift["market_trade"])
        self.assertIn("(not a market trade)", deals.render(gift))

    def test_bad_insider_json(self):
        with self.assertRaises(deals.DealsError):
            deals.parse_insiders("<html>")


class Symbols(unittest.TestCase):
    def test_clean_symbol(self):
        self.assertEqual(deals.clean_symbol("m&m.ns"), "M&M")
        self.assertEqual(deals.clean_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO")
        for bad in ("", "A B", "x;rm", "-A"):
            with self.assertRaises(deals.DealsError):
                deals.clean_symbol(bad)

    def test_the_insider_url_encodes_the_symbol(self):
        seen = []
        real = deals.download
        deals.download = lambda url, timeout=30: seen.append(url) or '{"data": []}'
        try:
            deals.load_insiders("M&M")
            deals.load_insiders(None, "01-01-2026", "01-03-2026")
        finally:
            deals.download = real
        self.assertIn("symbol=M%26M", seen[0])
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[1]).query)
        self.assertEqual((q["from_date"], q["to_date"]), (["01-01-2026"], ["01-03-2026"]))

    def test_nse_needs_a_browser_agent_and_referer(self):
        self.assertIn("Chrome", deals.HEADERS["User-Agent"])
        self.assertEqual(deals.HEADERS["Referer"], "https://www.nseindia.com/")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def portfolio(self, *tickers):
        os.makedirs("trader", exist_ok=True)
        with open(os.path.join("trader", "portfolio.json"), "w") as fh:
            json.dump({"cash": 1000, "positions": [{"ticker": t, "shares": 1} for t in tickers]}, fh)

    def test_show_filters_by_kind_symbol_and_client(self):
        code, out, _ = run(["show", "--offline", DIR, "--kind", "block", "--client", "goldman", "--json"])
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual([r["client"] for r in rows], ["GOLDMAN SACHS BANK EUROPE SE"])
        self.assertEqual(rows[0]["qty"], 10550000)
        want = sum(1 for n in ("bulk.csv", "block.csv") for x in raw_csv(n) if x["Symbol"] == "SWIGGY")
        code, out, _ = run(["show", "--offline", DIR, "--symbol", "swiggy", "--json"])
        self.assertEqual(len(json.loads(out)), want)
        code, out, _ = run(["show", "--offline", DIR, "--symbol", "NOSUCH"])
        self.assertEqual(code, 1)
        self.assertIn("No matching deals", out)

    def test_show_marks_held_and_says_your_call(self):
        self.portfolio("SWIGGY")
        code, out, _ = run(["show", "--offline", DIR, "--kind", "block"])
        self.assertIn("HELD 2026-10-30  block    SWIGGY", out)
        self.assertNotIn("HELD 2026-10-30  block    TMCV", out)
        self.assertIn("Your call", out)

    def test_insiders_warns_when_the_feed_looks_old(self):
        code, out, _ = run(["insiders", "--symbol", "RELIANCE", "--offline", DIR, "--today", "2026-11-02"])
        self.assertEqual(code, 0)
        self.assertIn("newest insider filing here is dated 2026-02-18", out)
        code, out, _ = run(["insiders", "--symbol", "RELIANCE", "--offline", DIR, "--today", "2026-03-01"])
        self.assertNotIn("newest insider filing", out)

    def test_insiders_argument_errors(self):
        self.assertIn("needs --symbol", run(["insiders"])[2])
        self.assertIn("go together", run(["insiders", "--from", "2026-01-01"])[2])
        self.assertIn("wants a date", run(["insiders", "--from", "soon", "--to", "2026-01-01"])[2])
        self.assertIn("no offline file", run(["show", "--offline", "missing"])[2])

    def test_new_alerts_once_then_only_on_a_new_row(self):
        shutil.copy(os.path.join(DIR, "bulk.csv"), "bulk.csv")
        shutil.copy(os.path.join(DIR, "block.csv"), "block.csv")
        args = ["new", "--offline", ".", "--today", "2026-10-31", "--state", "s.json"]
        code, out, _ = run(args)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("NEW "), len(raw_csv("bulk.csv")) + len(raw_csv("block.csv")))
        code, out, _ = run(args)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new", out)
        with open("block.csv", "a") as fh:
            fh.write("31-OCT-2026,TMCV,Tata Motors Limited,TEST CLIENT,BUY,100,397.00\n")
        code, out, _ = run(args)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("NEW "), 1)
        self.assertIn("TEST CLIENT", out)

    def test_new_first_run_skips_old_rows_and_dry_run_saves_nothing(self):
        args = ["new", "--offline", DIR, "--today", "2027-01-15", "--state", "s.json", "--dry-run"]
        code, out, _ = run(args)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("s.json"))

    def test_new_reads_insiders_for_held_stocks(self):
        self.portfolio("RELIANCE")
        code, out, _ = run(["new", "--offline", DIR, "--today", "2026-03-01", "--state", "s.json"])
        self.assertEqual(code, 0)
        self.assertIn("NEW HELD 2026-02-18  insider  RELIANCE", out)
        self.assertIn("BALANADU NARAYAN", out)

    def test_new_filter_keeps_held_stocks(self):
        self.portfolio("TMCV")
        code, out, _ = run(["new", "--offline", DIR, "--client", "goldman", "--today", "2026-10-31",
                            "--state", "s.json", "--json"])
        syms = {r["symbol"] for r in json.loads(out)["new"]}
        want = {x["Symbol"] for n in ("bulk.csv", "block.csv") for x in raw_csv(n)
                if x["Symbol"] == "TMCV" or "goldman" in x["Client Name"].lower()}
        self.assertEqual(syms, want)
        self.assertIn("TMCV", syms)

    def test_new_reports_a_failed_source_and_keeps_going(self):
        os.mkdir("partial")
        shutil.copy(os.path.join(DIR, "bulk.csv"), "partial")
        code, out, err = run(["new", "--offline", "partial", "--today", "2026-10-31", "--state", "s.json"])
        self.assertEqual(code, 0)
        self.assertIn("no offline file", err)
        self.assertIn("NEW ", out)
        os.mkdir("empty")
        code, _, err = run(["new", "--offline", "empty", "--today", "2026-10-31", "--state", "t.json"])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists("t.json"))

    def test_broken_state_and_portfolio_are_plain_errors(self):
        with open("s.json", "w") as fh:
            fh.write("{nope")
        code, _, err = run(["new", "--offline", DIR, "--state", "s.json"])
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        os.mkdir("trader")
        with open(os.path.join("trader", "portfolio.json"), "w") as fh:
            fh.write("[1]")
        code, _, err = run(["show", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("not a valid portfolio", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md_and_no_leftovers(self):
        with open(os.path.join(ROOT, "trader-deals", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-deals\n"))
        low = " ".join(text.lower().split())
        for must in ("after the session ends", "in and out", "not a market trade", "nothing in this pack places an order",
                     "looks behind", "user decides"):
            self.assertIn(must, low)
        with open(os.path.join(ROOT, "trader-deals", "deals.py"), encoding="utf-8") as fh:
            code = fh.read()
        for never in ("subprocess", "pdftotext", "Congress", "Pelosi", "STOCK Act"):
            self.assertNotIn(never, code + text)


if __name__ == "__main__":
    unittest.main()

"""trader-analyst tests. Fixtures in tests/fixtures/trader-analyst/, all real, fetched Nov 2 2026:

timeseries-<SYM>.json  Yahoo fundamentals-timeseries. RELIANCE and M&M skip the Sep 2025
                       quarter; HDFCBANK has two quarters and no gross or operating lines.
ann-<SYM>.json         NSE corporate announcements, the results rows
nsefin-<SYM>.json      NSE corporates-financial-results, newest rows (stops at Dec 2024)
"""

import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

facts = load("trader-analyst", "facts")
DIR = os.path.join(FIX, "trader-analyst")


def doc(sym):
    with open(os.path.join(DIR, "timeseries-%s.json" % sym), encoding="utf-8") as fh:
        return json.load(fh)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


class Reliance(unittest.TestCase):
    def setUp(self):
        self.r = facts.analyse(doc("RELIANCE"), quarters=8, name="Reliance Industries Limited")

    def test_latest_quarter(self):
        q = self.r["rows"][0]
        self.assertEqual(q["end"], "2026-06-30")
        self.assertEqual(q["fiscal"], "Q1 FY27")
        self.assertEqual(q["revenue"], 3094680000000)
        self.assertEqual(q["net_income"], 209460000000)
        self.assertEqual(q["eps_diluted"], 15.48)
        self.assertEqual(q["revenue_yoy"], 27.0)     # vs 2,43,632 crore in Jun 2025
        self.assertEqual(q["net_income_yoy"], -22.4)  # vs 26,994 crore
        self.assertEqual(q["eps_yoy"], -22.4)        # vs 19.95
        self.assertEqual(q["gross_margin"], 26.8)    # 83,004 / 3,09,468
        self.assertEqual(q["operating_margin"], 10.5)  # 32,417 / 3,09,468
        self.assertEqual(q["net_margin"], 6.8)       # 20,946 / 3,09,468

    def test_the_missing_quarter_is_named(self):
        self.assertEqual([r["end"] for r in self.r["rows"]],
                         ["2026-06-30", "2026-03-31", "2025-12-31", "2025-06-30", "2025-03-31"])
        self.assertEqual(self.r["missing"], ["2025-09-30"])
        self.assertIn("GAP: Yahoo has no numbers for the quarter ending 2025-09-30", facts.gap_notes(self.r)[0])

    def test_no_year_on_year_without_the_year_ago_quarter(self):
        by = {r["end"]: r for r in self.r["rows"]}
        for end in ("2025-12-31", "2025-06-30", "2025-03-31"):
            for key in ("revenue_yoy", "net_income_yoy", "eps_yoy"):
                self.assertIsNone(by[end][key], end)
        self.assertEqual(self.r["no_year_ago"], ["2025-12-31", "2025-06-30", "2025-03-31"])
        self.assertEqual(by["2026-03-31"]["revenue_yoy"], 12.5)  # 2,94,059 vs 2,61,388
        self.assertIn("No year-on-year for 2025-12-31, 2025-06-30, 2025-03-31", " ".join(facts.gap_notes(self.r)))

    def test_no_trailing_twelve_months_across_a_gap(self):
        self.assertEqual(self.r["ttm"], {})
        self.assertIn("Last 4 quarters not shown", " ".join(facts.gap_notes(self.r)))

    def test_trailing_twelve_months_when_four_are_consecutive(self):
        def block(key, vals):
            return {"meta": {"type": [key]}, key: [
                {"asOfDate": e, "periodType": "3M", "reportedValue": {"raw": v}} for e, v in vals]}
        ends = ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
        d = {"timeseries": {"result": [block("quarterlyTotalRevenue", [(e, 100.0) for e in ends]),
                                       block("quarterlyDilutedEPS", [(e, 1.25) for e in ends])]}}
        r = facts.analyse(d)
        self.assertEqual(r["ttm"], {"revenue": 400.0, "eps_diluted": 5.0})
        self.assertEqual(r["missing"], [])
        self.assertNotIn("Last 4", " ".join(facts.gap_notes(r)))

    def test_what_changed_in_words(self):
        lines = facts.changes(self.r)
        self.assertIn("revenue ₹3,09,468 crore (+27.0% vs a year earlier)", lines[0])
        self.assertIn("net income ₹20,946 crore (-22.4%)", lines[0])
        self.assertIn("diluted EPS ₹15.48 (-22.4%)", lines[0])
        self.assertTrue(any(l.startswith("Gross margin 26.8% (+0.2 points") for l in lines))  # 26.6 in March

    def test_a_skipped_quarter_is_not_the_quarter_before(self):
        rows = [r for r in self.r["rows"] if r["end"] <= "2025-12-31"]    # next row is Jun 2025: 184 days back
        lines = facts.changes({"rows": rows})
        self.assertFalse(any("vs the quarter before" in l for l in lines))

    def test_numbers_lag_results_note(self):
        f = facts.latest_filings(facts.Fetcher(offline=DIR), "RELIANCE", today=datetime.date(2026, 11, 2))
        self.assertEqual(f["results"][0]["period"], "2026-09-30")
        self.assertEqual(f["results"][0]["filed"], "2026-10-30")
        # two PDFs for one period, one line
        self.assertEqual([x["period"] for x in f["results"]],
                         ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30",
                          "2025-03-31"])
        self.assertIn("Yahoo's numbers stop at 2026-06-30, but NSE shows results for the period ending "
                      "2026-09-30, filed 2026-10-30", facts.stale_note(self.r, f))
        self.assertEqual(f["xbrl"]["period"], "2024-12-31")
        self.assertEqual(f["xbrl"]["basis"], "Consolidated")
        self.assertEqual(f["xbrl"]["filed"], "2025-01-16")
        self.assertTrue(f["xbrl"]["url"].endswith("INDAS_117297_1348248_16012025081520.xml"))


class Filings(unittest.TestCase):
    def test_period_end_formats(self):
        pe = facts.period_end
        self.assertEqual(pe("results for the quarter and half year ended September 30, 2026."), "2026-09-30")
        self.assertEqual(pe("financial results for the period ended 30 June 2026."), "2026-06-30")
        self.assertEqual(pe("ended 31st March, 2026"), "2026-03-31")
        self.assertIsNone(pe("Outcome of Board Meeting held on July 30, 2026."))
        self.assertIsNone(pe(""))

    def test_only_results_rows_count(self):
        rows = [{"desc": "Press Release", "an_dt": "30-Oct-2026 19:43:24",
                 "attchmntText": "financial results for the quarter ended September 30, 2026"},
                {"desc": "Outcome of Board Meeting", "an_dt": "29-Oct-2026 10:00:00",
                 "attchmntText": "Appointment of Secretarial Auditor"},
                {"desc": "Financial Result Updates", "an_dt": "bad date",
                 "attchmntText": "financial results for the period ended March 31, 2026"}]
        self.assertEqual(facts.results_filings(rows), [])

    def test_no_note_when_yahoo_is_current(self):
        r = facts.analyse(doc("M&M"))
        f = facts.latest_filings(facts.Fetcher(offline=DIR), "M&M")
        self.assertEqual(f["results"][0]["period"], "2026-06-30")
        self.assertIsNone(facts.stale_note(r, f))

    def test_nse_refusing_keeps_the_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "timeseries-M&M.json"), "w", encoding="utf-8") as fh:
                json.dump(doc("M&M"), fh)
            code, out, err = run(["M&M", "--offline", d])
        self.assertEqual(code, 0, err)
        self.assertIn("note: NSE announcements:", out)
        self.assertIn("note: NSE results list:", out)
        self.assertIn("57,533", out)


class Helpers(unittest.TestCase):
    def test_crore_uses_indian_groups(self):
        self.assertEqual(facts.crore(3094680000000), "3,09,468")
        self.assertEqual(facts.crore(209460000000), "20,946")
        self.assertEqual(facts.crore(1234567 * 1e7), "12,34,567")
        self.assertEqual(facts.crore(500 * 1e7), "500")
        self.assertEqual(facts.crore(-20946 * 1e7), "-20,946")
        self.assertEqual(facts.crore(None), "-")

    def test_fiscal_labels_run_april_to_march(self):
        self.assertEqual([facts.fiscal_label(e) for e in ("2026-06-30", "2026-09-30", "2026-12-31", "2027-03-31")],
                         ["Q1 FY27", "Q2 FY27", "Q3 FY27", "Q4 FY27"])
        self.assertEqual(facts.fiscal_label("2026-05-31"), "")

    def test_missing_quarters_across_a_year_boundary(self):
        self.assertEqual(facts.missing_quarters(["2026-06-30", "2025-09-30"]), ["2026-03-31", "2025-12-31"])
        self.assertEqual(facts.missing_quarters(["2026-03-31", "2025-12-31"]), [])
        self.assertEqual(facts.missing_quarters(["2026-03-31"]), [])

    def test_symbols(self):
        self.assertEqual(facts.yahoo_symbol("m&m.ns"), "M%26M.NS")
        self.assertEqual(facts.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        with self.assertRaises(facts.FactsError):
            facts.clean_ticker("A?B")

    def test_one_offs_are_flagged(self):
        result = {"rows": [
            {"end": "2026-06-30", "revenue": 100.0, "net_income": 90.0,
             "operating_income": 30.0, "eps_diluted": 1.0, "revenue_yoy": 10.0,
             "net_income_yoy": 200.0, "eps_yoy": 200.0, "gross_margin": None,
             "operating_margin": 30.0, "net_margin": 90.0}]}
        self.assertTrue(any("above operating income" in l for l in facts.changes(result)))


class Bank(unittest.TestCase):
    def test_thin_data_stays_blank(self):
        r = facts.analyse(doc("HDFCBANK"))
        self.assertEqual([x["end"] for x in r["rows"]], ["2025-06-30", "2025-03-31"])
        q = r["rows"][0]
        self.assertIsNone(q["gross_margin"])
        self.assertIsNone(q["operating_margin"])
        self.assertIsNone(r["rows"][1]["eps_diluted"])    # Yahoo sent one EPS only
        self.assertEqual(q["net_margin"], 19.0)           # 16,258 / 85,346
        self.assertFalse(any(l.startswith("Gross margin") for l in facts.changes(r)))

    def test_empty_answer_is_an_error(self):
        with self.assertRaises(facts.FactsError):
            facts.analyse({"timeseries": {"result": [{"meta": {"type": ["quarterlyTotalRevenue"]},
                                                      "quarterlyTotalRevenue": []}]}})


class Cli(unittest.TestCase):
    def test_report_and_metrics_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            code, out, err = run(["RELIANCE", "M&M", "--offline", DIR, "--metrics", path])
            self.assertEqual(code, 0, err)
            with open(path) as fh:
                data = json.load(fh)
        self.assertIn("Reliance Industries Limited (RELIANCE). ₹ crore except EPS.", out)
        self.assertIn("NOTE: Yahoo's numbers stop at 2026-06-30", out)
        self.assertIn("Results filing: period 2026-09-30, filed 2026-10-30", out)
        self.assertIn("wrote", out)
        self.assertNotIn(chr(0x2014), out)
        rows = {r["ticker"]: r for r in data["companies"]}
        self.assertEqual(sorted(rows), ["M&M", "RELIANCE"])
        self.assertEqual(rows["RELIANCE"]["revenue_growth_yoy"], 27.0)
        self.assertEqual(rows["M&M"]["eps_growth_yoy"], 33.4)   # 48.59 vs 36.43
        self.assertIsNone(rows["RELIANCE"]["revenue_ttm"])      # no four consecutive quarters
        self.assertEqual(rows["RELIANCE"]["currency"], "INR")

    def test_unknown_and_bad_symbols(self):
        code, _, err = run(["ZZZZ", "--offline", DIR, "--no-filings"])
        self.assertEqual(code, 2)
        self.assertIn("ZZZZ", err)
        code, _, err = run(["A?B", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("not an NSE symbol", err)
        code, out, err = run(["RELIANCE", "ZZZZ", "--offline", DIR, "--no-filings"])
        self.assertEqual(code, 1)                               # one good, one failed
        self.assertIn("Q1 FY27", out)

    def test_json_output(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--json", "--quarters", "4"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data[0]["result"]["rows"]), 4)
        self.assertEqual(data[0]["result"]["missing"], ["2025-09-30"])
        self.assertIn("2026-09-30", data[0]["note"])
        self.assertTrue(data[0]["gaps"])


if __name__ == "__main__":
    unittest.main()

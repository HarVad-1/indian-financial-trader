"""trader-portfolio tests. Holdings here are SYNTHETIC; field names follow common export and API shapes."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load

portfolio = load("trader-portfolio", "portfolio")
check = load("trader-risk", "check")
ticket = load("trader-ticket", "ticket")

NESTED = {"status": "SUCCESS", "payload": {"holdings": [
    {"trading_symbol": "RELIANCE", "isin": "INE002A01018", "quantity": 10, "average_price": 1100.0},
    {"trading_symbol": "M&M", "quantity": 5, "average_price": 3000, "ltp": 3200},
    {"trading_symbol": "SOLD", "quantity": 0, "average_price": 50}]}}
CSV_EXPORT = ("Holdings statement\nClient,XYZ\n\n"
              "Stock Name,ISIN,Quantity,Average buy price,Closing price\n"
              '"Reliance Industries",INE002A01018,10,"1,100.00","1,227.80"\n'
              '"Big Holdings",INE000000000,"1,00,000",10,12\n'
              "Total,,,,\n")


class Work(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, content):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content if isinstance(content, str) else json.dumps(content))
        return p

    def run_it(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = portfolio.main(list(argv) + ["--prices", self.prices if hasattr(self, "prices") else self.none])
        return code, out.getvalue(), err.getvalue()


class Summary(Work):
    def setUp(self):
        super().setUp()
        self.prices = self.file("prices.json", {"prices": {"RELIANCE": 1227.80}})

    def test_nested_json_value_pnl_and_weights(self):
        code, text, _ = self.run_it(self.file("h.json", NESTED), "--json")
        self.assertEqual(code, 0)
        s = json.loads(text)
        # RELIANCE 10 x 1100 -> 10 x 1227.80; M&M 5 x 3000 -> 5 x 3200
        self.assertEqual((s["invested"], s["value"], s["pnl"], s["pnl_pct"]), (26000.0, 28278.0, 2278.0, 8.76))
        self.assertEqual([(r["ticker"], r["value"], r["pnl"], r["weight_pct"]) for r in s["positions"]],
                         [("M&M", 16000.0, 1000.0, 56.58), ("RELIANCE", 12278.0, 1278.0, 43.42)])
        self.assertEqual(s["warnings"], [])

    def test_report_words(self):
        code, text, _ = self.run_it(self.file("h.json", NESTED), "--cash", "100000")
        self.assertIn("Invested ₹26,000. Value ₹28,278.", text)
        self.assertIn("P&L ₹2,278 (+8.76%)", text)
        self.assertIn("Cash ₹1,00,000.", text)
        self.assertIn("FLAG: M&M is 56.6% of your holdings (over 25%)", text)
        self.assertIn("not a recommendation", text)
        for never in (" should ", "recommend buy", "sell it"):
            self.assertNotIn(never, text)

    def test_concentration_flags(self):
        h = [{"ticker": t, "label_only": False, "shares": 1, "avg": v, "ltp": v}
             for t, v in (("A", 24.0), ("B", 24.0), ("C", 24.0), ("D", 14.0), ("E", 14.0))]
        s = portfolio.summarise(h)
        self.assertEqual(s["flags"], ["your top 3 holdings are 72.0% of the total (over 60%)"])
        self.assertEqual(portfolio.summarise(h, max_top3=80)["flags"], [])
        self.assertEqual(len(portfolio.summarise(h, max_weight=20)["flags"]), 4)

    def test_no_price_is_valued_at_cost_and_says_so(self):
        h = [{"ticker": "ITC", "label_only": False, "shares": 2, "avg": 400.0, "ltp": None}]
        s = portfolio.summarise(h)
        self.assertEqual((s["value"], s["positions"][0]["pnl"], s["pnl_covers"]), (800.0, None, 0))
        self.assertIn("valued at cost", s["warnings"][0])


class Csv(Work):
    def test_title_lines_indian_commas_totals_and_names(self):
        code, text, _ = self.run_it(self.file("h.csv", CSV_EXPORT), "--json")
        self.assertEqual(code, 0)
        s = json.loads(text)
        self.assertEqual([(r["ticker"], r["shares"]) for r in s["positions"]],
                         [("Big Holdings", 100000.0), ("Reliance Industries", 10.0)])
        self.assertEqual(s["positions"][0]["value"], 1200000.0)
        self.assertEqual(s["positions"][1]["pnl"], 1278.0)
        self.assertIn("is a name, not an NSE symbol", " ".join(s["warnings"]))

    def test_a_map_names_the_columns(self):
        p = self.file("m.csv", "scrip_name,held,cost\nITC,2,400\n")
        code, text, _ = self.run_it(p, "--map", "symbol=scrip_name,qty=held,avg=cost", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["positions"][0]["ticker"], "ITC")
        code, _, err = self.run_it(p, "--map", "symbol=nope,qty=held,avg=cost")
        self.assertEqual(code, 1)
        self.assertIn("no such column", err)
        code, _, err = self.run_it(p, "--map", "colour=red")
        self.assertIn("--map wants", err)


class Errors(Work):
    def expect(self, path, *words):
        code, _, err = self.run_it(path)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        for w in words:
            self.assertIn(w, err)

    def test_unknown_columns_name_what_was_seen(self):
        self.expect(self.file("a.csv", "foo,bar\n1,2\n"), "Columns seen: foo, bar", "--map")
        self.expect(self.file("b.json", [{"foo": 1, "bar": 2}]), "Columns seen: foo, bar")
        self.expect(self.file("c.json", [{"symbol": "ITC", "quantity": 1}]), "an average price")

    def test_bad_files(self):
        self.expect(self.none, "no holdings file")
        self.expect(self.file("d.json", "{nope"), "not valid JSON")
        self.expect(self.file("e.json", {"status": "ok"}), "no list of holdings")
        self.expect(self.file("f.json", {"holdings": []}), "no holdings")
        self.expect(self.file("g.xlsx", "x"), "save it as CSV")
        self.expect(self.file("h.json", [{"symbol": "ITC", "quantity": "many", "average_price": 1}]), "must be numbers")
        self.expect(self.file("i.json", [{"symbol": "ITC", "quantity": -1, "average_price": 1}]), "not negative")
        self.expect(self.file("j.json", [{"symbol": "ITC", "quantity": 0, "average_price": 1}]), "zero quantity")


class Write(Work):
    def test_write_needs_cash(self):
        out = os.path.join(self.d, "trader", "portfolio.json")
        code, _, err = self.run_it(self.file("h.json", NESTED), "--write", out)
        self.assertEqual(code, 1)
        self.assertIn("--write needs your cash", err)
        self.assertFalse(os.path.exists(out))

    def test_the_file_is_the_shape_the_other_tools_read(self):
        out = os.path.join(self.d, "trader", "portfolio.json")
        code, _, _ = self.run_it(self.file("h.json", NESTED), "--cash", "50000", "--write", out)
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 50000.0)
        self.assertEqual(pf["positions"][1]["ticker"], "RELIANCE")
        self.assertEqual(pf["positions"][1]["lots"][0]["cost"], 11000.0)

        # trader-risk: a sell of what you hold is an exit, more than that is a short
        sell = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "qty": 10, "product": "CNC", "type": "market"}
        now = check.parse_now("2026-10-19 10:00")
        self.assertEqual(check.check([sell], {}, portfolio=pf, now=now)["results"][0]["kind"], "exit")
        r = check.check([dict(sell, qty=11)], {}, portfolio=pf, now=now)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))

        # trader-ticket: a fill relieves the imported lot
        after = ticket.apply_fill(out, {}, "RELIANCE", "sell", 10, 1227.80, "2026-10-19")
        self.assertEqual(after["cash"], 62278.0)
        self.assertEqual([p["ticker"] for p in after["positions"]], ["M&M"])

    def test_cash_is_kept_from_the_existing_file_or_read_from_the_answer(self):
        out = self.file("portfolio.json", {"cash": 1234.5, "positions": []})
        code, _, _ = self.run_it(self.file("h.json", NESTED), "--write", out)
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["cash"], 1234.5)
        doc = dict(NESTED, available_cash="2,500")
        code, text, _ = self.run_it(self.file("h2.json", doc), "--json")
        self.assertEqual(json.loads(text)["cash"], 2500.0)


if __name__ == "__main__":
    unittest.main()

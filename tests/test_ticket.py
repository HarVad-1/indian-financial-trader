import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-ticket", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
PLAN_PATH = os.path.join(FIX, "trader-budget", "plan-reliance.json")
NOW = "2026-10-19 09:50"
TODAY = "2026-10-19"
RELIANCE_PLAIN = ("BUY 40 RELIANCE on NSE, Intraday (MIS), market / stop-loss 1215.35 / target 1240.25 / max loss "
                  "about ₹498 if the stop fills at 1215.35 (it can fill worse in a fast market)")


def reliance(**over):
    with open(ORDERS_PATH, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class NeverPlaces(unittest.TestCase):
    def test_no_order_tool_anywhere(self):
        with open(os.path.join(ROOT, "trader-ticket", "ticket.py"), encoding="utf-8") as fh:
            src = fh.read()
        for never in ("place_stock_order", "Alpaca", "urllib", "socket", "subprocess", "requests"):
            self.assertNotIn(never, src)


class TicketShape(unittest.TestCase):
    def test_plain_words(self):
        self.assertEqual(ticket.plain(reliance()), RELIANCE_PLAIN)
        sell = {"symbol": "ITC", "exchange": "NSE", "side": "sell", "qty": 1, "product": "CNC", "type": "limit",
                "entry": 405}
        self.assertEqual(ticket.plain(sell), "SELL 1 ITC on NSE, Delivery (CNC), limit 405.00")
        lim = reliance(type="limit", entry=1225.0)
        self.assertEqual(ticket.plain(lim).split(" / ")[0], "BUY 40 RELIANCE on NSE, Intraday (MIS), limit 1225.00")
        self.assertEqual(ticket.max_loss(lim), (386.0, 1215.35))     # 40 x 9.65

    def test_max_loss_without_a_plan_or_a_stop(self):
        o = reliance()
        o.pop("plan")
        self.assertEqual(ticket.max_loss(o), (498.0, 1215.35))
        self.assertEqual(ticket.max_loss({"symbol": "ITC", "qty": 2}), (None, None))

    def test_rupee_grouping(self):
        self.assertEqual(ticket._money(100000), "₹1,00,000")
        self.assertEqual(ticket._money(1227.85), "₹1,227.85")


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "ticket.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)

    def test_before_the_risk_check_it_says_so(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT CHECKED", text)

    def test_show_prints_what_to_enter_and_nothing_else_happens(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("to enter yourself in Groww. Nothing here places it.", text)
        for part in RELIANCE_PLAIN.split(" / "):
            self.assertIn("  " + part + "\n", text)
        self.assertIn("trader-risk PASS", text)
        self.assertIn("not a recommendation", text)
        self.assertIn("your call", text)
        self.assertIn("set them in Groww yourself", text)
        self.assertFalse(os.path.exists(self.state))
        out = json.loads(run(self.args("show", "--index", "1", "--json"))[1])
        self.assertEqual((out["order"]["qty"], out["order"]["product"], out["broker"]), (40, "MIS", "groww"))
        self.assertEqual((out["risk_passed"], out["max_loss"]), (True, 498.0))
        self.assertNotIn("plan", out["order"])

    def test_placing_is_recorded_and_risk_counts_it(self):
        self.run_risk()
        code, text, _ = run(self.args("placed", "--index", "1", "--filled-qty", "40", "--filled-price", "1227.85",
                                      "--portfolio", self.pf, now="2026-10-19 09:51"))
        self.assertEqual(code, 0)
        self.assertIn("you placed order 1 in Groww", text)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["placed", "filled"])
        rec = st["orders"][0]
        self.assertEqual((rec["kind"], rec["max_loss"], rec["date"], rec["risk"]), ("entry", 498.0, TODAY, "PASS"))
        self.assertEqual(rec["at"], "2026-10-19T09:51:00+05:30")
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 50886.0)       # 1,00,000 - 40 x 1227.85
        self.assertEqual(pf["positions"][0]["shares"], 40)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("RECORDED: placed", text)
        code, _, err = run(self.args("placed", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("already recorded", err)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss ₹498", text)

        # trader-risk now counts it: a second RELIANCE buy today is a duplicate, and ₹498 is used
        res = check.check([reliance()], {"budget": 100000}, state=st, now=check.parse_now("2026-10-19 10:30"))
        self.assertIn("already recorded placing", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 498.0))

    def test_flatten_by_the_close(self):
        self.run_risk()
        run(self.args("placed", "--index", "1", "--filled-qty", "40", "--filled-price", "1227.85",
                      "--portfolio", self.pf))
        late = "2026-10-19 15:10"
        code, text, _ = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", PLAN_PATH, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 40 RELIANCE on NSE, Intraday (MIS), market (flat by 15:10 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("square-off", text)
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        o = doc["orders"][0]
        self.assertEqual((o["side"], o["qty"], o["product"], o["type"]), ("sell", 40, "MIS", "market"))
        self.assertEqual(self.run_risk(now=late), 0)       # after the 15:00 entry cutoff: an exit still passes
        with open(self.risk, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        code, _, _ = run(self.args("placed", "--index", "1", "--filled-qty", "40", "--filled-price", "1235.10",
                                   "--portfolio", self.pf, now="2026-10-19 15:11"))
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"]), (100290.0, []))     # 50,886 + 40 x 1235.10
        code, _, err = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_with_the_qty_from_groww(self):
        code, text, _ = run(["flatten", "--symbol", "m&m.ns", "--qty", "8", "--product", "CNC", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("SELL 8 M&M on NSE, Delivery (CNC), market", text)
        self.assertNotIn("Check it against Groww", text)
        self.assertEqual(run(["flatten", "--symbol", "ITC", "--qty", "0", "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_the_one_checked(self):
        self.run_risk()
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = 41
        with open(self.orders, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-16 09:50")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_a_blocked_order_shows_why_and_the_log_stays_true(self):
        self.assertEqual(self.run_risk(now="2026-10-19 09:25"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-19 09:25"))
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, text, _ = run(self.args("placed", "--index", "1", now="2026-10-19 09:26"))
        self.assertEqual(code, 0)
        self.assertIn("trader-risk blocked this order", text)
        self.assertEqual(self.state_doc()["orders"][0]["risk"], "BLOCK")

    def test_skipped_is_logged_and_does_not_count(self):
        self.run_risk()
        code, text, _ = run(self.args("skipped", "--index", "1", "--note", "spread too wide"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("RECORDED: skipped", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn('note "spread too wide"', text)
        self.assertIn("0 placed today", text)

    def test_a_fill_needs_both_numbers(self):
        self.run_risk()
        code, _, err = run(self.args("placed", "--index", "1", "--filled-qty", "40"))
        self.assertEqual(code, 1)
        self.assertIn("both", err)
        self.assertFalse(os.path.exists(self.state))

    def test_bad_index(self):
        code, _, err = run(self.args("placed", "--index", "9"))
        self.assertEqual(code, 1)
        self.assertIn("no order 9", err)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 3, "lots": [
            {"id": "ITC-1", "date": "2025-01-02", "shares": 2, "cost": 700.0},
            {"id": "ITC-2", "date": "2026-01-02", "shares": 1, "cost": 400.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "2.5", "--price", "420",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 1050.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])
        self.assertEqual(lots[0]["shares"], 0.5)
        self.assertEqual(lots[0]["cost"], 200.0)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "5", "--price", "420",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()

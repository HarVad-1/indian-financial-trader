import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
NOW = "2026-10-19 09:50"
TODAY = "2026-10-19"
# RELIANCE is the reel's close; the others are SYNTHETIC round numbers, for the arithmetic only
PRICES = {"RELIANCE": 1227.80, "HDFCBANK": 1000.0, "TCS": 3000.0, "INFY": 1500.0, "ITC": 400.0}
INLINE = "RELIANCE=30,HDFCBANK=25,TCS=20,INFY=15,ITC=10"
# the shipped config, key for key
CONTRACT_CFG = {
    "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000, "min_order_inr": 500,
    "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "15:00", "flat_by": "15:10",
    "broker": "groww", "blocked": [], "allow_options": False, "allow_margin": False, "allow_short": False,
}
# a long-term split's rules, for the weights mode tests
SPLIT = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 40000, "min_order_inr": 500,
         "blocked": []}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path=TARGETS):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def rupees(result):
    return {p["ticker"]: p["rupees"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-19T04:37:13Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"RELIANCE": price},
            "detail": {"RELIANCE": {"time": quote_time, "source": "yahoo"}}}


class TheReel(unittest.TestCase):
    """₹1,00,000, 10% buffer, ₹500 max loss, RELIANCE risk 12.45 -> 40 shares, ₹49,112, max loss ₹498."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_reliance_is_40_shares_bound_by_the_max_loss(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 40)
        self.assertEqual(s["bound_by"], "max loss")
        self.assertEqual(s["risk_per_share"], 12.45)
        self.assertEqual(s["cost"], 49112.0)
        self.assertEqual(s["max_loss"], 498.0)
        self.assertEqual(s["reward_risk"], 1.0)
        self.assertEqual(s["cash_buffer"], 10000)
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 40), ("position cap", 61), ("max order", 61), ("cash", 73)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["max loss"])
        self.assertIsNone(s["refusal"])

    def test_the_order_has_plain_fields(self):
        o = size.trade_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": 40, "product": "MIS", "type": "market",
            "entry": 1227.8, "stop": 1215.35, "target": 1240.25,
            "plan": {"risk_per_share": 12.45, "max_loss": 498.0, "setup": "orb", "entry_time": "9:50 AM",
                     "flat_by": "15:10"}})
        self.assertIn("bound by your ₹500 max loss", why)
        self.assertIn("₹498", why)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(os.path.join(FIX, "trader-risk", "orders-reliance.json"), encoding="utf-8") as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.trade_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_a_limit_order(self):
        o = size.trade_order(size.size_trade(plan(), cfg(), limit=1227.80, today=TODAY))
        self.assertEqual((o["type"], o["entry"], o["qty"]), ("limit", 1227.8, 40))
        s = size.size_trade(plan(), cfg(), limit=1225.00, today=TODAY)
        self.assertEqual((s["risk_per_share"], s["qty"], s["max_loss"]), (9.65, 51, 492.15))
        self.assertEqual(size.trade_order(s)["entry"], 1225.0)

    def test_product(self):
        self.assertEqual(size.trade_order(size.size_trade(plan(), cfg(), today=TODAY, product="CNC"))["product"], "CNC")
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(), cfg(), today=TODAY, product="BO")

    def test_an_ns_suffix_is_stripped(self):
        self.assertEqual(size.size_trade(plan(symbol="reliance.ns"), cfg(), today=TODAY)["symbol"], "RELIANCE")

    def test_indian_digit_grouping(self):
        for x, want in ((100000, "₹1,00,000"), (49112, "₹49,112"), (498, "₹498"), (12345678.5, "₹1,23,45,678.50"),
                        (1000, "₹1,000"), (-150000, "-₹1,50,000")):
            self.assertEqual(size.money(x), want)

    def test_cli_writes_one_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 40 shares of RELIANCE = ₹49,112 at 1227.80", text)
            self.assertIn("40 x 12.45 = ₹498", text)
            self.assertIn("<- binds", text)
            self.assertIn("not financial advice", text)
            self.assertIn("your call", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertNotIn("paper", doc)
            self.assertEqual(doc["created_at"], "2026-10-19T04:20:00Z")
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 498.0)

    def test_cli_json_limit_and_product(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--product", "CNC", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 40)
        self.assertEqual((out["orders"][0]["type"], out["orders"][0]["product"]), ("limit", "CNC"))


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=700.0), cfg(), today=TODAY)       # one share risks 527.80
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your ₹500 max loss per trade", s["refusal"])
        self.assertIsNone(size.trade_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(plan(stop=700.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        self.assertEqual(size.size_trade(plan(), cfg(max_order_inr=20000), today=TODAY)["bound_by"], "max order")
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (16, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=30000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (16, "cash", 20000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=2000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (61, "position cap"))

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1235.475, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1235.48, "price", 20.13))
        self.assertEqual((s["qty"], s["max_loss"], s["reward_risk"]), (24, 483.12, 0.24))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1235.475, today=TODAY)
        self.assertIn("reward:risk 0.24 (target 1240.25, entry 1235.48, stop 1215.35) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.trade_order(s))
        lower = size.size_trade(plan(), cfg(), price=1220.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (1227.8, 40))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump(prices_doc(1235.475), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-10-19 10:10"])
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["qty"], out["orders"]), (1, 24, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-19 10:45"])[1])
            self.assertEqual(out["sizing"]["qty"], 40)
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 30000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (16, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1230.0), "not below the entry"),
                        (plan(target=1220.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1245.00, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-21")
        self.assertIn("make a new plan", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=4.0, stop=3.0, target=5.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=1), today=TODAY)
        self.assertEqual(s["qty"], 1)
        self.assertIn("under your ₹500 minimum order", s["refusal"])
        self.assertIsNone(size.trade_order(s))

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class WeightsMode(unittest.TestCase):
    """₹1,00,000, 10% cash, 25% cap, whole shares only."""

    def check_split(self, r):
        # RELIANCE pinned at its ₹25,000 cap; the other ₹65,000 goes by weight; floors, then one more INFY
        self.assertEqual({p["ticker"]: p["shares"] for p in r["positions"]},
                         {"RELIANCE": 20, "HDFCBANK": 23, "TCS": 6, "INFY": 10, "ITC": 23})
        self.assertEqual(rupees(r), {"RELIANCE": 24556.0, "HDFCBANK": 23000.0, "TCS": 18000.0, "INFY": 15000.0,
                                     "ITC": 9200.0})
        self.assertEqual(r["cash_buffer"], 10000)
        self.assertEqual(r["invested"], 89756.0)
        self.assertEqual(r["idle"], 244.0)
        self.assertEqual(r["cap"], 25000)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE"])

    def test_from_the_weights_file(self):
        self.check_split(size.size(rows(), split(), prices=PRICES))

    def test_from_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg(INLINE))
        self.check_split(size.size(w, split(), prices=PRICES))

    def test_renormalised_weights(self):
        w = size.parse_weights({"A": 3, "B": 1})
        r = size.size(w, {"budget": 100000, "cash_buffer_pct": 0, "max_position_pct": 100, "min_order_inr": 1},
                      prices={"A": 100.0, "B": 100.0})
        self.assertEqual({p["ticker"]: p["weight"] for p in r["positions"]}, {"A": 75.0, "B": 25.0})

    def test_orders_are_plain_delivery_buys(self):
        r = size.size(rows(), split(), prices=PRICES)
        orders = size.orders_from(r, "an example")
        self.assertEqual(orders[0], {"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": 20, "product": "CNC",
                                     "type": "market", "entry": 1227.8, "why": orders[0]["why"]})
        self.assertIn("capped at 25%", orders[0]["why"])
        self.assertEqual([o["qty"] for o in orders], [20, 23, 6, 10, 23])

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"A": 60, "B": 30, "C": 10})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 1},
                      prices={"A": 1.0, "B": 1.0, "C": 1.0})
        self.assertEqual(rupees(r), {"A": 400, "B": 400, "C": 200})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A", "B"])

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"RELIANCE": 50, "ITC": 50})
        r = size.size(w, split(), prices=PRICES)
        self.assertEqual(rupees(r), {"RELIANCE": 24556.0, "ITC": 24800.0})        # 20 and 62 shares under the ₹25,000 cap
        self.assertEqual(r["idle"], 90000 - 24556 - 24800)
        self.assertIn("every stock is at its cap", size.report(r, size.orders_from(r)))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"A": 90, "B": 7, "C": 3})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 50},
                      prices={"A": 1.0, "B": 1.0, "C": 1.0})
        self.assertNotIn("C", rupees(r))
        self.assertEqual(sum(rupees(r).values()), 900)
        self.assertTrue(all(v >= 50 for v in rupees(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "C")

    def test_a_share_too_dear_for_its_slice_is_dropped(self):
        w = size.parse_weights({"A": 90, "MRF": 10})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 0, "max_position_pct": 100, "min_order_inr": 500},
                      prices={"A": 100.0, "MRF": 150000.0})
        self.assertEqual(r["dropped"][0]["ticker"], "MRF")
        self.assertIn("more than its slice", r["dropped"][0]["reason"])

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(), split(blocked=["itc"]), prices=PRICES)
        self.assertNotIn("ITC", rupees(r))
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(), split(max_position_pct=100), prices=PRICES, top=2)
        self.assertEqual(sorted(rupees(r)), ["HDFCBANK", "RELIANCE"])

    def test_prices_are_required(self):
        with self.assertRaises(size.SizeError) as e:
            size.size(rows(), split())
        self.assertIn("whole shares need a price", str(e.exception))

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(), {"cash_buffer_pct": 10}, prices=PRICES)
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "ITC", "name": "", "weight": 1}, {"ticker": "ITC", "name": "", "weight": 2}], split(),
                      prices=PRICES)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(split(), fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"prices": PRICES}, fh)
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual([o["symbol"] for o in doc["orders"]], ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC"])
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--json"])
            self.assertEqual(json.loads(text)["result"]["idle"], 244.0)
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)


if __name__ == "__main__":
    unittest.main()

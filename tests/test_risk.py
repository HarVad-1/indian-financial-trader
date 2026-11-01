import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-ticket", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
NOW = "2026-10-19 09:50"
TODAY = "2026-10-19"
SPLIT_TODAY = "2026-10-13"
# RELIANCE is the reel's close; the others are SYNTHETIC round numbers, for the arithmetic only
PRICES = {"RELIANCE": 1227.80, "HDFCBANK": 1000.0, "TCS": 3000.0, "INFY": 1500.0, "ITC": 400.0}
QUOTE = 1235.475                                           # SYNTHETIC later quote
QUOTE_TIME = datetime.datetime(2026, 10, 19, 4, 37, 13)    # 10:07:13 IST
# a long-term split's rules, with every day-trade check switched off: the basic checks still hold
SPLIT = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 40000, "min_order_inr": 500,
         "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None, "no_entries_after": None,
         "max_trades_per_day": 0, "max_daily_loss_inr": 0, "max_loss_per_trade_inr": 0, "min_reward_risk": 0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT)
    c.update(over)
    return c


def reliance(**over):
    with open(ORDERS_PATH, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def trade(sym, qty, entry, stop, target):
    return {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": qty, "product": "MIS", "type": "market",
            "entry": entry, "stop": stop, "target": target}


def buy(sym, qty, product="CNC"):
    return {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": qty, "product": product, "type": "market"}


def split_orders():
    return [buy("RELIANCE", 20), buy("HDFCBANK", 23), buy("TCS", 6), buy("INFY", 10), buy("ITC", 23)]


def placed(sym, side="buy", day=TODAY, max_loss=498.0, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T09:55:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class TheReel(unittest.TestCase):
    def test_the_reliance_order_passes_every_check(self):
        res = check.check([reliance()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["stop"], r["target"]), ("entry", 1227.8, 1215.35, 1240.25))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["reward_risk"]), (12.45, 498.0, 1.0))
        self.assertEqual(r["value_inr"], 49112.0)
        self.assertEqual(res["cash_after"], 50888.0)
        self.assertEqual(res["date"], TODAY)
        self.assertNotIn("paper", res)

    def test_41_shares_is_blocked_by_the_max_loss_only(self):
        res = check.check([reliance(qty=41)], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 41 shares x 12.45 risk a share = ₹510.45 at the stop, over your ₹500 max loss per trade"])

    def test_fingerprint_is_shared_with_the_ticket(self):
        o = reliance()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty=41)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop=1215.00)), check.fingerprint(o))
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy RELIANCE 40 shares, entry", text)
            self.assertIn("entry 1227.80 (the plan's entry), stop 1215.35, target 1240.25: risk 12.45 a share", text)
            self.assertIn("max loss ₹498 (limit ₹500), reward:risk 1.00 (min 1)", text)
            self.assertIn("1 passed, 0 blocked at 2026-10-19 09:50 IST", text)
            self.assertIn("not a recommendation", text)
            self.assertIn("not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(reliance()))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = reliance()
        o.pop("stop")
        o.pop("target")
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the entry 1227.80", reasons(self.one(reliance(stop=1230.0))))
        res = self.one(reliance(target=1227.0))
        self.assertIn("target: the target 1227.00 must be above the entry 1227.80", reasons(res))

    def test_reward_risk(self):
        res = self.one(reliance(target=1235.0))                 # 7.20 / 12.45
        self.assertIn("reward:risk: 0.58", reasons(res))
        self.assertEqual(verdicts(self.one(reliance(target=1235.0), min_reward_risk=0.5)), ["PASS"])

    def test_a_fresh_higher_price_is_the_entry(self):
        later = at("2026-10-19 10:10")
        res = check.check([reliance()], cfg(), prices={"RELIANCE": QUOTE}, now=later,
                          price_times={"RELIANCE": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (1235.48, "price", 20.13))
        self.assertIn("loss: 40 shares x 20.13 risk a share = ₹805.20", reasons(res))

    def test_a_stale_or_future_price_is_not_used(self):
        for now in ("2026-10-19 10:45", "2026-10-19 09:50"):
            res = check.check([reliance()], cfg(), prices={"RELIANCE": QUOTE}, now=at(now),
                              price_times={"RELIANCE": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 1227.8)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        res = check.check([reliance()], cfg(), prices={"RELIANCE": 1220.00}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 1227.8)

    def test_a_limit_order_uses_its_limit_price(self):
        o = reliance(type="limit", entry=1225.0)
        res = check.check([o], cfg(), prices={"RELIANCE": QUOTE}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (1225.0, 386.0, "PASS"))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("TCS"), placed("INFY")]}
        res = check.check([reliance()], cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, ₹996 of ₹1,500 daily loss", res["results"][0]["info"][-1])
        three = {"orders": [placed("TCS"), placed("INFY"), placed("ITC")]}
        res = check.check([reliance()], cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: ₹1,494 already at risk today plus ₹498 for this one = ₹1,992, over your ₹1,500", why)
        yesterday = {"orders": [placed(s, day="2026-10-16") for s in ("TCS", "INFY", "ITC")]}
        self.assertEqual(verdicts(check.check([reliance()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_exits_and_skips_do_not_count_as_trades(self):
        st = {"orders": [placed("TCS"), placed("TCS", side="sell", kind="exit", max_loss=None),
                         placed("INFY", status="skipped"), placed("ITC", status="filled")]}
        res = check.check([reliance()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [trade(s, 10, 50.0, 10.0, 90.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: ₹1,200 already at risk today plus ₹400 for this one = ₹1,600", why)

    def test_entry_window_ist(self):
        cases = [("2026-10-19 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-10-19 15:01", "no new entries after 15:00 IST (it is 15:01)"),
                 ("2026-10-19 08:00", "the market is closed (Mon 2026-10-19 08:00"),
                 ("2026-10-19 09:14", "the market is closed"),
                 ("2026-10-19 15:30", "the market is closed"),
                 ("2026-10-17 10:15", "the market is closed today (weekend"),
                 ("2026-10-20 10:15", "the market is closed today (NSE holiday: Dussehra"),
                 ("2026-11-10 10:15", "the market is closed today (NSE holiday: Diwali-Balipratipada"),
                 ("2026-11-08 18:15", "the market is closed today (weekend"),    # Muhurat is not modelled
                 ("2026-11-02T03:55:00Z", "no new entries before 09:30 IST (it is 09:25)")]
        for now, want in cases:
            res = self.one(reliance(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-19 09:30", "2026-10-19 15:00", "2026-11-02T04:00:00Z"):
            self.assertEqual(verdicts(self.one(reliance(), now=now)), ["PASS"], now)
        res = self.one(reliance(), now="2026-10-17 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError):
            self.one(reliance(), no_entries_before="9.30am")

    def test_outside_the_holiday_list_it_warns(self):
        res = self.one(reliance(), now="2027-03-01 10:00")
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("nseindia.com", " ".join(res["warnings"]))
        self.assertEqual(self.one(reliance(), now=NOW)["warnings"], [])

    def test_the_window_uses_ist_not_the_machine(self):
        self.assertEqual(check.to_ist(at("2026-10-19 09:50")), datetime.datetime(2026, 10, 19, 9, 50))
        self.assertEqual(at("2026-10-19 09:50"), datetime.datetime(2026, 10, 19, 4, 20))
        self.assertEqual(at("2026-12-01 09:50"), datetime.datetime(2026, 12, 1, 4, 20))        # no daylight time

    def test_a_short_entry_when_allowed(self):
        short = trade("RELIANCE", 40, 1227.80, 1240.25, 1215.35)
        short["side"] = "sell"
        res = self.one(short)
        self.assertIn("short: selling more RELIANCE than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 498.0, 1.0))
        res = self.one(dict(short, stop=1220.0), allow_short=True)
        self.assertIn("must be above the entry 1227.80 for a sell", reasons(res))

    def test_order_format(self):
        self.assertIn("positive whole number", reasons(self.one(reliance(qty=8.5))))
        self.assertIn("positive whole number", reasons(self.one(reliance(qty=0))))
        self.assertIn("positive whole number", reasons(self.one(reliance(qty=None))))
        self.assertIn("product must be MIS", reasons(self.one(reliance(product="BO"))))
        self.assertIn("product must be MIS", reasons(self.one(reliance(product=None))))
        self.assertIn("NSE orders only", reasons(self.one(reliance(exchange="BSE"))))
        self.assertIn("unknown order type", reasons(self.one(reliance(type="stop"))))
        self.assertEqual(verdicts(self.one(reliance(product="CNC"))), ["PASS"])
        o = reliance(type="limit")
        o.pop("entry")
        self.assertIn("needs its limit price", reasons(self.one(o)))

    def test_nse_symbols(self):
        for sym in ("M&M", "BAJAJ-AUTO", "3MINDIA", "reliance.ns"):
            self.assertEqual(verdicts(self.one(reliance(symbol=sym))), ["PASS"], sym)
        self.assertEqual(self.one(reliance(symbol="reliance.ns"))["results"][0]["symbol"], "RELIANCE")


class Exits(unittest.TestCase):
    PF = {"cash": 50888.0, "positions": [{"ticker": "RELIANCE", "shares": 40,
                                          "lots": [{"date": TODAY, "shares": 40, "cost": 49112.0}]}]}
    SELL = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "qty": 40, "product": "MIS", "type": "market"}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("RELIANCE"), placed("TCS"), placed("INFY")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("RELIANCE", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["RELIANCE"], max_order_inr=100, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-10-19 15:20"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-17 12:00"))
        self.assertEqual(verdicts(res), ["PASS"])

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.SELL, qty=20)
        res = check.check([half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1230.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1230.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.SELL, type="limit")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("needs its limit price", reasons(res))
        res = check.check([dict(self.SELL, qty=41)], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more RELIANCE than you hold (40 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([reliance(), self.SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class BasicChecks(unittest.TestCase):
    """The checks that hold for any order (day-trade checks off)."""

    def one(self, order, **over):
        return check.check([order], split(**over), prices=PRICES, today=SPLIT_TODAY)["results"][0]

    def test_the_five_buys_pass_on_a_fresh_account(self):
        res = check.check(split_orders(), split(), prices=PRICES, today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 10244.0)
        self.assertEqual(res["results"][0]["value_inr"], 24556.0)
        self.assertEqual(res["results"][0]["amount"], "20 shares")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(split_orders() + [buy("RELIANCE", 1)], split(), prices=PRICES, today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_the_example_config_blocks_buys_without_a_stop(self):
        res = check.check(split_orders(), cfg(), prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        r = self.one(buy("ITC", 200), max_position_pct=100)
        self.assertIn("₹80,000 is over your ₹40,000 max order", " ".join(r["reasons"]))
        self.assertIn("₹400 is under your ₹500 minimum", " ".join(self.one(buy("ITC", 1))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("ITC", 2), blocked=["ITC"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_fno_off_unless_allowed(self):
        for sym in ("RELIANCE26NOV1300CE", "RELIANCE26NOVFUT", "NIFTY26N0425000PE"):
            opt = {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": 500, "product": "MIS", "type": "limit",
                   "entry": 12.50}
            r = self.one(opt)
            self.assertEqual(r["verdict"], "BLOCK", sym)
            self.assertIn("F&O", " ".join(r["reasons"]))
            r = self.one(opt, allow_options=True)
            self.assertEqual(r["verdict"], "PASS", sym)
            self.assertTrue(r["warnings"])

    def test_crypto_and_junk_symbols(self):
        self.assertIn("NSE stocks and ETFs only", " ".join(self.one(buy("BTC/INR", 1))["reasons"]))

    def test_format_rules(self):
        bad = {"symbol": "ITC", "side": "hold", "qty": 2, "product": "CNC"}
        self.assertIn("side must be buy or sell", " ".join(self.one(bad)["reasons"]))
        self.assertIn("no symbol", " ".join(self.one(buy("", 2))["reasons"]))
        self.assertIn("entry must be a positive number", " ".join(self.one(dict(buy("ITC", 2), entry=-1))["reasons"]))

    def test_share_orders_need_a_price(self):
        self.assertIn("no price for ZZZZ", " ".join(self.one(buy("ZZZZ", 2))["reasons"]))
        r = self.one(buy("ITC", 2))
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["value_inr"], 800.0)

    def test_margin(self):
        r = check.check([buy("HDFCBANK", 5)], split(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 1000, "positions": []}, prices=PRICES, today=SPLIT_TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))

    PF = {"cash": 10000.0, "positions": [{"ticker": "ITC", "shares": 23,
                                          "lots": [{"date": "2026-10-13", "shares": 23, "cost": 9200.0}]}]}

    def sell_itc(self, qty):
        return {"symbol": "ITC", "exchange": "NSE", "side": "sell", "qty": qty, "product": "CNC", "type": "market"}

    def test_short_sales_are_blocked(self):
        r = check.check([self.sell_itc(30)], split(), portfolio=self.PF, prices=PRICES, today=SPLIT_TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        r = check.check([self.sell_itc(3)], split(), portfolio=self.PF, prices=PRICES, today=SPLIT_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [self.sell_itc(20), buy("HDFCBANK", 15)]
        res = check.check(orders, split(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("HDFCBANK", 15)], split(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=SPLIT_TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": SPLIT_TODAY + "T10:00:00"}
                         for i in range(10)]}
        res = check.check([buy("ITC", 2)], split(), prices=PRICES, state=st, today=SPLIT_TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("ITC", 2)], split(), prices=PRICES, state=st, today="2026-10-14")
        self.assertEqual(verdicts(res), ["PASS"])
        one = {"orders": [{"symbol": "ITC", "side": "buy", "status": "placed", "at": SPLIT_TODAY + "T10:00:00"}]}
        res = check.check([buy("ITC", 2)], split(), prices=PRICES, state=one, today=SPLIT_TODAY)
        self.assertIn("already recorded placing", reasons(res))
        skipped = {"orders": [{"symbol": "ITC", "side": "buy", "status": "skipped", "at": SPLIT_TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("ITC", 2)], split(), prices=PRICES, state=skipped,
                                              today=SPLIT_TODAY)), ["PASS"])


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-19T04:40:00Z", "prices": {"RELIANCE": QUOTE},
                           "detail": {"RELIANCE": {"time": "2026-10-19T04:37:13Z", "fetched": "2026-10-19T04:40:00Z"}}},
                          fh)
            none = os.path.join(d, "none")
            base = ["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:10"]), 1)
            self.assertIn("40 shares x 20.13 risk a share = ₹805.20", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:45"]), 0)

    def test_a_split_with_its_own_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(split(), fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"prices": PRICES}, fh)
            orders = os.path.join(d, "orders.json")
            with open(orders, "w") as fh:
                json.dump({"orders": split_orders()}, fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", orders, "--config", c, "--portfolio", none, "--prices", pp,
                                   "--state", none, "--today", SPLIT_TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], SPLIT_TODAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))
            with open(orders, "w") as fh:
                json.dump({"orders": split_orders() + [buy("RELIANCE", 1)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", orders, "--config", c, "--portfolio", none, "--prices", pp,
                                   "--state", none, "--today", SPLIT_TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", ORDERS_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", ORDERS_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


if __name__ == "__main__":
    unittest.main()

"""trader-exit tests. Bars for the ATR stop reuse tests/fixtures/trader-entry/
(RELIANCE's real Oct 19 2026 five-minute bars, see test_entry.py)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

REL = os.path.join(FIX, "trader-entry", "RELIANCE-2026-10-19-5m.json")

# trader/plan.json for the reel, exactly as the contract writes it
REEL_PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-19", "setup": "orb",
             "entry": 1227.80, "entry_time": "9:50 AM", "entry_bar": 6, "interval": "5m",
             "opening_range": {"high": 1223.40, "low": 1207.30, "minutes": 15},
             "stop": 1215.35, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
             "target": 1240.25, "target_r": 1.0, "target_label": "1R (one times the risk)",
             "risk_per_share": 12.45, "reward_risk": 1.0, "flat_by": "15:10"}
REEL_ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-19",
              "status": "triggered", "time": "9:50 AM", "bar_start": "09:45", "bar_index": 6, "price": 1227.80,
              "level": 1223.40, "interval": "5m", "opening_range": {"high": 1223.40, "low": 1207.30, "minutes": 15},
              "source": "yahoo", "checked_at": "2026-10-19T04:20:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1223.40, ol=1207.30, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_the_reel_or_mid_1r(self):
        p = c(1227.80, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("1215.35"))           # (1223.40 + 1207.30) / 2
        self.assertEqual(p["risk"], Decimal("12.45"))
        self.assertEqual(p["target"], Decimal("1240.25"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")

    def test_a_midpoint_between_paise_rounds_down(self):
        p = c(1227.80, "or-mid", "1R", 1223.40, 1207.35)
        self.assertEqual(p["stop"], Decimal("1215.37"))           # floor(1215.375)
        self.assertIn("rounded down", p["stop_detail"])

    def test_the_default_2r(self):
        p = c(1227.80, "or-mid")
        self.assertEqual(p["target"], Decimal("1252.70"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_paise(self):
        p = c(1227.800048828125, "or-mid", "1R", 1223.4000244140625, 1207.300048828125)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("1227.80"), Decimal("1215.35"), Decimal("1240.25")))

    def test_other_stops(self):
        p = c(1227.80, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1207.30"), Decimal("20.50"), Decimal("1268.80")))
        p = c(1227.80, "price:1215.359")
        self.assertEqual(p["stop"], Decimal("1215.35"))              # rounds away from the entry
        p = c(1227.80, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1215.52"), Decimal("12.28"), Decimal("1252.36")))
        p = c(1227.80, "atr:1.5", atr=7.4572, n=7)
        self.assertEqual(p["stop"], Decimal("1216.61"))              # 1227.80 - 11.1858 = 1216.6142
        self.assertEqual(p["stop_label"], "1.5 x ATR(7) under the entry")

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.49"))            # 100 + 1.485
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(1227.80, "or-mid", "price:1245")
        self.assertEqual(p["reward_risk"], Decimal("1.38"))         # 17.20 / 12.45
        self.assertEqual(p["target_label"], "your price (1.38R)")
        self.assertEqual(p["target_r"], Decimal("1.38"))

    def test_refusals_say_why(self):
        cases = [("price:1227.80", "2R", "at or above the entry"),
                 ("price:1228", "2R", "at or above the entry"),
                 ("or-mid", "price:1227", "at or below the entry"),
                 ("price:1227.50", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1227.80, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1227.80 is 0.6139: a \u20b90.62 risk is allowed, \u20b90.61 is not
        self.assertEqual(c(1227.80, "price:1227.18")["risk"], Decimal("0.62"))
        with self.assertRaises(exitp.PlanError):
            c(1227.80, "price:1227.19")
        with self.assertRaises(exitp.PlanError):
            c(1210.00, "or-mid")                                     # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1227.80, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1227.80, "or-mid", oh=1207.30, ol=1223.40)

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, REL)
        day = exitp.datetime.date(2026, 10, 19)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=6)
        self.assertEqual(len(bars), 7)                              # 9:15 ... the 9:45 entry bar
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1223.40 - 1211.90, places=3)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 7)
        self.assertAlmostEqual(atr, sum(trs) / 7, places=9)
        self.assertAlmostEqual(atr, 7.45715, places=4)
        # later in the day it uses the last 14 bars; the file's in-progress 10:30 bar and tick never count
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 15)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)


class Symbols(unittest.TestCase):
    def test_nse_symbols(self):
        self.assertEqual(exitp.nse_symbol("reliance.ns"), "RELIANCE")
        self.assertEqual(exitp.nse_symbol("m&m"), "M&M")
        self.assertEqual(exitp.nse_symbol("bajaj-auto"), "BAJAJ-AUTO")
        for bad in ("", "BRK.B", "A B"):
            with self.assertRaises(exitp.PlanError, msg=bad):
                exitp.nse_symbol(bad)

    def test_the_yahoo_url_and_the_files_symbol(self):
        with open(REL) as fh:
            doc = json.load(fh)
        doc["chart"]["result"][0]["meta"]["symbol"] = "M&M.NS"
        calls, real = [], exitp.yahoo_get

        def fake(path):
            calls.append(path)
            return json.dumps(doc)
        exitp.yahoo_get = fake
        try:
            exitp.load_chart("M&M", "5m", exitp.datetime.date(2026, 10, 19))
            self.assertIn("/chart/M%26M.NS?", calls[0])
            with self.assertRaises(exitp.PlanError):
                exitp.load_chart("INFY", "5m", exitp.datetime.date(2026, 10, 19))
        finally:
            exitp.yahoo_get = real


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-19 09:51"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "RELIANCE", "--offline", REL, "--now", "2026-10-19 09:50", "--write"])
        self.assertEqual(code, 0)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Stop          \u20b91,215.35  opening-range midpoint", text)
        self.assertIn("Target        \u20b91,240.25  1R (one times the risk)", text)
        self.assertIn("Risk a share  \u20b912.45 (1.01% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:10", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 1252.70)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-19 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("\u20b91,252.70", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-21 10:20"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance", "--entry", "1227.80", "--or-high", "1223.40", "--or-low", "1207.30",
                                  "--time", "9:50", "--entry-bar", "6", "--date", "2026-10-19", "--target", "1R",
                                  "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", REL, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1216.61, 11.19, 1250.18))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 7))
        self.assertAlmostEqual(p["atr"], 7.4572, places=3)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", REL, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1223.41", "--json")    # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1223.41, 1215.35, None))

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, setup="level", level=1223.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("v2 plans longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:1227.70")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1227.80", "--or-high", "1223.40")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)
        code, _, err = self.plan("--symbol", "BRK.B", "--entry", "100", "--stop", "pct:1")
        self.assertEqual(code, 1)
        self.assertIn("not an NSE symbol", err)

    def test_flat_by_config_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:00", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:00")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:45"})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("flat_by in the config 15:45 is at or after the 3:30 PM close", err)
        os.remove(os.path.join("trader", "config.json"))

        code, _, err = self.plan("--flat-by", "15:30")
        self.assertEqual(code, 1)
        self.assertIn("--flat-by 15:30 is at or after the 3:30 PM close", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, time="3:12 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertIn("You make every buy and sell decision. Nothing in this pack places an order", text[text.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money", "/trader-timemachine", "/trader-budget", "longs only", "\u20b91,215.35", "\u20b91,240.25", "\u20b912.45", "1252.70", "too tight to survive the spread", "square-off"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "tsla", "nyse", "new york", "$"):
            self.assertNotIn(never, low.replace("${claude_skill_dir}", ""))


if __name__ == "__main__":
    unittest.main()

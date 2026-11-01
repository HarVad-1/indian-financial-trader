"""trader-scanner tests. Fixtures in tests/fixtures/trader-scanner/ (real data, read 2026-11-02):

wall5m-2026-10-19.json.gz      33 asked, 32 real Yahoo 5-minute NSE charts cut to Oct 19 2026, plus ZZZZQX (no data)
daily-spark-2026-10-30.json.gz the same names' 3-month daily closes through Oct 30, as one spark answer
spark-3mo-live-2026-10-30.json a real 20-symbol spark answer, unchanged
spark-5m-2026-10-30.json       a real 20-symbol 5-minute spark answer of Oct 30, unchanged
MM-5m-2026-10-30.json          M&M's real v8 chart of Oct 30, unchanged
AXISBANK-5m-2026-10-30.json    AXISBANK's real v8 chart of Oct 30, unchanged; spark leaves out its 9:20 and 9:25 bars
nselist/EQUITY_L.csv           rows of NSE's real EQUITY_L.csv: EQ, BE and BZ
Expected numbers come from a separate reading of the raw bars, not from this tool.
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-10-19.json.gz")
DAILY = os.path.join(SDIR, "daily-spark-2026-10-30.json.gz")
LIVE = os.path.join(SDIR, "spark-3mo-live-2026-10-30.json")
NSELIST = os.path.join(SDIR, "nselist")
SPARK5 = os.path.join(SDIR, "spark-5m-2026-10-30.json")
MM5 = os.path.join(SDIR, "MM-5m-2026-10-30.json")
AXIS5 = os.path.join(SDIR, "AXISBANK-5m-2026-10-30.json")
DT = datetime.datetime
D = datetime.date
EVE = "2026-11-02 09:00"     # before Monday's open: closes through Fri Oct 30


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(text):
    """IST wall-clock text -> unix seconds."""
    utc = scan.ist_to_utc(DT.strptime(text, "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


class Universe(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(NSELIST, scan.NSE_FILE), encoding="utf-8") as fh:
            self.text = fh.read()
        self.kept, self.counts = scan.build_universe(self.text)

    def test_only_series_eq_is_kept(self):
        self.assertEqual([s for s, _ in self.kept],
                         ["20MICRONS", "3MINDIA", "BAJAJ-AUTO", "M&M", "NAM-INDIA", "RELIANCE", "TCS"])
        self.assertEqual(self.counts, {"rows": 12, "keep": 7, "series BE": 3, "series BZ": 2})
        self.assertEqual(dict(self.kept)["M&M"], "Mahindra & Mahindra Limited")

    def test_a_bad_symbol_is_dropped(self):
        kept, counts = scan.build_universe("SYMBOL,NAME OF COMPANY, SERIES\nAB CD,Odd Limited,EQ\nTCS,TCS,EQ\n")
        self.assertEqual(kept, [("TCS", "TCS")])
        self.assertEqual(counts["not a plain symbol"], 1)

    def test_not_the_equity_list(self):
        with self.assertRaises(scan.ScanError):
            scan.build_universe("<html>maintenance</html>")

    def test_nse_gets_a_browser_and_a_referer(self):
        seen = {}

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            seen.update(req.headers, url=req.full_url)
            return Answer(self.text.encode())
        old = scan.urllib.request.urlopen
        scan.urllib.request.urlopen = fake
        try:
            self.assertEqual(scan.nse_get(scan.NSE_URL), self.text)
        finally:
            scan.urllib.request.urlopen = old
        self.assertEqual(seen["url"], "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")
        self.assertIn("Chrome/", seen["User-agent"])
        self.assertEqual(seen["Referer"], "https://www.nseindia.com/")


class Symbols(unittest.TestCase):
    def test_yahoo_mapping_both_ways(self):
        self.assertEqual(scan.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("M&M"), "M%26M.NS")
        self.assertEqual(scan.to_yahoo("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        for y, want in (("M&M.NS", "M&M"), ("M%26M.NS", "M&M"), ("BAJAJ-AUTO.NS", "BAJAJ-AUTO"), ("TCS.NS", "TCS")):
            self.assertEqual(scan.from_yahoo(y), want)

    def test_urls(self):
        self.assertEqual(scan.spark_path(["M&M", "tcs"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,TCS.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("BAJAJ-AUTO", "1d", "5m"),
                         "/v8/finance/chart/BAJAJ-AUTO.NS?range=1d&interval=5m")

    def test_typed_symbols(self):
        class A(object):
            symbols, watchlist, universe, limit = "reliance.ns, m&m,BAJAJ-AUTO,TCS.NS,TCS", None, None, None
        self.assertEqual(scan.read_symbols(A()), ["RELIANCE", "M&M", "BAJAJ-AUTO", "TCS"])
        A.symbols = "RELIANCE;DROP"
        with self.assertRaises(scan.ScanError) as cm:
            scan.read_symbols(A())
        self.assertIn("not an NSE symbol", str(cm.exception))


class Time(unittest.TestCase):
    def test_ist_clock(self):
        self.assertEqual(scan.ts_to_ist(1792386900), DT(2026, 10, 19, 10, 45))
        self.assertEqual(scan.parse_now("2026-10-19 10:00"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.parse_now("2026-12-01 09:15"), DT(2026, 12, 1, 3, 45))       # no daylight time
        self.assertEqual(scan.parse_now("2026-10-19T04:30:00Z"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(scan.clock_label(DT(2026, 10, 19, 9, 50)), "9:50 AM")
        self.assertEqual(scan.clock_label(datetime.time(12, 5)), "12:05 PM")
        with self.assertRaises(scan.ScanError):
            scan.parse_now("tomorrow")
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_holidays_and_session(self):
        self.assertEqual(scan.session(D(2026, 10, 19)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 10, 20)))                                    # Dussehra
        self.assertIn("Dussehra", scan.trading_day(D(2026, 10, 20))[1])
        self.assertEqual(scan.trading_day(D(2026, 11, 8))[1], "weekend")                    # Muhurat counts as closed


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        with open(LIVE, encoding="utf-8") as fh:
            data = scan.parse_spark(fh.read())
        self.assertEqual(len(data), 20)
        self.assertIn("M&M", data)
        self.assertIn("BAJAJ-AUTO", data)
        r = data["RELIANCE"]
        self.assertEqual(len(r["t"]), 64)
        self.assertIsNone(r["h"])                  # spark is closes only
        self.assertEqual(round(r["c"][-1], 2), 1164.9)
        self.assertEqual(r["meta"]["regularMarketVolume"], 14753650)
        closes = scan.daily_closes(r, scan.parse_now(EVE))
        self.assertEqual((closes[-1][0], round(closes[-1][1], 2)), (D(2026, 10, 30), 1164.9))
        # the same answer read during Friday's session: Friday's bar is still in progress
        closes = scan.daily_closes(r, scan.parse_now("2026-10-30 12:00"))
        self.assertEqual((closes[-1][0], round(closes[-1][1], 2)), (D(2026, 10, 29), 1167.4))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        with open(LIVE, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=TCS.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/TCS.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        with open(LIVE, encoding="utf-8") as fh:
            names = sorted(scan.parse_spark(fh.read()))[:19]
        syms = names + ["ZZZZQX"] + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertEqual(failed["ZZZZQX"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now(EVE), 50, 100000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Daily(unittest.TestCase):
    # From a separate reading of the closes, through Fri Oct 30 2026.
    EXPECT = {
        "SUNPHARMA": ["reclaim_ma20"], "NTPC": ["reclaim_ma20"],
        "POWERGRID": ["breakout_20d"], "LT": ["breakout_20d"], "INFY": ["breakout_20d"],
        "KOTAKBANK": ["breakout_20d"], "WIPRO": ["breakout_20d"],
        "HCLTECH": ["breakout_20d", "bull_flag"], "TCS": ["bull_flag"],
        "NESTLEIND": ["oversold"],   # RSI 29.8
        "YESBANK": ["oversold"],     # RSI 16.4
    }

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 32)
        self.assertNotIn("ZZZZQX", self.data)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now(EVE), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 10, 30))
        self.assertEqual(counts, {"requested": 32, "fetched": 32, "illiquid": 0, "short": 0, "scanned": 32,
                                  "hits": 11})
        lt = [h for h in hits if h["symbol"] == "LT"][0]
        self.assertEqual((lt["price"], lt["rsi"], lt["close_date"]), (3871.7, 61.6, "2026-10-30"))

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-30 09:00"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-30 12:00"), 0, 0)
        self.assertEqual(before, during)
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-30 15:29"))
        self.assertEqual(round(bars[-1][1], 2), 1167.4)
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-30 15:30"))
        self.assertEqual(round(bars[-1][1], 2), 1164.9)

    def test_liquidity_filter(self):
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now(EVE), 50, 100000)
        self.assertEqual(counts["illiquid"], 2)        # YESBANK and IDEA under Rs 50
        self.assertNotIn("YESBANK", [h["symbol"] for h in hits])
        self.assertEqual(len(hits), 10)
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now(EVE), 50, 1000000)
        self.assertEqual(counts["illiquid"], 5)        # and BAJAJ-AUTO, MARUTI, ULTRACEMCO under a million shares

    def test_most_rupees_traded_first(self):
        hits, _, _ = scan.scan_daily(self.data, ["LT", "WIPRO"], scan.parse_now(EVE), 0, 0)
        self.assertEqual([h["symbol"] for h in hits], ["LT", "WIPRO"])   # 3871.7 x 1,765,873 beats 198.31 x 28,869,751

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(1, 11)], "c": [100.0] * 10,
             "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-21 09:00"), 50, 100000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_the_textbook(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, datetime.time(15, 0))
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_reel_scan_at_ten(self):
        hits, counts, failed = self.scan_at("2026-10-19 10:00")
        self.assertEqual(len(self.syms), 33)
        self.assertEqual(counts["scanned"], 32)
        self.assertEqual(counts["candidates"], 15)
        self.assertEqual(self.errors, {"ZZZZQX": "no data for ZZZZQX: No data found, symbol may be delisted"})
        # sorted by time, then symbol
        self.assertEqual([(h["at"], h["symbol"]) for h in hits],
                         [("9:35 AM", "SBIN"), ("9:35 AM", "ULTRACEMCO"), ("9:40 AM", "HCLTECH"), ("9:40 AM", "MARUTI"),
                          ("9:45 AM", "TCS"), ("9:45 AM", "WIPRO"), ("9:50 AM", "RELIANCE"), ("9:50 AM", "TITAN")])
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual(rel, {"symbol": "RELIANCE", "setups": ["orb"], "price": 1227.8, "at": "9:50 AM",
                               "bar_start": "09:45", "bar_index": 6, "or_high": 1223.4, "or_low": 1207.3,
                               "pct_over": 0.36})

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:00", min_price=50)
        self.assertEqual(counts["illiquid"], 2)            # YESBANK and IDEA
        self.assertEqual(len(hits), 8)
        hits, counts, _ = self.scan_at("2026-10-19 10:00", min_price=1000)
        self.assertEqual(counts["illiquid"], 13)
        self.assertEqual(len(hits), 6)                     # SBIN and WIPRO drop out
        self.assertNotIn("WIPRO", [h["symbol"] for h in hits])

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-19 09:49", syms=["RELIANCE"])
        self.assertEqual(hits, [])                         # the 9:45 bar is not done until 9:50
        hits, _, _ = self.scan_at("2026-10-19 09:50", syms=["RELIANCE"])
        self.assertEqual(hits[0]["at"], "9:50 AM")

    def test_the_live_tick_is_dropped(self):
        s = bars_series("2026-10-19", [100, 100.2, 100.1, 100.3, 100.5, 100.6], extra=[("2026-10-19 09:47:13", 101.0)])
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 9, 48))
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars], ["09:15", "09:20", "09:25", "09:30", "09:35",
                                                                         "09:40"])
        self.assertTrue(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 9, 48), 3))

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, day, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, D(2026, 10, 19), DT(2026, 10, 19, 10, 0), 15,
                                             0, 0, confirm)
        self.assertEqual(len(hits), 8)
        self.assertEqual(len(asked), 15)
        self.assertEqual(counts["candidates"], 15)         # stage 1 skipped the other 17

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s.NS" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE", "IDEA"], D(2026, 10, 19),
                                             DT(2026, 10, 19, 10, 0), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["RELIANCE"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-19", [100, 100.2, 100.1, None, 100.5, 100.6, 100.7, 100.8, 100.9])
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 10, 0), 3))

    def test_yahoos_empty_last_bars_do_not_make_a_chart_unusable(self):
        # the real charts: RELIANCE has no 15:20 close that day, HDFCBANK no 15:15 or 15:20
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 15, 30)
        bars = scan.session_bars(self.data["RELIANCE"], day, cut)
        self.assertEqual((len(bars), bars[-2]["start"].strftime("%H:%M")), (74, "15:15"))
        self.assertTrue(scan.usable(bars, day, cut, 3))
        bars = scan.session_bars(self.data["HDFCBANK"], day, cut)
        self.assertEqual((len(bars), bars[-2]["start"].strftime("%H:%M")), (73, "15:10"))
        self.assertTrue(scan.usable(bars, day, cut, 3))

    def test_a_30_minute_range(self):
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40
        s = bars_series("2026-10-19", closes, highs=[c + 0.2 for c in closes])
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], D(2026, 10, 19), DT(2026, 10, 19, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")         # range 9:15 to 9:45 (high 101.4), the 9:45 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)

    def test_window_messages(self):
        until = datetime.time(15, 0)

        def msg(now, at=None, until=until):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-17 10:00"))
        self.assertIn("Dussehra", msg("2026-10-20 10:00"))
        self.assertIn("runs 9:15 to 9:30 AM", msg("2026-10-19 09:25"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:25"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:32"))
        self.assertIn("expired", msg("2026-10-19 15:15"))
        self.assertIn("expired", msg("2026-10-19 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM", msg("2026-10-19 16:00"))
        self.assertIn("later than now", msg("2026-10-19 09:40", at=datetime.time(10, 0)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-19 17:00", at=datetime.time(16, 0)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), datetime.time(10, 0), 15, until),
                         (D(2026, 10, 19), DT(2026, 10, 19, 10, 0)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 09:35"), None, 15, until)[1],
                         DT(2026, 10, 19, 9, 35))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-19 10:00"), None, 7, until)


class RealFiveMinute(unittest.TestCase):
    """A real spark 5-minute answer and M&M's v8 chart, read after the close of Fri Oct 30 2026."""

    def setUp(self):
        with open(SPARK5, encoding="utf-8") as fh:
            self.spark = scan.parse_spark(fh.read())
        with open(MM5, encoding="utf-8") as fh:
            self.v8 = scan.parse_chart(fh.read(), "M&M")
        self.now = scan.parse_now("2026-10-30 11:00")

    def test_spark_and_v8_agree_on_the_ampersand_symbol(self):
        self.assertEqual(len(self.spark), 20)
        self.assertIn("M&M", self.spark)
        self.assertIn("BAJAJ-AUTO", self.spark)
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 0))
        a = scan.session_bars(self.spark["M&M"], day, cut)
        b = scan.session_bars(self.v8, day, cut)
        self.assertEqual(len(a), 21)                       # 9:15 to 10:55
        self.assertEqual([x["start"] for x in a], [x["start"] for x in b])
        self.assertEqual([round(x["c"], 2) for x in b[:4]], [2893.2, 2885.0, 2891.6, 2887.8])

    def test_m_and_m_stayed_under_its_range(self):
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 0))
        hits, counts, failed = scan.scan_orb(self.spark, ["M&M"], day, cut, 15, 50, 0, lambda s: self.v8)
        self.assertEqual((hits, failed, counts["scanned"]), ([], {}, 1))
        bars = scan.session_bars(self.v8, day, cut)
        self.assertEqual(round(max(x["h"] for x in bars[:3]), 2), 2903.0)
        self.assertEqual(round(min(x["l"] for x in bars[:3]), 2), 2880.6)

    def test_spark_gaps_do_not_lose_a_chart_but_a_real_gap_does(self):
        with open(AXIS5, encoding="utf-8") as fh:
            v8 = scan.parse_chart(fh.read(), "AXISBANK")
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 0))
        spark = self.spark["AXISBANK"]
        starts = [b["start"].strftime("%H:%M") for b in scan.session_bars(spark, day, cut)]
        self.assertNotIn("09:20", starts)                  # spark has no 9:20 or 9:25 bar; v8 does
        self.assertNotIn("09:25", starts)
        self.assertFalse(scan.usable(scan.session_bars(spark, day, cut), day, cut, 3))
        hits, counts, failed = scan.scan_orb(self.spark, ["AXISBANK"], day, cut, 15, 50, 0, lambda s: v8)
        self.assertEqual((counts["candidates"], counts["confirmed"], failed), (1, 1, {}))
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual({k: hits[0][k] for k in ("at", "bar_index", "price", "or_high", "or_low", "pct_over")},
                         {"at": "9:45 AM", "bar_index": 5, "price": 1223.9, "or_high": 1223.8, "or_low": 1218.6,
                          "pct_over": 0.01})
        # checked against spark's own bars, the gap is real to the tool: unusable, not a failed fetch
        hits, counts, failed = scan.scan_orb(self.spark, ["AXISBANK"], day, cut, 15, 50, 0, lambda s: spark)
        self.assertEqual((hits, failed, counts["scanned"], counts["unusable"]), ([], {}, 0, 1))

    def test_stage_two_cap_takes_the_most_traded(self):
        data, _ = scan.load_offline(WALL)
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=5)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (5, 10))
        self.assertEqual(asked, ["TCS", "HDFCBANK", "RELIANCE", "SBIN", "INFY"])   # most rupees traded by 10:00
        self.assertEqual(sorted(h["symbol"] for h in hits), ["RELIANCE", "SBIN", "TCS"])
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 8)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", NSELIST, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("Kept 7 NSE stocks in series EQ", text)
        self.assertIn("3 series BE, 2 series BZ", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 7)
        self.assertIn("M&M|Mahindra & Mahindra Limited", lines)

        # daily over that universe with the fixture charts: only BAJAJ-AUTO, M&M, RELIANCE and TCS are in both
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", EVE, "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 4 of 7 charts (3 failed or had no data)", text)
        self.assertIn("(₹50+ and 100,000+ shares)", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 7, 4, 3))
        self.assertEqual(doc["hits"], [{"symbol": "TCS", "setups": ["bull_flag"], "price": 2502.6,
                                        "close_date": "2026-10-30", "rsi": 66.8, "volume": 3016983}])
        self.assertEqual(doc["as_of"], "2026-10-30 close")
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 100000})

        # the reel's 10:00 scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:00", "--min-price", "0",
                             "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("15-minute range (9:15 to 9:30 AM)", text)
        self.assertIn("8 of 32 charts closed above their opening-range high by 10:00 AM", text)
        self.assertIn("9:50 AM  RELIANCE     ₹1,227.80  range 1,207.30 to 1,223.40  +0.36% over", text)
        self.assertIn("not a list of stocks to buy", text)
        self.assertIn("The choice is yours", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-19T04:30:00Z", "mode": "orb",
                          "as_of": "2026-10-19 10:00 IST", "universe": 33, "scanned": 32, "source": "yahoo"})
        self.assertEqual(len(doc["hits"]), 8)

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:00", "--watchlist", self.wl, "--min-price", "0",
                             "--json", "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (8, 8, 8))

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the Oct 19 bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def plain(y):
            return urllib.parse.unquote(y).replace(".NS", "")

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    r = ((bundle.get(plain(y)) or {}).get("chart") or {}).get("result")
                    if r:
                        q = r[0]["indicators"]["quote"][0]
                        res.append({"symbol": urllib.parse.unquote(y), "response": [dict(
                            r[0], indicators={"quote": [{"close": q["close"], "volume": q["volume"]}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[plain(path.split("/chart/")[1].split("?")[0])])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-19 10:05")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:00", "--min-price", "0",
                                 "--max-confirm", "5", "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("32 usable; 15 candidates, 5 confirmed", text)
        self.assertIn("PARTIAL: 10 more candidates were not checked", text)
        self.assertIn("3 of the 5 checked charts closed above their opening-range high by 10:00 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 5)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 2)    # 33 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        self.assertTrue(any("M%26M.NS" in c for c in calls))

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-17 10:00", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:30", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "09:40"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:00", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:45", "--symbols", "RELIANCE",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:45 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_a_year_without_a_holiday_list_warns(self):
        code, _, err = run(["daily", "--offline", DAILY, "--now", "2027-01-05 09:00", "--no-write", "--symbols", "TCS"])
        self.assertIn("Holiday list covers 2026 only: check nseindia.com for 2027-01-05", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym in ("RELIANCE", "M&M", "TCS", "ZZZZQX"):
            with open(os.path.join(folder, sym + ".json"), "w") as fh:
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-19 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 3, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["TCS", "RELIANCE"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nRELIANCE|Reliance Industries Limited\nm&m\n\nRELIANCE\n")
        self.assertEqual(scan.symbols_from_file(txt), ["RELIANCE", "M&M", "RELIANCE"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "tcs"}, {"symbol": "M&M"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["TCS", "M&M"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", EVE, "--json", "--no-write"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (2, 2))
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", EVE, "--json", "--no-write"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_daily_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", EVE, "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 11)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")


if __name__ == "__main__":
    unittest.main()

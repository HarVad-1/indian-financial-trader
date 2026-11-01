#!/usr/bin/env python3
"""Earnings reader for trader-analyst. Standard library only.

Pulls a company's quarterly numbers from Yahoo Finance and its results filings
from NSE, and lays out what changed, quarter by quarter: revenue, gross,
operating and net margins, net income, diluted EPS, and growth against the same
quarter a year earlier. Amounts are ₹ crore.

    python3 facts.py RELIANCE
    python3 facts.py RELIANCE M&M --quarters 8
    python3 facts.py RELIANCE --json
    python3 facts.py RELIANCE TCS INFY --metrics trader/metrics.json

Sources (no key; both unofficial, so either can lag or refuse):
  https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/RELIANCE.NS?type=...
      the numbers. Yahoo keeps about five quarters and can skip some.
  https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE&from_date=...&to_date=...
      the results PDFs and the period each one covers
  https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=RELIANCE&period=Quarterly
      XBRL files for results. This list can stop well behind the announcements.

The gaps are the point of the report, so it says them out loud:
  - GAP: a quarter between two others that Yahoo does not have.
  - No year-on-year: the year-ago quarter is missing, so growth is blank, never guessed.
  - Last 4 quarters are shown only when they are four consecutive quarters.
  - NOTE: NSE shows results for a later period than Yahoo has. Read the PDF.
Fiscal labels assume an April to March year (Q1 FY27 ends June 2026).

--metrics FILE merges one row per ticker into a JSON file (for notes or a sheet).
Offline: --offline DIR reads timeseries-<SYM>.json, ann-<SYM>.json and
nsefin-<SYM>.json from DIR.

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import calendar
import datetime as _dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMESERIES = ("https://%s.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
              "?type=%s&period1=%d&period2=%d")
ANNOUNCEMENTS = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
                 "&from_date=%s&to_date=%s")
RESULTS_LIST = "https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=%s&period=Quarterly"
YAHOO_UA = "Mozilla/5.0 (compatible; indian-financial-trader/1.0; +https://github.com/HarVad-1/indian-financial-trader)"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")
LOOKBACK_DAYS = 400   # how far back to read announcements for results PDFs
CRORE = 1e7

TYPES = {"revenue": "quarterlyTotalRevenue", "gross_profit": "quarterlyGrossProfit",
         "operating_income": "quarterlyOperatingIncome", "net_income": "quarterlyNetIncome",
         "eps_diluted": "quarterlyDilutedEPS"}


class FactsError(Exception):
    pass


def clean_ticker(text):
    """RELIANCE, m&m, RELIANCE.NS -> the plain NSE symbol. Raises on anything else."""
    sym = re.sub(r"\.NS$", "", str(text).strip().upper())
    if not TICKER.match(sym):
        raise FactsError("%r is not an NSE symbol" % text)
    return sym


def yahoo_symbol(sym):
    return urllib.parse.quote(clean_ticker(sym), safe="-") + ".NS"


class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline, self.pause, self._last = offline, pause, 0.0

    def json(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        # Yahoo 429s browser agents; NSE drops anything else
        headers = {"User-Agent": YAHOO_UA if "finance.yahoo.com" in url else BROWSER_UA,
                   "Accept-Encoding": "gzip"}
        if "nseindia.com" in url:
            headers["Referer"] = "https://www.nseindia.com/"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except urllib.error.HTTPError as exc:
            raise FactsError("HTTP %s for %s" % (exc.code, url))
        except (urllib.error.URLError, OSError) as exc:
            raise FactsError("could not reach %s: %s" % (url, getattr(exc, "reason", exc)))
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise FactsError("%s did not send JSON" % url)


def fetch_numbers(fetcher, sym):
    """Yahoo's timeseries document: query2, then query1 when query2 answers 429 or 5xx."""
    now = int(time.time())
    types = ",".join(TYPES.values())
    for host in ("query2", "query1"):
        try:
            return fetcher.json(TIMESERIES % (host, yahoo_symbol(sym), types, now - 6 * 366 * 86400, now),
                                "timeseries-%s.json" % sym)
        except FactsError as exc:
            if host == "query1" or fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                raise


# ---------------------------------------------------------------- numbers

def _date(s):
    return _dt.date.fromisoformat(s)


def series(doc, key):
    """{end: value} of three-month figures for one metric. Yahoo pads with nulls."""
    out = {}
    want = TYPES[key]
    for block in (doc.get("timeseries") or {}).get("result") or []:
        for q in block.get(want) or []:
            if q and q.get("periodType") == "3M" and (q.get("reportedValue") or {}).get("raw") is not None:
                out[q["asOfDate"]] = q["reportedValue"]["raw"]
    return out


def quarter_before(d):
    """The month-end three months before a quarter end."""
    month = d.month - 3
    year = d.year
    if month < 1:
        month += 12
        year -= 1
    return _dt.date(year, month, calendar.monthrange(year, month)[1])


def fiscal_label(end):
    """Indian fiscal quarter, April to March: 2026-06-30 is Q1 FY27."""
    d = _date(end)
    q = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(d.month)
    if not q:
        return ""
    return "%s FY%02d" % (q, (d.year + (1 if d.month > 3 else 0)) % 100)


def missing_quarters(ends):
    """Quarters between the first and last end that are not in `ends`."""
    have = sorted(_date(e) for e in ends)
    out = []
    for older, newer in zip(have, have[1:]):
        d = quarter_before(newer)
        while d > older + _dt.timedelta(days=10):
            out.append(d.isoformat())
            d = quarter_before(d)
    return sorted(out, reverse=True)


def consecutive(newer, older):
    return (_date(newer) - _date(older)).days <= 100


def _yoy_match(series_, end, days=365, slack=10):
    target = _date(end) - _dt.timedelta(days=days)
    best = None
    for e in series_:
        gap = abs((_date(e) - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, e)
    return series_[best[1]] if best else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def analyse(doc, quarters=6, name=None):
    """Every number in the report, as a dict."""
    s = {key: series(doc, key) for key in TYPES}
    rev = s["revenue"]
    if not rev and not s["net_income"]:
        raise FactsError("Yahoo has no quarterly revenue or net income (is it an NSE symbol?)")
    every = sorted(set(rev) | set(s["net_income"]), reverse=True)
    rows = []
    for end in every[:quarters]:
        r, ni, eps = rev.get(end), s["net_income"].get(end), s["eps_diluted"].get(end)
        gp, oi = s["gross_profit"].get(end), s["operating_income"].get(end)
        rows.append({
            "end": end, "fiscal": fiscal_label(end),
            "revenue": r, "gross_profit": gp, "operating_income": oi, "net_income": ni,
            "eps_diluted": round(eps, 2) if eps is not None else None,
            "gross_margin": margin(gp, r), "operating_margin": margin(oi, r),
            "net_margin": margin(ni, r),
            "revenue_yoy": pct(r, _yoy_match(rev, end)),
            "net_income_yoy": pct(ni, _yoy_match(s["net_income"], end)),
            "eps_yoy": pct(eps, _yoy_match(s["eps_diluted"], end)),
            "has_year_ago": _yoy_match(rev, end) is not None or _yoy_match(s["net_income"], end) is not None,
        })
    ttm = {}
    for key in ("revenue", "net_income", "eps_diluted"):
        last4 = sorted(s[key])[-4:]
        if len(last4) == 4 and (_date(last4[-1]) - _date(last4[0])).days < 300:
            ttm[key] = sum(s[key][e] for e in last4)
    if "eps_diluted" in ttm:
        ttm["eps_diluted"] = round(ttm["eps_diluted"], 2)
    return {"entity": name, "rows": rows, "ttm": ttm, "missing": missing_quarters(every),
            "no_year_ago": [r["end"] for r in rows if not r["has_year_ago"]]}


def crore(v):
    """Rupees -> "3,09,468": whole crore with Indian digit groups."""
    if v is None:
        return "-"
    digits = "%d" % round(abs(v) / CRORE)
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ("-" if v < 0 else "") + ",".join(groups + [tail])


def stale_note(result, filings):
    """A note when NSE already has results for a later period than Yahoo's numbers."""
    rep = ((filings or {}).get("results") or [None])[0]
    if not rep or not rep.get("period") or not result["rows"]:
        return None
    latest = result["rows"][0]["end"]
    if rep["period"] <= latest:
        return None
    return ("Yahoo's numbers stop at %s, but NSE shows results for the period ending %s, filed %s. "
            "Read the filing: %s" % (latest, rep["period"], rep["filed"], rep["url"]))


def gap_notes(result):
    out = []
    if result["missing"]:
        out.append("GAP: Yahoo has no numbers for the quarter ending %s. Read the NSE filing for it."
                   % ", ".join(result["missing"]))
    if result["no_year_ago"]:
        out.append("No year-on-year for %s: the year-ago quarter is missing." % ", ".join(result["no_year_ago"]))
    if "revenue" not in result["ttm"]:
        out.append("Last 4 quarters not shown: Yahoo does not have four consecutive quarters.")
    return out


def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    # only a true neighbour counts as "the quarter before": Yahoo can skip quarters
    prev = rows[1] if len(rows) > 1 and consecutive(cur["end"], rows[1]["end"]) else None
    out = []

    def money(v):
        return "n/a" if v is None else "₹%s crore" % crore(v)

    line = "Quarter ending %s: revenue %s" % (cur["end"], money(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net income %s" % money(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS ₹%.2f" % cur["eps_diluted"]
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                      ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if prev and prev[key] is not None:
            s += " (%+.1f points vs the quarter before)" % (cur[key] - prev[key])
        out.append(s + ".")
    if cur["net_income"] is not None and cur["operating_income"] is not None \
            and cur["net_income"] > cur["operating_income"]:
        out.append("Net income is above operating income: it includes gains outside the "
                   "business (investments, tax items). Check the results filing before reading it as growth.")
    if prev and cur["revenue"] is not None and prev["revenue"]:
        out.append("Revenue %+.1f%% vs the quarter before (many businesses are seasonal: "
                   "compare with a year earlier first)." % pct(cur["revenue"], prev["revenue"]))
    return out


# ---------------------------------------------------------------- filings

PERIOD = re.compile(r"\bended\s+(?:on\s+)?(?:the\s+)?([A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
                    r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\.?,?\s+\d{4})")


def period_end(text):
    """The first "ended September 30, 2026" or "ended 30 June 2026" -> ISO date, or None."""
    m = PERIOD.search(" ".join((text or "").split()))
    if not m:
        return None
    raw = re.sub(r"(?<=\d)(?:st|nd|rd|th)|[.,]", "", m.group(1))
    for fmt in ("%B %d %Y", "%d %B %Y", "%b %d %Y", "%d %b %Y"):
        try:
            return _dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def results_filings(rows):
    """Results announcements, newest first, one per period (a company often files two PDFs)."""
    found = []
    for r in rows:
        desc, text = r.get("desc") or "", r.get("attchmntText") or ""
        if not re.search(r"financial result|outcome of board", desc, re.I):
            continue
        if not re.search(r"financial (?:results?|statements?)", text, re.I):
            continue
        try:
            when = _dt.datetime.strptime(r.get("an_dt", ""), "%d-%b-%Y %H:%M:%S")
        except ValueError:
            continue
        found.append((when, {"filed": when.date().isoformat(), "period": period_end(text),
                             "url": r.get("attchmntFile") or "", "name": r.get("sm_name")}))
    found.sort(key=lambda x: x[0], reverse=True)
    out, seen = [], set()
    for _, f in found:
        key = f["period"] or f["filed"]
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _iso_day(text):
    """NSE's "16-Jan-2025 20:15" -> 2025-01-16, or "" when it is not that shape."""
    try:
        return _dt.datetime.strptime((text or "").split(" ")[0], "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def latest_xbrl(rows):
    """Newest quarter on NSE's results list, consolidated when there is one."""
    best = None
    for r in rows:
        try:
            end = _dt.datetime.strptime(r.get("toDate", ""), "%d-%b-%Y").date().isoformat()
        except ValueError:
            continue
        rank = (end, r.get("consolidated") == "Consolidated")
        if best is None or rank > best[0]:
            best = (rank, {"period": end, "filed": _iso_day(r.get("filingDate")),
                           "basis": r.get("consolidated"), "url": r.get("xbrl") or "",
                           "name": r.get("companyName")})
    return best[1] if best else None


def latest_filings(fetcher, sym, today=None):
    """NSE's links. Either endpoint can refuse: the numbers still print, with a note."""
    today = today or _dt.date.today()
    code = urllib.parse.quote(sym, safe="-")
    out = {"results": [], "xbrl": None, "notes": []}
    since = today - _dt.timedelta(days=LOOKBACK_DAYS)
    try:
        rows = fetcher.json(ANNOUNCEMENTS % (code, since.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y")),
                            "ann-%s.json" % sym)
        out["results"] = results_filings(rows if isinstance(rows, list) else [])
    except FactsError as exc:
        out["notes"].append("NSE announcements: %s" % exc)
    try:
        rows = fetcher.json(RESULTS_LIST % code, "nsefin-%s.json" % sym)
        out["xbrl"] = latest_xbrl(rows if isinstance(rows, list) else [])
    except FactsError as exc:
        out["notes"].append("NSE results list: %s" % exc)
    return out


# ---------------------------------------------------------------- output

def _p(v):
    return "-" if v is None else "%+.1f%%" % v if v < 0 or v > 0 else "0.0%"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(ticker, result, filings):
    out = ["%s (%s). ₹ crore except EPS. Source: Yahoo Finance (unofficial), NSE filings for links." % (
        result["entity"] or ticker, ticker), ""]
    out.append("%-11s %-8s %10s %8s %7s %7s %7s %10s %8s %7s" % (
        "quarter end", "fiscal", "revenue", "rev yoy", "gross", "oper", "net",
        "net inc", "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        out.append("%-11s %-8s %10s %8s %7s %7s %7s %10s %8s %7s" % (
            r["end"], r["fiscal"], crore(r["revenue"]), _p(r["revenue_yoy"]),
            _pm(r["gross_margin"]), _pm(r["operating_margin"]), _pm(r["net_margin"]),
            crore(r["net_income"]), "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"],
            _p(r["eps_yoy"])))
    t = result["ttm"]
    if t:
        out.append("")
        out.append("Last 4 quarters: revenue %s, net income %s, diluted EPS %s." % (
            "₹%s crore" % crore(t["revenue"]) if t.get("revenue") is not None else "-",
            "₹%s crore" % crore(t["net_income"]) if t.get("net_income") is not None else "-",
            "₹%.2f" % t["eps_diluted"] if t.get("eps_diluted") is not None else "-"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    notes = gap_notes(result)
    if notes:
        out.append("")
        out.extend(notes)
    if filings:
        out.append("")
        for f in filings["results"][:3]:
            out.append("Results filing: period %s, filed %s  %s" % (f["period"] or "not stated", f["filed"], f["url"]))
        x = filings["xbrl"]
        if x:
            out.append("Newest on NSE's XBRL list: period %s, %s, filed %s  %s" % (
                x["period"], x["basis"], x["filed"], x["url"]))
        for n in filings["notes"]:
            out.append("note: " + n)
    note = stale_note(result, filings)
    if note:
        out.append("")
        out.append("NOTE: " + note)
    return "\n".join(out) + "\n"


def metrics_row(ticker, result):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"ticker": ticker, "name": result["entity"],
            "period_end": cur.get("end"), "revenue_ttm": t.get("revenue"),
            "net_income_ttm": t.get("net_income"), "eps_ttm": t.get("eps_diluted"),
            "revenue_growth_yoy": cur.get("revenue_yoy"), "eps_growth_yoy": cur.get("eps_yoy"),
            "net_income_growth_yoy": cur.get("net_income_yoy"),
            "gross_margin": cur.get("gross_margin"), "operating_margin": cur.get("operating_margin"),
            "net_margin": cur.get("net_margin"), "currency": "INR", "source": "Yahoo Finance timeseries"}


def company_name(filings):
    for f in (filings or {}).get("results") or []:
        if f.get("name"):
            return f["name"]
    return ((filings or {}).get("xbrl") or {}).get("name")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="+", help="NSE symbols, e.g. RELIANCE M&M")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--no-filings", action="store_true", help="skip NSE's results links")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per ticker into this JSON")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0
    for t in args.tickers:
        try:
            tick = clean_ticker(t)
            doc = fetch_numbers(fetcher, tick)
            filings = None if args.no_filings else latest_filings(fetcher, tick)
            result = analyse(doc, quarters=args.quarters, name=company_name(filings))
        except FactsError as exc:
            print("facts: %s: %s" % (t, exc), file=sys.stderr)
            failed += 1
            continue
        results.append({"ticker": tick, "result": result, "filings": filings, "changes": changes(result),
                        "gaps": gap_notes(result), "note": stale_note(result, filings)})
        rows.append(metrics_row(tick, result))
        if not args.json:
            sys.stdout.write(render(tick, result, filings))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2))
    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = {r["ticker"]: r for r in json.load(fh).get("companies", [])}
        for r in rows:
            existing[r["ticker"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": _dt.date.today().isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r["ticker"])},
                      fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())

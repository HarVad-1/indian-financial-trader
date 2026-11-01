#!/usr/bin/env python3
"""News monitor for trader-news. Standard library only.

Watches headlines and NSE company announcements for the stocks you watch or
hold and prints only what is new since the last check, newest first, with how
far each stock has moved today and a flag on anything that touches your tickers.

    python3 news.py                                  # watchlist + plan + portfolio tickers
    python3 news.py --tickers RELIANCE M&M BAJAJ-AUTO
    python3 news.py --tickers RELIANCE --all --hours 72  # the last 3 days, seen or not
    python3 news.py --no-filings                     # headlines only
    python3 news.py --no-moves                       # skip the price line per ticker

Your tickers, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. Plain NSE symbols; a typed .NS is stripped.

Sources, one request each per ticker:
  - Google News RSS, searched on the company's name (Yahoo's headline feed is
    empty for NSE stocks):
    https://news.google.com/rss/search?q="Reliance Industries" share when:2d&hl=en-IN&gl=IN&ceid=IN:en
    Links go through Google's redirect. Some items carry only a date: they
    print "time n/a" and count as published some time that day.
  - NSE corporate announcements (what the company told the exchange):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYM>&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
    Share-certificate losses, newspaper clippings and trading-window notices
    are dropped as noise. NSE's endpoint is unofficial: if it refuses, the tool
    says so and the headlines still run.
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYM>.NS?range=1d&interval=5m
  - Company names: NSE's list of equities, read once per new ticker:
    https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv

Times print in IST. A --now with no offset is IST. State:
trader/state/news.json remembers what you have seen (30 days), so an item
shows once. Offline: --offline DIR reads news-<SYM>.xml, nse-<SYM>.json,
chart-<SYM>.json and EQUITY_L.csv from DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import csv
import datetime as _dt
import email.utils
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

GOOGLE_NEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_FEED = ("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s"
            "&from_date=%s&to_date=%s")
NSE_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
YAHOO_UA = "Mozilla/5.0 (compatible; indian-financial-trader/1.0; +https://github.com/HarVad-1/indian-financial-trader)"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")
NOISE = re.compile(r"loss of share certificate|duplicate share|newspaper publication|trading window", re.I)
IMPORTANT = re.compile(r"financial result|outcome of board|action\(s\) taken|acquisition|scheme of arrangement"
                       r"|buyback|change in (?:director|management)|resignation|cessation|fraud|insolvency"
                       r"|default|delisting|restructuring|clarification|news verification|credit rating", re.I)
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b|\bnet profit\b|\bPAT\b|\bQ[1-4]\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|(?:price )?target price|price target|\boutperform\b|\bunderperform\b|\bbrokerages?\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bNCLT\b|\bantitrust\b|\bjudge\b|\bcourt\b|\braids?\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bdeal\b|\bstake\b|\bblock deal\b|\bbulk deal\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbonus issue\b|\brights issue\b|\bQIP\b|\bIPO\b|\bstock split\b|\boffer for sale\b|\bOFS\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\binsolvency\b|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b|\bpledge"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\b(?:upper|lower) circuit\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bCCI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bGST (?:notice|demand)\b"),
    ("macro", r"\bFed\b|\brepo rate\b|\bMPC\b|\bCPI\b|\binflation\b|\bBudget\b|\btariffs?\b|\bNifty\b|\bSensex\b|\bFIIs?\b|\bFPIs?\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))  # India has no daylight time
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)


def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


def clean_ticker(text):
    """RELIANCE, m&m, RELIANCE.NS -> the plain NSE symbol. Raises on anything else."""
    sym = re.sub(r"\.NS$", "", str(text).strip().upper())
    if not TICKER.match(sym):
        raise NewsError("%r is not an NSE symbol" % text)
    return sym


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
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
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except (urllib.error.URLError, OSError) as exc:
            raise NewsError("could not reach %s: %s" % (url, getattr(exc, "reason", exc)))


def yahoo_symbol(ticker):
    """RELIANCE -> RELIANCE.NS, M&M -> M%26M.NS (the NSE board on Yahoo)."""
    return urllib.parse.quote(clean_ticker(ticker), safe="-") + ".NS"


def short_name(name):
    return re.sub(r"\s+(?:limited|ltd\.?)$", "", (name or "").strip(), flags=re.I)


def about_company(title, ticker, name):
    """Does a headline name the company? Google's search also returns stories that only
    mention it deep in the text. The first word, the symbol or the initials must be in the title."""
    # ponytail: first-word match lets "Tata Steel" through for Tata Motors and misses
    # nicknames; --loose shows everything if a real story goes missing
    short = short_name(name)
    if not short:
        return True
    words = [w for w in re.findall(r"[A-Za-z0-9]+", short) if w.lower() not in ("of", "and", "the")]
    initials = "".join(w[0] for w in words).upper()
    upper = {ticker, initials, initials + "L"}
    if any(re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(u), title) for u in upper if len(u) >= 2):
        return True
    return bool(re.search(r"\b%s\b" % re.escape(words[0]), title, re.I)) if words else True


def fill_names(fetcher, watch, state, errors):
    """Company names for the search and for mentions, from NSE's equity list.
    Only the tickers in use are cached in the state."""
    cached = state.setdefault("names", {})
    for t in watch:
        watch[t] = watch[t] or cached.get(t)
    if all(watch.values()):
        return watch
    try:
        text = fetcher.get(NSE_LIST, "EQUITY_L.csv").decode("utf-8", "replace")
    except NewsError as exc:
        errors.append("NSE company list: %s (searching on the symbol instead)" % exc)
        return watch
    listed = {r["SYMBOL"].strip(): r["NAME OF COMPANY"].strip()
              for r in csv.DictReader(io.StringIO(text)) if r.get("SYMBOL")}
    for t in watch:
        if not watch[t] and t in listed:
            watch[t] = cached[t] = listed[t]
    return watch


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None. ISO with no offset is IST."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
    return d.astimezone(_dt.timezone.utc)


def parse_nse_time(value):
    """NSE's "19-Oct-2026 08:04:11" is IST -> aware UTC datetime, or None."""
    try:
        d = _dt.datetime.strptime((value or "").strip(), "%d-%b-%Y %H:%M:%S")
    except ValueError:
        return None
    return d.replace(tzinfo=IST).astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Google News RSS -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        source = _text(it, "source")
        if source and title.endswith(" - " + source):
            title = title[:-len(source) - 3].strip()
        pub = _text(it, "pubDate")
        # ponytail: 07:00:00 GMT is how Google marks a date with no time; a story truly
        # published at that second prints "time n/a" too, harmless
        out.append({"id": "rss:" + guid, "source": source or "Google News", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": "", "link": link,
                    "time": parse_time(pub), "date_only": pub.endswith(" 07:00:00 GMT")})
    return out


def parse_nse(data, ticker):
    """NSE corporate announcements JSON -> items, without the routine noise."""
    try:
        rows = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("NSE sent something other than JSON for %s" % ticker)
    if not isinstance(rows, list):
        raise NewsError("NSE sent no announcement list for %s" % ticker)
    out = []
    for r in rows:
        desc = (r.get("desc") or "").strip()
        text = " ".join((r.get("attchmntText") or "").split())
        if NOISE.search(desc):
            continue
        title = "%s: %s" % (desc, text[:110]) if text else desc
        link = r.get("attchmntFile") or ""
        out.append({"id": "nse:%s" % (r.get("seq_id") or "%s|%s" % (r.get("an_dt"), desc)),
                    "source": "NSE", "kind": "filing", "form": desc, "ticker": ticker,
                    "title": title, "summary": text, "important": bool(IMPORTANT.search(desc)),
                    "link": link if link.startswith("http") else "",
                    "time": parse_nse_time(r.get("an_dt")), "date_only": False})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(CHART % ("query2", yahoo_symbol(t)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", yahoo_symbol(t)), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\(%s\)" % re.escape(t), r"\b(?:NSE|BSE)\s*:\s*%s\b" % re.escape(t)]
        name = short_name(name)
        if len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, now, hours=48, filings=True, errors=None, loose=False):
    errors = [] if errors is None else errors
    items = []
    days = max(1, int(math.ceil(hours / 24.0)))
    today = now.astimezone(IST)
    since = (today - _dt.timedelta(hours=hours)).strftime("%d-%m-%Y")
    for t in sorted(watch):
        words = '"%s" share when:%dd' % (short_name(watch[t]) or t, days)
        try:
            found = parse_rss(fetcher.get(GOOGLE_NEWS % urllib.parse.quote_plus(words), "news-%s.xml" % t), t)
            kept = [i for i in found if loose or about_company(i["title"], t, watch[t])]
            if len(kept) < len(found):
                errors.append("%d headlines for %s hidden: the title never names it (--loose shows them)"
                              % (len(found) - len(kept), t))
            items += kept
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
        if filings:
            url = NSE_FEED % (urllib.parse.quote(t, safe="-"), since, today.strftime("%d-%m-%Y"))
            try:
                items += parse_nse(fetcher.get(url, "nse-%s.json" % t), t)
            except NewsError as exc:
                errors.append("NSE announcements for %s: %s" % (t, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    today = now.astimezone(IST).date()
    for it in items:
        if it.get("date_only") and it["time"] is not None:     # a date, not a moment: today counts
            day = it["time"].astimezone(IST).date()
            if day > today or day < cutoff.astimezone(IST).date():
                continue
        elif it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-6s ₹%s %+.2f%% vs yesterday's close ₹%s%s  %s" % (
            m["ticker"], "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        if it.get("date_only") and it["time"]:
            when = to_ist(it["time"]).strftime("%Y-%m-%d time n/a  ")
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-filings", action="store_true", help="headlines only")
    ap.add_argument("--loose", action="store_true", help="keep headlines whose title never names the company")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time (IST with no offset), for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        wanted = [clean_ticker(t) for t in args.tickers or []]
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    known = watch_from_files(args.portfolio, args.watchlist, args.plan)
    watch = {t: known.get(t) for t in wanted} if wanted else known
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    if now is None:
        print("news: --now wants an ISO time like 2026-10-19T09:50", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    fill_names(fetcher, watch, state, errors)
    items = collect(fetcher, watch, now, hours=args.hours, filings=not args.no_filings, errors=errors,
                    loose=args.loose)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())

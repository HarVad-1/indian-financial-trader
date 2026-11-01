#!/usr/bin/env python3
"""Big-trade and insider tracker for trader-deals. Standard library only.

Reads what NSE publishes about large trades and company insiders:

    python3 deals.py show                                # the last session's bulk and block deals
    python3 deals.py show --kind block --client goldman  # one list, one client
    python3 deals.py show --symbol SWIGGY --json
    python3 deals.py insiders --symbol RELIANCE          # insider filings for one stock
    python3 deals.py insiders --from 2026-01-01 --to 2026-03-01   # every stock, by date
    python3 deals.py new --symbol M&M --client "mutual fund"      # only what is new since last check

Where the data comes from (public NSE pages; a browser User-Agent is required):
  - Bulk deals:  https://nsearchives.nseindia.com/content/equities/bulk.csv
  - Block deals: https://nsearchives.nseindia.com/content/equities/block.csv
  - Insiders:    https://www.nseindia.com/api/corporates-pit?index=equities&symbol=RELIANCE
    (optional from_date and to_date, DD-MM-YYYY). With a symbol and no dates NSE
    sends a short recent list. Every stock at once needs dates.

What these rows are, and are not:
  - Both CSVs hold one session and post after it ends, so the trade is already over.
  - A bulk deal is a client trading over 0.5% of a company's equity in a day.
    Many are one client buying and selling the same stock the same day. Those
    rows are tagged "in and out".
  - An insider row can be a gift, a pledge or an off-market transfer. Only
    "Market Purchase" and "Market Sale" are trades on the exchange.
  - None of it says what the trader will do next or why they traded.

State: `new` remembers the rows it has shown in trader/state/deals.json, so each
alerts once. On the first run it shows only the last 30 days. Rows in stocks
listed in trader/portfolio.json are marked HELD, and `new` also reads insider
filings for those stocks. Offline: --offline DIR reads bulk.csv, block.csv and
pit-<SYMBOL>.json from a folder instead of the network. The tests use it.

Exit codes: 0 rows found, 1 nothing found, 2 an error (for `new`, every source failed).
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CSV_URL = "https://nsearchives.nseindia.com/content/equities/%s.csv"
PIT_URL = "https://www.nseindia.com/api/corporates-pit?%s"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Referer": "https://www.nseindia.com/"}
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
PAUSE = 1.0        # seconds between requests to NSE
FIRST_DAYS = 30    # the first `new` run only shows this far back
STALE_DAYS = 30    # warn when the newest insider filing is older than this
MARKET_TRADES = ("Market Purchase", "Market Sale")
CSV_COLUMNS = ("Date", "Symbol", "Client Name", "Buy/Sell", "Quantity Traded")
PRICE_COLUMN = "Trade Price / Wght. Avg. Price"


class DealsError(Exception):
    pass


def today_ist():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + IST_OFFSET).date()


def clean_symbol(text):
    """'m&m.ns' -> 'M&M'. Anything else that is not an NSE symbol is refused."""
    sym = re.sub(r"\.NS$", "", str(text or "").strip().upper())
    if not TICKER.match(sym):
        raise DealsError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return sym


def parse_date(text):
    """'30-OCT-2026' or '18-Feb-2026 19:06' -> '2026-10-30'. None if it is not a date."""
    try:
        return datetime.datetime.strptime(str(text or "").strip()[:11], "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def number(text):
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


def rupees(x):
    return "-" if x is None else "₹{:,.2f}".format(x)


def count(x):
    return "-" if x is None else "{:,.0f}".format(x)


def download(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise DealsError("NSE answered HTTP %s for %s" % (exc.code, url))
    except (urllib.error.URLError, OSError) as exc:
        raise DealsError("could not reach %s: %s" % (url, getattr(exc, "reason", exc)))


def read_file(folder, name):
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise DealsError("no offline file %s" % path)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- bulk and block

def parse_deals(text, kind):
    """One bulk.csv or block.csv as rows. A line with no real date (NSE's 'NO
    RECORDS' on a quiet day) is skipped. Same client, stock and day on both sides
    is tagged in_and_out."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = [(f or "").strip() for f in (reader.fieldnames or [])]
    if not all(c in fields for c in CSV_COLUMNS):
        raise DealsError("NSE's %s list did not come back as the expected CSV" % kind)
    reader.fieldnames = fields
    rows = []
    for raw in reader:
        date = parse_date(raw.get("Date"))
        if not date:
            continue
        symbol = (raw.get("Symbol") or "").strip().upper()
        client = " ".join((raw.get("Client Name") or "").split())
        side = (raw.get("Buy/Sell") or "").strip().upper()
        qty, price = number(raw.get("Quantity Traded")), number(raw.get(PRICE_COLUMN))
        rows.append({"kind": kind, "date": date, "symbol": symbol,
                     "name": (raw.get("Security Name") or "").strip(), "client": client, "side": side,
                     "qty": qty, "price": price,
                     "value_inr": round(qty * price, 2) if qty is not None and price is not None else None,
                     "id": "|".join(str(x) for x in (kind, date, symbol, client, side, qty, price))})
    sides = {}
    for r in rows:
        sides.setdefault((r["date"], r["symbol"], r["client"]), set()).add(r["side"])
    for r in rows:
        r["in_and_out"] = sides[(r["date"], r["symbol"], r["client"])] >= {"BUY", "SELL"}
    return rows


def load_deals(kind, offline=None):
    name = "%s.csv" % kind
    return parse_deals(read_file(offline, name) if offline else download(CSV_URL % kind), kind)


# ---------------------------------------------------------------- insiders

def filed_at(row):
    try:
        return datetime.datetime.strptime(str(row["filed"]), "%d-%b-%Y %H:%M")
    except ValueError:
        return datetime.datetime.min


def parse_insiders(text):
    """NSE's corporates-pit JSON as rows, newest filing first."""
    try:
        data = json.loads(text)["data"]
    except (ValueError, KeyError, TypeError):
        raise DealsError("NSE's insider list did not come back as the expected JSON")
    rows = []
    for raw in data:
        qty, value = number(raw.get("secAcq")), number(raw.get("secVal"))
        mode = (raw.get("acqMode") or "").strip()
        rows.append({"kind": "insider", "date": parse_date(raw.get("date")), "filed": raw.get("date"),
                     "symbol": (raw.get("symbol") or "").strip().upper(), "name": (raw.get("company") or "").strip(),
                     "client": " ".join((raw.get("acqName") or "").split()),
                     "category": (raw.get("personCategory") or "").strip(),
                     "side": (raw.get("tdpTransactionType") or "").strip(), "mode": mode,
                     "market_trade": mode in MARKET_TRADES,
                     "trade_from": parse_date(raw.get("acqfromDt")), "trade_to": parse_date(raw.get("acqtoDt")),
                     "qty": qty, "value_inr": value,
                     "price": round(value / qty, 2) if qty and value else None,
                     "url": raw.get("xbrl"), "in_and_out": False,
                     "id": "insider|%s|%s|%s" % (raw.get("did"), raw.get("pid"), raw.get("acqName"))})
    rows.sort(key=filed_at, reverse=True)
    return rows


def nse_date(text, flag):
    """2026-01-31 or 31-01-2026 -> 31-01-2026, the form NSE wants."""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%d-%m-%Y")
        except ValueError:
            pass
    raise DealsError("%s wants a date like 2026-01-31, not %r" % (flag, text))


def load_insiders(symbol=None, start=None, end=None, offline=None):
    if offline:
        if not symbol:
            raise DealsError("--offline insiders needs --symbol")
        return parse_insiders(read_file(offline, "pit-%s.json" % symbol))
    params = [("index", "equities")]
    if symbol:
        params.append(("symbol", symbol))
    if start:
        params += [("from_date", start), ("to_date", end)]
    return parse_insiders(download(PIT_URL % urllib.parse.urlencode(params)))


def stale_note(rows, today):
    """The insider feed returned nothing after 2 May 2026 when this was written, so say when it looks old."""
    dates = [r["date"] for r in rows if r["kind"] == "insider" and r["date"]]
    if dates and (today - datetime.date.fromisoformat(max(dates))).days > STALE_DAYS:
        return ("Note: the newest insider filing here is dated %s. NSE's feed may have stopped updating, "
                "so no recent rows does not mean no recent trades. Check nseindia.com." % max(dates))
    return None


# ---------------------------------------------------------------- filters and state

def select(rows, symbols=None, clients=None):
    """Keep a row when it matches a symbol or a client. No filters keep everything."""
    if not symbols and not clients:
        return list(rows)
    wanted = [c.lower() for c in clients or []]
    return [r for r in rows if r["symbol"] in (symbols or []) or any(c in r["client"].lower() for c in wanted)]


def held_symbols(path):
    if not path or not os.path.isfile(path):
        return set()
    try:
        with open(path, encoding="utf-8") as fh:
            positions = json.load(fh).get("positions", [])
        return {str(p.get("ticker") or p.get("symbol")).upper() for p in positions
                if p.get("ticker") or p.get("symbol")}
    except (ValueError, AttributeError):
        raise DealsError("%s is not a valid portfolio file" % path)


def load_state(path):
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            raise DealsError("%s is not valid JSON: fix or delete it" % path)
    return {"seen": [], "last_check": None}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def new_since(rows, state, today, first_days=FIRST_DAYS):
    """Rows not seen before. On the first run: only the last first_days days."""
    if state.get("last_check") is None:
        cutoff = (today - datetime.timedelta(days=first_days)).isoformat()
        return [r for r in rows if (r["date"] or "") >= cutoff]
    seen = set(state.get("seen", []))
    return [r for r in rows if r["id"] not in seen]


# ---------------------------------------------------------------- output

def render(row, held=()):
    flag = "HELD " if row["symbol"] in held else ""
    if row["kind"] == "insider":
        tag = "" if row["market_trade"] else "  (not a market trade)"
        return "%s%s  insider  %-12s %s %s (%s): %s, %s shares, %s, %s%s" % (
            flag, row["date"], row["symbol"], row["client"], row["side"].lower(), row["category"] or "-",
            row["mode"] or "-", count(row["qty"]), rupees(row["value_inr"]),
            "traded %s" % (row["trade_from"] or "-"), tag)
    return "%s%s  %-7s  %-12s %-4s %s shares at %s (%s)  %s%s" % (
        flag, row["date"], row["kind"], row["symbol"], row["side"], count(row["qty"]), rupees(row["price"]),
        rupees(row["value_inr"]), row["client"], "  (in and out)" if row["in_and_out"] else "")


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--offline", metavar="DIR", help="read saved NSE files from this folder")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--today", help="YYYY-MM-DD, for reproducible runs")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("show", help="the last session's bulk and block deals")
    p.add_argument("--kind", choices=("bulk", "block", "all"), default="all")
    p.add_argument("--symbol", action="append", help="repeat for more stocks")
    p.add_argument("--client", action="append", help="part of a client's name; repeat for more")
    common(p)

    p = sub.add_parser("insiders", help="insider filings for one stock, or every stock by date")
    p.add_argument("--symbol")
    p.add_argument("--from", dest="start", help="first filing date, YYYY-MM-DD")
    p.add_argument("--to", dest="end", help="last filing date, YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=50)
    common(p)

    p = sub.add_parser("new", help="rows not seen at the last check")
    p.add_argument("--symbol", action="append", help="repeat for more stocks")
    p.add_argument("--client", action="append", help="part of a client's name; repeat for more")
    p.add_argument("--state", default="trader/state/deals.json")
    p.add_argument("--dry-run", action="store_true", help="do not update the state file")
    common(p)
    return ap


def _today(args):
    if not args.today:
        return today_ist()
    try:
        return datetime.date.fromisoformat(args.today)
    except ValueError:
        raise DealsError("--today wants YYYY-MM-DD, not %r" % args.today)


def _show(args):
    symbols = [clean_symbol(s) for s in args.symbol or []]
    held = held_symbols(args.portfolio)
    rows = []
    for i, kind in enumerate(("bulk", "block") if args.kind == "all" else (args.kind,)):
        if i and not args.offline:
            time.sleep(PAUSE)
        rows += load_deals(kind, args.offline)
    picked = select(rows, symbols, args.client)
    if args.json:
        print(json.dumps(picked, indent=2, ensure_ascii=False))
    else:
        for r in picked:
            print(render(r, held))
        if not picked:
            print("No matching deals in NSE's latest lists.")
        else:
            print("Posted after the session ended. A big trade is not a signal by itself. Your call.")
    return 0 if picked else 1


def _insiders(args):
    symbol = clean_symbol(args.symbol) if args.symbol else None
    if bool(args.start) != bool(args.end):
        raise DealsError("--from and --to go together")
    if not symbol and not args.start:
        raise DealsError("insiders needs --symbol, or --from and --to for every stock")
    start = nse_date(args.start, "--from") if args.start else None
    end = nse_date(args.end, "--to") if args.end else None
    rows = load_insiders(symbol, start, end, args.offline)[:args.limit]
    held = held_symbols(args.portfolio)
    note = stale_note(rows, _today(args))
    if args.json:
        print(json.dumps({"rows": rows, "note": note}, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(render(r, held))
        if not rows:
            print("No insider filings came back. NSE's feed may be behind: check nseindia.com.")
        if note:
            print(note)
    return 0 if rows else 1


def _new(args):
    today = _today(args)
    symbols = [clean_symbol(s) for s in args.symbol or []]
    held = held_symbols(args.portfolio)
    state = load_state(args.state)
    insider_symbols = sorted(set(symbols) | {s for s in held if TICKER.match(s)})
    rows, errors, sources = [], [], 0
    for kind in ("bulk", "block"):
        sources += 1
        try:
            if sources > 1 and not args.offline:
                time.sleep(PAUSE)
            # a stock you hold counts as a filter, but only when you asked to filter at all
            rows += select(load_deals(kind, args.offline), symbols + sorted(held) if symbols or args.client else None,
                           args.client)
        except DealsError as exc:
            errors.append(str(exc))
    for sym in insider_symbols:
        sources += 1
        try:
            if not args.offline:
                time.sleep(PAUSE)
            rows += load_insiders(sym, offline=args.offline)
        except DealsError as exc:
            errors.append(str(exc))
    if len(errors) == sources:
        raise DealsError("; ".join(errors))
    fresh = new_since(rows, state, today)
    note = stale_note(rows, today)
    if args.json:
        print(json.dumps({"new": fresh, "errors": errors, "note": note}, indent=2, ensure_ascii=False))
    else:
        if not fresh:
            print("Nothing new since %s." % (state.get("last_check") or "the last check"))
        for r in fresh:
            print("NEW " + render(r, held))
        if note:
            print(note)
    for e in errors:
        print("deals: %s" % e, file=sys.stderr)
    if not args.dry_run:
        state["seen"] = sorted(set(state.get("seen", [])) | {r["id"] for r in rows})
        state["last_check"] = today.isoformat()
        save_state(args.state, state)
    return 0 if fresh else 1


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        return {"show": _show, "insiders": _insiders, "new": _new}[args.cmd](args)
    except (DealsError, OSError) as exc:
        print("deals: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

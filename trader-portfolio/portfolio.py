#!/usr/bin/env python3
"""Portfolio view for trader-portfolio. Standard library only, no network.

Reads your holdings from a JSON or CSV file (a Groww export, or a read-only
Groww MCP tool's answer that Claude saved to a file), then prints value, P&L,
weights and concentration flags. It reports facts. It never says what to buy
or sell, and it never talks to a broker.

    python3 portfolio.py holdings.json
    python3 portfolio.py holdings.csv --cash 25000 --write    # -> trader/portfolio.json
    python3 portfolio.py holdings.json --prices trader/prices.json --json

Column names differ between exports and MCP servers, so common names are
mapped (below). A file it cannot map is an error naming the columns it saw;
--map symbol=my_col,qty=my_col2 names them yourself. Needs a symbol (or a
name), a quantity and an average price. The last price comes from the file,
else from trader/prices.json, else the holding is valued at cost and says so.

--write needs a cash figure: --cash, a "cash" key in the file, or the cash
already in trader/portfolio.json. Holdings replace the positions there, in the
shape trader-budget, trader-risk and trader-ticket read.
"""

import argparse
import csv
import datetime
import json
import os
import re
import sys

ALIASES = {
    "symbol": ("symbol", "trading_symbol", "tradingsymbol", "ticker", "nse_symbol", "scrip", "scrip_code"),
    "name": ("stock_name", "company_name", "company", "name", "instrument", "stock", "security_name"),
    "qty": ("quantity", "qty", "shares", "holding_quantity", "net_quantity", "total_quantity", "demat_quantity"),
    "avg": ("average_price", "avg_price", "averageprice", "avgprice", "average_buy_price", "avg_buy_price",
            "average_cost", "avg_cost", "buy_price", "buy_avg", "cost_price"),
    "ltp": ("ltp", "last_price", "last_traded_price", "current_price", "market_price", "cmp", "closing_price",
            "close_price", "close"),
}
CASH_KEYS = ("cash", "available_cash", "available_balance", "cash_balance")
WRAPPERS = ("holdings", "payload", "data", "result", "results", "positions")
MAX_WEIGHT_PCT = 25.0
MAX_TOP3_PCT = 60.0


class PortfolioError(Exception):
    pass


def norm(key):
    """"Average buy price" -> "average_buy_price"."""
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def number(x):
    """1,22,780.50 or ₹500 -> a float, None if it is not a number."""
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(re.sub(r"[₹,\s]|^Rs\.?", "", str(x)))
    except ValueError:
        return None


def columns(keys, overrides=None):
    """{field: original key} for the fields these keys can fill."""
    by_norm = {norm(k): k for k in keys}
    out = {}
    for field, names in ALIASES.items():
        wanted = (overrides or {}).get(field)
        if wanted:
            if norm(wanted) not in by_norm:
                raise PortfolioError("--map %s=%s: no such column. Columns seen: %s"
                                     % (field, wanted, ", ".join(map(str, keys))))
            out[field] = by_norm[norm(wanted)]
            continue
        for n in names:
            if n in by_norm:
                out[field] = by_norm[n]
                break
    return out


def usable(cols):
    return ("symbol" in cols or "name" in cols) and "qty" in cols


def find_rows(doc):
    """(list of holding dicts, cash or None) from an answer of unknown shape."""
    cash = None
    for _ in range(4):
        if isinstance(doc, list):
            return doc, cash
        if not isinstance(doc, dict):
            break
        low = {norm(k): v for k, v in doc.items()}
        for k in CASH_KEYS:
            if cash is None and number(low.get(k)) is not None:
                cash = number(low[k])
        inner = [low[k] for k in WRAPPERS if isinstance(low.get(k), (list, dict))]
        if not inner:
            break
        doc = inner[0]
    raise PortfolioError("found no list of holdings in the JSON (looked under %s)" % ", ".join(WRAPPERS))


def read_csv_rows(path):
    """Exports often start with title lines: the header is the first row that maps."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    seen = []
    for i, row in enumerate(rows):
        if usable(columns(row)):
            return [dict(zip(row, r)) for r in rows[i + 1:] if any(c.strip() for c in r)], row
        if len(row) > 1 and not seen:
            seen = row
    raise PortfolioError("no header row in %s with a symbol or name and a quantity. Columns seen: %s. "
                         "Name them with --map symbol=...,qty=...,avg=..." % (path, ", ".join(seen) or "none"))


def read_holdings(path, overrides=None):
    """(rows as dicts, cash or None). Errors say what was seen."""
    if not os.path.exists(path):
        raise PortfolioError("no holdings file at %s" % path)
    if path.lower().endswith((".xlsx", ".xls")):
        raise PortfolioError("%s is a spreadsheet: save it as CSV first" % path)
    if path.lower().endswith(".csv"):
        if overrides:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            return rows, None
        return read_csv_rows(path)[0], None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except json.JSONDecodeError as e:
        raise PortfolioError("%s is not valid JSON: %s" % (path, e))
    return find_rows(doc)


def parse_rows(rows, overrides=None):
    """Rows -> [{"ticker", "label_only", "shares", "avg", "ltp"}]. Zero-quantity rows are dropped."""
    if not rows:
        raise PortfolioError("the file has no holdings")
    if not any(isinstance(r, dict) for r in rows):
        raise PortfolioError("each holding should be an object, got %r" % (rows[0],))
    keys = list(dict.fromkeys(k for r in rows if isinstance(r, dict) for k in r))   # a field may be on only some rows
    cols = columns(keys, overrides)
    missing = [w for w, ok in (("a symbol or name", "symbol" in cols or "name" in cols), ("a quantity", "qty" in cols),
                               ("an average price", "avg" in cols)) if not ok]
    if missing:
        raise PortfolioError("could not find %s. Columns seen: %s. Name them with --map symbol=...,qty=...,avg=..."
                             % (" or ".join(missing), ", ".join(map(str, keys))))
    out = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise PortfolioError("holding %d should be an object, got %r" % (i, r))
        sym = str(r.get(cols["symbol"]) or "").strip().upper() if "symbol" in cols else ""
        sym = re.sub(r"\.NS$", "", sym)
        label = sym or (str(r.get(cols["name"]) or "").strip() if "name" in cols else "")
        qty, avg = number(r.get(cols["qty"])), number(r.get(cols["avg"]))
        if qty is None and avg is None:
            continue                      # a totals or blank row
        if not label:
            raise PortfolioError("holding %d has no symbol or name: %r" % (i, r))
        if qty is None or qty < 0 or avg is None or avg < 0:
            raise PortfolioError("holding %d (%s): quantity %r and average price %r must be numbers, not negative"
                                 % (i, label, r.get(cols["qty"]), r.get(cols["avg"])))
        if qty == 0:
            continue
        ltp = number(r.get(cols["ltp"])) if "ltp" in cols else None
        out.append({"ticker": label, "label_only": not sym, "shares": qty, "avg": avg,
                    "ltp": ltp if ltp and ltp > 0 else None})
    if not out:
        raise PortfolioError("every holding in the file has zero quantity")
    return out


def summarise(holdings, prices=None, cash=None, max_weight=MAX_WEIGHT_PCT, max_top3=MAX_TOP3_PCT):
    """Pure function: value, P&L, weights, flags. Weights are of the holdings, cash excluded."""
    rows, warnings = [], []
    for h in holdings:
        ltp = h["ltp"] or (prices or {}).get(h["ticker"])
        invested = round(h["shares"] * h["avg"], 2)
        if ltp:
            value = round(h["shares"] * ltp, 2)
            pnl = round(value - invested, 2)
        else:
            value, pnl = invested, None
            warnings.append("no price for %s: valued at cost, P&L unknown" % h["ticker"])
        if h["label_only"]:
            warnings.append("%s is a name, not an NSE symbol: trader-risk cannot match it to an order" % h["ticker"])
        rows.append({"ticker": h["ticker"], "shares": h["shares"], "avg": h["avg"], "ltp": ltp, "invested": invested,
                     "value": value, "pnl": pnl,
                     "pnl_pct": round(100.0 * pnl / invested, 2) if pnl is not None and invested else None})
    total = sum(r["value"] for r in rows)
    rows.sort(key=lambda r: -r["value"])
    for r in rows:
        r["weight_pct"] = round(100.0 * r["value"] / total, 2) if total else 0.0
    priced = [r for r in rows if r["pnl"] is not None]
    pnl = round(sum(r["pnl"] for r in priced), 2)
    priced_cost = sum(r["invested"] for r in priced)
    flags = ["%s is %.1f%% of your holdings (over %g%%)" % (r["ticker"], r["weight_pct"], max_weight)
             for r in rows if r["weight_pct"] > max_weight]
    top3 = sum(r["weight_pct"] for r in rows[:3])
    if len(rows) > 3 and top3 > max_top3:
        flags.append("your top 3 holdings are %.1f%% of the total (over %g%%)" % (top3, max_top3))
    return {"positions": rows, "invested": round(sum(r["invested"] for r in rows), 2), "value": round(total, 2),
            "pnl": pnl, "pnl_pct": round(100.0 * pnl / priced_cost, 2) if priced_cost else None,
            "pnl_covers": len(priced), "cash": cash, "flags": flags, "warnings": warnings}


def money(x):
    """Indian digit grouping: ₹1,00,000 or -₹49,112.50."""
    x = float(x)
    rupees, paise = ("%.2f" % abs(x)).split(".")
    if len(rupees) > 3:
        rupees = re.sub(r"\B(?=(\d\d)+$)", ",", rupees[:-3]) + "," + rupees[-3:]
    return ("-" if x < 0 else "") + "₹" + rupees + ("" if paise == "00" else "." + paise)


def report(s):
    lines = ["%-20s %8s %10s %10s %13s %13s %7s" % ("holding", "qty", "avg", "last", "value", "P&L", "weight")]
    for r in s["positions"]:
        lines.append("%-20s %8g %10.2f %10s %13s %13s %6.1f%%" % (
            r["ticker"][:20], r["shares"], r["avg"], "%.2f" % r["ltp"] if r["ltp"] else "-", money(r["value"]),
            money(r["pnl"]) if r["pnl"] is not None else "-", r["weight_pct"]))
    lines.append("")
    lines.append("Invested %s. Value %s." % (money(s["invested"]), money(s["value"])))
    lines.append("P&L %s (%+.2f%%) on the %d of %d holdings with a price."
                 % (money(s["pnl"]), s["pnl_pct"] or 0, s["pnl_covers"], len(s["positions"])))
    if s["cash"] is not None:
        lines.append("Cash %s." % money(s["cash"]))
    for f in s["flags"]:
        lines.append("FLAG: " + f)
    for w in s["warnings"]:
        lines.append("WARNING: " + w)
    lines.append("Facts about your holdings, not a recommendation. Check them in Groww. Not financial advice.")
    return "\n".join(lines)


def portfolio_doc(s, cash, date):
    """The shape trader-budget, trader-risk and trader-ticket read."""
    return {"cash": cash, "source": "trader-portfolio", "as_of": date, "positions": [
        {"ticker": r["ticker"], "shares": r["shares"],
         "lots": [{"id": r["ticker"] + "-1", "date": date, "shares": r["shares"], "cost": r["invested"]}]}
        for r in s["positions"]]}


def load_prices(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except json.JSONDecodeError as e:
        raise PortfolioError("%s is not valid JSON: %s" % (path, e))
    prices = doc.get("prices", {}) if isinstance(doc, dict) else {}
    return {str(k).upper(): number(v) for k, v in prices.items() if number(v)}


def parse_map(text):
    out = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        if "=" not in part or part.split("=")[0].strip() not in ALIASES:
            raise PortfolioError("--map wants field=column pairs, fields: %s" % ", ".join(ALIASES))
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Value, P&L, weights and flags for your holdings (trader-portfolio).")
    ap.add_argument("file", help="holdings, JSON or CSV")
    ap.add_argument("--cash", type=float, help="cash in the account")
    ap.add_argument("--map", help="name columns yourself: symbol=col,qty=col,avg=col,ltp=col")
    ap.add_argument("--prices", default="trader/prices.json", help="last prices (default trader/prices.json if it exists)")
    ap.add_argument("--max-weight", type=float, default=MAX_WEIGHT_PCT, help="flag a holding over this percent")
    ap.add_argument("--max-top3", type=float, default=MAX_TOP3_PCT, help="flag a top 3 over this percent")
    ap.add_argument("--write", nargs="?", const="trader/portfolio.json", metavar="PATH",
                    help="write the portfolio (default trader/portfolio.json)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        overrides = parse_map(a.map)
        rows, file_cash = read_holdings(a.file, overrides)
        cash = a.cash if a.cash is not None else file_cash
        old_cash = None
        if a.write and os.path.exists(a.write):
            try:
                with open(a.write, encoding="utf-8") as fh:
                    old_cash = number(json.load(fh).get("cash"))
            except (json.JSONDecodeError, AttributeError) as e:
                raise PortfolioError("%s is not a portfolio file: %s" % (a.write, e))
        if cash is None:
            cash = old_cash
        s = summarise(parse_rows(rows, overrides), load_prices(a.prices), cash, a.max_weight, a.max_top3)
        if a.write:
            if cash is None:
                raise PortfolioError("--write needs your cash: pass --cash (0 is fine if that is true). "
                                     "trader-budget and trader-risk size against it")
            today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30))
            d = os.path.dirname(a.write)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = a.write + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(portfolio_doc(s, cash, today.date().isoformat()), fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.write)
    except PortfolioError as e:
        print("portfolio.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(s, indent=2, ensure_ascii=False) if a.json else report(s))
    if a.write and not a.json:
        print("Wrote %s." % a.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())

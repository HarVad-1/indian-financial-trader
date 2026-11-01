#!/usr/bin/env python3
"""Order tickets and the trade log for trader-ticket. Standard library only.

This tool never talks to a broker and never places anything. You decide, and
you place every order yourself in Groww. It prints what you would enter, then
records what you say you did:

  show      renders each proposed order as a plain ticket: what to enter in
            Groww, the stop and target to set, the max loss at the stop, and
            whether trader-risk passed THIS exact order today (matched by
            fingerprint).
  placed    records that you placed one order in Groww, so trader-risk can
            hold the daily limits. A log of what happened: it also records an
            order trader-risk blocked, and says so.
  skipped   records that you decided not to place it.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol's open position by
            flat_by. It still goes through trader-risk.
  log       prints today's records.

    python3 ticket.py show                    # every order
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py placed --index 1 --filled-qty 40 --filled-price 1227.85
    python3 ticket.py skipped --index 1 --note "spread too wide"
    python3 ticket.py fill --symbol RELIANCE --side sell --qty 40 --price 1240.25
    python3 ticket.py flatten --symbol RELIANCE --qty 40
    python3 ticket.py log

Everything is written to trader/state/ticket.json (append only). Dates are
IST dates; --now "2026-10-19 09:50" pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

ORDER_FIELDS = ("symbol", "exchange", "side", "qty", "product", "type", "entry", "stop", "target")
PRODUCT_WORDS = {"MIS": "Intraday", "CNC": "Delivery"}


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, no tz files)

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)  # India has no daylight time


def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TicketError("--now wants a time like \"2026-10-19 09:50\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> "2026-10-19T09:50:00+05:30"."""
    return to_ist(now_utc).replace(microsecond=0).isoformat() + "+05:30"


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a record belongs to: its "date", else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _qty(v):
    f = _f(v)
    if f is None:
        return str(v)
    return str(int(f)) if f == int(f) else repr(f)


def _money(v):
    """Indian digit grouping: ₹1,00,000 or ₹49,112.50."""
    v = float(v)
    rupees, paise = ("%.2f" % abs(v)).split(".")
    if len(rupees) > 3:
        rupees = re.sub(r"\B(?=(\d\d)+$)", ",", rupees[:-3]) + "," + rupees[-3:]
    return ("-" if v < 0 else "") + "₹" + rupees + ("" if paise == "00" else "." + paise)


def _p(v):
    return "%.2f" % float(v)


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, or (None, stop)."""
    stop = _f(order.get("stop"))
    q = _f(order.get("qty"))
    if not stop or not q:
        return None, stop
    plan = order.get("plan") or {}
    if str(order.get("type", "market")) != "limit" and _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    entry = _f(order.get("entry"))
    if not entry:
        return None, stop
    return round(q * int(round(abs(entry - stop) * 100)) / 100.0, 2), stop


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    how = "limit %s" % _p(order["entry"]) if order.get("type") == "limit" and order.get("entry") else "market"
    product = str(order.get("product") or "?")
    parts = ["%s %s %s on %s, %s (%s), %s" % (
        str(order.get("side", "")).upper(), _qty(order.get("qty")), order.get("symbol", "?"),
        order.get("exchange", "NSE"), PRODUCT_WORDS.get(product, product), product, how)]
    if order.get("stop") is not None:
        parts.append("stop-loss %s" % _p(order["stop"]))
    if order.get("target") is not None:
        parts.append("target %s" % _p(order["target"]))
    loss, stop = max_loss(order)
    if loss is not None:
        parts.append("max loss about %s if the stop fills at %s (it can fill worse in a fast market)"
                     % (_money(loss), _p(stop)))
    return parts


def plain(order):
    """BUY 40 RELIANCE on NSE, Intraday (MIS), market / stop-loss 1215.35 / target 1240.25 / max loss about ₹498 ..."""
    return " / ".join(plain_parts(order))


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "ticket log", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest record today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(passed, label, risk_result). Information for you, never a gate on what you do."""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    rec = latest(state, fp, today)
    if rec:
        return False, "RECORDED: %s at %s" % (rec.get("status"), rec.get("at")), rv
    if rv is None:
        return False, "NOT CHECKED: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    return True, "trader-risk PASS: inside your limits. That is a rules check, not a recommendation.", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (no check: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


# ---------------------------------------------------------------- rendering

def render(order, idx, total, risk, state, today):
    passed, label, rv = status_of(order, risk, state, today)
    lines = ["ORDER %d of %d, to enter yourself in Groww. Nothing here places it." % (idx, total)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if order.get("stop") is not None:
        lines.append("  The stop and target only exist if you set them in Groww yourself.")
    return "\n".join(lines), passed


def ticket_json(order, idx, risk, state, today):
    passed, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    return {"index": idx, "risk_passed": passed, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "broker": "groww", "order": {k: order[k] for k in ORDER_FIELDS
                                                                           if order.get(k) is not None},
            "why": order.get("why", ""), "max_loss": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_qty(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
        "qty": _qty(qty), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _qty(qty), symbol.upper(), _p(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-19")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["risk_passed"] for t in out) else 1
    blocks, any_passed = [], False
    for i, o in picks:
        text, passed = render(o, i, len(orders), risk, state, today)
        blocks.append(text)
        any_passed = any_passed or passed
    print("\n\n".join(blocks))
    print("\nWhether to place it is your call. Education and tooling, not financial advice.")
    return 0 if any_passed else 1


def _record(a, status):
    """One log entry for the order at --index. Returns (order, entry, state)."""
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    done = latest(state, fp, today)
    if done:
        raise TicketError("order %d is already recorded as %s at %s" % (a.index, done.get("status"), done.get("at")))
    rv, _ = risk_verdict(risk, fp, today)
    loss, stop = max_loss(order)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp,
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"), "qty": order.get("qty"),
           "product": order.get("product"), "plain": plain(order), "status": status, "risk": (rv or {}).get("verdict")}
    if status == "placed":
        rec.update(kind=kind_of(order, rv), max_loss=loss, stop=stop)
    if a.note:
        rec["note"] = a.note
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    return order, rec, cfg, now, today


def cmd_placed(a):
    if bool(a.filled_qty) != bool(a.filled_price):
        raise TicketError("give both --filled-qty and --filled-price, or neither")
    order, rec, cfg, now, today = _record(a, "placed")
    print("Recorded: you placed order %d in Groww: %s." % (a.index, rec["plain"]))
    if rec.get("risk") == "BLOCK":
        print("trader-risk blocked this order. It is in the log anyway, so the daily limits stay true.")
    if a.filled_qty:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
    else:
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    if rec.get("kind") == "entry" and order.get("stop") is not None:
        print("When your stop or target fills later, record that sell the same way.")
    return 0


def cmd_skipped(a):
    _, rec, _, _, _ = _record(a, "skipped")
    print("Recorded: you skipped order %d (%s)." % (a.index, rec["plain"]))
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-19")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, a.symbol.upper(),
                                                                _money(a.price), date, a.portfolio,
                                                                _money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = re.sub(r"\.NS$", "", a.symbol.strip().upper())
    now, today = _clock(a)
    source = "what you read from Groww"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0:
            raise TicketError("--qty wants the number of shares you hold, like --qty 40")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check Groww and pass --qty if you do "
                              "hold some" % (sym, a.portfolio))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or "15:10"
    order = {"symbol": sym, "exchange": "NSE", "side": "sell", "qty": int(qty) if qty == int(qty) else qty,
             "product": a.product, "type": "market",
             "why": "flat by %s IST: closes the %s day trade before the close (qty from %s)"
             % (flat_by, sym, source)}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-ticket", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "note": "Flatten ticket. Nothing in this pack places an order: you place it in Groww.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against Groww." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show. If you act on it: cancel any open stop or target for these shares in Groww")
    print("     first, place the sell yourself, then record it (ticket.py placed).")
    print("Check your broker's own intraday square-off time: it can close an MIS position for you.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("Nothing recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        loss = (" max loss %s" % _money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        note = (' note "%s"' % r["note"]) if r.get("note") else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-8s %s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), loss, note))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your trade log (trader-ticket). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 09:50\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/ticket.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("placed", help="record that you placed one order in Groww")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("skipped", help="record that you decided not to place one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--note")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/ticket.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from Groww (default: trader/portfolio.json)")
    p.add_argument("--product", default="MIS", choices=["MIS", "CNC"])
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's records")
    p.add_argument("--state", default="trader/state/ticket.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "placed": cmd_placed, "skipped": cmd_skipped, "fill": cmd_fill,
                "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

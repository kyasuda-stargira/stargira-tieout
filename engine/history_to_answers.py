#!/usr/bin/env python3
"""
Turn a rebuilt filing history (<TICKER>_history.json from filings_map.py) into an
answers file for verticals/listed_corporate.yaml.

Usage:
  history_to_answers.py <history.json> [--out DIR] [--forecast-years 5]
                        [--price 123.45 --price-date 2026-10-30]
                        [--shares-millions 7433.04]

Writes <DIR>/<ticker>_filings.yaml (DIR defaults to the history file's folder).
Every default in it comes from the company's own history, and a comment beside
each value says how it was derived. The price is never looked up: it stays 0
("not entered") unless the user passes --price, and --price needs --price-date.
The share count defaults to the latest annual filing's count (US: the 10-K
cover page; Japan: issued shares less treasury shares in the 有価証券報告書);
--shares-millions replaces it with the user's own count (for example a diluted
one), in millions.

Rules:
  - The last `history.years` fiscal years (from the schema) become the actual
    years in the model; the last of them is the opening balance sheet.
  - Ratios use the last 3 fiscal years in the history (fewer if it is shorter).
  - A line the filing does not tag is shown as 0 and listed on the Data_Notes
    sheet. Nothing is plugged: if the last actual year's balance sheet does not
    balance exactly (assets = liabilities + mezzanine equity + equity), the
    script stops instead of forcing it.
Amounts are written in millions of the filing's currency.
"""

import argparse
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from schemas import load_schema  # noqa: E402
SCHEMA = os.path.join(os.path.dirname(HERE), "verticals", "listed_corporate.yaml")
BANK_SCHEMA = os.path.join(os.path.dirname(HERE), "verticals", "listed_bank.yaml")
SCALE = 1000000.0          # filed values are in currency units; the model works in millions
WINDOW = 3                 # years used for the ratio defaults


class Refused(Exception):
    pass


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class History:
    def __init__(self, h):
        self.h = h
        self.years = h["years"]
        self.fys = [str(y["fy"]) for y in self.years]
        self.missing = []

    def raw(self, line, fy):
        x = self.h["lines"].get(line, {}).get(fy)
        return None if x is None else x["val"]

    def get(self, line, fy, label=None, record=True):
        """Value in millions; None if not tagged (and recorded when label given)."""
        v = self.raw(line, fy)
        if v is None:
            if label and record:
                self.missing.append("FY%s %s: not tagged in the filing; shown as 0" % (fy, label))
            return None
        return v / SCALE

    def z(self, line, fy, label):
        v = self.get(line, fy, label)
        return 0.0 if v is None else v

    # composite lines -----------------------------------------------------------
    def cash(self, fy, record=True):
        one = self.get("cash_and_sti", fy)
        if one is not None:
            return one
        c = self.get("cash", fy, "cash and cash equivalents", record)
        sti = self.get("short_term_investments", fy)
        return (c or 0.0) + (sti or 0.0)

    def debt(self, fy, record=True):
        parts = [("short_term_borrowings", "short-term borrowings / commercial paper"),
                 ("current_portion_ltd", "current portion of long-term debt"),
                 ("long_term_debt", "long-term debt")]
        # Japanese maps split bonds and commercial paper out; a US history has no such lines.
        parts += [(k, lbl) for k, lbl in [("commercial_paper", "commercial paper"),
                                          ("current_bonds", "current portion of bonds"),
                                          ("bonds", "bonds")] if k in self.h["lines"]]
        vals = [self.get(k, fy, lbl, record) for k, lbl in parts]
        if all(v is None for v in vals):
            return None
        return sum(v or 0.0 for v in vals)

    def equity(self, fy):
        e = self.get("total_equity", fy)
        if e is not None:
            return e
        p = self.get("equity_parent", fy)
        if p is None:
            return None
        return p + (self.get("nci", fy) or 0.0)

    def total_liabilities(self, fy):
        tl = self.get("total_liabilities", fy)
        if tl is not None:
            return tl, None
        lae, eq = self.get("liabilities_and_equity", fy), self.equity(fy)
        if lae is None or eq is None:
            return None, None
        temp = self.get("temporary_equity", fy) or 0.0
        return lae - eq - temp, ("FY%s total liabilities not tagged: taken as the tagged "
                                 "liabilities-and-equity total less equity" % fy)


def ratio(num, den):
    return None if not den else num / den


def fnum(x, places=10):
    return repr(round(float(x), places))


def derive(hist, schema):
    """Return (values, comments, notes) for every scalar input."""
    d = {a["id"]: a["default"] for a in schema["assumptions"]}
    fys = hist.fys
    win = fys[-WINDOW:]
    last = fys[-1]
    vals, why = {}, {}

    def use(key, value, text, fallback_text=None):
        if value is None:
            vals[key] = d[key]
            why[key] = "schema default (%s)" % fallback_text
        else:
            vals[key] = value
            why[key] = text

    def sums(*lines, years=win):
        """Sums over the years where every line is tagged."""
        rows = [[hist.get(l, fy) for l in lines] for fy in years]
        rows = [r for r in rows if all(x is not None for x in r)]
        return [sum(r[i] for r in rows) for i in range(len(lines))], len(rows)

    # growth: CAGR over up to WINDOW years
    span = min(WINDOW, len(fys) - 1)
    r0, r1 = hist.get("revenue", fys[-1 - span]), hist.get("revenue", last)
    g = None
    if span >= 1 and r0 and r1 and r0 > 0 and r1 > 0:
        g = (r1 / r0) ** (1.0 / span) - 1
    use("rev_growth", g, "%d-year revenue CAGR, FY%s %s to FY%s %s (m)" % (
        span, fys[-1 - span], fnum(r0 or 0, 3), last, fnum(r1 or 0, 3)),
        "revenue not tagged at both ends")

    (rev, cogs), n = sums("revenue", "cogs")
    use("gross_margin", None if not n else 1 - ratio(cogs, rev),
        "1 - sum(cost of revenue) / sum(revenue) over %d years to FY%s" % (n, last),
        "cost of revenue not tagged")
    for key, line, label in [("ebit_margin", "ebit", "EBIT"),
                             ("da_pct_rev", "da", "D&A"),
                             ("capex_pct_rev", "capex", "capex")]:
        (rev, x), n = sums("revenue", line)
        use(key, None if not n else ratio(x, rev),
            "sum(%s) / sum(revenue) over %d years to FY%s" % (label, n, last),
            "%s not tagged" % label)

    days = d["g_days_in_year"]
    rev_l, cogs_l = hist.get("revenue", last), hist.get("cogs", last)
    for key, line, base, base_name in [
            ("wc_receivable_days", "receivables", rev_l, "revenue"),
            ("wc_inventory_days", "inventory", cogs_l, "cost of revenue"),
            ("wc_payable_days", "payables", cogs_l, "cost of revenue")]:
        bal = hist.get(line, last)
        v = None if bal is None or not base else bal / base * days
        use(key, v, "FY%s %s / %s x %d days" % (last, line, base_name, days),
            "%s or %s not tagged in FY%s" % (line, base_name, last))

    (tax, pre), n = sums("tax", "pretax_income")
    use("g_tax_rate", ratio(tax, pre) if n and pre > 0 else None,
        "effective rate: sum(income tax) / sum(income before tax) over %d years to FY%s" % (n, last),
        "income tax or pre-tax income not tagged, or pre-tax income not positive")

    # interest: the last WINDOW years with both interest and opening debt tagged
    pairs = []
    for i in range(len(fys) - 1, 0, -1):
        it, od = hist.get("interest_expense", fys[i]), hist.debt(fys[i - 1], record=False)
        if it is not None and od:
            pairs.append((fys[i], it, od))
        if len(pairs) == WINDOW:
            break
    use("fin_interest_rate",
        sum(p[1] for p in pairs) / sum(p[2] for p in pairs) if pairs else None,
        "sum(interest expense) / sum(opening debt), FY%s" % ", FY".join(p[0] for p in reversed(pairs)),
        "no year with both interest expense and opening debt tagged")

    for key, line, label in [("fin_dividend_payout", "dividends_paid", "dividends paid"),
                             ("fin_buyback_pct_ni", "buybacks", "share buybacks")]:
        rows = [(hist.get(line, fy), hist.get("net_income", fy)) for fy in win]
        rows = [(x or 0.0, ni) for x, ni in rows if ni is not None]
        ni_sum = sum(ni for _, ni in rows)
        use(key, sum(x for x, _ in rows) / ni_sum if rows and ni_sum > 0 else None,
            "sum(%s) / sum(net income) over %d years to FY%s (untagged years count as 0)"
            % (label, len(rows), last), "net income not positive over the window")

    use("val_shares_diluted", *shares_default(hist))
    return vals, why


def shares_default(hist):
    """(millions or None, how it was derived, fallback reason) from the filing's count."""
    so = hist.h.get("shares_outstanding")
    return (so and so["val"] / SCALE,
            so and "%s, %s shares as of %s, %s %s %s (%s)" % (
                so["tag"], "{:,}".format(so["val"]), so["end"], so["form"],
                hist.h.get("filing_id_label", "accn"), so["accn"],
                so.get("basis") or "cover-page shares outstanding; basic, not diluted"),
            "the history file carries no cover-page share count")


def _rounding_fix(hist, y, ta, tl, eq):
    """A filing that rounds each subtotal (Japanese filers round to ¥1m) can show
    liabilities + equity one unit away from its own total, while its tagged
    liabilities-and-equity total equals total assets exactly. Then total
    liabilities is read as that filed total less equity, and a note says so.
    Only for a gap of at most one reporting unit; anything larger is refused."""
    fy = str(y["fy"])
    lae = hist.get("liabilities_and_equity", fy)
    if tl is None or eq is None or lae is None:
        return None
    temp = hist.get("temporary_equity", fy) or 0.0
    gap = hist.raw("total_assets", fy) - round((tl + temp + eq) * SCALE)
    unit = y.get("unit") or 1
    if gap == 0 or abs(gap) > unit or hist.raw("liabilities_and_equity", fy) != hist.raw("total_assets", fy):
        return None
    new = lae - eq - temp
    return new, ("FY%s rounding: the filed total liabilities (%s m) plus equity (%s m) is %s m "
                 "away from the filed total assets and the filed liabilities-and-equity total "
                 "(%s m), because each subtotal is rounded. Total liabilities is taken as that "
                 "filed total less equity (%s m); the gap is one reporting unit, not a plug of "
                 "a real difference" % (fy, fnum(tl, 3), fnum(eq, 3), fnum(-gap / SCALE, 3),
                                         fnum(lae, 3), fnum(new, 3)))


def history_slots(hist, n_slots):
    """Per actual year: {field: value}, oldest first. Raises Refused on a bad opening."""
    if len(hist.fys) < n_slots:
        raise Refused("only %d fiscal year(s) in the history; the listed model shows %d"
                      % (len(hist.fys), n_slots))
    slots, notes = [], []
    for y in hist.years[-n_slots:]:
        fy = str(y["fy"])
        ta = hist.get("total_assets", fy)
        eq = hist.equity(fy)
        tl, tl_note = hist.total_liabilities(fy)
        rounding = _rounding_fix(hist, y, ta, tl, eq)
        if rounding:
            tl, tl_note = rounding
        if ta is None or eq is None or tl is None:
            raise Refused("FY%s: total assets, total liabilities or equity not tagged, so the "
                          "balance sheet cannot be opened" % fy)
        if tl_note:
            notes.append(tl_note)
        temp = hist.get("temporary_equity", fy) or 0.0
        s = {"fy": y["fy"], "end": y["end"], "form": y["form"], "accn": y["accn"],
             "status": y["status"]}
        for f, line, label in [("revenue", "revenue", "revenue"),
                               ("cogs", "cogs", "cost of revenue"),
                               ("da", "da", "D&A"), ("ebit", "ebit", "EBIT"),
                               ("interest", "interest_expense", "interest expense"),
                               ("pretax", "pretax_income", "income before tax"),
                               ("tax", "tax", "income tax"),
                               ("net_income", "net_income", "net income"),
                               ("receivables", "receivables", "receivables"),
                               ("inventory", "inventory", "inventory"),
                               ("ppe", "ppe", "PP&E"),
                               ("payables", "payables", "accounts payable"),
                               ("cfo", "cfo", "cash from operations"),
                               ("capex", "capex", "capex"),
                               ("cfi", "cfi", "cash from investing"),
                               ("dividends", "dividends_paid", "dividends paid"),
                               ("buybacks", "buybacks", "share buybacks"),
                               ("cff", "cff", "cash from financing")]:
            s[f] = hist.z(line, fy, label)
        s["cash"] = hist.cash(fy)
        debt = hist.debt(fy)
        s["debt"] = debt or 0.0
        s["equity"] = eq
        s["other_assets"] = ta - (s["cash"] + s["receivables"] + s["inventory"] + s["ppe"])
        s["other_liabilities"] = tl + temp - s["payables"] - s["debt"]
        gap = hist.raw("total_assets", fy) - round((tl + temp + eq) * SCALE)
        s["_gap"] = gap
        slots.append(s)
    if slots[-1]["_gap"] != 0:
        raise Refused("FY%s (the opening balance sheet) does not balance: assets less "
                      "liabilities, mezzanine equity and equity = %s. Tie-Out never plugs a "
                      "gap, so no model is built" % (slots[-1]["fy"], "{:,}".format(slots[-1]["_gap"])))
    return slots, notes


def notes_head(hist, slots):
    """Source credit, actual years, tie-out flags and untagged lines (shared by
    the corporate and bank answers)."""
    h = hist.h
    out = ["## Source",
           "%s; company ID %s; %s; values in %s, shown in millions." % (
               h["source"], h.get("cik"), h.get("standard"), h.get("currency"))]
    out += [str(c) for c in (h.get("credit") or [])]
    out.append("Rebuilt from the filings below and edited into this model by Stargira "
               "Advisors Ltd (Tie-Out). Each actual year is the filed figure; nothing is plugged.")
    if h.get("statements_basis"):
        out.append(h["statements_basis"])
    idl = h.get("filing_id_label", "accn")
    out.append("## Actual years in this model")
    for s, y in zip(slots, hist.years[-len(slots):]):
        sf = y.get("source_filings") or {}
        out.append("FY%s, year end %s: %s %s %s (filed %s%s); statements read from P&L %s, "
                   "balance sheet %s, cash flow %s; history tie-out: %s" % (
                       y["fy"], y["end"], y["form"], idl, y["accn"], y["filed"],
                       "; " + y["basis"] if y.get("basis") else "", sf.get("pl"),
                       sf.get("bs"), sf.get("cf"), y["status"]))
    older = hist.fys[:-len(slots)]
    if older:
        out.append("Earlier years FY%s-FY%s are in %s_history.json and feed the ratio "
                   "defaults only." % (older[0], older[-1], h["ticker"]))
    out.append("## Tie-out flags from the filings map (never plugged)")
    out += ["FY%s %s: %s" % (f["fy"], f["check"], f["reason"]) for f in h["flags"]] or ["none"]
    out.append("## Lines not tagged in the actual years (shown as 0)")
    out += sorted(set(hist.missing)) or ["none"]
    return out


def data_notes(hist, slots, why, extra):
    h = hist.h
    out = notes_head(hist, slots)
    if extra:
        out += ["## Notes on the balance sheet"] + extra
    out.append("## How each default was derived (all can be changed on the Assumptions sheet)")
    out += ["%s: %s" % (k, v) for k, v in why.items()]
    out.append("val_wacc, val_terminal_growth: schema placeholders, not derived from the filing")
    out.append("val_share_price: typed by the user; the tool never fetches a price")
    so = h.get("shares_outstanding")
    if so and so.get("note"):
        out.append("Share count: " + so["note"])
    out.append("## Warnings from the filings map")
    out += list(h["warnings"]) or ["none"]
    out.append("## What the forecast leaves out")
    out += ["Net debt = debt less cash and short-term investments. Leases, long-term "
            "investments, pensions and debt the filing tags only under its own extension "
            "tags are not in it, so net debt and EV can be understated or overstated.",
            "Other assets, other liabilities and debt stay flat at the last actual year. "
            "Cash never closes below the minimum (fin_min_cash_pct of last year-end cash): "
            "a revolver with no limit draws to hold it there and repays from surplus cash. "
            "Non-controlling interests are not split out of forecast profit.",
            "Revenue grows at one rate; margins, D&A and capex are fixed shares of revenue."]
    return out


def write_answers(path, hist, vals, why, slots, notes, args):
    h = hist.h
    lines = ["# Listed-corporate answers for %s (%s), generated by history_to_answers.py"
             % (h["entity"], h["ticker"]),
             "# from %s. Amounts in millions of %s." % (os.path.basename(args.history),
                                                       h.get("currency")),
             "# Build: python engine/build_model.py verticals/listed_corporate.yaml %s <out.xlsx>"
             % os.path.abspath(path), ""]
    src = "%s, company ID %s; processed with Stargira Tie-Out (Stargira Advisors Ltd)" % (
        h["source"], h.get("cik"))
    if h.get("credit"):           # EDINET (PDL1.0): the source credit plus who edited it
        src = "%s, EDINET code %s. %s" % (h["source"], h.get("cik"), " ".join(h["credit"]))
    head = [("g_entity_name", h["entity"], "from the filing"),
            ("g_ticker", h["ticker"], "as given to the fetch"),
            ("g_reporting_ccy", h.get("currency"), "the filing's monetary unit"),
            ("g_data_source", src, "copied from the history file"),
            ("g_forecast_years", args.forecast_years, "forecast years (5-10)")]
    for k, v, c in head:
        lines.append("%s: %s   # %s" % (k, json.dumps(v, ensure_ascii=False), c))
    lines.append("")
    for k, v in vals.items():
        lines.append("%s: %s   # %s" % (k, fnum(v), why[k]))
    lines.append("")
    if args.price is not None:
        lines.append("val_share_price: %s   # source: user, typed on the command line" % fnum(args.price))
        lines.append("val_price_date: %s   # date the user gave for that price" % json.dumps(args.price_date))
    else:
        lines.append("val_share_price: 0.0   # NOT ENTERED: type the price here (source: user)")
        lines.append("# val_price_date: \"YYYY-MM-DD\"   # set this with the price")
    lines.append('val_price_source: "user"')
    lines.append('val_shares_source: "%s"   # "user" when --shares-millions replaced the filed count'
                 % ("user" if args.shares_millions is not None else "filing"))
    lines += ["", "# Actual years (oldest first); the last one is the opening balance sheet."]
    order = ["fy", "end", "form", "accn", "status", "revenue", "cogs", "da", "ebit", "interest",
             "pretax", "tax", "net_income", "cash", "receivables", "inventory", "ppe",
             "other_assets", "payables", "debt", "other_liabilities", "equity", "cfo", "capex",
             "cfi", "dividends", "buybacks", "cff"]
    comments = {"cash": "cash and equivalents + short-term investments (or the one combined line)",
                "debt": "short-term borrowings + current + long-term debt as tagged",
                "other_assets": "total assets less cash, receivables, inventory and PP&E",
                "other_liabilities": "total liabilities + mezzanine equity less payables and debt",
                "equity": "total equity incl. non-controlling interests"}
    for n, s in enumerate(slots, 1):
        lines.append("# FY%s" % s["fy"])
        for f in order:
            v = s[f]
            txt = json.dumps(v, ensure_ascii=False) if isinstance(v, str) else (str(v) if f == "fy" else fnum(v, 6))
            c = comments.get(f)
            lines.append("hist_%d_%s: %s%s" % (n, f, txt, "   # " + c if c and n == 1 else ""))
    lines += ["", "# Lines for the Data_Notes sheet (text, not inputs)."]
    lines.append(yaml.safe_dump({"data_notes": data_notes(hist, slots, why, notes)},
                                allow_unicode=True, width=1000, sort_keys=False).rstrip())
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Bank chart (sector "bank"): answers for verticals/listed_bank.yaml ─────────

LIQUID_LINES = [("cash", "cash and due from banks"),
                ("reverse_repos", "reverse repos / cash lent against securities"),
                ("trading_assets", "trading assets"),
                ("securities", "investment securities")]
BORROWING_LINES = [("repo_borrowings", "repo borrowings"),
                   ("short_term_borrowings", "short-term borrowings"),
                   ("long_term_debt", "long-term debt")]
COE_DEFAULT = 0.10     # placeholder cost of equity; see its derivation line
LONG_RUN_GROWTH = 0.02  # placeholder long-run growth of residual income


class BankHistory(History):
    """A history on the bank chart (docs/MODELS.md "Financial companies")."""

    def __init__(self, h):
        History.__init__(self, h)
        notes = h.get("chart_notes") or []
        # J-GAAP prints 貸出金 before the allowance (chart_notes say so); US loans are net.
        self.gross_loans = any("before the allowance" in str(n) for n in notes)
        self.securities_firm = (h.get("statement_format") == "securities"
                                or "securities firm" in str(h.get("routing") or ""))

    def total(self, fy, lines, record=True):
        """Sum of the tagged lines; None if none is tagged."""
        vals = [self.get(k, fy, lbl, record) for k, lbl in lines]
        if all(v is None for v in vals):
            return None
        return sum(v or 0.0 for v in vals)

    def allowance_netted(self, fy, record=True):
        if not self.gross_loans:
            return 0.0
        a = self.get("loan_allowance", fy, "allowance for loan losses (貸倒引当金)", record)
        return a or 0.0

    def loans_net(self, fy, record=False):
        loans = self.get("loans", fy, "loans", record)
        if loans is None:
            return None
        return loans - self.allowance_netted(fy, record)

    def liquid(self, fy, record=False):
        return self.total(fy, LIQUID_LINES, record)

    def deposits_all(self, fy, record=False):
        lines = [("deposits", "deposits")]
        if "negotiable_cds" in self.h["lines"]:
            lines.append(("negotiable_cds", "negotiable certificates of deposit (譲渡性預金)"))
        return self.total(fy, lines, record)

    def borrowings(self, fy, record=False):
        return self.total(fy, BORROWING_LINES, record)

    def earning(self, fy):
        ln, lq = self.loans_net(fy), self.liquid(fy)
        return None if ln is None and lq is None else (ln or 0.0) + (lq or 0.0)

    def ibl(self, fy):
        d, b = self.deposits_all(fy), self.borrowings(fy)
        return None if d is None and b is None else (d or 0.0) + (b or 0.0)

    def ni_total(self, fy):
        t = self.get("net_income_incl_nci", fy)
        if t is not None:
            return t
        ni = self.get("net_income", fy)
        return None if ni is None else ni + (self.get("net_income_nci", fy) or 0.0)


def fin_cagr(fys, fn, name):
    """(CAGR over the last WINDOW years, how) or (None, why not). Shared by the
    bank and insurer drivers."""
    last = fys[-1]
    span = min(WINDOW, len(fys) - 1)
    if span < 1:
        return None, "only one year in the history"
    a, b = fn(fys[-1 - span]), fn(last)
    if not a or not b or a <= 0 or b <= 0:
        return None, "%s not positive at both ends" % name
    return ((b / a) ** (1.0 / span) - 1,
            "%d-year CAGR of %s, FY%s %s to FY%s %s (m)" % (
                span, name, fys[-1 - span], fnum(a, 3), last, fnum(b, 3)))


def fin_on_opening(fys, num, den, num_name, den_name):
    """sum(num, year t) / sum(den, year t-1) over the last WINDOW years with both."""
    rows = []
    for i in range(len(fys) - 1, 0, -1):
        x, o = num(fys[i]), den(fys[i - 1])
        if x is not None and o:
            rows.append((fys[i], x, o))
        if len(rows) == WINDOW:
            break
    if not rows or sum(r[2] for r in rows) <= 0:
        return None, "no year with both %s and opening %s" % (num_name, den_name)
    return (sum(r[1] for r in rows) / sum(r[2] for r in rows),
            "sum(%s) / sum(opening %s), FY%s (opening = the year before)" % (
                num_name, den_name, ", FY".join(r[0] for r in reversed(rows))))


def fin_window_ratio(win, num, den, text, fallback):
    """(sum(num) / sum(den) over the window years with both, text % count, fallback)."""
    rows = [(num(fy), den(fy)) for fy in win]
    rows = [(a, b) for a, b in rows if a is not None and b is not None]
    tot = sum(b for _, b in rows)
    return (sum(a for a, _ in rows) / tot if rows and tot > 0 else None,
            text % len(rows), fallback)


def prior_year(hist, n_slots):
    """(fy, year end) of the balance sheet one year before the first actual year, or
    None when the history holds no earlier year (IFRS 17: the report's opening
    balance sheet counts)."""
    i = len(hist.fys) - n_slots - 1
    if i >= 0:
        return hist.fys[i], hist.years[i]["end"]
    op = hist.h.get("opening_balance") if getattr(hist, "ifrs", False) else None
    return (str(op["fy"]), op["end"]) if op else None


OPENING_RATIOS = {"bank": "yield, cost of funds, NIM, NII margin, credit cost and ROE",
                  "insurer": "investment yield, crediting rate and ROE"}


def set_opening(hist, n_slots, kind, fields, notes):
    """hist.opening = {field: value} for the opening-balance inputs (hist_open_<field>),
    with a Data_Notes line. Empty when there is no earlier balance sheet: the first
    actual year's opening-based ratios then read blank. Nothing is invented."""
    first = hist.fys[-n_slots]
    py = prior_year(hist, n_slots)
    if py is None or hist.get("total_assets", py[0]) is None:
        hist.opening = {}
        notes.append("Opening balance: none. The history holds no balance sheet before the first "
                     "actual year (FY%s is the earliest filed), so the FY%s ratios on opening "
                     "balances (%s) are blank. No figure is invented." % (first, first,
                                                                          OPENING_RATIOS[kind]))
        return
    fy, end = py
    seen = len(hist.missing)
    hist.opening = {"fy": int(fy)}
    hist.opening.update((f, fn(fy)) for f, fn in fields)
    # Untagged parts go in this note, not in the list for the actual years.
    gone = sorted(set(m.split(": ")[0].replace("FY%s " % fy, "") for m in hist.missing[seen:]))
    del hist.missing[seen:]
    notes.append("Opening balance (FY%s year end, %s): the filed figures on the Assumptions sheet "
                 "under 'Opening balance (actuals)'. They feed only the FY%s ratios on opening "
                 "balances (%s), never the forecast or the defaults.%s" % (
                     fy, end, first, OPENING_RATIOS[kind],
                     " Not tagged that year, counted as 0 as in the actual years: %s." % ", ".join(gone)
                     if gone else ""))


DISTRIBUTION_CAP = 1.0   # default dividends + buybacks at most 100% of net income to owners


def cap_distributions(vals, why):
    """Cap the DEFAULT combined payout (dividends + buybacks) at 100% of net income.
    Above it, the buyback share is scaled down (and, if dividends alone exceed it,
    dividends are capped and buybacks set to 0). The how-derived lines keep the
    actual 3-year ratios. A default at or below the cap is left exactly as it was."""
    div, bb = vals["fin_dividend_payout"], vals["fin_buyback_pct_ni"]
    if div + bb <= DISTRIBUTION_CAP:
        return
    new_div = min(div, DISTRIBUTION_CAP)
    new_bb = DISTRIBUTION_CAP - new_div
    note = ("; DEFAULT CAPPED: over the window dividends + buybacks were %.2f%% of net income "
            "(dividends %.2f%%, buybacks %.2f%%), above the %.0f%% cap on the combined default "
            "payout, so %s. Type the actual ratio back in to keep paying out more than profit"
            % (100 * (div + bb), 100 * div, 100 * bb, 100 * DISTRIBUTION_CAP,
               "buybacks are scaled down to %.2f%%" % (100 * new_bb) if new_div == div else
               "dividends are capped at %.0f%% and buybacks set to 0" % (100 * DISTRIBUTION_CAP)))
    for key, new in (("fin_dividend_payout", new_div), ("fin_buyback_pct_ni", new_bb)):
        if new != vals[key]:
            why[key] = "%s; actual 3-year ratio %.4f%%%s" % (why[key], 100 * vals[key], note)
            vals[key] = new


def fin_capital_defaults(hist, win, use, vals, why, target_key):
    """Payout, buybacks, the typed-in capital target, cost of equity and long-run
    growth (bank and insurer templates)."""
    last = hist.fys[-1]
    for key, line, label in [("fin_dividend_payout", "dividends_paid", "dividends paid"),
                             ("fin_buyback_pct_ni", "buybacks", "share buybacks")]:
        rows = [(hist.get(line, fy), hist.get("net_income", fy)) for fy in win]
        rows = [(x or 0.0, ni) for x, ni in rows if ni is not None]
        ni_sum = sum(ni for _, ni in rows)
        use(key, sum(x for x, _ in rows) / ni_sum if rows and ni_sum > 0 else None,
            "sum(%s) / sum(net income to owners) over %d years to FY%s (untagged years count as 0)"
            % (label, len(rows), last), "net income not positive over the window")
    cap_distributions(vals, why)

    eq, ta = hist.get("equity_parent", last), hist.get("total_assets", last)
    use(target_key, eq / ta if eq is not None and ta else None,
        "typed-in capital check (source: user). Default = FY%s equity to owners / total assets, "
        "%s / %s (m). Regulatory ratios such as CET1 are not in the filing data" % (
            last, fnum(eq or 0, 3), fnum(ta or 0, 3)), "equity or total assets not tagged")
    vals["val_cost_of_equity"] = COE_DEFAULT
    why["val_cost_of_equity"] = (
        "placeholder 10%, not derived from the filing or from market data: a round starting "
        "figure. Replace it with your own estimate (for example risk-free rate + beta x equity "
        "risk premium)")
    vals["val_terminal_growth"] = LONG_RUN_GROWTH
    why["val_terminal_growth"] = ("placeholder 2%, not derived from the filing: long-run growth of "
                                  "residual income after the last forecast year; keep it below "
                                  "the cost of equity")


def bank_derive(hist, schema):
    """Return (values, how each was derived) for the bank drivers."""
    d = {a["id"]: a["default"] for a in schema["assumptions"]}
    fys = hist.fys
    win = fys[-WINDOW:]
    last = fys[-1]
    vals, why = {}, {}

    def use(key, value, text, fallback_text=None):
        if value is None:
            vals[key] = d[key]
            why[key] = "schema default (%s)" % fallback_text
        else:
            vals[key] = value
            why[key] = text

    def cagr(fn, name):
        return fin_cagr(fys, fn, name)

    def on_opening(num, den, num_name, den_name):
        return fin_on_opening(fys, num, den, num_name, den_name)

    g, txt = cagr(hist.loans_net, "net loans" + (" (貸出金 less 貸倒引当金)" if hist.gross_loans else ""))
    use("bk_loan_growth", g, txt, txt)
    g, txt = cagr(hist.deposits_all, "deposits" + (" incl. 譲渡性預金" if "negotiable_cds" in hist.h["lines"] else ""))
    if g is None and all(not hist.deposits_all(fy) for fy in fys):
        g, txt = 0.0, "0: no deposits are tagged in any year (the deposit line is 0)"
    use("bk_deposit_growth", g, txt, txt)

    get = lambda line: (lambda fy: hist.get(line, fy))  # noqa: E731
    for key, num, den, nn, dn in [
            ("bk_asset_yield", get("interest_income"), hist.earning, "interest income",
             "interest-earning assets (net loans + cash + reverse repos + trading assets + securities)"),
            ("bk_funding_cost", get("interest_expense"), hist.ibl, "interest expense",
             "interest-bearing liabilities (deposits + repos + short-term borrowings + long-term debt, as tagged)"),
            ("bk_fee_income_pct_assets", get("noninterest_income"), get("total_assets"),
             "noninterest income", "total assets"),
            ("bk_credit_cost", get("provision"), hist.loans_net, "provision for credit losses",
             "net loans")]:
        v, txt = on_opening(num, den, nn, dn)
        if key == "bk_credit_cost" and v is None and all(hist.get("provision", fy) is None for fy in win):
            v, txt = 0.0, ("0: no provision is tagged in FY%s-FY%s%s" % (
                win[0], last, "; for a securities firm credit costs sit inside operating "
                "expenses (chart_notes)" if hist.securities_firm else ""))
        use(key, v, txt, txt)

    def window_ratio(key, num, den, text, fallback):
        use(key, *fin_window_ratio(win, num, den, text, fallback))

    window_ratio("bk_cost_income", get("opex"), get("total_revenue"),
                 "sum(operating expenses) / sum(net revenue) over %%d years to FY%s" % last,
                 "operating expenses or net revenue not tagged")
    window_ratio("g_tax_rate", get("tax"), get("pretax_income"),
                 "effective rate: sum(income tax) / sum(income before tax) over %%d years to FY%s" % last,
                 "income tax or pre-tax income not tagged, or pre-tax income not positive")
    window_ratio("bk_nci_share",
                 lambda fy: None if hist.ni_total(fy) is None or hist.get("net_income", fy) is None
                 else hist.ni_total(fy) - hist.get("net_income", fy), hist.ni_total,
                 "sum(NCI share = net income incl. NCI - net income to owners) / sum(net income "
                 "incl. NCI) over %%d years to FY%s" % last, "net income not tagged")
    fin_capital_defaults(hist, win, use, vals, why, "bk_target_equity_assets")
    use("val_shares_diluted", *shares_default(hist))
    return vals, why


def bank_slots(hist, n_slots):
    """Per actual year: {field: value}, oldest first. Raises Refused on a bad opening."""
    if len(hist.fys) < n_slots:
        raise Refused("only %d fiscal year(s) in the history; the bank model shows %d, as the "
                      "listed model does, and a model needs at least 3 years"
                      % (len(hist.fys), n_slots))
    slots, notes = [], []
    for y in hist.years[-n_slots:]:
        fy = str(y["fy"])
        ta = hist.get("total_assets", fy)
        eq = hist.equity(fy)
        tl, tl_note = hist.total_liabilities(fy)
        rounding = _rounding_fix(hist, y, ta, tl, eq)
        if rounding:
            tl, tl_note = rounding
        if ta is None or eq is None or tl is None or hist.get("equity_parent", fy) is None:
            raise Refused("FY%s: total assets, total liabilities or equity not tagged, so the "
                          "balance sheet cannot be opened" % fy)
        if tl_note:
            notes.append(tl_note)
        temp = hist.get("temporary_equity", fy) or 0.0
        s = {"fy": y["fy"], "end": y["end"], "form": y["form"], "accn": y["accn"],
             "status": y["status"]}
        for f, line, label in [("interest_income", "interest_income", "interest income"),
                               ("interest_expense", "interest_expense", "interest expense"),
                               ("noninterest_income", "noninterest_income", "noninterest income"),
                               ("opex", "opex", "operating expenses"),
                               ("pretax", "pretax_income", "income before tax"),
                               ("tax", "tax", "income tax"),
                               ("net_income", "net_income", "net income to owners"),
                               ("dividends", "dividends_paid", "dividends paid"),
                               ("buybacks", "buybacks", "share buybacks")]:
            s[f] = hist.z(line, fy, label)
        prov = hist.get("provision", fy, "provision for credit losses",
                        record=not hist.securities_firm)
        s["provision"] = prov or 0.0
        nit = hist.ni_total(fy)
        s["net_income_total"] = s["net_income"] if nit is None else nit
        for line, label in LIQUID_LINES:
            s[line] = hist.z(line, fy, label)
        s["loans"] = hist.z("loans", fy, "loans")
        s["loan_allowance_netted"] = hist.allowance_netted(fy)
        s["total_assets"] = ta
        s["deposits"] = hist.deposits_all(fy, record=True) or 0.0
        s["borrowings"] = hist.borrowings(fy, record=True) or 0.0
        s["total_liabilities"] = tl + temp
        s["equity_parent"] = hist.get("equity_parent", fy)
        s["total_equity"] = eq
        s["_gap"] = hist.raw("total_assets", fy) - round((tl + temp + eq) * SCALE)
        slots.append(s)
    if slots[-1]["_gap"] != 0:
        raise Refused("FY%s (the opening balance sheet) does not balance: assets less "
                      "liabilities, mezzanine equity and equity = %s. Tie-Out never plugs a "
                      "gap, so no model is built" % (slots[-1]["fy"], "{:,}".format(slots[-1]["_gap"])))
    if hist.gross_loans:
        s = slots[-1]
        notes.append("Net loans = loans as printed (貸出金, %s m in FY%s) less the whole allowance "
                     "line (貸倒引当金, %s m). That allowance also covers assets other than loans, "
                     "so net loans are slightly understated and other assets slightly overstated; "
                     "total assets are as filed." % (fnum(s["loans"], 3), s["fy"],
                                                     fnum(s["loan_allowance_netted"], 3)))

    def loans_net(fy):
        return hist.z("loans", fy, "loans") - hist.allowance_netted(fy)

    def liquid(fy):
        return sum(hist.z(line, fy, label) for line, label in LIQUID_LINES)

    def deposits(fy):
        return hist.deposits_all(fy, record=True) or 0.0

    def borrowings(fy):
        return hist.borrowings(fy, record=True) or 0.0
    # The same rules as the actual years above (an untagged part is 0, named in the note).
    set_opening(hist, n_slots, "bank", [
        ("loans", loans_net), ("deposits", deposits),
        ("total_assets", lambda fy: hist.get("total_assets", fy)),
        ("earning", lambda fy: loans_net(fy) + liquid(fy)),
        ("ibl", lambda fy: deposits(fy) + borrowings(fy)),
        ("equity_parent", lambda fy: hist.get("equity_parent", fy))], notes)
    return slots, notes


def bank_data_notes(hist, slots, why, extra):
    h = hist.h
    out = notes_head(hist, slots)
    out.append("## How the filing was read onto the bank chart")
    out.append(h.get("routing") or "sector: bank")
    out += [str(n) for n in (h.get("chart_notes") or [])]
    out += ["## Notes on the balance sheet"] + extra + [
        "Liquid assets = cash and due from banks + reverse repos + trading assets + investment "
        "securities, as tagged. Interest-earning assets = net loans + liquid assets. Other assets "
        "= total assets less both.",
        "Deposits include negotiable certificates of deposit where filed apart (Japan: 預金 + "
        "譲渡性預金). Borrowings = repos + short-term borrowings + long-term debt as tagged; a "
        "line tagged only under the company's own element sits in other liabilities instead, "
        "and the cost of funds is measured on the same base, so the forecast stays consistent.",
        "Equity to owners is the parent's shareholders' equity; total equity less it is shown as "
        "'NCI and other equity' (Japan: incl. share options, 新株予約権)."]
    out.append("## How each default was derived (all can be changed on the Assumptions sheet)")
    out += ["%s: %s" % (k, v) for k, v in why.items()]
    out.append("val_share_price: typed by the user; the tool never fetches a price")
    so = h.get("shares_outstanding")
    if so and so.get("note"):
        out.append("Share count: " + so["note"])
    out.append("## Warnings from the filings map")
    out += list(h["warnings"]) or ["none"]
    out.append("## What the bank model leaves out")
    out += [
        "Regulatory capital ratios (CET1, Tier 1, leverage ratio) are not in the filing data "
        "this tool reads. The capital check uses equity to owners / total assets against a "
        "target you type (bk_target_equity_assets); compare it with the ratios the bank "
        "publishes in its own capital disclosures.",
        "No interest-rate sensitivity: the yield on assets and the cost of funds are fixed "
        "ratios on opening balances. There is no repricing gap, deposit beta, rate path or "
        "duration of the securities book.",
        "Net loans and deposits grow at one rate each. Borrowings, other assets, other "
        "liabilities and non-controlling interests stay flat at the last actual year. Liquid "
        "assets are the balancing line; the Liquidity sheet rebuilds them from the year's flows "
        "and its check must read zero, and a flag shows if they turn negative.",
        "Noninterest income is one ratio to opening total assets: fees, trading and securities "
        "gains are not forecast apart. Extraordinary and other items below the operating line "
        "are 0 in the forecast.",
        "The non-controlling interests' share of net income is a fixed proportion, treated as "
        "paid out to the minority holders, so their equity stays flat.",
        "Liquidity rules (LCR, NSFR) and funding concentration are not modelled.",
        "Valuation is residual income (book equity + the present value of profit above the "
        "cost of equity). There is no free-cash-flow DCF and no enterprise value: deposits are "
        "the bank's raw material, not its debt."]
    if hist.securities_firm:
        out.append("Securities firm on the bank chart: the bank drivers fit a broker-dealer only "
                   "loosely. Its 'interest' is financial revenue and expense (金融収益 / 金融費用) "
                   "on trading, margin and repo-type positions, most of its balance sheet is "
                   "trading and securities-financing positions rather than loans, and its "
                   "deposits sit (if anywhere) under a banking subsidiary's own element. Read the "
                   "forecast as a rough shape, not a broker-dealer model.")
    return out


BANK_ORDER = ["fy", "end", "form", "accn", "status", "interest_income", "interest_expense",
              "noninterest_income", "opex", "provision", "pretax", "tax", "net_income_total",
              "net_income", "cash", "reverse_repos", "trading_assets", "securities", "loans",
              "loan_allowance_netted", "total_assets", "deposits", "borrowings",
              "total_liabilities", "equity_parent", "total_equity", "dividends", "buybacks"]
BANK_COMMENTS = {
    "noninterest_income": "net revenue less net interest income",
    "loans": "as filed (US: net of the allowance; Japan: 貸出金 before it)",
    "loan_allowance_netted": "deducted to reach net loans (0 where loans are filed net)",
    "deposits": "deposits + negotiable certificates of deposit where filed apart",
    "borrowings": "repos + short-term borrowings + long-term debt as tagged",
    "total_liabilities": "total liabilities + mezzanine equity",
    "total_equity": "total equity incl. non-controlling interests (Japan: net assets)"}


def write_fin_answers(path, hist, vals, why, slots, notes, args, title, schema_file, order,
                      comments, notes_fn, warning=None):
    """Answers file for a financial-company template (bank or insurer)."""
    h = hist.h
    lines = ["# %s answers for %s (%s), generated by history_to_answers.py"
             % (title, h["entity"], h["ticker"]),
             "# from %s. Amounts in millions of %s." % (os.path.basename(args.history),
                                                       h.get("currency")),
             "# Build: python engine/build_model.py %s %s <out.xlsx>"
             % (schema_file, os.path.abspath(path)), ""]
    if warning:
        lines[-1:-1] = ["# " + warning]
    src = "%s, company ID %s; processed with Stargira Tie-Out (Stargira Advisors Ltd)" % (
        h["source"], h.get("cik"))
    if h.get("credit"):
        src = "%s, EDINET code %s. %s" % (h["source"], h.get("cik"), " ".join(h["credit"]))
    head = [("g_entity_name", h["entity"], "from the filing"),
            ("g_ticker", h["ticker"], "as given to the fetch"),
            ("g_reporting_ccy", h.get("currency"), "the filing's monetary unit"),
            ("g_data_source", src, "copied from the history file"),
            ("g_forecast_years", args.forecast_years, "forecast years (5-10)")]
    for k, v, c in head:
        lines.append("%s: %s   # %s" % (k, json.dumps(v, ensure_ascii=False), c))
    lines.append("")
    for k, v in vals.items():
        lines.append("%s: %s   # %s" % (k, fnum(v), why[k]))
    lines.append("")
    if args.price is not None:
        lines.append("val_share_price: %s   # source: user, typed on the command line" % fnum(args.price))
        lines.append("val_price_date: %s   # date the user gave for that price" % json.dumps(args.price_date))
    else:
        lines.append("val_share_price: 0.0   # NOT ENTERED: type the price here (source: user)")
        lines.append("# val_price_date: \"YYYY-MM-DD\"   # set this with the price")
    lines.append('val_price_source: "user"')
    lines.append('val_shares_source: "%s"   # "user" when --shares-millions replaced the filed count'
                 % ("user" if args.shares_millions is not None else "filing"))
    lines += ["", "# Actual years (oldest first); the last one is the opening balance sheet."]
    for n, s in enumerate(slots, 1):
        lines.append("# FY%s" % s["fy"])
        for f in order:
            v = s[f]
            txt = json.dumps(v, ensure_ascii=False) if isinstance(v, str) else (str(v) if f == "fy" else fnum(v, 6))
            c = comments.get(f)
            lines.append("hist_%d_%s: %s%s" % (n, f, txt, "   # " + c if c and n == 1 else ""))
    op = getattr(hist, "opening", None)
    if op:
        lines += ["", "# Opening balance: the year end before the first actual year (filed figures; "
                  "they feed only that year's ratios on opening balances)."]
        for f, v in op.items():
            lines.append("hist_open_%s: %s" % (f, str(v) if f == "fy" else
                                                ("null" if v is None else fnum(v, 6))))
    elif op is not None:
        lines += ["", "# Opening balance: none filed before the first actual year, so the "
                  "hist_open_ inputs stay empty (see Data_Notes)."]
    lines += ["", "# Lines for the Data_Notes sheet (text, not inputs)."]
    lines.append(yaml.safe_dump({"data_notes": notes_fn(hist, slots, why, notes)},
                                allow_unicode=True, width=1000, sort_keys=False).rstrip())
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_bank_answers(path, hist, vals, why, slots, notes, args):
    write_fin_answers(path, hist, vals, why, slots, notes, args, "Listed-bank",
                      "verticals/listed_bank.yaml", BANK_ORDER, BANK_COMMENTS, bank_data_notes)


def one_standard(hist):
    """Refuse a history whose years carry different accounting standards (an IFRS 17
    history records its standard per year; J-GAAP and IFRS years are never mixed)."""
    stds = [y.get("standard") for y in hist.years]
    if any(stds) and len(set(stds)) > 1:
        raise Refused("the history mixes accounting standards (%s); J-GAAP and IFRS years are "
                      "never put into one model" % ", ".join(
                          "FY%s %s" % (y["fy"], y.get("standard") or "not recorded")
                          for y in hist.years))


# Lines a financial model cannot stand without, and the main drivers built on them.
# A driver on a missing core line would fall back to the template's placeholder, and a
# model on placeholder drivers still passes verify_model, so the build is refused.
# Loans and deposits joined the bank list on 2026-11-02: WFC tags no loan book after
# FY2021 (its 10-K files "net loans" under its own tag only), and a model built
# without it had 4% placeholder loan growth and a 15.5% asset yield.
CORE_LINES = {"bank": ["interest_income", "interest_expense", "loans", "deposits"],
              "insurer": ["premiums", "claims"]}
CORE_WHY = {"loans": "without the loan book, loan growth is the template placeholder and the "
                     "asset yield and credit cost are measured on the wrong base",
            "deposits": "without deposits, deposit growth is a placeholder and the funding "
                        "cost is measured on the wrong base"}
CORE_DRIVERS = {"bank": {"bk_asset_yield": ["interest_income"],
                         "bk_funding_cost": ["interest_expense"],
                         "bk_loan_growth": ["loans"],
                         "bk_deposit_growth": ["deposits"]},
                "insurer": {"ins_premium_growth": ["premiums"],
                            "ins_loss_ratio": ["claims", "premiums"],
                            "ins_expense_ratio": ["claims", "premiums"]}}


def core_line_refusal(hist, kind, slots, why):
    """A plain reason to refuse when a core line is missing in every actual year, or
    when a main driver falls back to a placeholder because a core line is missing."""
    act = [str(s["fy"]) for s in slots]
    span = "FY%s-FY%s" % (act[0], act[-1])
    gone = [k for k in CORE_LINES[kind] if all(hist.raw(k, fy) is None for fy in act)]
    if gone:
        return ("no value for %s in any actual year (%s), even after any derivation the map allows. "
                "The model's main drivers would be template placeholders, not the company's "
                "own figures, so no model is built%s. Read the history's warnings for the tags "
                "tried, and check the filing's own statement" % (
                    " or ".join(gone), span,
                    "".join(" (%s)" % CORE_WHY[k] for k in gone if k in CORE_WHY)))
    for drv, needs in CORE_DRIVERS[kind].items():
        if drv not in why or not why[drv].startswith("schema default"):
            continue
        holes = ["%s (FY%s)" % (k, ", FY".join(fy for fy in hist.fys if hist.raw(k, fy) is None))
                 for k in needs if any(hist.raw(k, fy) is None for fy in hist.fys)]
        if holes:
            return ("the main driver %s would be the template placeholder, because %s %s "
                    "missing. No model is built on a placeholder driver" % (
                        drv, " and ".join(holes), "is" if len(holes) == 1 else "are"))
    return None


def fin_main(hist, args, kind, default_schema, slots_fn, derive_fn, write_fn):
    """Shared driver for the bank and insurer answers files."""
    schema = load_schema(args.schema or default_schema)
    try:
        one_standard(hist)
        ifrs = getattr(hist, "ifrs", False)
        if (schema["meta"].get("basis") == "ifrs17") != ifrs:
            raise Refused(
                "the schema %s is %s, but this history is %s. The 2-actual-year IFRS 17 template "
                "(verticals/listed_insurer_ifrs.yaml) is only for IFRS 17 insurer histories "
                "; every other history keeps its 3-year template" % (
                    os.path.basename(args.schema or default_schema),
                    "the IFRS 17 template" if not ifrs else "not the IFRS 17 template",
                    "an IFRS 17 insurer" if ifrs else hist.h.get("standard")))
        slots, notes = slots_fn(hist, int(schema["history"]["years"]))
    except Refused as e:
        print("%s: %s" % (hist.h.get("ticker"), e))
        return 2
    vals, why = derive_fn(hist, schema)
    refusal = core_line_refusal(hist, kind, slots, why)
    if refusal:
        print("%s: refused: %s" % (hist.h.get("ticker"), refusal))
        return 2
    if args.shares_millions is not None:
        why["val_shares_diluted"] = ("source: user, typed on the command line, in millions "
                                     "(the filing default was %s m: %s)"
                                     % (fnum(vals["val_shares_diluted"]), why["val_shares_diluted"]))
        vals["val_shares_diluted"] = args.shares_millions
    out = args.out or os.path.dirname(os.path.abspath(args.history))
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "%s_%s_filings.yaml" % (hist.h["ticker"].lower(), kind))
    write_fn(path, hist, vals, why, slots, notes, args)
    print("wrote %s (%s template: %s)" % (
        path, kind, os.path.relpath(args.schema or default_schema, os.path.dirname(HERE))))
    if getattr(hist, "ifrs", False) and len(slots) < 3:
        print(TWO_YEAR_WARNING)
    for k in vals:
        if why[k].startswith("schema default") or why[k].startswith("placeholder"):
            print("WARNING: %s is a placeholder, not the company's own figure: %s" % (k, why[k]))
    if hist.missing:
        print("WARNING: %d line(s) not tagged in the actual years, shown as 0 (see Data_Notes): %s"
              % (len(set(hist.missing)), "; ".join(sorted(set(hist.missing)))))
    return 0


def bank_main(hist, args):
    return fin_main(BankHistory(hist.h), args, "bank", BANK_SCHEMA, bank_slots, bank_derive,
                    write_bank_answers)


# ── Insurer chart (sector "insurer"): answers for verticals/listed_insurer.yaml ─

INSURER_SCHEMA = os.path.join(os.path.dirname(HERE), "verticals", "listed_insurer.yaml")
LIFE_LINES = ("future_policy_benefits", "policyholder_account_balances", "interest_credited")
PREMIUM_BASIS_WRITTEN = ("Premium basis: WRITTEN, not earned (J-GAAP non-life: 正味収入保険料 + "
                         "生命保険料; there is no earned-premium element). The workbook labels the "
                         "line 'Premiums Written'.")
# Reserve lines found by hand to be a tagged PART of a printed balance-sheet line, not a
# printed line of their own (step 3 second route, checked against the 10-K). Keyed by CIK.
TAGGED_PART_OF_PRINTED = {
    "1099219": ("MetLife: its claims liability (LiabilityForClaimsAndClaimsAdjustmentExpense, "
                "17,130 in FY2025) is not its own printed line. It is a tagged part of the printed "
                "line 'Other policy-related balances' (20,070, the company's own element "
                "met_OtherPolicyRelatedBalances). So the claims reserve and the reserve total are "
                "labelled 'as tagged'. The rest of that printed line and the market risk "
                "benefits sit in other liabilities.")}


class InsurerHistory(BankHistory):
    """A history on the insurer chart (docs/MODELS.md "Insurer chart")."""

    def __init__(self, h):
        BankHistory.__init__(self, h)
        # IFRS 17 (maps/ifrs_insurer.yaml): 2 actual years, plus the opening balance
        # sheet one year earlier where the report files it (balance-sheet lines only).
        self.ifrs = h.get("basis") == "ifrs17"
        op = h.get("opening_balance") if self.ifrs else None
        self.opening_fy = str(op["fy"]) if op else None
        self.ofys = ([self.opening_fy] if op else []) + self.fys
        notes = [str(n) for n in (h.get("chart_notes") or [])]
        self.japan = bool(h.get("credit")) or "investment_expenses" in h["lines"]
        self.written = any("WRITTEN" in n for n in notes)
        last = self.fys[-1]
        if h.get("insurer_type"):
            self.itype = h["insurer_type"]
            self.itype_basis = "insurer_type in the history file (set by the filings map)"
        else:
            filed = [k for k in LIFE_LINES if self.get(k, last) is not None]
            self.itype = "life" if filed else "non_life"
            self.itype_basis = (
                "inferred: the FY%s filing tags %s" % (last, ", ".join(filed)) if filed else
                "inferred: the FY%s filing tags none of the life lines (%s)" % (last, ", ".join(LIFE_LINES)))
        self.life = self.itype == "life"
        # Years whose claims are the map's fallback derivation (no claims tag had a value).
        claims = h["lines"].get("claims", {})
        self.claims_derived = [fy for fy in self.fys if "derived_from" in (claims.get(fy) or {})]
        self.claims_label = next((claims[fy].get("label") for fy in self.claims_derived), None)
        self.has_reserve_change = any(self.get("reserve_change", fy) is not None
                                      for fy in self.fys[-WINDOW:])

    def raw(self, line, fy):
        if self.opening_fy is not None and fy == self.opening_fy:
            x = self.h["opening_balance"]["lines"].get(line)
            return None if x is None else x["val"]
        return BankHistory.raw(self, line, fy)

    def nii(self, fy):
        ii = self.get("investment_income", fy)
        if self.ifrs:
            # IFRS 17: the net financial result = investment result + insurance finance result
            # (the latter negative for an expense).
            return None if ii is None else ii + (self.get("insurance_finance_result", fy) or 0.0)
        return None if ii is None else ii - (self.get("investment_expenses", fy) or 0.0)

    def neg(self, line):
        """fy -> minus the line (IFRS 17 files expenses as negative results)."""
        def f(fy):
            v = self.get(line, fy)
            return None if v is None else -v
        return f

    def inv_gains(self, fy):
        """Investment and derivative gains as tagged (US). Japan has no separate line: its
        gains are inside gross investment income (J-GAAP) or the investment result (IFRS).
        None when neither is tagged."""
        if self.ifrs:
            return None
        vals = [self.get(k, fy) for k in ("investment_gains", "derivative_gains")]
        return None if all(v is None for v in vals) else sum(v or 0.0 for v in vals)

    def other_income(self, fy):
        """Total revenue less premiums, fees, investment income, gains and reserve reversals
        (US: other revenues and revenue tagged only under the company's own elements;
        Japan: その他経常収益). IFRS 17 files no total revenue: other income as tagged."""
        if self.ifrs:
            return self.get("other_income", fy)
        tr, pr = self.get("total_revenue", fy), self.get("premiums", fy)
        if tr is None or pr is None:
            return None
        return tr - pr - (self.inv_gains(fy) or 0.0) - sum(
            self.get(k, fy) or 0.0 for k in ("fee_income", "investment_income", "reserve_reversal"))

    def ph_dividends(self, fy):
        """US: policyholder dividends (inside total expenses); Japan life: 契約者配当準備金繰入額."""
        for k in ("policyholder_dividends", "policyholder_dividend_provision"):
            v = self.get(k, fy)
            if v is not None:
                return v
        return None


def insurer_derive(hist, schema):
    """Return (values, how each was derived) for the insurer drivers."""
    d = {a["id"]: a["default"] for a in schema["assumptions"]}
    fys = hist.fys
    win = fys[-WINDOW:]
    last = fys[-1]
    vals, why = {}, {}

    def use(key, value, text, fallback_text=None):
        if value is None:
            vals[key] = d[key]
            why[key] = "schema default (%s)" % fallback_text
        else:
            vals[key] = value
            why[key] = text

    def window_ratio(key, num, den, text, fallback):
        use(key, *fin_window_ratio(win, num, den, text, fallback))

    get = lambda line: (lambda fy: hist.get(line, fy))  # noqa: E731
    prem = "premiums written" if hist.written else "premiums"
    span = "%%d years to FY%s" % last
    g, txt = fin_cagr(fys, get("premiums"), prem)
    if hist.written:
        txt += ("; these are premiums WRITTEN (正味収入保険料 + 生命保険料), not earned: J-GAAP "
                "has no earned-premium element (chart_notes)")
    use("ins_premium_growth", g, txt, txt)
    window_ratio("ins_loss_ratio", get("claims"), get("premiums"),
                 "sum(claims and benefits) / sum(%s) over %s%s" % (
                     prem, span, "; claims PAID (Japan), the reserve build is the separate "
                     "reserve-change driver" if hist.japan else "; US claims and benefits are "
                     "incurred and include the change in reserves"),
                 "claims or premiums not tagged")
    if hist.has_reserve_change:
        window_ratio("ins_reserve_change_pct", get("reserve_change"), get("premiums"),
                     "sum(reserve change = reserve provision less reversal, 責任準備金等繰入額 - "
                     "戻入額) / sum(%s) over %s" % (prem, span), "reserve change not tagged")
    else:
        vals["ins_reserve_change_pct"] = 0.0
        why["ins_reserve_change_pct"] = (
            "0, not applicable: the filing has no separate reserve-change line (US GAAP claims "
            "and benefits already include the change in reserves)")
    dwin = [fy for fy in win if fy in hist.claims_derived]
    if dwin and why["ins_loss_ratio"].startswith("sum("):
        why["ins_loss_ratio"] += (
            ". DERIVED, not a tagged claims line: in FY%s no claims tag has a value, so claims "
            "are %s (the history's warnings name the company's own tags)" % (
                ", FY".join(dwin), hist.claims_label))
    window_ratio("ins_expense_ratio", get("opex"), get("premiums"),
                 "sum(operating expenses incl. acquisition costs) / sum(%s) over %s. Operating "
                 "expenses are derived by the map (US: total expenses less claims, interest "
                 "credited and policyholder dividends; Japan: 経常費用 less claims, reserve provision "
                 "and investment expenses), so they hold every other expense line the filing has "
                 "(for a US life insurer, also the change in market risk benefits and liability "
                 "remeasurement)" % (prem, span), "operating expenses or premiums not tagged")
    if dwin and why["ins_expense_ratio"].startswith("sum("):
        why["ins_expense_ratio"] += (
            ". With claims derived in FY%s, operating expenses there are exactly DAC "
            "amortisation + operating costs and expenses + interest expense" % ", FY".join(dwin))
    if any(hist.ph_dividends(fy) is not None for fy in win):
        window_ratio("ins_ph_dividend_pct", lambda fy: hist.ph_dividends(fy) or 0.0, get("premiums"),
                     "sum(policyholder dividends%s) / sum(%s) over %s" % (
                         " = 契約者配当準備金繰入額, below extraordinary items" if hist.japan else "",
                         prem, span), "premiums not tagged")
    else:
        vals["ins_ph_dividend_pct"] = 0.0
        why["ins_ph_dividend_pct"] = "0, not applicable: no policyholder dividends filed in FY%s-FY%s" % (win[0], last)
    window_ratio("ins_nci_share",
                 lambda fy: None if hist.ni_total(fy) is None or hist.get("net_income", fy) is None
                 else hist.ni_total(fy) - hist.get("net_income", fy), hist.ni_total,
                 "sum(NCI share = net income incl. NCI - net income to owners) / sum(net income "
                 "incl. NCI) over %%d years to FY%s" % last, "net income not tagged")
    v, txt = fin_on_opening(fys, hist.nii, get("investments"),
                            "net investment income%s" % (" (資産運用収益 - 資産運用費用, incl. "
                                                         "realised gains and losses)" if hist.japan else ""),
                            "investments")
    use("ins_investment_yield", v, txt + "; no circularity: the forecast also uses opening investments", txt)
    r, _, _ = fin_window_ratio(win, hist.inv_gains, get("premiums"), "%d", "")
    vals["ins_inv_gains_pct"] = 0.0
    why["ins_inv_gains_pct"] = (
        "0 by default: investment and derivative gains are volatile and not projected. For "
        "reference, sum(investment gains + derivative gains, as tagged) / sum(%s) over "
        "FY%s-FY%s = %s; type that to project them%s" % (
            prem, win[0], last, "n/a (none tagged)" if r is None else "%.4f%%" % (100 * r),
            ". Japan: gains are inside gross investment income (資産運用収益), so this row is 0"
            if hist.japan else ""))
    window_ratio("ins_other_income_pct", hist.other_income, get("premiums"),
                 "sum(other income = total revenue less premiums, fees, investment income, gains "
                 "and reserve reversals%s) / sum(%s) over %s. Projected, because the matching "
                 "costs stay inside operating expenses; set 0 to leave it out" % (
                     "; Japan: その他経常収益" if hist.japan else
                     "; US: other revenues and revenue tagged only under the company's own elements",
                     prem, span), "total revenue or premiums not tagged")
    if any(hist.get("interest_credited", fy) is not None for fy in win):
        v, txt = fin_on_opening(fys, get("interest_credited"), get("policyholder_account_balances"),
                                "interest credited", "policyholder account balances")
        use("ins_crediting_rate", v, txt, txt)
    else:
        vals["ins_crediting_rate"] = 0.0
        why["ins_crediting_rate"] = ("0, not applicable: no interest credited to policyholder "
                                     "accounts is filed in FY%s-FY%s" % (win[0], last))
    vals["ins_reserve_rollforward"] = 1 if hist.has_reserve_change else 0
    why["ins_reserve_rollforward"] = (
        "1: reserves roll forward with the reserve change (opening + change), because the filing "
        "shows that change as its own P&L line" if hist.has_reserve_change else
        "0: reserves are held at a fixed ratio to premiums, because the filing has no separate "
        "reserve-change line (it sits inside claims and benefits)")
    res, pr = hist.get("policy_reserves", last), hist.get("premiums", last)
    use("ins_reserves_pct_premiums", res / pr if res is not None and pr else None,
        "FY%s policy reserves / %s, %s / %s (m), held%s" % (
            last, prem, fnum(res or 0, 3), fnum(pr or 0, 3),
            "; used only when the reserve method is 0" if hist.has_reserve_change else ""),
        "policy reserves or premiums not tagged")
    window_ratio("g_tax_rate", get("tax"), get("pretax_income"),
                 "effective rate: sum(income tax) / sum(income before tax) over %%d years to FY%s" % last,
                 "income tax or pre-tax income not tagged, or pre-tax income not positive")
    fin_capital_defaults(hist, win, use, vals, why, "ins_target_equity_assets")
    why["ins_target_equity_assets"] = why["ins_target_equity_assets"].replace(
        "Regulatory ratios such as CET1 are", "Solvency ratios (ESR, SMR, RBC) are")
    use("val_shares_diluted", *shares_default(hist))
    return vals, why


# (field, history line or method, label, applies) per actual year; applies(hist) False means
# the row reads 0 and is listed as not applicable rather than as untagged.
INSURER_FIELDS = [
    ("premiums", "premiums", "premiums", None),
    ("fee_income", "fee_income", "policy fees and commissions", lambda h: h.life and not h.japan),
    ("investment_income", "investment_income", "investment income", None),
    ("investment_expenses", "investment_expenses", "investment expenses", lambda h: h.japan),
    ("claims", "claims", "claims and benefits", None),
    ("reserve_change", "reserve_change", "change in policy reserves", lambda h: h.japan),
    ("interest_credited", "interest_credited", "interest credited", lambda h: h.life and not h.japan),
    ("opex", "opex", "operating expenses", None),
    ("pretax", "pretax_income", "income before tax", None),
    ("tax", "tax", "income tax", None),
    ("net_income", "net_income", "net income to owners", None),
    ("cash", "cash", "cash", None),
    ("investments", "investments", "investments", None),
    ("claims_reserve", "claims_reserve", "claims reserve", None),
    ("unearned_premiums", "unearned_premiums", "unearned premiums", lambda h: not h.life and not h.japan),
    ("future_policy_benefits", "future_policy_benefits", "future policy benefits", lambda h: h.life and not h.japan),
    ("policyholder_account_balances", "policyholder_account_balances",
     "policyholder account balances", lambda h: h.life and not h.japan),
    ("underwriting_reserve", "policy_reserve", "責任準備金", lambda h: h.japan),
    ("dividend_reserve", "dividend_reserve", "契約者配当準備金", lambda h: h.japan and h.life),
    ("policy_reserves", "policy_reserves", "policy reserves (total)", None),
    ("debt", "debt", "debt", None),
    ("dividends", "dividends_paid", "dividends paid", None),
    ("buybacks", "buybacks", "share buybacks", None)]
NA_REASON = {
    "fee_income": "policy fees are a US life line; J-GAAP and non-life filings do not file them apart",
    "investment_expenses": "US net investment income is already net of investment expenses",
    "reserve_change": "US GAAP claims and benefits include the change in reserves",
    "interest_credited": "life only (US); Japan does not file it as a separate line",
    "unearned_premiums": "US non-life only; Japan holds it inside 責任準備金等, a life insurer files none",
    "future_policy_benefits": "US life only (Japan: inside 責任準備金)",
    "policyholder_account_balances": "US life only (Japan: inside 責任準備金)",
    "underwriting_reserve": "a Japanese line (責任準備金); US reserves are split as above",
    "dividend_reserve": "Japanese life only (契約者配当準備金)",
    "policyholder_dividends": "no policyholder dividends filed (a non-life insurer usually has none)"}


def insurer_slots(hist, n_slots):
    """Per actual year: {field: value}, oldest first. Raises Refused on a bad opening.
    Also returns the notes, with the rows that read 0 because they do not apply."""
    if len(hist.fys) < n_slots:
        raise Refused("only %d fiscal year(s) in the history; the insurer model shows %d, as the "
                      "listed model does, and a model needs at least %d years"
                      % (len(hist.fys), n_slots, n_slots))
    slots, notes, na = [], [], set()
    for y in hist.years[-n_slots:]:
        fy = str(y["fy"])
        ta = hist.get("total_assets", fy)
        eq = hist.equity(fy)
        tl, tl_note = hist.total_liabilities(fy)
        rounding = _rounding_fix(hist, y, ta, tl, eq)
        if rounding:
            tl, tl_note = rounding
        if ta is None or eq is None or tl is None or hist.get("equity_parent", fy) is None:
            raise Refused("FY%s: total assets, total liabilities or equity not tagged, so the "
                          "balance sheet cannot be opened" % fy)
        if tl_note:
            notes.append(tl_note)
        temp = hist.get("temporary_equity", fy) or 0.0
        s = {"fy": y["fy"], "end": y["end"], "form": y["form"], "accn": y["accn"],
             "status": y["status"]}
        for f, line, label, applies in INSURER_FIELDS:
            ok = applies is None or applies(hist)
            v = hist.get(line, fy, label, record=ok)
            if v is None and not ok:
                na.add(f)
            s[f] = v or 0.0
        s["investment_gains"] = hist.inv_gains(fy) or 0.0
        s["other_income"] = hist.other_income(fy) or 0.0
        phd = hist.ph_dividends(fy)
        if phd is None:
            na.add("policyholder_dividends")
        s["policyholder_dividends"] = phd or 0.0
        nit = hist.ni_total(fy)
        s["net_income_total"] = s["net_income"] if nit is None else nit
        s["total_assets"] = ta
        s["total_liabilities"] = tl + temp
        s["equity_parent"] = hist.get("equity_parent", fy)
        s["total_equity"] = eq
        s["_gap"] = hist.raw("total_assets", fy) - round((tl + temp + eq) * SCALE)
        slots.append(s)
    if slots[-1]["_gap"] != 0:
        raise Refused("FY%s (the opening balance sheet) does not balance: assets less "
                      "liabilities, mezzanine equity and equity = %s. Tie-Out never plugs a "
                      "gap, so no model is built" % (slots[-1]["fy"], "{:,}".format(slots[-1]["_gap"])))
    hist.not_applicable = ["%s: 0 in every actual year, not applicable (%s)" % (f, NA_REASON[f])
                           for f in [x[0] for x in INSURER_FIELDS] + ["policyholder_dividends"]
                           if f in na and all(s[f] == 0 for s in slots)]
    insurer_opening(hist, n_slots, notes)
    return slots, notes


def insurer_opening(hist, n_slots, notes):
    """The opening-balance inputs, by the same rules as the actual years (an untagged
    line is 0; IFRS 17 non-life files no account balances)."""
    def pab(fy):
        if hist.ifrs:
            return 0.0
        return hist.get("policyholder_account_balances", fy) or 0.0
    set_opening(hist, n_slots, "insurer", [
        ("investments", lambda fy: hist.get("investments", fy, "investments") or 0.0),
        ("policyholder_account_balances", pab),
        ("equity_parent", lambda fy: hist.get("equity_parent", fy))], notes)


def insurer_data_notes(hist, slots, why, extra):
    h = hist.h
    out = notes_head(hist, slots)
    out.append("## How the filing was read onto the insurer chart")
    out.append(h.get("routing") or "sector: insurer")
    out.append("Insurer type: %s (%s). One template serves both; rows that do not apply read 0 "
               "and are listed below." % (hist.itype.replace("_", "-"), hist.itype_basis))
    out.append(PREMIUM_BASIS_WRITTEN if hist.written else
               "Premium basis: earned, net of reinsurance (the filing's premiums-earned line).")
    if h.get("fixture_note"):
        out.append(str(h["fixture_note"]))
    out += [str(n) for n in (h.get("chart_notes") or [])]
    out += ["## Notes on the P&L"] + [
        "Net investment income = investment income less investment expenses (Japan files both "
        "gross, incl. realised gains and losses; US net investment income is already net and "
        "excludes gains).",
        "Investment and derivative gains are the tagged gain lines (US: net investment gains "
        "and net derivative gains). Japan files no separate line: its realised gains and losses "
        "are inside gross investment income, so the row is 0. They are not projected "
        "(ins_inv_gains_pct is 0 by default).",
        "Other income = total revenue less premiums, fees, investment income, gains and "
        "reserve reversals. US: other revenues and any revenue the company tags only under its "
        "own elements. Japan: その他経常収益. It is projected at its 3-year ratio to premiums "
        "(ins_other_income_pct), because the matching costs (Japan: その他経常費用) stay inside "
        "operating expenses; set it to 0 to leave it out.",
        "Other items in pre-tax = the filed pre-tax profit less the operating result. Japan: "
        "extraordinary income less extraordinary losses (pre-tax includes them). 0 in the "
        "forecast.",
        "Change in policy reserves: Japan files the provision (責任準備金等繰入額, in 経常費用) and "
        "the reversal (責任準備金等戻入額, in 経常収益, i.e. inside revenue); the model nets them into "
        "one line and leaves the reversal out of revenue, so nothing is counted twice."]
    if hist.japan and hist.life:
        out.append("Policyholder dividends (Japan life) are 契約者配当準備金繰入額. The filing puts "
                   "them below extraordinary items, outside 経常費用 (total expenses); the model "
                   "shows them as an underwriting cost, and pre-tax profit is unchanged.")
    if hist.written:
        out.append("Japanese non-life premiums are premiums WRITTEN (正味収入保険料 + 生命保険料), "
                   "not earned, and claims are PAID, not incurred: J-GAAP has no earned-premium "
                   "element. The loss and expense ratios are on premiums written; the change in "
                   "unearned premiums and outstanding claims sits in the reserve change.")
    out += ["## Notes on the balance sheet"] + extra
    if hist.japan:
        out.append("Policy reserves are 保険契約準備金 as filed. Its parts (支払備金, 責任準備金 or "
                   "責任準備金等, 契約者配当準備金) are shown in the actual years; 'reserves not split "
                   "out' is the filed total less the parts (here only the ¥1m rounding of each "
                   "printed subtotal, if any).")
    else:
        out.append("Policy reserves are 'as tagged': the sum of the tagged reserve elements "
                   "(claims reserve, unearned premiums, future policy benefits, policyholder "
                   "account balances), not a printed total. A tagged element is not always its "
                   "own printed line. The rest of such a printed line, and any reserve tagged "
                   "only under the company's own elements, sits in other liabilities.")
        if str(h.get("cik")) in TAGGED_PART_OF_PRINTED:
            out.append(TAGGED_PART_OF_PRINTED[str(h.get("cik"))])
    out += ["Investments are as filed (US: total investments) or the sum of the tagged "
            "investment lines (Japan: for example コールローン, 買現先勘定, 買入金銭債権, 金銭の信託, "
            "有価証券 and 貸付金; the history's tag list says which). Other assets = total assets "
            "less cash and investments.",
            "Other liabilities = total liabilities (incl. mezzanine equity) less policy reserves "
            "and debt. Equity to owners is the parent's shareholders' equity; total equity less "
            "it is 'NCI and other equity' (Japan: incl. share options, 新株予約権)."]
    out.append("## Rows that do not apply (shown as 0)")
    out += hist.not_applicable or ["none"]
    out.append("## How each default was derived (all can be changed on the Assumptions sheet)")
    out += ["%s: %s" % (k, v) for k, v in why.items()]
    out.append("val_share_price: typed by the user; the tool never fetches a price")
    so = h.get("shares_outstanding")
    if so and so.get("note"):
        out.append("Share count: " + so["note"])
    out.append("## Warnings from the filings map")
    out += list(h["warnings"]) or ["none"]
    out.append("## What the insurer model leaves out")
    out += [
        "Embedded value (equity plus the value of in-force business) and the IFRS 17 "
        "contractual service margin (the profit expected on contracts in force, held as a "
        "liability until earned) are not modelled. Book value is equity as filed.",
        "Investment gains are not projected: realised, unrealised and derivative gains are 0 "
        "in the forecast unless you type a ratio (ins_inv_gains_pct). Other income is "
        "projected (ins_other_income_pct).",
        "Solvency ratios (ESR, SMR / ソルベンシー・マージン比率, RBC) are not in the filing data "
        "this tool reads; type them in where you need them and compare. The capital check "
        "uses equity to owners / total assets against a target you type "
        "(ins_target_equity_assets).",
        "No catastrophe or reserve-development scenarios, no reinsurance split (premiums and "
        "claims are net as filed), no interest-rate or market sensitivity, and no asset-"
        "liability matching: the investment yield and crediting rate are fixed ratios on "
        "opening balances.",
        "Premiums grow at one rate and fees with them. Claims, the reserve change, expenses and "
        "policyholder dividends are fixed shares of premiums. Cash, debt, other assets, other "
        "liabilities and non-controlling interests stay flat at the last actual year. "
        "Investments are the balancing line; Investment_Flows rebuilds them from the year's "
        "flows and its check must read zero, and a flag shows if they turn negative.",
        "Reserves: %s Policyholder account balances keep their share of the reserves." % (
            "they roll forward with the reserve change (opening + change), because the filing "
            "shows it as its own line." if hist.has_reserve_change else
            "they are held at the last actual year's ratio to premiums, because the change in "
            "reserves sits inside claims and benefits."),
        "The non-controlling interests' share of net income is a fixed proportion, treated as "
        "paid out to the minority holders, so their equity stays flat.",
        "Valuation is residual income (book equity + the present value of profit above the "
        "cost of equity). There is no free-cash-flow DCF and no enterprise value: reserves are "
        "the insurer's operating liabilities, not its debt."]
    return out


INSURER_ORDER = ["fy", "end", "form", "accn", "status", "premiums", "fee_income",
                 "investment_income", "investment_expenses", "investment_gains", "other_income", "claims",
                 "reserve_change", "interest_credited", "policyholder_dividends", "opex", "pretax",
                 "tax", "net_income_total", "net_income", "cash", "investments", "total_assets",
                 "claims_reserve", "unearned_premiums", "future_policy_benefits",
                 "policyholder_account_balances", "underwriting_reserve", "dividend_reserve",
                 "policy_reserves", "debt", "total_liabilities", "equity_parent", "total_equity",
                 "dividends", "buybacks"]
INSURER_COMMENTS = {
    "premiums": "US: earned, net; Japan non-life: WRITTEN (正味収入保険料 + 生命保険料)",
    "investment_income": "US: net investment income; Japan: 資産運用収益, gross",
    "investment_gains": "investment + derivative gains as tagged (Japan: inside investment income, 0)",
    "other_income": "total revenue less premiums, fees, investment income, gains and reserve reversals",
    "reserve_change": "Japan: provision less reversal; US: 0 (inside claims)",
    "policyholder_dividends": "US: inside total expenses; Japan life: 契約者配当準備金繰入額",
    "opex": "derived by the map; incl. acquisition costs",
    "claims_reserve": "as tagged (it can be a tagged part of a printed line; see Data_Notes)",
    "policy_reserves": "US: sum of the tagged reserve lines; Japan: 保険契約準備金 as filed",
    "total_liabilities": "total liabilities + mezzanine equity",
    "total_equity": "total equity incl. non-controlling interests (Japan: net assets)"}


def write_insurer_answers(path, hist, vals, why, slots, notes, args):
    write_fin_answers(path, hist, vals, why, slots, notes, args, "Listed-insurer",
                      "verticals/listed_insurer.yaml", INSURER_ORDER, INSURER_COMMENTS,
                      insurer_data_notes)


def insurer_main(hist, args):
    ih = InsurerHistory(hist.h)
    if ih.ifrs:
        return fin_main(ih, args, "insurer", INSURER_IFRS_SCHEMA, insurer_ifrs_slots,
                        insurer_ifrs_derive, write_insurer_ifrs_answers)
    return fin_main(ih, args, "insurer", INSURER_SCHEMA, insurer_slots,
                    insurer_derive, write_insurer_answers)


# ── IFRS 17 insurers (history basis "ifrs17"): verticals/listed_insurer_ifrs.yaml ──
# Tokio Marine, SOMPO and MS&AD moved to IFRS in the year to March
# 2026 and build with 2 actual years. This is the only 2-year allowance: the
# 2-year schema refuses any other history, and every other history keeps its
# 3-year template.

INSURER_IFRS_SCHEMA = os.path.join(os.path.dirname(HERE), "verticals", "listed_insurer_ifrs.yaml")
TWO_YEAR_WARNING = (
    "WARNING: ONLY 2 ACTUAL YEARS (IFRS 17). This group moved to IFRS in the year to March "
    "2026, so its IFRS report holds 2 years of P&L and cash flow, so it is built with "
    "2 actual years. Every default below is a 1- or 2-year figure, not the usual "
    "3-year one, so treat the drivers as rough. Japan GAAP years are never mixed in.")
IFRS_TWO_YEAR = " (2-year window: only 2 IFRS years are filed; the usual window is 3)"
PREMIUM_BASIS_IFRS = ("Premium basis: IFRS 17 INSURANCE REVENUE, gross of reinsurance (not "
                      "premiums written or earned). The workbook labels the line 'Insurance "
                      "Revenue'.")


def insurer_ifrs_derive(hist, schema):
    """Return (values, how each was derived) for the IFRS 17 insurer drivers."""
    d = {a["id"]: a["default"] for a in schema["assumptions"]}
    fys = hist.fys
    win = fys[-WINDOW:]
    last = fys[-1]
    vals, why = {}, {}

    def use(key, value, text, fallback_text=None):
        if value is None:
            vals[key] = d[key]
            why[key] = "schema default (%s)" % fallback_text
        else:
            vals[key] = value
            why[key] = text

    def window_ratio(key, num, den, text, fallback):
        use(key, *fin_window_ratio(win, num, den, text, fallback))

    def na(key, text):
        vals[key] = 0.0
        why[key] = "0, not applicable: " + text

    get = lambda line: (lambda fy: hist.get(line, fy))  # noqa: E731
    span = "%%d years to FY%s" % last
    g, txt = fin_cagr(fys, get("premiums"), "insurance revenue (IFRS 17)")
    use("ins_premium_growth", g, txt + ("; only 2 IFRS years are filed, so this is one year's "
                                        "growth" if len(fys) == 2 else ""), txt)
    window_ratio("ins_loss_ratio", get("claims"), get("premiums"),
                 "sum(insurance service expenses) / sum(insurance revenue) over %s; IFRS 17 "
                 "insurance service expenses hold incurred claims, attributable expenses and "
                 "amortised acquisition cash flows (one line on the face)" % span,
                 "insurance service expenses or insurance revenue not tagged")
    window_ratio("ins_reinsurance_pct", hist.neg("reinsurance_result"), get("premiums"),
                 "sum(net expense from reinsurance contracts held = minus the filed reinsurance "
                 "result) / sum(insurance revenue) over %s" % span,
                 "reinsurance result or insurance revenue not tagged")
    na("ins_reserve_change_pct", "IFRS 17 files no reserve-change line; the movement in "
       "insurance contract liabilities is inside insurance service expenses and insurance "
       "finance expenses")
    window_ratio("ins_expense_ratio", get("opex"), get("premiums"),
                 "sum(other expenses = non-attributable 一般管理費 + その他の費用 + 金融費用) / "
                 "sum(insurance revenue) over %s. Attributable expenses are inside insurance "
                 "service expenses, so this is not a J-GAAP or US expense ratio" % span,
                 "other expenses or insurance revenue not tagged")
    na("ins_ph_dividend_pct", "an IFRS 17 non-life group files no policyholder dividends")
    window_ratio("ins_nci_share",
                 lambda fy: None if hist.ni_total(fy) is None or hist.get("net_income", fy) is None
                 else hist.ni_total(fy) - hist.get("net_income", fy), hist.ni_total,
                 "sum(NCI share = net income incl. NCI - net income to owners) / sum(net income "
                 "incl. NCI) over %%d years to FY%s" % last, "net income not tagged")
    v, txt = fin_on_opening(hist.ofys, hist.nii, get("investments"),
                            "net financial result (investment result + net insurance finance "
                            "result)", "investments")
    if hist.opening_fy and "FY%s" % fys[0] in txt:
        txt += ("; the FY%s opening investments come from the IFRS opening balance sheet at %s "
                "(balance sheet only)" % (hist.opening_fy, hist.h["opening_balance"]["end"]))
    elif len(fys) == 2:
        txt += "; 1 year only: the report files no balance sheet a year before FY%s" % fys[0]
    use("ins_investment_yield", v, txt + "; no circularity: the forecast also uses opening "
        "investments", txt)
    vals["ins_inv_gains_pct"] = 0.0
    why["ins_inv_gains_pct"] = ("0: IFRS realised and unrealised investment gains are inside the "
                                "net investment result, which the yield above already projects")
    window_ratio("ins_other_income_pct", hist.other_income, get("premiums"),
                 "sum(other income = その他の収益 + その他の営業収益 + equity-method share) / "
                 "sum(insurance revenue) over %s. Projected, because the matching costs stay in "
                 "other expenses; set 0 to leave it out" % span,
                 "other income or insurance revenue not tagged")
    na("ins_crediting_rate", "no interest credited to policyholder accounts is filed (IFRS 17 "
       "non-life)")
    vals["ins_reserve_rollforward"] = 0
    why["ins_reserve_rollforward"] = (
        "0: insurance contract liabilities are held at a fixed ratio to insurance revenue, because "
        "IFRS 17 shows no reserve-change line to roll them forward with")
    res, pr = hist.get("policy_reserves", last), hist.get("premiums", last)
    use("ins_reserves_pct_premiums", res / pr if res is not None and pr else None,
        "FY%s insurance contract liabilities (incl. the CSM) / insurance revenue, %s / %s (m), "
        "held" % (last, fnum(res or 0, 3), fnum(pr or 0, 3)),
        "insurance contract liabilities or insurance revenue not tagged")
    window_ratio("g_tax_rate", get("tax"), get("pretax_income"),
                 "effective rate: sum(income tax) / sum(income before tax) over %%d years to FY%s" % last,
                 "income tax or pre-tax income not tagged, or pre-tax income not positive")
    fin_capital_defaults(hist, win, use, vals, why, "ins_target_equity_assets")
    why["ins_target_equity_assets"] = why["ins_target_equity_assets"].replace(
        "Regulatory ratios such as CET1 are", "Solvency ratios (ESR) are")
    use("val_shares_diluted", *shares_default(hist))
    if len(fys) == 2:
        for k in why:
            why[k] = why[k].replace("actual 3-year ratio", "actual 2-year ratio")
            if "over 2 years" in why[k]:
                why[k] = why[k].replace("over 2 years", "over 2 years" + IFRS_TWO_YEAR, 1)
            elif "FY%s, FY%s (opening" % (fys[0], fys[1]) in why[k]:
                why[k] += IFRS_TWO_YEAR
    return vals, why


# IFRS 17 rows per actual year: (field, fy -> value, label, applies). A row whose
# `applies` is False reads 0 and is listed as not applicable.
def insurer_ifrs_fields(hist):
    g = lambda line: (lambda fy, label: hist.get(line, fy, label))  # noqa: E731

    def neg(line):
        def f(fy, label):
            v = hist.get(line, fy, label)
            return None if v is None else -v
        return f
    return [
        ("premiums", g("premiums"), "insurance revenue", True),
        ("investment_income", g("investment_income"), "net investment result", True),
        ("investment_expenses", neg("insurance_finance_result"), "net insurance finance result", True),
        ("claims", g("claims"), "insurance service expenses", True),
        ("reinsurance_expense", neg("reinsurance_result"), "reinsurance result", True),
        ("opex", g("opex"), "other expenses", True),
        ("pretax", g("pretax_income"), "income before tax", True),
        ("tax", g("tax"), "income tax", True),
        ("net_income", g("net_income"), "net income to owners", True),
        ("cash", g("cash"), "cash", True),
        ("investments", g("investments"), "investments", True),
        ("policy_reserves", g("policy_reserves"), "insurance contract liabilities", True),
        ("debt", g("debt"), "bonds and borrowings", True),
        ("dividends", g("dividends_paid"), "dividends paid", True),
        ("buybacks", g("buybacks"), "share buybacks", True)]


IFRS_NA = {
    "fee_income": "IFRS 17 non-life: no policy-fee line",
    "investment_gains": "IFRS gains are inside the net investment result",
    "reserve_change": "IFRS 17 files no reserve-change line",
    "interest_credited": "IFRS 17 non-life: no interest credited",
    "policyholder_dividends": "IFRS 17 non-life: no policyholder dividends",
    "claims_reserve": "IFRS 17: the liability for incurred claims is inside insurance contract "
                      "liabilities and is not split on the face",
    "unearned_premiums": "IFRS 17: the liability for remaining coverage is inside insurance "
                         "contract liabilities and is not split on the face",
    "future_policy_benefits": "US life only",
    "policyholder_account_balances": "US life only",
    "underwriting_reserve": "a J-GAAP line (責任準備金)",
    "dividend_reserve": "a J-GAAP life line (契約者配当準備金)"}


def insurer_ifrs_slots(hist, n_slots):
    """Per actual year, as insurer_slots, for an IFRS 17 history."""
    if len(hist.fys) < n_slots:
        raise Refused("only %d fiscal year(s) in the history; the IFRS 17 insurer model shows %d"
                      % (len(hist.fys), n_slots))
    slots, notes = [], []
    for y in hist.years[-n_slots:]:
        fy = str(y["fy"])
        ta, eq = hist.get("total_assets", fy), hist.equity(fy)
        tl, tl_note = hist.total_liabilities(fy)
        rounding = _rounding_fix(hist, y, ta, tl, eq)
        if rounding:
            tl, tl_note = rounding
        if ta is None or eq is None or tl is None or hist.get("equity_parent", fy) is None:
            raise Refused("FY%s: total assets, total liabilities or equity not tagged, so the "
                          "balance sheet cannot be opened" % fy)
        if tl_note:
            notes.append(tl_note)
        s = {"fy": y["fy"], "end": y["end"], "form": y["form"], "accn": y["accn"],
             "status": y["status"]}
        for f, fn, label, _ in insurer_ifrs_fields(hist):
            s[f] = fn(fy, label) or 0.0
        for f in IFRS_NA:
            s[f] = 0.0
        s["other_income"] = hist.other_income(fy) or 0.0
        nit = hist.ni_total(fy)
        s["net_income_total"] = s["net_income"] if nit is None else nit
        s["total_assets"] = ta
        s["total_liabilities"] = tl
        s["equity_parent"] = hist.get("equity_parent", fy)
        s["total_equity"] = eq
        s["_gap"] = hist.raw("total_assets", fy) - round((tl + eq) * SCALE)
        slots.append(s)
    if slots[-1]["_gap"] != 0:
        raise Refused("FY%s (the opening balance sheet) does not balance: assets less "
                      "liabilities and equity = %s. Tie-Out never plugs a gap, so no model is "
                      "built" % (slots[-1]["fy"], "{:,}".format(slots[-1]["_gap"])))
    hist.not_applicable = ["%s: 0 in every actual year, not applicable (%s)" % (f, r)
                           for f, r in IFRS_NA.items()]
    insurer_opening(hist, n_slots, notes)
    return slots, notes


def insurer_ifrs_data_notes(hist, slots, why, extra):
    h = hist.h
    out = ["## Warning: only 2 actual years (IFRS 17)", TWO_YEAR_WARNING] if len(slots) < 3 else []
    out += notes_head(hist, slots)
    op = h.get("opening_balance")
    if op:
        out.append("Opening balance sheet FY%s (%s): %s; balance check %s. It feeds the "
                   "FY%s opening investments in the investment-yield default and the opening-balance "
                   "inputs (balance sheet only)." % (
                       op["fy"], op["end"], op["basis"], op["balance"]["status"], slots[0]["fy"]))
    out.append("## How the filing was read onto the insurer chart")
    out.append(h.get("routing") or "sector: insurer")
    out.append("Insurer type: non-life, IFRS 17 (basis ifrs17 in the history file). Rows that "
               "do not apply read 0 and are listed below.")
    out.append(PREMIUM_BASIS_IFRS)
    out += [str(n) for n in (h.get("chart_notes") or [])]
    if h.get("element_prefixes"):
        pres = sorted(set(h["element_prefixes"].values()))
        out.append("The insurance lines are the group's own elements (prefix %s), read by local "
                   "name: %s." % (", ".join(pres), ", ".join(sorted(h["element_prefixes"]))))
    out += ["## Notes on the P&L",
            "Insurance service result = insurance revenue - insurance service expenses - the net "
            "expense from reinsurance contracts held (the workbook's own row for it).",
            "Net financial result = the net investment result as filed (interest, realised and "
            "unrealised gains, less investment expenses) - net insurance finance expenses. The "
            "workbook shows it on the 'Net Financial Result' row; the forecast projects it as a "
            "yield on opening investments.",
            "Other income (その他の収益, その他の営業収益, the equity-method share) and other "
            "expenses (non-attributable 一般管理費, その他の費用, 金融費用) are their own rows. "
            "'Other items in pre-tax' is the filed pre-tax profit less the operating result: the "
            "¥1m rounding of the printed lines, if any.",
            "Investment gains, the reserve change, interest credited and policyholder dividends "
            "read 0: IFRS 17 non-life files none of them apart."]
    out += ["## Notes on the balance sheet"] + extra
    out += ["Insurance contract liabilities (保険契約負債) are the 'policy reserves' row. They "
            "include the contractual service margin (CSM, the profit not yet earned on contracts "
            "in force), which the filing does not tag on the face and the model does not split "
            "out.",
            "Investments are the tagged investment lines (securities, loans, other financial "
            "investments, investment property, reverse repos). Other assets = total assets less "
            "cash and investments: they hold reinsurance and insurance contract assets, "
            "derivatives, equity-method investments, intangibles and goodwill.",
            "Other liabilities = total liabilities less insurance contract liabilities and bonds "
            "and borrowings: they hold investment contract liabilities, reinsurance contract "
            "liabilities, repos, derivatives and deferred tax."]
    out.append("## Rows that do not apply (shown as 0)")
    out += hist.not_applicable
    out.append("## How each default was derived (all can be changed on the Assumptions sheet)")
    out += ["%s: %s" % (k, v) for k, v in why.items()]
    out.append("val_share_price: typed by the user; the tool never fetches a price")
    so = h.get("shares_outstanding")
    if so and so.get("note"):
        out.append("Share count: " + so["note"])
    out.append("## Warnings from the filings map")
    out += list(h["warnings"]) or ["none"]
    out.append("## What the insurer model leaves out")
    out += [
        "The IFRS 17 contractual service margin (CSM) is inside insurance contract liabilities "
        "and is not modelled separately: no CSM release, new-business CSM or CSM roll-forward. "
        "The risk adjustment and loss components are not modelled either.",
        "Embedded value is not modelled. Book value is equity to owners as filed.",
        "Insurance finance income and expenses that the group takes to OCI (the discount-rate "
        "effect) are not modelled; the forecast's other equity movements are 0.",
        "Solvency ratios (ESR) are not in the filing data this tool reads; type them in where "
        "you need them. The capital check uses equity to owners / total assets against a "
        "target you type (ins_target_equity_assets).",
        "No catastrophe, reserve-development, interest-rate or market scenarios. Insurance "
        "revenue grows at one rate; insurance service expenses, the reinsurance expense and "
        "other expenses are fixed shares of it. Insurance contract liabilities hold their last "
        "ratio to insurance revenue. Cash, debt, other assets (incl. reinsurance contract "
        "assets), other liabilities and NCI stay flat. Investments are the balancing line.",
        "Valuation is residual income (book equity + the present value of profit above the "
        "cost of equity), not a free-cash-flow DCF."]
    return out


IFRS_ORDER = ["fy", "end", "form", "accn", "status", "premiums", "fee_income",
              "investment_income", "investment_expenses", "investment_gains", "other_income",
              "claims", "reinsurance_expense", "reserve_change", "interest_credited",
              "policyholder_dividends", "opex", "pretax", "tax", "net_income_total", "net_income",
              "cash", "investments", "total_assets", "claims_reserve", "unearned_premiums",
              "future_policy_benefits", "policyholder_account_balances", "underwriting_reserve",
              "dividend_reserve", "policy_reserves", "debt", "total_liabilities", "equity_parent",
              "total_equity", "dividends", "buybacks"]
IFRS_COMMENTS = {
    "premiums": "IFRS 17 insurance revenue (not premiums written or earned)",
    "investment_income": "net investment result as filed (interest + gains - investment expenses)",
    "investment_expenses": "IFRS 17 net insurance finance expenses (minus the filed result)",
    "other_income": "その他の収益 + その他の営業収益 + equity-method share",
    "claims": "IFRS 17 insurance service expenses",
    "reinsurance_expense": "net expense from reinsurance contracts held (minus the filed result)",
    "opex": "non-attributable 一般管理費 + その他の費用 + 金融費用",
    "policy_reserves": "insurance contract liabilities, incl. the CSM",
    "total_equity": "total equity incl. non-controlling interests"}


def write_insurer_ifrs_answers(path, hist, vals, why, slots, notes, args):
    write_fin_answers(path, hist, vals, why, slots, notes, args, "Listed-insurer (IFRS 17)",
                      "verticals/listed_insurer_ifrs.yaml", IFRS_ORDER, IFRS_COMMENTS,
                      insurer_ifrs_data_notes,
                      warning=TWO_YEAR_WARNING if len(slots) < 3 else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("history")
    ap.add_argument("--out", default=None)
    ap.add_argument("--forecast-years", type=int, default=5)
    ap.add_argument("--price", type=float, default=None)
    ap.add_argument("--price-date", default=None)
    ap.add_argument("--shares-millions", type=float, default=None,
                    help="share count in millions, replacing the filing's default count")
    ap.add_argument("--schema", default=None,
                    help="schema file (default: listed_corporate.yaml, or listed_bank.yaml / "
                         "listed_insurer.yaml when the history's sector is bank / insurer)")
    args = ap.parse_args(argv)
    if args.price is not None and not args.price_date:
        ap.error("--price needs --price-date")
    if args.shares_millions is not None and args.shares_millions <= 0:
        ap.error("--shares-millions must be above 0")
    if not 5 <= args.forecast_years <= 10:
        ap.error("--forecast-years must be 5 to 10")
    hist = History(load(args.history))
    if hist.h.get("sector") == "bank":
        return bank_main(hist, args)
    if hist.h.get("sector") == "insurer":
        return insurer_main(hist, args)
    schema = load_schema(args.schema or SCHEMA)
    try:
        one_standard(hist)
        slots, notes = history_slots(hist, int(schema["history"]["years"]))
    except Refused as e:
        print("%s: %s" % (hist.h.get("ticker"), e))
        return 2
    vals, why = derive(hist, schema)
    if args.shares_millions is not None:
        why["val_shares_diluted"] = ("source: user, typed on the command line, in millions "
                                     "(the filing default was %s m: %s)"
                                     % (fnum(vals["val_shares_diluted"]), why["val_shares_diluted"]))
        vals["val_shares_diluted"] = args.shares_millions
    out = args.out or os.path.dirname(os.path.abspath(args.history))
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "%s_filings.yaml" % hist.h["ticker"].lower())
    write_answers(path, hist, vals, why, slots, notes, args)
    print("wrote %s" % path)
    fallback = [k for k in vals if why[k].startswith("schema default")]
    for k in fallback:
        print("WARNING: %s is the schema placeholder, not the company's own figure: %s"
              % (k, why[k]))
    if hist.missing:
        print("WARNING: %d line(s) not tagged in the actual years, shown as 0 (see Data_Notes): %s"
              % (len(set(hist.missing)), "; ".join(sorted(set(hist.missing)))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

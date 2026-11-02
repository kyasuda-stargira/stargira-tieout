#!/usr/bin/env python3
"""
Rebuild a listed company's annual history from its filed XBRL facts, and tie it out.

Reads:  a saved SEC companyfacts JSON (from fetch_filings.py) + a tag map
        (engine/maps/us_gaap.yaml)
Emits:  <out>/<TICKER>_history.json  lines x years, the source tag and accession
                                     number (accn) of every value, tie-out
                                     results, flags and warnings
        <out>/<TICKER>_history.md    a short summary

Usage:
  filings_map.py us <companyfacts.json> --ticker AAPL --out output/filings

How values are picked:
  - Only 10-K and 10-K/A facts count. A fact's own dates decide its period:
    `fy`/`fp` describe the filing, not the period, so they are not used for that.
  - Flows (revenue, cash from operations ...): start..end about one year,
    ending on the fiscal year end. Stocks (assets, cash ...): instant at the
    fiscal year end.
  - Per line, the first tag in the map's list that has a value wins; within that
    tag the latest filing wins, so restated figures replace the originals.
  - Each fiscal year carries the accn of its own 10-K; each value its own accn.

Tie-out per year (history only; nothing is ever plugged):
  1. balance:    assets = liabilities + equity (tagged totals)
  2. cash:       CFO + CFI + CFF + FX = net change; opening + change = closing;
                 cash-flow closing cash = balance-sheet cash, reconciled through
                 restricted cash when the filing tags it
  3. net income: P&L net income links to the cash-flow starting line;
                 the retained-earnings roll-forward is reported (never fails a year)
Tolerance is one reporting unit (e.g. $1M when every value is a whole million).
A year that fails a check is FLAGGED with the reason.

Banks and insurers: the map's `unsupported` block detects them, and its `route`
list sends each to its own map (maps/us_gaap_bank.yaml, us_gaap_insurer.yaml),
which fills the financial-company chart (docs/MODELS.md). One that fits both is
routed by its saved SEC SIC code (bank 60xx or insurer 63xx; Citi, 6021) and is
otherwise refused, like one that fits neither ("not supported yet"). A securities firm is refused too, until a
broker-dealer template exists: broker_dealer_reason() reads its
SEC SIC code (<TICKER>_sic.json, saved by fetch_filings.py beside the companyfacts
file) or, with no SIC, its deposits and loans as a share of total assets.
Sector maps add derived lines (`derive`) and P&L footings (check 4, `pl`).
A line with both `tags` and `derive` takes a tag when one has a value that year
and is derived only when none does (the value then carries the derive `label`).
A `helper: true` line feeds such a fallback and stays in the history only when a
derivation used it; an `omit_if_empty: true` line is left out, with no warning,
when no year has a value. A `parts_must_foot: true` line keeps a sum of its
`parts` only in a year where its `fail` footing then ties (drop_unfooted_parts;
the bank provision since CECL). A footing's `inside_if_ties` names a term some
filers already count inside another one: it is left out only when the footing
misses with it and ties without it, and the test name says so (Citi's gain on
disposal, inside its net revenue).
The history records its `sector`: corporate, bank or insurer.

Japan (`jp`): jp_route() reads the DEI industry code (either case) or, failing
that, the element suffixes (BNK / INS / SEC), and sends a J-GAAP bank to
maps/jgaap_bank.yaml, an insurer to jgaap_insurer_life.yaml or
jgaap_insurer_nonlife.yaml (by its premium line). A securities firm (code SEC, or
the SEC suffix) is refused until a broker-dealer template exists;
maps/jgaap_securities.yaml is kept for that template, unused. An IFRS non-life
insurer that files IFRS 17 lines goes to maps/ifrs_insurer.yaml (jp_ifrs_insurer),
which reads the group's own elements by local name, because EDINET's IFRS taxonomy
has no industry elements. Any other IFRS bank, insurer or securities firm is refused.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import date, timedelta

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAP = os.path.join(HERE, "maps", "us_gaap.yaml")
UNITS = (1000000, 1000, 1)


class Unsupported(Exception):
    pass


# loading ----------------------------------------------------------------------
def load_map(path=DEFAULT_MAP):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_facts(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_sic(facts_path, data):
    """The SIC record fetch_filings.py saves beside <TICKER>_companyfacts.json as
    <TICKER>_sic.json, or None when there is none (e.g. a --from-file run) or it is
    for another CIK."""
    if not facts_path.endswith("_companyfacts.json"):
        return None
    path = facts_path[:-len("_companyfacts.json")] + "_sic.json"
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if str(rec.get("cik")).lstrip("0") != str(data.get("cik")).lstrip("0") or not rec.get("sic"):
        return None
    return rec


def load_statements(facts_path, data):
    """The 10-K statement rows fetch_filings.py saves beside <TICKER>_companyfacts.json
    as <TICKER>_statements.json (only when a line has no standard tag), or None when
    there is none or it is for another CIK."""
    if not facts_path.endswith("_companyfacts.json"):
        return None
    path = facts_path[:-len("_companyfacts.json")] + "_statements.json"
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if str(rec.get("cik")).lstrip("0") != str(data.get("cik")).lstrip("0"):
        return None
    return rec


def extension_rows(st, spec):
    """The company's own rows on the statement page a line's `extension` names whose
    element name contains every one of its words."""
    ext = spec.get("extension")
    page = ((st or {}).get("pages") or {}).get(ext["statement"]) if ext else None
    if not page:
        return [], None
    words = [w.lower() for w in ext["words"]]
    rows = [r for r in page["rows"] if r["custom"]
            and all(w in r["element"].split(":")[-1].lower() for w in words)]
    return rows, page


def statement_line(st, spec, y):
    """A line the company files only under its own tag, as printed on its 10-K
    statement page for the year ending y["end"]; None unless exactly one row fits."""
    rows, page = extension_rows(st, spec)
    if len(rows) != 1:
        return None
    for i, (end, months) in enumerate(zip(page["ends"], page["months"])):
        if end == y["end"] and months in (None, 12) and rows[0]["values"][i] is not None:
            return {"val": rows[0]["values"][i] * page["scale"], "tag": rows[0]["element"],
                    "accn": st["accn"], "form": st["form"], "filed": st["filed"],
                    "printed_label": rows[0]["label"], "page": page["file"]}
    return None


def d(s):
    return date.fromisoformat(s)


def annual_facts(gaap, tag, cmap):
    """10-K facts for one tag in the map's unit (empty list if absent)."""
    forms = set(cmap["meta"]["forms"])
    units = gaap.get(tag, {}).get("units", {})
    return [f for f in units.get(cmap["meta"]["unit"], []) if f.get("form") in forms]


def is_annual(fact, cmap):
    lo, hi = cmap["meta"]["flow_days"]
    return "start" in fact and lo <= (d(fact["end"]) - d(fact["start"])).days <= hi


# fiscal years -----------------------------------------------------------------
def fiscal_years(gaap, cmap):
    """One entry per fiscal year end, oldest first: {fy, end, accn, form, filed}.

    A filing's own period end is the latest annual-duration end that carries at
    least a quarter of that filing's annual facts (the current year, not a stray
    comparative). The fiscal-year label is the `fy` of the first 10-K for it.
    """
    by_accn = {}
    for tag in gaap:
        for f in annual_facts(gaap, tag, cmap):
            if is_annual(f, cmap):
                by_accn.setdefault(f["accn"], []).append(f)
    ends = {}
    for accn, facts in by_accn.items():
        counts = {}
        for f in facts:
            counts[f["end"]] = counts.get(f["end"], 0) + 1
        floor = max(counts.values()) / 4.0
        end = max(e for e, n in counts.items() if n >= floor)
        first = facts[0]
        ends.setdefault(end, []).append({"accn": accn, "form": first["form"],
                                         "filed": first["filed"], "fy": first.get("fy")})
    years, used = [], set()
    for end in sorted(ends):
        filings = sorted(ends[end], key=lambda x: (x["form"] != "10-K", x["filed"]))
        own = filings[0]
        fy = own["fy"] if own["fy"] and own["fy"] not in used else d(end).year
        used.add(fy)
        years.append({"fy": fy, "end": end, "accn": own["accn"],
                      "form": own["form"], "filed": own["filed"],
                      "amendments": [x["accn"] for x in filings[1:]]})
    return years[-cmap["meta"]["max_years"]:]


# value picking ----------------------------------------------------------------
def pick(gaap, tag, kind, end, cmap, prefer=None):
    """10-K value of tag for the period ending `end`, or None.

    The fact from filing `prefer` (an accn) wins if there is one; otherwise the
    latest-filed fact wins, so a restated figure replaces the original.
    """
    best = None
    for f in annual_facts(gaap, tag, cmap):
        if f["end"] != end:
            continue
        if kind == "flow" and not is_annual(f, cmap):
            continue
        if kind == "stock" and "start" in f:
            continue
        rank = (f["accn"] == prefer, f["filed"])
        if best is None or rank > (best["accn"] == prefer, best["filed"]):
            best = f
    if best is None:
        return None
    return {"val": best["val"], "tag": tag, "accn": best["accn"],
            "form": best["form"], "filed": best["filed"]}


def pick_line(gaap, spec, end, cmap, prefer=None):
    """First tag in the line's list with a value wins.

    Optional keys: `sign: -1` turns a filed payment (negative on a Japanese cash
    flow statement) into the positive amount the chart expects; `parts` (a list
    of tag lists) sums the parts that are tagged when no total tag is. A part
    written as {tags: [...], sign: -1} is subtracted (a Japanese bank's loan-loss
    reversal, filed on the income side, nets against its provision)."""
    sign = spec.get("sign", 1)
    for tag in spec["tags"]:
        v = pick(gaap, tag, spec["kind"], end, cmap, prefer)
        if v is not None:
            if sign != 1:
                v = dict(v, val=v["val"] * sign)
            return v
    parts = []
    for part in spec.get("parts", []):
        tags, psign = (part["tags"], part.get("sign", 1)) if isinstance(part, dict) else (part, 1)
        x = pick_line(gaap, {"tags": tags, "kind": spec["kind"]}, end, cmap, prefer)
        if x is not None:
            parts.append((psign, x))
    if not parts:
        return None
    text = " ".join(("- " if s < 0 else "+ ") + x["tag"] for s, x in parts)
    return {"val": sign * sum(s * x["val"] for s, x in parts),
            "tag": text[2:] if text.startswith("+ ") else text,
            "accn": parts[0][1]["accn"], "form": parts[0][1]["form"], "filed": parts[0][1]["filed"]}


def part_tags(spec):
    """Every tag a line's `parts` names, for messages."""
    return [t for p in spec.get("parts", []) for t in (p["tags"] if isinstance(p, dict) else p)]


def pick_any(gaap, tags, kind, end, cmap, prefer=None):
    return pick_line(gaap, {"tags": tags, "kind": kind}, end, cmap, prefer)


def pick_restricted(gaap, end, cmap, prefer=None):
    """Restricted cash at `end`: the total tag, else the sum of tagged parts."""
    rc = cmap.get("restricted_cash") or {"total": [], "parts": []}
    total = pick_any(gaap, rc["total"], "stock", end, cmap, prefer)
    if total is not None:
        return total
    parts = [pick_any(gaap, tags, "stock", end, cmap, prefer) for tags in rc["parts"]]
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    return {"val": sum(p["val"] for p in parts), "tag": " + ".join(p["tag"] for p in parts),
            "accn": parts[0]["accn"], "form": parts[0]["form"], "filed": parts[0]["filed"]}


def prior_end(gaap, year, cmap):
    """Previous fiscal year end = the day before this year's flow start."""
    for tag in cmap["meta"].get("period_tags", ("NetCashProvidedByUsedInOperatingActivities",
                                                 "NetIncomeLoss", "ProfitLoss", "Revenues")):
        for f in annual_facts(gaap, tag, cmap):
            if f["end"] == year["end"] and is_annual(f, cmap):
                return (d(f["start"]) - timedelta(days=1)).isoformat()
    return None


def reporting_unit(values):
    """Largest of $1M / $1k / $1 that divides every value."""
    vals = [v for v in values if v]
    for u in UNITS:
        if vals and all(v % u == 0 for v in vals):
            return u
    return 1


# detection --------------------------------------------------------------------
def unsupported_reason(gaap, years, cmap):
    rule = cmap.get("unsupported") or {}
    if not rule.get("enabled"):
        return None
    for tag in rule.get("require_absent", []):
        if any(pick(gaap, tag, "stock", y["end"], cmap) for y in years):
            return None
    hits = [t for t in rule.get("any_present", []) if annual_facts(gaap, t, cmap)]
    if not hits:
        return None
    return "%s (no %s; has %s)" % (rule["message"].strip(),
                                   ", ".join(rule["require_absent"]), ", ".join(hits))


def sic_says(route, sic):
    """True when the saved SIC record names this route's sector (`sic` codes or
    `sic_prefix`)."""
    if not sic or not sic.get("sic"):
        return False
    code = str(sic["sic"])
    return (code in [str(c) for c in route.get("sic") or []]
            or bool(route.get("sic_prefix")) and code.startswith(str(route["sic_prefix"])))


def route_sector(gaap, years, cmap, reason, sic=None):
    """(sector, map) for a company the `unsupported` rule caught, from the rule's
    `route` list: exactly one route must have a 10-K value in the selected years.
    When more than one has, a saved SEC SIC code that names exactly one of them
    breaks the tie (Citi: both lines, SIC 6021, a bank). Raises Unsupported with a
    plain reason otherwise."""
    routes = (cmap.get("unsupported") or {}).get("route") or []
    ends = {y["end"] for y in years}

    def present(tag):
        return any(f["end"] in ends for f in annual_facts(gaap, tag, cmap))
    hits = [(r, [t for t in r["any_present"] if present(t)]) for r in routes]
    hits = [(r, tags) for r, tags in hits if tags]
    if len(hits) == 1:
        r, tags = hits[0]
        return (r["sector"], load_map(os.path.join(HERE, "maps", r["map"])),
                "routed to the %s chart (maps/%s) because it has %s" % (
                    r["sector"], r["map"], ", ".join(tags)))
    if not hits:
        raise Unsupported("%s It has none of %s in the years selected, so neither the "
                          "bank nor the insurer chart fits it" % (
                              reason, " / ".join("%s (%s)" % (", ".join(r["any_present"]),
                                                              r["sector"]) for r in routes)))
    both = " and ".join("%s (%s)" % (", ".join(tags), r["sector"]) for r, tags in hits)
    named = [(r, tags) for r, tags in hits if sic_says(r, sic)]
    if len(named) == 1:
        r = named[0][0]
        return (r["sector"], load_map(os.path.join(HERE, "maps", r["map"])),
                "routed to the %s chart (maps/%s) because it has %s, and its SEC SIC code %s "
                "(%s) says %s" % (r["sector"], r["map"], both, sic["sic"],
                                  sic.get("sicDescription") or "", r["sector"]))
    why = ("its SEC SIC code %s does not name a bank or an insurer" % sic["sic"]
           if sic and sic.get("sic") else "no SEC SIC code is saved")
    raise Unsupported("%s It has %s, so it is not routed rather than guessed: %s. A bank "
                      "(%s) or insurer (%sxx) SIC code in <TICKER>_sic.json, saved by "
                      "fetch_filings.py, would settle it" % (
                          reason, both, why,
                          ", ".join(c for r in routes for c in (r.get("sic") or [])),
                          "/".join(r["sic_prefix"] for r in routes if r.get("sic_prefix"))))


def broker_dealer_reason(sic, h, cmap):
    """The refusal text for a securities firm the bank route caught, else None.

    The SEC SIC code decides when it was saved. Without it, the latest year with
    deposits and total assets decides: a broker-dealer group's deposits are a small
    share of its assets (GS and MS 14.4-30.9%, banks 51.9-82.1%, FY2016-FY2025), and
    so are its loans when tagged (5.8-18.9% against 26.5-60.5%)."""
    rule = (cmap.get("unsupported") or {}).get("broker_dealer")
    if not rule:
        return None
    if sic:
        if str(sic["sic"]) not in [str(c) for c in rule["sic"]]:
            return None
        detail = "SEC SIC code %s, %s" % (sic["sic"], sic.get("sicDescription") or "")
        return rule["message"].strip().format(detail=detail.rstrip(", "))
    lines = h["lines"]

    def val(key, fy):
        v = (lines.get(key) or {}).get(fy) or (lines.get(key) or {}).get(str(fy))
        return v and v["val"]
    for y in reversed(h["years"]):
        fy = y["fy"]
        assets, deposits = val("total_assets", fy), val("deposits", fy)
        if not assets or deposits is None:
            continue
        loans = val("loans", fy)
        if deposits / assets >= rule["max_deposits_to_assets"]:
            return None
        if loans is not None and loans / assets >= rule["max_loans_to_assets"]:
            return None
        detail = ("no SEC SIC code saved; in FY%s its deposits are %.0f%% of total assets%s, "
                  "below the %.0f%%%s that mark a bank" % (
                      fy, 100.0 * deposits / assets,
                      "" if loans is None else " and its loans %.0f%%" % (100.0 * loans / assets),
                      100 * rule["max_deposits_to_assets"],
                      "" if loans is None else " and %.0f%%" % (100 * rule["max_loans_to_assets"])))
        return rule["message"].strip().format(detail=detail)
    return None


def derive_line(lines, spec, fy):
    """A line computed from other chart lines (`derive: {plus, minus, require}`).
    Lines in `require` (default: all) must be there; others count as 0 if missing.
    None if a required line is missing or no line is there."""
    dv = spec["derive"]
    terms = [(1, k) for k in dv.get("plus", [])] + [(-1, k) for k in dv.get("minus", [])]
    need = dv.get("require", [k for _, k in terms])
    if any(lines.get(k, {}).get(fy) is None for k in need):
        return None
    used = [(s, k, lines[k][fy]) for s, k in terms if lines.get(k, {}).get(fy) is not None]
    if not used:
        return None
    text = " ".join(("- " if s < 0 else "+ ") + k for s, k, _ in used)
    first = used[0][2]
    return {"val": sum(s * v["val"] for s, _, v in used),
            "tag": "derived: " + (text[2:] if text.startswith("+ ") else text),
            "accn": first["accn"], "form": first["form"], "filed": first["filed"],
            "derived_from": {k: v["val"] for _, k, v in used}}


def drop_unused(lines, cmap):
    """Remove `helper` lines no derivation used and `omit_if_empty` lines with no value."""
    used = {k for by in lines.values() for v in by.values() for k in v.get("derived_from", {})}
    for key, spec in cmap["lines"].items():
        if (spec.get("helper") and key not in used) or (spec.get("omit_if_empty")
                                                       and not lines[key]):
            del lines[key]


def drop_unfooted_parts(lines, fy, cmap, tol):
    """A `parts_must_foot: true` line whose value this year is a sum of parts (no
    total tag had one) is kept only if every `fail` footing using it then ties.
    Otherwise the value is removed, so the year shows the line as missing, and the
    returned texts (for the year's flag) say what the parts added to."""
    texts = []
    for key, spec in cmap["lines"].items():
        v = lines.get(key, {}).get(fy)
        if not spec.get("parts_must_foot") or v is None or v["tag"] in spec["tags"]:
            continue
        for ft in cmap.get("footings", []):
            if not ft.get("fail") or key not in ft.get("plus", []) + ft.get("minus", []):
                continue
            t = check_footings(lines, fy, {"footings": [ft], "meta": cmap["meta"]}, tol)["tests"]
            if t and not t[0]["ok"]:
                del lines[key][fy]
                texts.append("%s: the tagged parts (%s) add to %s, but then %s is off by %s%s, so "
                             "a part is filed only under the company's own tag and no %s is shown"
                             % (key, v["tag"], fmt(v["val"]), t[0]["test"], fmt(t[0]["diff"]),
                                " (%s with %s left out)" % (
                                    fmt(t[0]["diff_left_out"]), ", ".join(
                                        k for k in ft["inside_if_ties"] if val(lines, k, fy)))
                                if "diff_left_out" in t[0] else "", key))
                break
    return texts


# tie-out ----------------------------------------------------------------------
def val(lines, key, fy):
    v = lines.get(key, {}).get(fy)
    return None if v is None else v["val"]


def compare(name, left, right, tol):
    diff = left - right
    return {"test": name, "left": left, "right": right, "diff": diff,
            "ok": abs(diff) <= tol}


def check_balance(lines, fy, tol):
    a = val(lines, "total_assets", fy)
    if a is None:
        return {"status": "flag", "reason": "total assets not tagged", "tests": []}
    tests = []
    liab = val(lines, "total_liabilities", fy)
    eq = val(lines, "total_equity", fy)
    if eq is None and val(lines, "equity_parent", fy) is not None:
        eq = val(lines, "equity_parent", fy) + (val(lines, "nci", fy) or 0)
    if liab is not None and eq is not None:
        rhs = liab + eq + (val(lines, "temporary_equity", fy) or 0)
        tests.append(compare("assets = liabilities + equity", a, rhs, tol))
    lse = val(lines, "liabilities_and_equity", fy)
    if lse is not None:
        tests.append(compare("assets = tagged liabilities-and-equity total", a, lse, tol))
    if not tests:
        return {"status": "flag", "tests": [],
                "reason": "no liabilities or equity totals tagged, cannot check"}
    bad = [t for t in tests if not t["ok"]]
    if bad:
        return {"status": "flag", "tests": tests,
                "reason": "; ".join("%s off by %s" % (t["test"], fmt(t["diff"])) for t in bad)}
    note = None if liab is not None else ("total liabilities not tagged; checked "
                                          "against the tagged liabilities-and-equity total only")
    return {"status": "pass", "tests": tests, "note": note}


EXT_CCE = "ext:"   # prefix for company-specific cash-reconciliation elements (Japan)


def check_cash(gaap, lines, year, opening, restricted, cmap, tol):
    fy = year["fy"]
    tests, reasons, notes = [], [], []
    cfo, cfi, cff = (val(lines, k, fy) for k in ("cfo", "cfi", "cff"))
    change = val(lines, "net_change_cash", fy)
    fx = val(lines, "fx_effect", fy) or 0
    if change is None and val(lines, "net_change_cash_before_fx", fy) is not None:
        change = val(lines, "net_change_cash_before_fx", fy) + fx
        notes.append("net change in cash taken as the tagged change before exchange-rate "
                     "effects plus the exchange-rate effect")
    other = val(lines, "other_cash_change", fy)
    if None in (cfo, cfi, cff, change):
        reasons.append("cash-flow totals not all tagged (CFO, CFI, CFF, net change)")
    else:
        tests.append(compare("CFO + CFI + CFF + FX = net change", cfo + cfi + cff + fx, change, tol))
    close = lines.get("end_cash_cf", {}).get(fy)
    if close is not None and opening is not None and change is not None:
        if other:
            tests.append(compare("opening + net change + other tagged changes = closing "
                                 "(cash flow)", opening + change + other, close["val"], tol))
        else:
            t = compare("opening + net change = closing (cash flow)",
                        opening + change, close["val"], tol)
            ext = [x for x in (pick(gaap, k, "flow", year["end"], cmap)
                               for k in gaap if k.startswith(EXT_CCE)) if x]
            if not t["ok"] and ext:
                extra = sum(x["val"] for x in ext)
                t2 = compare("opening + net change + the company's own reconciling lines "
                             "= closing (cash flow)", opening + change + extra, close["val"], tol)
                if t2["ok"]:
                    t = t2
                    notes.append("roll-forward closed by the company's own cash-reconciliation "
                                 "element(s) %s (%s), used only because they make the year tie"
                                 % (", ".join(x["tag"][len(EXT_CCE):] for x in ext), fmt(extra)))
            if not t["ok"] and cmap.get("disposal_group_cash"):
                prev = prior_end(gaap, year, cmap)
                dg = [pick_any(gaap, cmap["disposal_group_cash"], "stock", e, cmap)
                      for e in (prev, year["end"])] if prev else [None, None]
                if any(x and x["val"] for x in dg):
                    dg_open, dg_close = ((x["val"] if x else 0) for x in dg)
                    t2 = compare("opening + cash held for sale at the start + net change = "
                                 "closing + cash held for sale at the end (cash flow)",
                                 opening + dg_open + change, close["val"] + dg_close, tol)
                    if t2["ok"]:
                        t = t2
                        notes.append("roll-forward closed by cash held in a disposal group "
                                     "(%s: %s at the start, %s at the end), used only because "
                                     "it makes the year tie" % (
                                         next(x["tag"] for x in dg if x), fmt(dg_open),
                                         fmt(dg_close)))
            tests.append(t)
    elif close is not None and change is not None:
        notes.append("opening cash for this year not in the data; roll-forward not checked")
    for t in tests:
        if not t["ok"]:
            # Four figures each rounded (or truncated) to the unit can miss their
            # own rounded total by up to 3 units. Still flagged; the reason says so.
            small = (t["test"].startswith("CFO + CFI + CFF") and abs(t["diff"]) <= 3 * tol)
            reasons.append("%s off by %s%s" % (
                t["test"], fmt(t["diff"]),
                " (within the rounding of the four filed figures, each shown to %s; the filed "
                "figures themselves do not add)" % fmt(tol) if small else ""))
    bs = val(lines, "cash", fy)
    if bs is None and val(lines, "cash_and_sti", fy) is not None:
        bs = val(lines, "cash_and_sti", fy)
        notes.append("balance sheet shows one 'cash and short-term investments' line; "
                     "compared against that")
    if close is None or bs is None:
        reasons.append("closing cash or balance-sheet cash not tagged")
        bs_test = None
    else:
        bs_test = compare("closing cash (cash flow) = balance-sheet cash", close["val"], bs, tol)
        if close["tag"] in cmap["lines"]["cash"]["tags"] and bs_test["ok"]:
            notes.append("cash flow closes on the balance-sheet cash tag itself")
        elif not bs_test["ok"] and restricted is not None:
            gap = bs_test["diff"]
            what = restricted.get("label", "restricted cash")
            bs_test = compare("closing cash (cash flow) = balance-sheet cash + %s" % what,
                              close["val"], bs + restricted["val"], tol)
            bs_test["restricted_tag"] = restricted["tag"]
            if bs_test["ok"]:
                notes.append("reconciled through %s (%s)" % (what, restricted["tag"]))
            else:
                reasons.append("gap of %s between cash-flow closing cash and balance-sheet "
                               "cash; %s (%s, %s) does not explain it"
                               % (fmt(gap), what, restricted["tag"], fmt(restricted["val"])))
                bs_test["explained"] = True
        elif not bs_test["ok"]:
            reasons.append((cmap["meta"].get("cash_gap_reason") or
                            "cash-flow closing cash includes restricted cash (%s) and the "
                            "filing tags no restricted-cash balance, so the gap of %s cannot "
                            "be reconciled from the data") % (close["tag"], fmt(bs_test["diff"])))
            bs_test["explained"] = True
    if bs_test is not None:
        tests.append(bs_test)
        if not bs_test["ok"] and not bs_test.get("explained"):
            reasons.append("%s off by %s" % (bs_test["test"], fmt(bs_test["diff"])))
    return {"status": "flag" if reasons else "pass", "tests": tests,
            "reason": "; ".join(dict.fromkeys(reasons)) or None,
            "note": "; ".join(notes) or None}


def check_net_income(gaap, lines, year, re_open, cmap, tol):
    fy = year["fy"]
    ni = val(lines, "net_income", fy)
    total = val(lines, "net_income_incl_nci", fy)
    nci = val(lines, "net_income_nci", fy)
    tests, notes = [], []
    if ni is None:
        return {"status": "flag", "tests": [], "reason": "net income not tagged"}
    if total is not None:
        tests.append(compare("net income incl. NCI (cash-flow start) = P&L net income + NCI share",
                             total, ni + (nci or 0), tol))
    else:
        notes.append("no tag for net income including non-controlling interests: the "
                     "cash flow starts from the same net income value as the P&L, so the "
                     "link holds by construction")
    re_close = val(lines, "retained_earnings", fy)
    divs = pick_any(gaap, cmap["dividends_declared_tags"], "flow", year["end"], cmap,
                    year["source_filings"]["bs"])
    roll = None
    if re_close is not None and re_open is not None:
        paid = (divs["val"] * cmap.get("dividends_declared_sign", 1) if divs
                else (val(lines, "dividends_paid", fy) or 0))
        expected = re_open + ni - paid
        roll = compare("retained earnings: opening + net income - dividends = closing",
                       expected, re_close, tol)
        roll["dividends_tag"] = divs["tag"] if divs else "dividends_paid (cash flow)"
        if not roll["ok"]:
            notes.append("retained-earnings roll-forward differs by %s: other items "
                         "(buybacks charged to retained earnings, accounting changes) are "
                         "not in the data; reported, not failed" % fmt(roll["diff"]))
    else:
        notes.append("retained-earnings roll-forward not checked (opening or closing missing)")
    bad = [t for t in tests if not t["ok"]]
    return {"status": "flag" if bad else "pass", "tests": tests, "retained_earnings": roll,
            "reason": "; ".join("%s off by %s" % (t["test"], fmt(t["diff"])) for t in bad) or None,
            "note": "; ".join(notes) or None}


def check_footings(lines, fy, cmap, tol):
    """P&L arithmetic (sector maps). A `fail: true` footing that misses, or cannot
    be checked, flags the year; the others are reported in the note."""
    tests, reasons, notes = [], [], []
    for ft in cmap.get("footings", []):
        terms = [(1, k) for k in ft.get("plus", [])] + [(-1, k) for k in ft.get("minus", [])]
        opt = set(ft.get("optional", []))
        # `name_extra`: text added to the name in a year the optional line has a value.
        name = ft["name"] + "".join(t for k, t in (ft.get("name_extra") or {}).items()
                                    if val(lines, k, fy) is not None)
        missing = [k for _, k in terms if k not in opt and val(lines, k, fy) is None]
        # `result` may list fall-backs: the first line with a value is compared.
        results = ft["result"] if isinstance(ft["result"], list) else [ft["result"]]
        result = next((k for k in results if val(lines, k, fy) is not None), None)
        if result != results[0] and any(val(lines, k, fy) is not None
                                        for k in ft.get("fallback_unless", [])):
            result = None
        if result is None:
            missing.insert(0, " / ".join(results))
        if missing:
            msg = "%s not checked: %s not in the data" % (name, ", ".join(missing))
            (reasons if ft.get("fail") else notes).append(msg)
            continue
        left = sum(s * (val(lines, k, fy) or 0) for s, k in terms)
        t = compare(name, left, val(lines, result, fy), tol)
        # `inside_if_ties`: a term the filing may already count inside another one
        # (Citi's gain on disposal, inside its net revenue). It is left out only
        # when the footing misses with it and ties without it; the name says so.
        inside = [(s, k) for s, k in terms if k in (ft.get("inside_if_ties") or {})
                  and val(lines, k, fy)]
        if not t["ok"] and inside:
            out = {k for _, k in inside}
            name2 = ft["name"] + "".join(t_ for k, t_ in (ft.get("name_extra") or {}).items()
                                         if k not in out and val(lines, k, fy) is not None)
            name2 += "".join(" (left out: %s, %s)" % (ft["inside_if_ties"][k], fmt(val(lines, k, fy)))
                             for _, k in inside)
            t2 = compare(name2, left - sum(s * val(lines, k, fy) for s, k in inside),
                         val(lines, result, fy), tol)
            if t2["ok"]:
                t, t2["left_out"] = t2, sorted(out)
            else:
                t["diff_left_out"] = t2["diff"]
        t["result_line"] = result
        t["reported_only"] = not ft.get("fail")
        tests.append(t)
        if not t["ok"]:
            msg = "%s off by %s" % (name, fmt(t["diff"]))
            if "diff_left_out" in t:
                msg += " (%s with %s left out)" % (fmt(t["diff_left_out"]), ", ".join(
                    k for k in ft["inside_if_ties"] if val(lines, k, fy)))
            n_terms = len(terms) + 1
            if cmap["meta"].get("footing_rounding_note") and abs(t["diff"]) <= n_terms * tol:
                msg += (" (within the rounding of the %d filed figures, each shown to %s; the "
                        "filed figures themselves do not add)" % (n_terms, fmt(tol)))
            if ft.get("fail"):
                reasons.append(msg)
            else:
                notes.append(msg + "; reported, not failed")
    return {"status": "flag" if reasons else "pass", "tests": tests,
            "reason": "; ".join(reasons) or None, "note": "; ".join(notes) or None}


def fmt(n):
    return "{:,.0f}".format(n)


def shares_outstanding(data, years, cmap):
    """Cover-page shares outstanding (dei:EntityCommonStockSharesOutstanding).

    The count on the latest year's own 10-K wins, else the latest 10-K count.
    None if the file does not carry it.
    """
    facts = (data["facts"].get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
             .get("units", {}).get("shares", []))
    facts = [f for f in facts if f.get("form") in set(cmap["meta"]["forms"])]
    if not facts or not years:
        return None
    own = [f for f in facts if f["accn"] == years[-1]["accn"]]
    pool = own or facts
    best = max(pool, key=lambda f: (f["filed"], f["end"]))
    same = {f["val"] for f in pool if f["accn"] == best["accn"] and f["end"] == best["end"]}
    note = None
    if len(same) > 1:
        note = ("the filing carries %d different counts for this date (share classes?); "
                "the largest is shown, check the cover page" % len(same))
    return {"val": max(same), "end": best["end"], "accn": best["accn"], "form": best["form"],
            "filed": best["filed"], "tag": "dei:EntityCommonStockSharesOutstanding",
            "note": note}


# build ------------------------------------------------------------------------
def build_history(data, ticker, cmap):
    gaap = data["facts"][cmap["meta"].get("facts_key", "us-gaap")]
    years = data.get("years") or fiscal_years(gaap, cmap)
    years = [dict(y) for y in years]
    reason = data.get("unsupported")
    if reason:
        raise Unsupported(reason)
    reason = unsupported_reason(gaap, years, cmap)
    if reason:
        if not (cmap.get("unsupported") or {}).get("route"):
            raise Unsupported(reason)
        sector, smap, why = route_sector(gaap, years, cmap, reason, data.get("sic"))
        h = build_history(data, ticker, smap)
        if sector == "bank":
            refusal = broker_dealer_reason(data.get("sic"), h, cmap)
            if refusal:
                raise Unsupported(refusal)
        h["routing"] = why
        return h
    lines = {key: {} for key in cmap["lines"]}
    for y in years:
        if cmap["meta"].get("per_year_standard"):
            y["standard"] = cmap["meta"]["per_year_standard"]
        y["source_filings"] = {}
        for stmt, key in cmap["anchors"].items():
            a = pick_line(gaap, cmap["lines"][key], y["end"], cmap)
            y["source_filings"][stmt] = a and a["accn"]
        for key, spec in cmap["lines"].items():
            if "derive" in spec and not spec.get("tags"):
                continue
            prefer = y["source_filings"].get(spec["statement"])
            v = pick_line(gaap, spec, y["end"], cmap, prefer)
            if v is None and spec.get("extension"):
                v = statement_line(data.get("statements"), spec, y)
            if v is not None:
                lines[key][y["fy"]] = v
        for key, spec in cmap["lines"].items():
            if "derive" in spec and y["fy"] not in lines[key]:
                v = derive_line(lines, spec, y["fy"])
                if v is not None:
                    if spec.get("tags"):  # a fallback: no tag had a value this year
                        v["label"] = spec["derive"].get("label", spec["label"])
                    lines[key][y["fy"]] = v
    drop_unused(lines, cmap)
    checks, flags = {}, []
    for y in years:
        fy = y["fy"]
        unit = reporting_unit([v[fy]["val"] for v in lines.values() if fy in v])
        y["unit"] = unit
        prev = prior_end(gaap, y, cmap)
        close = lines["end_cash_cf"].get(fy)
        cf_src, bs_src = y["source_filings"]["cf"], y["source_filings"]["bs"]
        opening = pick(gaap, close["tag"], "stock", prev, cmap, cf_src) if close and prev else None
        restricted = (pick_restricted(gaap, y["end"], cmap, cf_src)
                      or (data.get("cash_bridges") or {}).get(y["end"]))
        re_open = (pick_any(gaap, cmap["lines"]["retained_earnings"]["tags"], "stock", prev,
                            cmap, bs_src) if prev else None)
        res = {"balance": check_balance(lines, fy, unit),
               "cash": check_cash(gaap, lines, y, opening and opening["val"], restricted, cmap, unit),
               "net_income": check_net_income(gaap, lines, y, re_open and re_open["val"], cmap, unit)}
        res["cash"]["cash_flow_closing_tag"] = close["tag"] if close else None
        if cmap.get("footings"):
            dropped = drop_unfooted_parts(lines, fy, cmap, unit)
            res["pl"] = check_footings(lines, fy, cmap, unit)
            if dropped:
                res["pl"]["reason"] = "; ".join([r for r in [res["pl"]["reason"]] if r] + dropped)
        y["status"] = "tied" if all(r["status"] == "pass" for r in res.values()) else "flagged"
        for name, r in res.items():
            if r["status"] != "pass":
                flags.append({"fy": fy, "check": name, "reason": r["reason"]})
        checks[fy] = res
    opening = opening_balance(gaap, years, cmap) if cmap["meta"].get("opening_balance") else None
    h = {"ticker": ticker.upper(), "sector": cmap["meta"].get("sector", "corporate"),
         "cik": data.get("cik"), "entity": data.get("entityName"),
         "source": data.get("source", "SEC EDGAR XBRL data (companyfacts)"),
         "standard": cmap["meta"]["standard"],
         "currency": cmap["meta"]["unit"], "years": years,
         "lines": {k: {str(fy): v for fy, v in by.items()} for k, by in lines.items()},
         "checks": {str(fy): c for fy, c in checks.items()},
         "shares_outstanding": (data["shares_outstanding"] if "shares_outstanding" in data
                                else shares_outstanding(data, years, cmap)),
         "flags": flags, "warnings": warnings(data, years, lines, cmap)}
    for key in ("credit", "filing_id_label", "statements_basis", "edinet_code", "routing",
                "element_prefixes"):
        if key in data:
            h[key] = data[key]
    if cmap["meta"].get("opening_balance"):
        if opening:
            h["opening_balance"] = opening
        else:
            h["warnings"].append("no balance sheet is filed for the year end before FY%s, so "
                                 "the ratios on opening balances use FY%s onwards only"
                                 % (years[0]["fy"], years[0]["fy"]))
    # Sector maps only: what kind of financial company, and how the chart's lines
    # differ from their US meaning (the templates label the lines from these).
    for key in ("insurer_type", "statement_format", "basis", "chart_notes"):
        if cmap["meta"].get(key):
            h[key] = cmap["meta"][key]
    return h


def opening_balance(gaap, years, cmap):
    """The balance sheet at the year end before the first year (an IFRS report's
    Prior2YearInstant, its IFRS opening balance sheet), with its own balance check.
    None when the filing has no total assets for that date."""
    if not years:
        return None
    prev = prior_end(gaap, years[0], cmap)
    if prev is None:
        return None
    first = years[0]
    got = {}
    for key, spec in cmap["lines"].items():
        if spec["kind"] != "stock" or "derive" in spec or spec["statement"] != "bs":
            continue
        v = pick_line(gaap, spec, prev, cmap, first["accn"])
        if v is not None:
            got[key] = v
    if "total_assets" not in got:
        return None
    tmp = {k: {"open": v} for k, v in got.items()}
    unit = reporting_unit([v["val"] for v in got.values()])
    return {"fy": d(prev).year, "end": prev, "accn": got["total_assets"]["accn"],
            "basis": "the opening balance sheet in the report for the year ended %s; balance "
                     "sheet only (no P&L or cash flow for that year)" % first["end"],
            "unit": unit, "lines": got, "balance": check_balance(tmp, "open", unit)}


def warnings(data, years, lines, cmap):
    out = []
    n = len(years)
    if n < cmap["meta"]["min_years"] and n == 2 and cmap["meta"].get("two_year_warning"):
        out.append(cmap["meta"]["two_year_warning"].strip())
    elif n < cmap["meta"]["min_years"]:
        out.append("only %d fiscal year(s) of 10-K data found; Tie-Out wants at least %d"
                   % (n, cmap["meta"]["min_years"]))
    fys = [y["fy"] for y in years]
    for key, by in lines.items():
        spec = cmap["lines"][key]
        if not by and "derive" in spec and spec.get("tags"):
            out.append("line %s: no mapped value and not derived in any year (tags tried: %s; "
                       "the derivation needs %s)" % (key, ", ".join(spec["tags"]), ", ".join(
                           spec["derive"].get("require") or spec["derive"].get("plus", []))))
            continue
        if not by and "derive" in spec:
            out.append("line %s: not derived in any year (it needs %s)"
                       % (key, ", ".join(spec["derive"].get("require", spec["derive"].get("plus", [])
                                                         + spec["derive"].get("minus", []))) or
                          "at least one of " + ", ".join(spec["derive"].get("plus", []))))
        elif not by:
            out.append("line %s: no mapped value in any year (tags tried: %s)"
                       % (key, ", ".join(spec["tags"] or part_tags(spec))))
        elif len(by) < n:
            gaps = [str(fy) for fy in fys if fy not in by]
            out.append("line %s: no mapped value in %s" % (key, ", ".join(gaps)))
        derived = [str(fy) for fy in fys if fy in by and "derived_from" in by[fy]]
        if derived and spec.get("tags"):
            note = (spec["derive"].get("notes_by_cik") or {}).get(str(data.get("cik")))
            out.append("line %s: no tag has a value (tags tried: %s) in %s, so it is %s there%s"
                       % (key, ", ".join(spec["tags"]), ", ".join("FY" + f for f in derived),
                          spec["derive"].get("label", "derived"), "; " + note if note else ""))
    if "extra_warnings" in data:
        return out + list(data["extra_warnings"])
    out.extend(statement_warnings(data.get("statements"), lines, cmap))
    other = sorted(set(data["facts"]) - {"us-gaap", "dei"})
    out.append("company-extension tags are not in companyfacts (the SEC file holds "
               "standard taxonomies only), so a line a company reports only under its "
               "own tag shows as missing above" + (
                   "; other taxonomies present and not mapped: %s" % ", ".join(other)
                   if other else ""))
    return out


def statement_warnings(st, lines, cmap):
    """What the 10-K statement pages added: lines read from the company's own row,
    rows that fit a line but were not used, and every company row (its own tag) on
    each primary statement."""
    if not st:
        return []
    out = []
    for key, spec in cmap["lines"].items():
        if not spec.get("extension"):
            continue
        used = sorted(fy for fy, v in lines.get(key, {}).items() if "printed_label" in v)
        rows, page = extension_rows(st, spec)
        if used:
            v = lines[key][used[-1]]
            out.append("line %s: FY%s taken from the company's own line \"%s\" (%s) on the "
                       "cash flow statement of its 10-K %s (%s); companyfacts holds standard "
                       "tags only. The printed line may include items other than %s"
                       % (key, ", FY".join(str(f) for f in used), v["printed_label"], v["tag"],
                          st["accn"], st["source"] + v["page"], spec["label"].lower()))
        elif len(rows) > 1:
            out.append("line %s: not read from the 10-K: %d of the company's own rows fit (%s)"
                       % (key, len(rows), "; ".join(r["element"] for r in rows)))
        elif page and not rows:
            out.append("line %s: no row of the company's own fits on its 10-K %s %s"
                       % (key, st["accn"], page["title"]))
    names = {"pl": "income statement", "bs": "balance sheet", "cf": "cash flow statement",
             "equity": "equity statement", "oci": "comprehensive income statement"}
    own = ["%s: %s" % (names.get(k, k), "; ".join("%s (%s)" % (r["label"], r["element"])
                                                   for r in p["rows"] if r["custom"]))
           for k, p in sorted(st["pages"].items()) if any(r["custom"] for r in p["rows"])]
    if own:
        out.append("company's own tags on the statements of its 10-K %s: %s"
                   % (st["accn"], " | ".join(own)))
    return out


# Japan: EDINET type-5 CSV -----------------------------------------------------
JP_FORM = "有価証券報告書"
JP_MAPS = {"Japan GAAP": os.path.join(HERE, "maps", "jgaap.yaml"),
           "IFRS": os.path.join(HERE, "maps", "ifrs.yaml")}
JP_PREFIX = {"Japan GAAP": "jppfs_cor:", "IFRS": "jpigp_cor:"}
IFRS_INSURER_MAP = os.path.join(HERE, "maps", "ifrs_insurer.yaml")
# An IFRS 17 insurer files these (under its own prefix; EDINET's IFRS taxonomy has
# no insurer elements). Non-life: its summary of business results shows 正味収入保険料.
IFRS17_LINES = ("InsuranceRevenueIFRS", "InsuranceContractLiabilitiesLiabilitiesIFRS")
NONLIFE_MARK = "jpcrp_cor:NetPremiumsWrittenSummaryOfBusinessResultsINS"
INSURER_CODES = ("ins", "in1", "in2")
# The plain contexts of the two years a report holds; anything with an extra
# member (segments, equity components ...) is not a statement total.
JP_CONTEXTS = ("CurrentYearDuration", "Prior1YearDuration", "CurrentYearInstant",
               "Prior1YearInstant", "Prior2YearInstant")
NONCON = "_NonConsolidatedMember"
PDL_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
EDINET_VIEW_URL = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc_id}"
CASH_NOTE = "ReconciliationOfEndingBalanceOfCashAndCashEquivalents"
NOTE_UNITS = {"百万円": 1000000, "千円": 1000, "円": 1}
# An amount in the cash note: with its unit (1,586,275百万円), or, once a unit has
# been seen (on an amount or in a "（単位：百万円）" header), bare and followed by
# a space (103,240 7,035) as some notes print the later rows. A bare amount with
# thousands separators may also run straight into the next one, as Tokio
# Marine's note prints its two year columns (871,993896,898 = 871,993 and
# 896,898). ASCII digits only, so dates (2025年３月31日) never match.
AMOUNT = re.compile(r"(?P<neg>[△▲]|-)?\s*(?P<num>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s*"
                    r"(?P<unit>百万円|千円|円)"
                    r"|(?P<dash>[－―])\s*(?P<dunit>百万円|千円|円)"
                    r"|単位[：:]\s*(?P<hunit>百万円|千円|円)"
                    r"|(?P<bneg>[△▲])?(?P<bnum>[0-9]{1,3}(?:,[0-9]{3})+(?=[^,]|$)|[0-9]+(?=\s|$))"
                    r"|(?P<bdash>[－―])(?=\s|$)")


def read_edinet_csv(path):
    """Rows of the annual report's main CSV inside an EDINET type-5 zip.

    Each row: {element, name, context, scope (連結/個別/その他), value}."""
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if n.endswith(".csv")
                       and os.path.basename(n).startswith("jpcrp") and "-asr-" in n)
        if not names:
            names = sorted(n for n in z.namelist() if n.endswith(".csv")
                           and os.path.basename(n).startswith("jpcrp"))
        if not names:
            raise ValueError("%s holds no jpcrp*.csv" % path)
        raw = z.read(names[0])
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    header = next(reader)
    if header[:3] != ["要素ID", "項目名", "コンテキストID"]:
        raise ValueError("%s: not an EDINET CSV (header %s)" % (path, header[:3]))
    return [{"element": r[0], "name": r[1], "context": r[2], "scope": r[4], "value": r[8]}
            for r in reader if len(r) >= 9]


def number(text):
    t = text.strip()
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return None


def parse_cash_note(text):
    """The cash note as columns of (label, value): one column per year shown.

    Returns [] when the text has no recognisable two-column table."""
    rows, last_end, unit = [], 0, None
    for m in AMOUNT.finditer(text):
        if m.group("hunit"):
            unit = NOTE_UNITS[m.group("hunit")]
            last_end = m.end()
            continue
        if m.group("unit") or m.group("dunit"):
            unit = NOTE_UNITS[m.group("unit") or m.group("dunit")]
        elif unit is None:
            continue                      # a bare number before any unit: not an amount
        if m.group("dash") or m.group("bdash"):
            v = 0
        else:
            v = int((m.group("num") or m.group("bnum")).replace(",", "")) * unit
            if m.group("neg") or m.group("bneg"):
                v = -v
        # 〃 (ditto) repeats the unit of the row above ("566,500 〃"); it is not a label.
        gap = text[last_end:m.start()].replace("〃", "").strip()
        if gap or not rows:
            rows.append([gap, []])
        rows[-1][1].append(v)
        last_end = m.end()
    width = max((len(r[1]) for r in rows), default=0)
    rows = [r for r in rows if len(r[1]) == width]
    return [[(r[0], r[1][c]) for r in rows] for c in range(width)]


def cash_bridge(note, bs_cash, cf_close):
    """Use the filing's own reconciliation note only if it is complete and foots:
    its first line is the balance-sheet cash, its last line the cash-flow closing
    cash, and the lines between add up to the difference (within one unit of the
    note, for rounding). Else None."""
    for col in parse_cash_note(note or ""):
        if len(col) < 3:
            continue
        first, middle, last = col[0][1], col[1:-1], col[-1][1]
        tol = reporting_unit([v for _, v in col])     # rounding in the note's own unit
        if (first == bs_cash and last == cf_close
                and abs(first + sum(v for _, v in middle) - last) <= tol):
            return {"val": last - first, "label": "the filing's cash reconciliation note",
                    "tag": "現金及び現金同等物の期末残高と貸借対照表の関係 (text note, parsed "
                           "and footed: %s)" % "; ".join("%s %s" % (l[:30], fmt(v))
                                                        for l, v in middle)}
    return None


def jp_standard(rows, dei):
    """Accounting standard from the elements present, cross-checked with the DEI."""
    said = dei.get("AccountingStandardsDEI", "")
    # The 主要な経営指標 summary carries US-GAAP-named elements in IFRS filings too
    # (e.g. OperatingIncomeLossUSGAAPSummaryOfBusinessResults), so only statement
    # elements count as evidence of US GAAP.
    if said == "US GAAP" or any("USGAAP" in r["element"]
                                and "SummaryOfBusinessResults" not in r["element"]
                                for r in rows):
        return "US GAAP"
    statement = [r for r in rows if r["context"] in JP_CONTEXTS and r["scope"] != "個別"
                 and number(r["value"]) is not None]
    if not statement:
        statement = [r for r in rows if r["context"].replace(NONCON, "") in JP_CONTEXTS
                     and number(r["value"]) is not None]
    ifrs = sum(r["element"].startswith("jpigp_cor:") for r in statement)
    jg = sum(r["element"].startswith("jppfs_cor:") for r in statement)
    found = "IFRS" if ifrs > jg else ("Japan GAAP" if jg else None)
    if found and said and said != found:
        raise Unsupported("the filing says %s but its statements are tagged as %s; not "
                          "mapped rather than guessed" % (said, found))
    return found or said or None


def parse_filing(path, entry):
    """One 有価証券報告書: its dates, DEI, statement facts, cash note and share count."""
    rows = read_edinet_csv(path)
    dei = {r["element"].split(":", 1)[1]: r["value"] for r in rows
           if r["element"].startswith("jpdei_cor:")}
    cover = {r["element"]: r["value"] for r in rows if r["element"].startswith("jpcrp_cor:")
             and r["context"] == "FilingDateInstant"}
    need = ("CurrentFiscalYearStartDateDEI", "CurrentFiscalYearEndDateDEI",
            "PreviousFiscalYearStartDateDEI", "PreviousFiscalYearEndDateDEI")
    if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dei.get(k, "")) for k in need):
        raise ValueError("%s: the DEI does not give both fiscal years' dates" % path)
    cs, ce, ps, pe = (d(dei[k]) for k in need)
    filed = (entry.get("submitDateTime") or cover.get("jpcrp_cor:FilingDateCoverPage") or "")[:10]
    consolidated = dei.get("WhetherConsolidatedFinancialStatementsArePreparedDEI") == "true"
    periods = {"CurrentYearDuration": (cs, ce), "Prior1YearDuration": (ps, pe),
               "CurrentYearInstant": (None, ce), "Prior1YearInstant": (None, pe),
               "Prior2YearInstant": (None, ps - timedelta(days=1))}
    facts, clashes, ext = {}, [], set()
    note = None
    for r in rows:
        ctx = r["context"]
        # The scope column reads 連結 on J-GAAP rows and その他 on IFRS rows; the
        # context ID is what separates consolidated from non-consolidated.
        if consolidated:
            if r["scope"] == "個別" or ctx not in JP_CONTEXTS:
                continue
        else:
            if r["scope"] == "連結" or ctx.replace(NONCON, "") not in JP_CONTEXTS:
                continue
            ctx = ctx.replace(NONCON, "")
        if CASH_NOTE in r["element"] and r["element"].endswith("TextBlock"):
            note = r["value"]
            continue
        v = number(r["value"])
        if v is None:
            continue
        prefix = r["element"].split(":", 1)[0]
        if not prefix.endswith("_cor"):
            ext.add(r["element"])
        key = (r["element"], ctx)
        if key in facts:
            if facts[key]["val"] != v:
                clashes.append("%s %s: %s and %s" % (r["element"], ctx, facts[key]["val"], v))
            continue
        start, end = periods[ctx]
        f = {"end": end.isoformat(), "val": v, "accn": entry["docID"], "form": JP_FORM,
             "filed": filed, "fy": ce.year}
        if start:
            f["start"] = start.isoformat()
        facts[key] = f
    issued = [r for r in rows if r["element"] ==
              "jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults"
              and r["context"] == "CurrentYearInstant" + NONCON and number(r["value"])]
    issued_tag = "jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults"
    if not issued:
        issued_tag = "jpcrp_cor:NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc"
        issued = [r for r in rows if r["element"] == issued_tag
                  and r["context"] == "FilingDateInstant" and number(r["value"])]
    treasury = [r for r in rows if r["element"] == "jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc"
                and r["context"] == "CurrentYearInstant" and number(r["value"]) is not None]
    return {"docID": entry["docID"], "filed": filed, "dei": dei, "rows": rows,
            "consolidated": consolidated, "cur": (cs, ce), "prev": (ps, pe), "facts": facts,
            "clashes": clashes, "extensions": sorted(ext), "cash_note": note,
            "issued": (number(issued[0]["value"]), issued_tag) if issued else None,
            "treasury": number(treasury[0]["value"]) if treasury else None,
            "name_en": dei.get("FilerNameInEnglishDEI"), "name_ja": dei.get("FilerNameInJapaneseDEI"),
            "edinet_code": dei.get("EDINETCodeDEI")}


DEI_INDUSTRY = ("IndustryCodeWhenConsolidatedFinancialStatementsArePreparedInAccordance"
                "WithIndustrySpecificRegulationsDEI")
DEI_INDUSTRY_NONCON = ("IndustryCodeWhenFinancialStatementsArePreparedInAccordanceWith"
                       "IndustrySpecificRegulationsDEI")


def jp_industry_code(filing):
    """The DEI industry code of a report (EDINET writes it in either case)."""
    return filing["dei"].get(DEI_INDUSTRY if filing["consolidated"] else DEI_INDUSTRY_NONCON, "")


def in_codes(code, codes):
    return code.lower() in {c.lower() for c in codes}


def jp_unsupported(filings, cmap):
    """Banks, insurers and securities firms, detected as before: the DEI industry
    code (either case), or no current assets plus an element with a financial
    suffix. Used where the map has no `route` list (IFRS)."""
    rule = cmap.get("unsupported") or {}
    if not rule.get("enabled"):
        return None
    code = jp_industry_code(filings[-1])
    if code and in_codes(code, rule.get("industry_codes", [])):
        return "%s (DEI industry code %s)" % (rule["message"].strip(), code)
    prefix = cmap["meta"]["prefix"]
    names = {el for f in filings for (el, _) in f["facts"]}
    if any(prefix + t in names for t in rule.get("require_absent", [])):
        return None
    hits = sorted(el for el in names if any(el.endswith(sfx) for sfx in rule.get("any_suffix", []))
                  or el in {prefix + t for t in rule.get("any_present", [])})
    if not hits:
        return None
    return "%s (no %s; has %s)" % (rule["message"].strip(), ", ".join(rule["require_absent"]),
                                   ", ".join(hits[:4]) + (" ..." if len(hits) > 4 else ""))


def jp_route(filings, cmap):
    """(map path, why) for a bank, insurer or securities firm, from the map's
    `unsupported.route` list; None for a company on the standard chart.

    Each route has DEI industry codes (compared ignoring case) and an element
    suffix. The code decides; with no financial code, the suffix does, when at
    least `min_suffix_elements` of the company's statement elements carry it and
    the route's own safeguard holds (`require_absent`: no current assets, for
    banks and insurers; `any_present`: a P&L line of that format, for securities
    firms). A code and a suffix that point to different routes, or an insurer
    that files both or neither of the life and non-life premium lines, are
    refused rather than guessed."""
    rule = cmap.get("unsupported") or {}
    routes = rule.get("route") or []
    if not rule.get("enabled") or not routes:
        return None
    prefix = cmap["meta"]["prefix"]
    names = {el[len(prefix):] for f in filings for (el, _) in f["facts"] if el.startswith(prefix)}
    code = jp_industry_code(filings[-1])
    by_code = next((r for r in routes if code and in_codes(code, r["industry_codes"])), None)
    counts = [(len([n for n in names if n.endswith(r["suffix"])]), r) for r in routes]
    floor = rule.get("min_suffix_elements", 3)

    def safeguard(r):
        if any(t in names for t in r.get("require_absent", [])):
            return False
        return not r.get("any_present") or any(t in names for t in r["any_present"])
    eligible = sorted(((n, i, r) for i, (n, r) in enumerate(counts) if n >= floor and safeguard(r)),
                      key=lambda x: (-x[0], x[1]))
    by_suffix = eligible[0][2] if eligible else None
    n_suffix = dict((id(r), n) for n, r in counts)
    if by_code is None and by_suffix is None:
        return None
    if by_code is not None and by_suffix is not None and by_code is not by_suffix:
        raise Unsupported("%s Its DEI industry code %s points to the %s chart, but most of its "
                          "elements end in %s (%d of them), so it is not routed rather than "
                          "guessed" % (rule["message"].strip(), code, by_code["label"],
                                       by_suffix["suffix"], n_suffix[id(by_suffix)]))
    r = by_code or by_suffix
    n = n_suffix[id(r)]
    why = ("its DEI industry code is %s" % code if by_code is not None else
           "it has no financial DEI industry code (%s) but %d of its statement elements end in %s"
           % (code or "none", n, r["suffix"]))
    if by_code is not None and n:
        why += " and %d of its statement elements end in %s" % (n, r["suffix"])
    if r.get("refuse"):
        detail = ("DEI industry code %s" % code if by_code is not None else
                  "no financial DEI industry code (%s), but %d of its statement elements end in %s"
                  % (code or "none", n, r["suffix"]))
        raise Unsupported(r["refuse"].strip().format(detail=detail))
    target = r.get("map")
    if r.get("by_element"):
        hits = [b for b in r["by_element"] if any(t in names for t in b["any_present"])]
        if len(hits) != 1:
            raise Unsupported("%s It is an insurer (%s), but it files %s of the life premium "
                              "line (%s) and the non-life premium line (%s), so neither format "
                              "is picked rather than guessed" % (
                                  rule["message"].strip(), why, "both" if hits else "neither",
                                  ", ".join(r["by_element"][0]["any_present"]),
                                  ", ".join(r["by_element"][1]["any_present"])))
        target = hits[0]["map"]
        why += ", and it files %s (%s format)" % (
            ", ".join(t for t in hits[0]["any_present"] if t in names), hits[0]["format"])
    return (os.path.join(HERE, "maps", target),
            "routed to the %s chart (maps/%s)%s because %s" % (
                r["sector"], target, "" if r["label"] == r["sector"] else " as a " + r["label"], why))


def local_name(el):
    return el.split(":", 1)[1] if ":" in el else el


def map_tags(cmap):
    """Every element (local) name a map's lines name, in tags or parts."""
    out = set()
    for spec in cmap["lines"].values():
        out.update(spec.get("tags") or [])
        out.update(part_tags(spec))
    return out


def jp_ifrs_insurer(filings, reason):
    """(map path, why) for an IFRS non-life insurer, else None (then it is refused).

    EDINET's IFRS taxonomy has no insurer elements, so the test reads the latest
    report's own IFRS 17 lines, by local name under any prefix: insurance revenue
    and insurance contract liabilities. It must also be non-life: its summary of
    business results shows 正味収入保険料 (jpcrp_cor, a non-life line). A bank or
    securities-firm DEI code is never routed here."""
    last = filings[-1]
    code = jp_industry_code(last)
    if code and in_codes(code, ("bnk", "bk1", "bk2", "sec")):
        return None
    names = {local_name(el) for (el, _) in last["facts"]
             if el.startswith("jpigp_cor:") or el in last["extensions"]}
    if not all(t in names for t in IFRS17_LINES):
        return None
    if not any(el == NONLIFE_MARK for (el, _) in last["facts"]):
        return None
    return (IFRS_INSURER_MAP,
            "routed to the insurer chart (maps/ifrs_insurer.yaml) as an IFRS 17 non-life insurer "
            "because %s, its latest report (%s) files %s under its own elements, and its summary "
            "of business results shows 正味収入保険料 (%s)" % (
                "its DEI industry code is %s" % code if code and in_codes(code, INSURER_CODES)
                else "it was detected as a financial company %s" % reason[reason.rfind("("):],
                last["docID"], " and ".join(IFRS17_LINES), local_name(NONLIFE_MARK)))


def jp_ifrs_financial(filings, standards, reason):
    """The refusal for an IFRS bank, insurer or securities firm other than an IFRS 17
    non-life insurer: EDINET's IFRS taxonomy has no industry elements, so there is no
    IFRS map for it. Says how many IFRS years there are and which reports use
    another standard."""
    ifrs = [f for f, s in zip(filings, standards) if s == "IFRS"]
    flows = sorted({end for f in ifrs for end in (f["cur"][1], f["prev"][1])})
    sheets = sorted({fact["end"] for f in ifrs for (el, ctx), fact in f["facts"].items()
                     if el == "jpigp_cor:AssetsIFRS"})
    other = [(f["docID"], s) for f, s in zip(filings, standards) if s != "IFRS"]
    text = ("not supported yet: this is a bank, insurer or securities firm %s whose latest report "
            "(%s) is under IFRS. EDINET's IFRS taxonomy has no bank or insurer elements, so its "
            "industry lines (insurance revenue, insurance contract liabilities, loans, deposits) "
            "are the company's own elements, and Tie-Out's only IFRS map for financial companies "
            "is for non-life insurers that file IFRS 17 lines (maps/ifrs_insurer.yaml). "
            "Its IFRS reports give %d year(s) of P&L and cash flow (%s) and %d balance sheet(s); "
            "a model needs at least 3 years." % (
                reason[reason.rfind("("):] if "(" in reason else "", filings[-1]["docID"],
                len(flows), ", ".join("FY%d" % e.year for e in flows), len(sheets)))
    if other:
        text += (" Its earlier report(s) %s are under %s; the two standards are not mixed into "
                 "one history." % (", ".join(d for d, _ in other),
                                   " / ".join(sorted({s for _, s in other}))))
    return text


def load_edinet(manifest_path):
    """Read the reports fetch_filings.py saved; return (data, cmap) for build_history.

    `data` has the companyfacts shape the US route reads (facts keyed by element,
    each fact with end/start/val/accn/form/filed), plus the years, the share count,
    the cash notes and the EDINET credit."""
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    folder = os.path.dirname(os.path.abspath(manifest_path))
    filings = [parse_filing(os.path.join(folder, e["file"]), e) for e in manifest["filings"]]
    filings.sort(key=lambda x: (x["cur"][1], x["filed"]))
    if not filings:
        raise ValueError("%s lists no filings" % manifest_path)
    notes = []
    standards = [jp_standard(f["rows"], f["dei"]) for f in filings]
    std = standards[-1]
    if std == "US GAAP":
        raise Unsupported("not supported yet: this company files its consolidated statements "
                          "under US GAAP (jpdei_cor:AccountingStandardsDEI = %s). Tie-Out maps "
                          "J-GAAP and IFRS EDINET filings only; it does not guess a US-GAAP map "
                          "from the summary figures" % filings[-1]["dei"].get("AccountingStandardsDEI"))
    if std not in JP_MAPS:
        raise Unsupported("not supported yet: accounting standard %r is not mapped" % std)
    dropped = [f["docID"] for f, s in zip(filings, standards) if s != std]
    if dropped:
        notes.append("reports %s use another accounting standard than the latest (%s) and "
                     "are left out" % (", ".join(dropped), std))
    all_filings, all_standards = filings, standards
    filings = [f for f, s in zip(filings, standards) if s == std]
    cmap = load_map(JP_MAPS[std])
    reason, routing = None, None
    route = jp_route(filings, cmap)
    if route:
        cmap = load_map(route[0])
        routing = route[1]
    else:
        reason = jp_unsupported(filings, cmap)
        if reason and std == "IFRS":
            ins = jp_ifrs_insurer(filings, reason)
            if ins is None:
                raise Unsupported(jp_ifrs_financial(all_filings, all_standards, reason))
            cmap, routing, reason = load_map(ins[0]), ins[1], None
            if dropped:
                notes[-1] += ("; an IFRS 17 history is never mixed with Japan GAAP years, so the "
                              "model has only the IFRS report's years")
    prefix = cmap["meta"]["prefix"]
    unit = cmap["meta"]["unit"]
    local = cmap["meta"].get("local_names")
    gaap, by_local = {}, {}
    for f in filings:
        for (el, _), fact in f["facts"].items():
            if local and (el.startswith(prefix) or el in f["extensions"]):
                # The insurer lines are the group's own elements: read them by local name.
                by_local.setdefault(local_name(el), {}).setdefault(
                    el.split(":", 1)[0], []).append(fact)
            elif el.startswith(prefix):
                gaap.setdefault(el[len(prefix):], {"units": {unit: []}})["units"][unit].append(fact)
            elif el in f["extensions"] and el.endswith("CCE"):
                # A company's own element in the cash reconciliation (EDINET names
                # these "...CCE"). check_cash uses it only if it makes the year tie.
                gaap.setdefault(EXT_CCE + el.split(":", 1)[1],
                                {"units": {unit: []}})["units"][unit].append(fact)
    element_prefixes = {}
    for name, pres in sorted(by_local.items()):
        # jpigp_cor wins when a local name is filed under two prefixes (not seen yet).
        if len(pres) > 1 and prefix[:-1] not in pres:
            raise Unsupported("not supported yet: %s is filed under two company prefixes (%s), so "
                              "the local-name map does not pick one rather than guess"
                              % (name, " and ".join(sorted(pres))))
        pre = prefix[:-1] if prefix[:-1] in pres else next(iter(pres))
        if len(pres) > 1:
            notes.append("%s is filed under %s; the %s value is used"
                         % (name, " and ".join(sorted(pres)), pre))
        gaap[name] = {"units": {unit: pres[pre]}}
        if pre + ":" != prefix:
            element_prefixes[name] = pre
    # one entry per year end: the newest report that shows that year
    ends = {}
    for f in filings:
        for (start, end), own in ((f["cur"], True), (f["prev"], False)):
            best = ends.get(end)
            if best is None or f["filed"] > best[0]["filed"]:
                ends[end] = (f, own)
    years = []
    for end in sorted(ends)[-cmap["meta"]["max_years"]:]:
        f, own = ends[end]
        years.append({"fy": end.year, "end": end.isoformat(), "accn": f["docID"], "form": JP_FORM,
                      "filed": f["filed"], "amendments": [],
                      "basis": ("its own report" if own else
                                "the prior-year figures in the report for the year ended %s"
                                % f["cur"][1].isoformat())})
    # the filing's own cash note, used only where it foots against the tagged figures
    bridges = {}
    cash_tags = cmap["lines"]["cash"]["tags"]
    close_tags = cmap["lines"]["end_cash_cf"]["tags"]
    for y in years:
        f = [x for x in filings if x["docID"] == y["accn"]][0]
        bs = next((f["facts"][(prefix + t, c)]["val"] for t in cash_tags
                   for c in ("CurrentYearInstant", "Prior1YearInstant")
                   if (prefix + t, c) in f["facts"] and f["facts"][(prefix + t, c)]["end"] == y["end"]),
                  None)
        cf = next((f["facts"][(prefix + t, c)]["val"] for t in close_tags
                   for c in ("CurrentYearInstant", "Prior1YearInstant")
                   if (prefix + t, c) in f["facts"] and f["facts"][(prefix + t, c)]["end"] == y["end"]),
                  None)
        if bs is not None and cf is not None and bs != cf:
            b = cash_bridge(f["cash_note"], bs, cf)
            if b:
                b.update(accn=f["docID"], form=JP_FORM, filed=f["filed"])
                bridges[y["end"]] = b
    last = filings[-1]
    shares = None
    if last["issued"]:
        n, tag = last["issued"]
        t = last["treasury"]
        shares = {"val": n - (t or 0), "end": last["cur"][1].isoformat(), "accn": last["docID"],
                  "form": JP_FORM, "filed": last["filed"],
                  "tag": tag + (" less jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc"
                                if t is not None else ""),
                  "basis": ("issued shares at the year end, %s, less treasury shares (自己株式等), "
                            "%s, from the 株式等の状況 section; basic, not diluted"
                            % ("{:,}".format(n), "{:,}".format(t)) if t is not None else
                            "issued shares at the year end, %s; treasury shares are not tagged, "
                            "so they are not deducted; basic, not diluted" % "{:,}".format(n)),
                  "note": None}
    for f in filings:
        notes += ["%s: %s gives two values for %s; the first is used" % (f["docID"], JP_FORM, c)
                  for c in f["clashes"]]
    if not last["consolidated"]:
        notes.append("the company prepares no consolidated statements "
                     "(jpdei_cor:WhetherConsolidatedFinancialStatementsArePreparedDEI = false), "
                     "so the non-consolidated (個別) statements are used")
    ext = sorted({e for f in filings for e in f["extensions"]})
    if local:
        used = map_tags(cmap)
        ext = [e for e in ext if local_name(e) not in used]
    notes.append("%d company-specific elements carry values in the statements and are not "
                 "mapped (a line the company tags only under its own element shows as missing "
                 "above)%s" % (len(ext), (": " + ", ".join(e.split(":", 1)[1] for e in ext)) if ext else ""))
    urls = ", ".join(EDINET_VIEW_URL.format(doc_id=f["docID"]) for f in reversed(filings))
    name = last["name_en"] or last["name_ja"]
    if last["name_en"] and last["name_ja"]:
        name = "%s (%s)" % (last["name_en"], last["name_ja"])
    data = {"cik": last["edinet_code"], "entityName": name, "facts": {"jp": gaap},
            "years": years, "unsupported": reason, "shares_outstanding": shares,
            "cash_bridges": bridges, "extra_warnings": notes, "filing_id_label": "docID",
            "edinet_code": last["edinet_code"],
            "source": "EDINET API v2 (Financial Services Agency), %s XBRL data in CSV form, %s "
                      "statements" % (JP_FORM, "consolidated (連結)" if last["consolidated"]
                                      else "non-consolidated (個別)"),
            "statements_basis": ("Years are labelled by the calendar year they end in (FY%d = "
                                 "the year ended %s). Each report holds two years; a year's "
                                 "figures come from the newest report that shows it."
                                 % (last["cur"][1].year, last["cur"][1].isoformat())),
            "credit": ["出典：EDINET閲覧（提出）サイト（%s）、PDL1.0（%s）" % (urls, PDL_URL),
                       "EDINET閲覧（提出）サイトのデータをもとにStargira Tie-Out（Stargira Advisors "
                       "Ltd）により加工。Processed with Stargira Tie-Out (Stargira Advisors Ltd): the "
                       "filed figures are re-arranged into Tie-Out's chart of accounts and shown in "
                       "millions of yen; this is not the government's original data."]}
    if routing:
        data["routing"] = routing
    if local:
        data["element_prefixes"] = element_prefixes
    return data, cmap


# output -----------------------------------------------------------------------
def summary_md(h):
    jp = h.get("filing_id_label") == "docID"
    rows = ["# %s (%s) history from %s" % (h["entity"], h["ticker"],
                                            "EDINET filings" if jp else "SEC filings"), "",
            "Source: %s, %s %s. Standard: %s. Values in %s." % (
                h["source"], "EDINET code" if jp else "CIK", h["cik"], h["standard"],
                h["currency"]), ""]
    rows += [str(c) for c in h.get("credit") or []]
    if h.get("statements_basis"):
        rows.append(h["statements_basis"])
    if h.get("sector", "corporate") != "corporate":
        rows.append("Sector: %s%s. %s." % (h["sector"], " (%s)" % h["insurer_type"].replace("_", "-")
                                          if h.get("insurer_type") else "",
                                          h.get("routing") or "sector map"))
    pl = any("pl" in c for c in h["checks"].values())
    rows += ["", "| FY | Year end | %s | Unit | Balance | Cash | Net income | %sStatus |"
             % ("有価証券報告書 docID" if jp else "10-K accn", "P&L | " if pl else ""),
             "|---|---|---|---|---|---|---|---|" + ("---|" if pl else "")]
    for y in h["years"]:
        c = h["checks"][str(y["fy"])]
        rows.append("| %s | %s | %s | %s | %s | %s | %s | %s%s |" % (
            y["fy"], y["end"], y["accn"], fmt(y["unit"]), c["balance"]["status"],
            c["cash"]["status"], c["net_income"]["status"],
            (c["pl"]["status"] + " | ") if pl else "", y["status"]))
    n = len(h["years"])
    tied = sum(1 for y in h["years"] if y["status"] == "tied")
    rows += ["", "%d fiscal years; %d tied out, %d flagged." % (n, tied, n - tied)]
    if n < 3 and h.get("basis") == "ifrs17":
        rows.append("**Only %d actual years (IFRS 17).** This IFRS non-life insurer builds with 2 "
                    "actual years; every default is a 1- or 2-year "
                    "figure. J-GAAP years are not mixed in." % n)
    elif n < 3:
        rows.append("**Fewer than 3 years of history: too short for a reliable model.**")
    op = h.get("opening_balance")
    if op:
        rows.append("Opening balance sheet (FY%s, %s, balance sheet only, from %s): balance %s."
                    % (op["fy"], op["end"], op["accn"], op["balance"]["status"]))
    if h["flags"]:
        rows += ["", "## Flags (never plugged)"]
        rows += ["- FY%s %s: %s" % (f["fy"], f["check"], f["reason"]) for f in h["flags"]]
    if h.get("chart_notes"):
        rows += ["", "## How the chart reads this filing"]
        rows += ["- " + n for n in h["chart_notes"]]
    rows += ["", "## Warnings (%d)" % len(h["warnings"])]
    rows += ["- " + w for w in h["warnings"]]
    return "\n".join(rows) + "\n"


def write_outputs(h, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "%s_history" % h["ticker"])
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(h, f, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(summary_md(h))
    return base + ".json", base + ".md"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("market", choices=["us", "jp"])
    ap.add_argument("facts", help="us: companyfacts JSON; jp: the <CODE>_edinet.json list, "
                    "both saved by fetch_filings.py")
    ap.add_argument("--ticker", required=True, help="ticker, or the securities code for jp")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", default=None, help="us only (jp picks the J-GAAP or IFRS map)")
    args = ap.parse_args(argv)
    try:
        if args.market == "jp":
            data, cmap = load_edinet(args.facts)
        else:
            data, cmap = load_facts(args.facts), load_map(args.map or DEFAULT_MAP)
            data["sic"] = load_sic(args.facts, data)
            data["statements"] = load_statements(args.facts, data)
        h = build_history(data, args.ticker, cmap)
    except Unsupported as e:
        print("%s: %s" % (args.ticker.upper(), e))
        return 2
    paths = write_outputs(h, args.out)
    n = len(h["years"])
    tied = sum(1 for y in h["years"] if y["status"] == "tied")
    span = "FY%s-FY%s" % (h["years"][0]["fy"], h["years"][-1]["fy"]) if n else "none"
    print("%s: %s, %d years (%s), %d tied out, %d flagged, %d warnings"
          % (h["ticker"], h["standard"], n, span, tied, n - tied, len(h["warnings"])))
    if n < 3 and h.get("basis") == "ifrs17":
        print("WARNING: only %d actual years (IFRS 17): this IFRS non-life insurer builds with 2 "
              "actual years; defaults are 1- or 2-year figures" % n)
    elif n < 3:
        print("only %d year(s) of history: fewer than 3, too short for a reliable model" % n)
    print("wrote %s, %s" % paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())

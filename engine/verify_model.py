#!/usr/bin/env python3
"""
Automated checks on a workbook written by build_model.py.

Usage:  verify_model.py <schema.yaml> <workbook.xlsx> [--evaluator auto|formulas|builtin]

Four checks; exit code 0 only if all pass:

  1. No-hardcode lint. Every calculation sheet (all sheets except Assumptions and
     _Manifest) may contain only formulas. Numeric literals inside formulas must be
     unit conversions from STRUCTURAL_CONSTANTS. Exemption: on the Timeline sheet,
     integer literals are period indices and month counts (the structure of the
     timeline, not business inputs); they are counted and reported, not failed.
  2. Named-range audit. Every schema input has exactly one named range pointing at
     one cell on Assumptions; no name exists that is not a schema input; no #REF!.
     Inputs that no formula uses are listed as warnings.
  3. No circular references, judged from the formula text the way Excel judges
     it (a reference inside an IF branch that is never taken still counts).
  4. Reconciliation. Every check line (a column headed "BS Check" / "CF Check", or a
     row labelled "CHECK:" for waterfall layouts) is evaluated from the workbook's
     own formulas and must be zero in every period.

Evaluation uses the `formulas` package if installed, else the built-in evaluator in
xl_eval.py. The report states which one ran.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml
from openpyxl import load_workbook
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils import column_index_from_string, get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_model import schema_assumptions  # noqa: E402

INPUT_SHEETS = {"Assumptions", "_Manifest", "READ_ME"}
STRUCTURAL_SHEETS = {"Timeline"}
# Unit conversions and arithmetic identities. Anything else in a calc formula fails.
STRUCTURAL_CONSTANTS = {
    0, 1,      # zero guards, identities, off-by-one
    2,         # average of opening and closing
    3,         # months per quarter
    4,         # quarters per year (fourth quarter test)
    12,        # months per year
    30,        # days per month (AR/AP day-count convention)
    100,       # percentage points per unit
    1000,      # $k per $M
    8760,      # hours per year
    10000,     # basis points per unit
    1000000,   # $ per $M
}
CHECK_HEADER = re.compile(r"^\s*(BS Check|CF Check)", re.I)
CHECK_ROW_LABEL = re.compile(r"^\s*CHECK:", re.I)
TOL = 1e-9


# ---------------------------------------------------------------------------
# 1. No-hardcode lint
# ---------------------------------------------------------------------------
def lint(wb):
    bad, errors, structural = [], [], 0
    for ws in wb.worksheets:
        if ws.title in INPUT_SHEETS:
            continue
        timeline = ws.title in STRUCTURAL_SHEETS
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                where = f"{ws.title}!{c.coordinate}"
                if isinstance(v, str) and v.startswith("="):
                    if "#REF!" in v or "#NAME?" in v:
                        errors.append(f"{where}: {v[:70]}")
                    for t in Tokenizer(v).items:
                        if t.type == Token.OPERAND and t.subtype == Token.NUMBER:
                            x = float(t.value)
                            if timeline and x.is_integer():
                                structural += 1
                            elif x not in STRUCTURAL_CONSTANTS:
                                bad.append(f"{where}: literal {t.value} in {v[:70]}")
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    if timeline and float(v).is_integer():
                        structural += 1
                    elif v != 0:
                        bad.append(f"{where}: constant {v}")
    return bad, errors, structural


# ---------------------------------------------------------------------------
# 2. Named-range audit
# ---------------------------------------------------------------------------
def audit_names(wb, schema):
    ids = [a["id"] for a in schema_assumptions(schema)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    names = dict(wb.defined_names.items())
    missing = sorted(set(ids) - set(names))
    orphans = sorted(set(names) - set(ids))
    bad_target = []
    for n, dn in names.items():
        t = dn.attr_text
        if "#REF!" in t or not re.fullmatch(r"'?Assumptions'?!\$?[A-Z]+\$?\d+", t):
            bad_target.append(f"{n} -> {t}")

    used = set()
    for ws in wb.worksheets:
        if ws.title in ("Assumptions", "_Manifest"):
            continue
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    for t in Tokenizer(c.value).items:
                        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
                            used.add(t.value.split("!")[-1])
    unused = sorted(set(ids) - used)
    return dupes, missing, orphans, bad_target, unused


# ---------------------------------------------------------------------------
# 3. Circular references (static: every reference counts, as in Excel, even
#    one inside an IF branch that is never taken)
# ---------------------------------------------------------------------------
CELL = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")


def _refs(text, sheet, names):
    if "!" in text:
        sheet, text = text.rsplit("!", 1)
        sheet = sheet.strip("'")
    text = text.replace("$", "")
    if text in names:
        return [names[text]]
    if ":" in text:
        a, b = (CELL.match(p) for p in text.split(":"))
        if not (a and b):
            return []
        c0, c1 = column_index_from_string(a[1]), column_index_from_string(b[1])
        return [(sheet, f"{get_column_letter(c)}{r}")
                for r in range(int(a[2]), int(b[2]) + 1) for c in range(c0, c1 + 1)]
    return [(sheet, text)] if CELL.match(text) else []


def find_cycle(wb):
    names = {n: (dn.attr_text.split("!")[0].strip("'"), dn.attr_text.split("!")[1].replace("$", ""))
             for n, dn in wb.defined_names.items()}
    graph = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    deps = []
                    for t in Tokenizer(c.value).items:
                        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
                            deps += _refs(t.value, ws.title, names)
                    graph[(ws.title, c.coordinate)] = deps
    state = {}                       # 1 = on the current path, 2 = done
    for start in graph:
        if start in state:
            continue
        path, stack = [start], [iter(graph[start])]
        state[start] = 1
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            elif nxt not in state and nxt in graph:
                state[nxt] = 1
                path.append(nxt)
                stack.append(iter(graph[nxt]))
    return None


# ---------------------------------------------------------------------------
# 4. Reconciliation
# ---------------------------------------------------------------------------
def find_check_lines(wb):
    """Return {label: [(sheet, coord), ...]} for every check line."""
    lines = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if not isinstance(c.value, str):
                    continue
                cells = []
                if c.row == 1 and CHECK_HEADER.match(c.value):
                    for r in range(2, ws.max_row + 1):
                        x = ws.cell(row=r, column=c.column)
                        if x.value is not None:
                            cells.append(x.coordinate)
                elif CHECK_ROW_LABEL.match(c.value):
                    for col in range(c.column + 1, ws.max_column + 1):
                        x = ws.cell(row=c.row, column=col)
                        if isinstance(x.value, str) and x.value.startswith("="):
                            cells.append(x.coordinate)
                if cells:
                    label = f"{ws.title}: {c.value.strip()[:40]}"
                    lines[label] = [(ws.title, coord) for coord in cells]
    return lines


def eval_builtin(path, cells):
    from xl_eval import Evaluator
    return Evaluator(path).values(cells)


def eval_formulas(path, cells):
    import formulas
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
    found = {}
    for k, v in sol.items():
        m = re.match(r"^'\[[^\]]+\](.+)'!([A-Z]+\d+)$", str(k))
        if m:
            found[(m.group(1).upper(), m.group(2))] = v
    out = {}
    for sheet, coord in cells:
        v = found.get((sheet.upper(), coord))
        try:
            out[(sheet, coord)] = float(v.value.ravel()[0])
        except Exception:
            out[(sheet, coord)] = v
    return out


def evaluate(path, cells, choice):
    if choice in ("auto", "formulas"):
        try:
            import formulas  # noqa: F401
            return "formulas", eval_formulas(path, cells)
        except ImportError:
            if choice == "formulas":
                raise
    return "builtin (xl_eval.py)", eval_builtin(path, cells)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("schema")
    ap.add_argument("workbook")
    ap.add_argument("--evaluator", default="auto",
                    choices=["auto", "formulas", "builtin"])
    args = ap.parse_args()

    schema = yaml.safe_load(Path(args.schema).read_text())
    wb = load_workbook(args.workbook)
    ok = True

    print(f"Workbook: {args.workbook}")
    print("\n1. No-hardcode lint")
    bad, errors, structural = lint(wb)
    print(f"   Timeline period indices exempted: {structural}")
    print(f"   Hardcoded numbers in calc sheets: {len(bad)}")
    for b in bad[:20]:
        print(f"     {b}")
    print(f"   Formula errors (#REF!/#NAME?):   {len(errors)}")
    for e in errors[:20]:
        print(f"     {e}")
    ok &= not bad and not errors

    print("\n2. Named ranges")
    dupes, missing, orphans, bad_target, unused = audit_names(wb, schema)
    print(f"   Schema inputs:                  {len(schema_assumptions(schema))}")
    print(f"   Duplicate ids in schema:        {dupes or 'none'}")
    print(f"   Inputs with no named range:     {missing or 'none'}")
    print(f"   Names not in schema (orphans):  {orphans or 'none'}")
    print(f"   Names not on one Assumptions cell: {bad_target or 'none'}")
    print(f"   Warning, inputs no formula uses ({len(unused)}): {', '.join(unused) or 'none'}")
    ok &= not (dupes or missing or orphans or bad_target)

    print("\n3. Circular references")
    cycle = find_cycle(wb)
    if cycle:
        print("   FAIL  " + " -> ".join(f"{s}!{c}" for s, c in cycle[:12]))
        ok = False
    else:
        print("   none")

    print("\n4. Reconciliation")
    lines = find_check_lines(wb)
    if not lines:
        print("   No check lines found (expected 'BS Check'/'CF Check' or 'CHECK:').")
        ok = False
    else:
        cells = [c for v in lines.values() for c in v]
        engine, vals = evaluate(args.workbook, cells, args.evaluator)
        print(f"   Evaluator: {engine}")
        for label, cs in lines.items():
            nums = [vals[c] for c in cs]
            non_num = [f"{c[1]}={v}" for c, v in zip(cs, nums)
                       if not isinstance(v, (int, float))]
            worst = max((abs(v) for v in nums if isinstance(v, (int, float))), default=0.0)
            line_ok = not non_num and worst <= TOL
            ok &= line_ok
            print(f"   {'PASS' if line_ok else 'FAIL'}  {label:<44} periods={len(cs):>3}  "
                  f"max |value|={worst:.2e}" + (f"  errors: {non_num[:5]}" if non_num else ""))

    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

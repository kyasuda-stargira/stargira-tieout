"""
Shared building blocks for vertical plugins (vertical_*.py).

A plugin declares every period sheet's columns up front (so any sheet can refer
to any other, in any build order), then fills each sheet row by row with a
function that returns {column key: formula}. Periods run down the rows; row r is
the same period on every sheet.
"""

from __future__ import annotations

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from build_model import (BORDER, CHECK_GOOD, SECTION_FILL, USD3, _style_header,
                         scenario_axes, scenario_rows)

TIMELINE_COLS = {"pid": "A", "start": "B", "label": "C", "year": "D",
                 "type": "E", "months": "F", "m0": "G", "m1": "H"}


class Model:
    def __init__(self, wb, schema, values):
        self.wb, self.schema, self.v = wb, schema, values
        self.cols = {"Timeline": TIMELINE_COLS}
        self.specs = {}
        self.scn = {}
        self.first = self.last = None

    # references -------------------------------------------------------------
    def ref(self, sheet, key, row):
        return f"{sheet}!{self.cols[sheet][key]}{row}"

    def rng(self, sheet, key):
        c = self.cols[sheet][key]
        return f"{sheet}!${c}${self.first}:${c}${self.last}"

    def t(self, key, row):
        return self.ref("Timeline", key, row)

    # period sheets ----------------------------------------------------------
    def declare(self, sheet, cols):
        """cols: [(key, header, number_format)]. Columns A/B hold period id/label."""
        self.specs[sheet] = cols
        self.cols[sheet] = {k: get_column_letter(i + 3) for i, (k, _, _) in enumerate(cols)}

    def fill(self, sheet, rowfn):
        cols = self.specs[sheet]
        ws = self.wb.create_sheet(sheet)
        heads = [("Period", 8), ("Label", 14)] + [(h, max(12, len(h) + 2)) for _, h, _ in cols]
        for i, (h, w) in enumerate(heads, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
            _style_header(ws.cell(row=1, column=i, value=h))
        for r in range(self.first, self.last + 1):
            ws.cell(row=r, column=1, value=f"=Timeline!A{r}").border = BORDER
            ws.cell(row=r, column=2, value=f"=Timeline!C{r}").border = BORDER
            f = rowfn(r)
            missing = [k for k, _, _ in cols if k not in f]
            if missing:
                raise KeyError(f"{sheet} row {r}: no formula for {missing}")
            for i, (k, _, fmt) in enumerate(cols, 3):
                c = ws.cell(row=r, column=i, value="=" + f[k])
                c.number_format = fmt or USD3
                c.border = BORDER
                if k.endswith("_check"):
                    c.fill = CHECK_GOOD
        ws.freeze_panes = "C2"

    # timeline ---------------------------------------------------------------
    def build_timeline(self, anchor, segments):
        """segments: [(count, months_per_period, kind)], kind monthly|quarterly|annual."""
        ws = self.wb.create_sheet("Timeline")
        heads = ["Period ID", "Period Start", "Period Label", "Year", "Type",
                 "Months", "Months from Start", "Months at End"]
        for i, h in enumerate(heads, 1):
            ws.column_dimensions[get_column_letter(i)].width = 16
            _style_header(ws.cell(row=1, column=i, value=h))
        r = 2
        for count, months, kind in segments:
            for _ in range(count):
                ws.cell(row=r, column=1, value=r - 2)
                start = f"={anchor}" if r == 2 else f"=EDATE(B{r-1}, F{r-1})"
                ws.cell(row=r, column=2, value=start).number_format = "mmm yyyy"
                if kind == "monthly":
                    lbl = f'=TEXT(B{r}, "mmm yyyy")'
                elif kind == "quarterly":
                    # "Q4 2027" only when the period is a calendar quarter; otherwise
                    # name its months, e.g. "Nov 27–Jan 28".
                    lbl = (f'=IF(MONTH(B{r})=ROUNDUP(MONTH(B{r})/3, 0)*3-2, '
                           f'"Q" & ROUNDUP(MONTH(B{r})/3, 0) & " " & YEAR(B{r}), '
                           f'TEXT(B{r}, "mmm yy") & "–" & TEXT(EDATE(B{r}, F{r}-1), "mmm yy"))')
                else:
                    lbl = f'="Yr to " & TEXT(EDATE(B{r}, F{r}-1), "mmm yyyy")'
                ws.cell(row=r, column=3, value=lbl)
                ws.cell(row=r, column=4, value=f"=YEAR(B{r})")
                ws.cell(row=r, column=5, value=f'="{kind}"')
                ws.cell(row=r, column=6, value=months)
                ws.cell(row=r, column=7, value=f"=(YEAR(B{r})-YEAR({anchor}))*12"
                                               f"+MONTH(B{r})-MONTH({anchor})")
                ws.cell(row=r, column=8, value=f"=G{r}+F{r}")
                for col in range(1, 9):
                    ws.cell(row=r, column=col).border = BORDER
                r += 1
        ws.freeze_panes = "A2"
        self.first, self.last = 2, r - 1

    # scenario switch --------------------------------------------------------
    def build_scenario(self):
        """One live value per scenario axis, picked by g_scenario."""
        ws = self.wb.create_sheet("Scenario")
        rows = scenario_rows(self.schema)
        heads = ["Axis", "Live value"] + [s["id"] for s in rows]
        for i, h in enumerate(heads, 1):
            ws.column_dimensions[get_column_letter(i)].width = 18
            _style_header(ws.cell(row=1, column=i, value=h))
        for r, axis in enumerate(scenario_axes(self.schema), 2):
            ids = [f"scn_{axis}_{s['id'].lower()}" for s in rows]
            ws.cell(row=r, column=1, value=axis).border = BORDER
            live = ws.cell(row=r, column=2, value=f"=CHOOSE(g_scenario, {', '.join(ids)})")
            live.border, live.fill = BORDER, SECTION_FILL
            live.font = Font(bold=True)
            for i, n in enumerate(ids, 3):
                ws.cell(row=r, column=i, value=f"={n}").border = BORDER
            self.scn[axis] = f"Scenario!$B${r}"

    # curves -----------------------------------------------------------------
    def curve(self, name, field, x):
        pts = [(f"crv_{name}_{n}_year", f"crv_{name}_{n}_{field}")
               for n in range(1, len(self.schema["curves"][name]) + 1)]
        return interp(x, pts)

    # single-value sheets ----------------------------------------------------
    def static_sheet(self, name, rows):
        """rows: [(key, label, formula, number_format)] -> self.cols[name][key] = $B$r."""
        ws = self.wb.create_sheet(name)
        ws.column_dimensions["A"].width = 48
        ws.column_dimensions["B"].width = 18
        for i, h in enumerate(["Item", "Value"], 1):
            _style_header(ws.cell(row=1, column=i, value=h))
        refs = {}
        for r, (key, label, formula, fmt) in enumerate(rows, 2):
            ws.cell(row=r, column=1, value=label).border = BORDER
            c = ws.cell(row=r, column=2, value="=" + formula)
            c.number_format = fmt or USD3
            c.border = BORDER
            refs[key] = f"{name}!$B${r}"
        return refs


def interp(x, pts):
    """Piecewise-linear through [(x_expr, y_expr)], flat before the first point
    and after the last. Every breakpoint is an input, so no literals."""
    expr = pts[-1][1]
    for (x0, y0), (x1, y1) in reversed(list(zip(pts, pts[1:]))):
        expr = (f"IF({x}<={x1}, {y0}+({y1}-{y0})*(({x})-{x0})/({x1}-{x0}), {expr})")
    return f"IF({x}<={pts[0][0]}, {pts[0][1]}, {expr})"

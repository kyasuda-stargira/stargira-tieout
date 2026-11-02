"""Bank and insurer front door: Start_Here and Dashboard.

Offline only. Builds from the trimmed history fixtures used by test_bank.py and
test_insurer.py, then checks the workbook as a user opens it:
  - Start_Here is the first sheet and the active one;
  - every link on it points at a sheet and cell that exist (the share price
    link lands on val_share_price);
  - the results read "enter a price" until a price is typed, and numbers after;
  - the sheet guide names exactly the template's sheets;
  - each Dashboard chart has its title and legend outside the plot, the y-axis
    crossing the x-axis, fiscal-year (FY...A/F) categories and whole-number
    amounts.

Run: python -m unittest discover -s tests
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
FIX = os.path.join(ROOT, "tests", "fixtures")

import test_bank  # noqa: E402
import test_insurer  # noqa: E402
from openpyxl.utils import column_index_from_string, coordinate_to_tuple  # noqa: E402

PRICED = ["--price", "300", "--price-date", "2026-10-30"]   # a test value, not a quote
NEEDS_PRICE = ("Share price you typed", "Residual-income value vs your price",
               "Market capitalisation (m)", "P/B (price / book value)", "P/E")
BANK_SHEETS = ["Dashboard", "Assumptions", "Timeline", "PnL", "Equity", "Balance_Sheet",
               "Liquidity", "Residual_Income", "Valuation", "Data_Notes"]
INSURER_SHEETS = ["Dashboard", "Assumptions", "Timeline", "PnL", "Equity", "Balance_Sheet",
                  "Investment_Flows", "Residual_Income", "Valuation", "Data_Notes"]


def links(ws):
    return [(c.coordinate, c.value, c.hyperlink.location)
            for row in ws.iter_rows() for c in row if c.hyperlink is not None]


def results(wb, values):
    """{Start_Here result label: evaluated value}."""
    ws, out, on = wb["Start_Here"], {}, False
    for r in range(1, ws.max_row + 1):
        label = ws.cell(row=r, column=2).value
        if isinstance(label, str) and label.startswith("Results"):
            on = True
            continue
        if on and not label:
            break
        if on:
            out[label] = values[("START_HERE", "C%d" % r)]
    return out


class FrontDoor:
    """Shared assertions; each subclass sets cls.plain, cls.priced and cls.SHEETS."""

    def test_start_here_first_and_active(self):
        for _, _, wb, _, code, text in (self.plain, self.priced):
            self.assertEqual(code, 0, text)
            self.assertEqual(wb.sheetnames[0], "Start_Here")
            self.assertEqual(wb.sheetnames[1], "Dashboard")
            self.assertEqual(wb.active.title, "Start_Here")
            # No other tab is selected, so Excel opens on the active one (Start_Here).
            self.assertEqual([w.title for w in wb.worksheets[1:] if w.sheet_view.tabSelected], [])

    def test_links_point_to_existing_cells(self):
        wb = self.plain[2]
        found = links(wb["Start_Here"])
        self.assertEqual(len(found), 3 + len(self.SHEETS))
        for coord, text, loc in found:
            m = re.match(r"^'([^']+)'!([A-Z]+)(\d+)$", loc)
            self.assertTrue(m, loc)
            sheet, col, row = m.group(1), m.group(2), int(m.group(3))
            self.assertIn(sheet, wb.sheetnames, coord)
            ws = wb[sheet]
            self.assertLessEqual(row, ws.max_row, loc)
            self.assertLessEqual(column_index_from_string(col), max(ws.max_column, 1), loc)
        steps = {text: loc for _, text, loc in found if text.startswith("Go to")}
        price = steps["Go to the share price cell"]
        r, c = coordinate_to_tuple(price.split("!")[1])
        self.assertEqual(c, 5)
        self.assertEqual(wb["Assumptions"].cell(row=r, column=3).value, "val_share_price")
        self.assertEqual(steps["Go to Assumptions"], "'Assumptions'!E2")
        self.assertEqual(steps["Go to Valuation"], "'Valuation'!A1")

    def test_sheet_guide_names_this_templates_sheets(self):
        wb = self.plain[2]
        guide = [text for coord, text, loc in links(wb["Start_Here"]) if loc.endswith("!A1")
                 and coord.startswith("B")]
        self.assertEqual(guide, self.SHEETS)
        self.assertEqual(sorted(guide), sorted(n for n in wb.sheetnames
                                               if n not in ("Start_Here", "_Manifest")))

    def test_results_enter_a_price_until_typed(self):
        plain = results(self.plain[2], self.plain[3])
        priced = results(self.priced[2], self.priced[3])
        self.assertEqual(len(plain), 7)
        for label, v in plain.items():
            if label in NEEDS_PRICE:
                self.assertEqual(v, "enter a price", label)
            else:
                self.assertIsInstance(float(v), float, label)
        for label, v in priced.items():
            self.assertIsInstance(float(v), float, label)
        self.assertEqual(float(priced["Share price you typed"]), 300.0)
        val = test_bank.valuation(self.priced[2], self.priced[3], "Residual-income value per share")
        self.assertEqual(float(priced["Residual-income value per share"]), float(val))

    def test_assumptions_annotated(self):
        ws = self.plain[2]["Assumptions"]
        self.assertEqual(ws["F1"].value, "Where this comes from / what to do")
        for r in range(2, ws.max_row + 1):
            aid = ws.cell(row=r, column=3).value or ""
            note = ws.cell(row=r, column=6).value
            if aid.startswith("hist_"):
                self.assertEqual(ws.cell(row=r, column=5).fill.fgColor.rgb, "00E7E6E6", aid)
                self.assertEqual(note, "Filed figure. Leave it as it is.")
            elif aid in self.DERIVED:
                self.assertTrue(str(note).startswith("Default: "), aid)

    def test_dashboard_chart_axes(self):
        wb, values = self.plain[2], self.plain[3]
        ws = wb["Dashboard"]
        heads = {c.value: c.column_letter for c in ws[1] if c.value}
        fy = heads["Fiscal year"]
        labels = [values[("DASHBOARD", "%s%d" % (fy, r))] for r in range(2, ws.max_row + 1)]
        self.assertEqual(labels[:3], [l for l in labels[:3] if re.match(r"^FY\d{4}A$", l)])
        self.assertTrue(all(re.match(r"^FY\d{4}F$", l) for l in labels[3:]), labels)
        charts = ws._charts
        self.assertEqual([c.title.tx.rich.p[0].r[0].t for c in charts], self.TITLES)
        for ch in charts:
            name = ch.title.tx.rich.p[0].r[0].t
            self.assertIs(ch.title.overlay, False, name)
            if ch.legend is not None:
                self.assertIs(ch.legend.overlay, False, name)
            self.assertEqual(ch.y_axis.crossAx, ch.x_axis.axId, name)
            self.assertEqual(ch.x_axis.axId, 10, name)   # text axis, not the date axis
            for s in ch.series:
                self.assertIn("$%s$" % fy, s.cat.strRef.f if s.cat.strRef else s.cat.numRef.f, name)
            fmt = ch.y_axis.number_format.formatCode
            self.assertEqual(fmt, "0.00%" if ("ratio" in name or "ROE" in name
                                             or "assets" in name) else "#,##0", name)


class BankFrontDoor(FrontDoor, unittest.TestCase):
    SHEETS = BANK_SHEETS
    TITLES = ["Net interest income and net revenue (m a year)",
              "Net income to owners (m a year)",
              "Net loans and deposits (m, year end)",
              "Equity to owners / assets against target",
              "ROE (on opening equity to owners)"]
    DERIVED = ("bk_loan_growth", "bk_deposit_growth", "bk_asset_yield", "bk_credit_cost")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        hist = os.path.join(FIX, "bank_8306_history_trim.json")
        cls.plain = test_bank.build(cls.tmp.name, hist, "plain")
        cls.priced = test_bank.build(cls.tmp.name, hist, "priced", PRICED)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class InsurerFrontDoor(FrontDoor, unittest.TestCase):
    SHEETS = INSURER_SHEETS
    TITLES = ["Premiums (m a year)",
              "Combined ratio (all underwriting costs / premiums)",
              "Net investment income (m a year)",
              "Net income to owners (m a year)",
              "Equity to owners (m, year end)",
              "ROE (on opening equity to owners)"]
    DERIVED = ("ins_premium_growth", "ins_loss_ratio", "ins_expense_ratio",
               "ins_investment_yield")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        hist = os.path.join(FIX, "insurer_trv_history_trim.json")
        cls.plain = test_insurer.build(cls.tmp.name, hist, "plain")
        cls.priced = test_insurer.build(cls.tmp.name, hist, "priced", PRICED)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_written_premiums_follow_the_pnl(self):
        """Japanese non-life files premiums written: the Dashboard says so."""
        tmp = self.tmp.name
        with open(os.path.join(ROOT, test_insurer.SCHEMA), encoding="utf-8") as f:
            text = f.read()
        text, k = re.subn(r"(?m)^  years: 3 .*$", "  years: 2", text)
        self.assertEqual(k, 1)
        schema = os.path.join(tmp, "insurer_2y.yaml")
        with open(schema, "w", encoding="utf-8") as f:
            f.write(text)
        _, _, wb, values, code, out = test_insurer.build(
            tmp, os.path.join(FIX, "insurer_8766_history_trim.json"), "tokio", schema=schema)
        self.assertEqual(code, 0, out)
        ws = wb["Dashboard"]
        heads = [c.value for c in ws[1] if c.value]
        self.assertIn("Premiums Written (m/yr)", heads)
        self.assertEqual(ws._charts[0].title.tx.rich.p[0].r[0].t, "Premiums Written (m a year)")
        self.assertEqual(ws._charts[1].title.tx.rich.p[0].r[0].t,
                         "Combined ratio (all underwriting costs / premiums written)")
        col = [c.column_letter for c in ws[1] if c.value == "Premiums Written (m/yr)"][0]
        self.assertEqual(float(values[("DASHBOARD", "%s2" % col)]),
                         float(values[("PNL", "C2")]))   # 12-month years: annual = filed


if __name__ == "__main__":
    unittest.main()

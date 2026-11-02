"""Listed-corporate model: history -> answers -> workbook -> verify_model.py.

Offline only. Starts from tests/fixtures/aapl_trim.json (Apple FY2018-FY2021,
trimmed SEC companyfacts), rebuilt into a history by filings_map.py, with a
synthetic cover-page share count added (the trimmed file carries no dei facts).

Run: python -m unittest discover -s tests
"""
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
FIX = os.path.join(ROOT, "tests", "fixtures")
SCHEMA = os.path.join("verticals", "listed_corporate.yaml")

import filings_map  # noqa: E402
import history_to_answers  # noqa: E402

PRICE = 100.0                   # a test value typed as the user would
LAST_ACCN = "0000320193-21-000105"   # Apple's FY2021 10-K
SHARES = 16406397000


def run(args):
    p = subprocess.run([sys.executable] + args, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=600)
    return p.returncode, p.stdout


def with_shares(data):
    """Add cover-page counts: one on the FY2021 10-K, a later one on a 10-Q."""
    data = copy.deepcopy(data)
    data["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2021-10-15", "val": SHARES, "accn": LAST_ACCN, "fy": 2021, "fp": "FY",
         "form": "10-K", "filed": "2021-10-29"},
        {"end": "2022-01-14", "val": 1, "accn": "0000320193-22-000007", "fy": 2022,
         "fp": "Q1", "form": "10-Q", "filed": "2022-01-28"}]}}}
    return data


def evaluate(path):
    import formulas
    sol = formulas.ExcelModel().loads(path).finish().calculate()
    out = {}
    for k, v in sol.items():
        m = re.match(r"^'\[[^\]]+\](.+)'!([A-Z]+\d+)$", str(k))
        if m:
            try:
                out[(m.group(1).upper(), m.group(2))] = v.value.ravel()[0]
            except Exception:
                pass
    return out


class ListedModel(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = with_shares(filings_map.load_facts(os.path.join(FIX, "aapl_trim.json")))
        cls.history = filings_map.build_history(data, "AAPL", filings_map.load_map())
        cls.hist_path = os.path.join(cls.tmp.name, "AAPL_history.json")
        with open(cls.hist_path, "w", encoding="utf-8") as f:
            json.dump(cls.history, f)
        code = history_to_answers.main([cls.hist_path, "--price", str(PRICE),
                                        "--price-date", "2026-10-30"])
        assert code == 0
        cls.answers = os.path.join(cls.tmp.name, "aapl_filings.yaml")
        cls.xlsx = os.path.join(cls.tmp.name, "aapl_model.xlsx")
        code, text = run(["engine/build_model.py", SCHEMA, cls.answers, cls.xlsx])
        assert code == 0, text
        cls.verify_code, cls.verify_text = run(["engine/verify_model.py", SCHEMA, cls.xlsx])
        cls.values = evaluate(cls.xlsx)
        from openpyxl import load_workbook
        cls.wb = load_workbook(cls.xlsx)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def valuation(self, label_start):
        ws = self.wb["Valuation"]
        for r in range(2, ws.max_row + 1):
            label = ws.cell(row=r, column=1).value
            if isinstance(label, str) and label.startswith(label_start):
                return self.values[("VALUATION", "B%d" % r)]
        self.fail("no Valuation row starting %r" % label_start)

    def test_verify_model_passes(self):
        self.assertEqual(self.verify_code, 0, self.verify_text)
        self.assertIn("Hardcoded numbers in calc sheets: 0", self.verify_text)
        self.assertIn("PASS  Balance_Sheet: BS Check", self.verify_text)
        self.assertIn("PASS  Cash_Flow: CF Check", self.verify_text)

    def test_ev_is_market_cap_plus_net_debt(self):
        mcap = self.valuation("Market capitalisation")
        net_debt = self.valuation("Net debt")
        ev = self.valuation("Enterprise value")
        self.assertAlmostEqual(ev, mcap + net_debt, places=6)
        self.assertAlmostEqual(mcap, PRICE * SHARES / 1e6, places=3)

    def test_share_count_from_own_10k_cover(self):
        so = self.history["shares_outstanding"]
        self.assertEqual((so["val"], so["accn"]), (SHARES, LAST_ACCN))

    def test_actuals_are_inputs_and_labelled(self):
        ws = self.wb["Timeline"]
        labels = [ws.cell(row=r, column=3).value for r in (2, 3, 4)]
        self.assertTrue(all("Actual (" in l and "filing ID" in l for l in labels), labels)
        # last actual year: label evaluates with the FY2021 accn; cash is the filed figure
        lab = self.values[("TIMELINE", "C4")]
        self.assertEqual(lab, "FY2021 Actual (10-K, filing ID %s)" % LAST_ACCN)
        bs = self.wb["Balance_Sheet"]
        self.assertEqual(bs["C4"].value, "=hist_3_cash")
        filed = (self.history["lines"]["cash"]["2021"]["val"]
                 + self.history["lines"]["short_term_investments"]["2021"]["val"]) / 1e6
        self.assertAlmostEqual(self.values[("BALANCE_SHEET", "C4")], filed, places=6)

    def test_flags_shown_on_data_notes(self):
        text = "\n".join(str(r[0]) for r in self.wb["Data_Notes"].iter_rows(values_only=True))
        self.assertIn("FY2021 cash:", text)          # the restricted-cash flag
        self.assertIn("never plugged", text)

    def test_builtin_evaluator_reads_valuation(self):
        """xl_eval.py (the fallback without `formulas`) gives the same Valuation values."""
        from xl_eval import Evaluator
        ws = self.wb["Valuation"]
        cells = [("Valuation", "B%d" % r) for r in range(2, ws.max_row + 1)
                 if isinstance(ws.cell(row=r, column=2).value, str)]
        got = Evaluator(self.xlsx).values(cells)
        for (sheet, coord), v in got.items():
            want = self.values[("VALUATION", coord)]
            if isinstance(want, (int, float)):
                self.assertAlmostEqual(v, want, places=6, msg=coord)
            else:
                self.assertEqual(str(v), str(want), coord)

    def test_user_share_count_replaces_cover_default(self):
        out = os.path.join(self.tmp.name, "shares")
        self.assertEqual(history_to_answers.main([self.hist_path, "--out", out,
                                                  "--shares-millions", "16500.5"]), 0)
        with open(os.path.join(out, "aapl_filings.yaml"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(re.findall(r"(?m)^val_shares_diluted: (\S+)", text), ["16500.5"])
        self.assertIn("source: user", text)

    def test_unbalanced_opening_is_refused(self):
        h = copy.deepcopy(self.history)
        h["lines"]["total_assets"]["2021"]["val"] += 5000000
        bad = os.path.join(self.tmp.name, "BAD_history.json")
        h["ticker"] = "BAD"
        with open(bad, "w", encoding="utf-8") as f:
            json.dump(h, f)
        self.assertEqual(history_to_answers.main([bad]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "bad_filings.yaml")))


def column_values(wb, values, sheet, header, rows):
    """Evaluated values of the column headed `header` on `sheet`, for `rows`."""
    ws = wb[sheet]
    for c in range(1, ws.max_column + 1):
        if ws.cell(row=1, column=c).value == header:
            col = ws.cell(row=1, column=c).column_letter
            return [values[(sheet.upper(), "%s%d" % (col, r))] for r in rows]
    raise KeyError("%s has no column %r" % (sheet, header))


class CashFloorAndRevolver(unittest.TestCase):
    """Cash never closes below the minimum in any forecast year (Apple fixture).

    Three cases: the history defaults; buybacks at 200% of net income (the
    revolver must draw); and a floor at 300% of opening cash with no buybacks
    (the revolver draws in year 1, then repays from surplus cash).
    """

    @classmethod
    def setUpClass(cls):
        from openpyxl import load_workbook
        cls.tmp = tempfile.TemporaryDirectory()
        data = with_shares(filings_map.load_facts(os.path.join(FIX, "aapl_trim.json")))
        history = filings_map.build_history(data, "AAPL", filings_map.load_map())
        hist_path = os.path.join(cls.tmp.name, "AAPL_history.json")
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump(history, f)
        assert history_to_answers.main([hist_path]) == 0
        with open(os.path.join(cls.tmp.name, "aapl_filings.yaml"), encoding="utf-8") as f:
            base = f.read()
        cases = {"base": {}, "buyback": {"fin_buyback_pct_ni": "2.0"},
                 "high_floor": {"fin_buyback_pct_ni": "0.0", "fin_min_cash_pct": "3.0"}}
        cls.cases = {}
        for name, changes in cases.items():
            text = base
            for key, val in changes.items():
                text, n = re.subn(r"(?m)^%s: .*$" % key, "%s: %s" % (key, val), text)
                if n == 0:
                    text += "\n%s: %s\n" % (key, val)
            answers = os.path.join(cls.tmp.name, "%s.yaml" % name)
            with open(answers, "w", encoding="utf-8") as f:
                f.write(text)
            xlsx = os.path.join(cls.tmp.name, "%s.xlsx" % name)
            code, out = run(["engine/build_model.py", SCHEMA, answers, xlsx])
            assert code == 0, out
            vcode, vout = run(["engine/verify_model.py", SCHEMA, xlsx])
            wb = load_workbook(xlsx)
            rows = range(5, wb["Timeline"].max_row + 1)      # forecast rows (3 actual years)
            vals = evaluate(xlsx)
            got = {h: column_values(wb, vals, "Funding", h, rows) for h in (
                "Cash Close (m)", "Minimum Cash (m)", "Revolver Draw (m)",
                "Revolver Repay (m)", "Revolver Close (m)")}
            got["bs_cash"] = column_values(wb, vals, "Balance_Sheet", "Cash (m)", rows)
            cls.cases[name] = (vcode, vout, got)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_floor(self, name):
        vcode, vout, got = self.cases[name]
        self.assertEqual(vcode, 0, vout)
        for cash, floor in zip(got["bs_cash"], got["Minimum Cash (m)"]):
            self.assertGreaterEqual(cash, floor - 1e-6, (name, got))
        self.assertTrue(all(x >= -1e-9 for x in got["Revolver Close (m)"]), got)
        return got

    def test_base_case_holds_the_floor(self):
        self.check_floor("base")

    def test_revolver_draws_when_distributions_exceed_cash(self):
        got = self.check_floor("buyback")
        self.assertGreater(max(got["Revolver Draw (m)"]), 0)
        for cash, floor in zip(got["bs_cash"], got["Minimum Cash (m)"]):
            self.assertAlmostEqual(cash, floor, places=6)   # held exactly at the floor

    def test_revolver_repays_from_surplus_cash(self):
        got = self.check_floor("high_floor")
        self.assertGreater(got["Revolver Draw (m)"][0], 0)
        self.assertGreater(sum(got["Revolver Repay (m)"]), 0)


if __name__ == "__main__":
    unittest.main()

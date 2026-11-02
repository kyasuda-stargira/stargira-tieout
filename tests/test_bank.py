"""Bank model: history -> answers -> workbook -> verify_model.py.

Offline only. Starts from trimmed history files written by filings_map.py:
tests/fixtures/bank_jpm_history_trim.json (JPMorgan FY2022-FY2025, US GAAP, loans
filed net) and bank_8306_history_trim.json (MUFG FY2023-FY2026, J-GAAP, loans
printed before the allowance).

Run: python -m unittest discover -s tests
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
FIX = os.path.join(ROOT, "tests", "fixtures")
SCHEMA = os.path.join("verticals", "listed_bank.yaml")

import history_to_answers  # noqa: E402

PRICE = 300.0                   # a test value typed as the user would, not a market quote


def run(args):
    p = subprocess.run([sys.executable] + args, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=600)
    return p.returncode, p.stdout


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


def column(wb, values, sheet, header, rows):
    ws = wb[sheet]
    for c in range(1, ws.max_column + 1):
        if ws.cell(row=1, column=c).value == header:
            col = ws.cell(row=1, column=c).column_letter
            return [values[(sheet.upper(), "%s%d" % (col, r))] for r in rows]
    raise KeyError("%s has no column %r" % (sheet, header))


def valuation(wb, values, label_start):
    ws = wb["Valuation"]
    for r in range(2, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        if isinstance(label, str) and label.startswith(label_start):
            return values[("VALUATION", "B%d" % r)]
    raise KeyError(label_start)


def independent_ri(a):
    """Residual-income value from the answers file alone, by the stated rules."""
    h = lambda k: a["hist_3_%s" % k]  # noqa: E731
    loans = h("loans") - h("loan_allowance_netted")
    liquid = sum(h(k) for k in ("cash", "reverse_repos", "trading_assets", "securities"))
    other_a = h("total_assets") - loans - liquid
    ta, dep, borr = h("total_assets"), h("deposits"), h("borrowings")
    other_l = h("total_liabilities") - dep - borr
    eq, nci = h("equity_parent"), h("total_equity") - h("equity_parent")
    coe, g, n = a["val_cost_of_equity"], a["val_terminal_growth"], a["g_forecast_years"]
    book, pv, ri = eq, 0.0, None
    for t in range(1, n + 1):
        rev = ((loans + liquid) * a["bk_asset_yield"] - (dep + borr) * a["bk_funding_cost"]
               + ta * a["bk_fee_income_pct_assets"])
        pre = rev - loans * a["bk_credit_cost"] - rev * a["bk_cost_income"]
        nit = pre - max(0.0, pre) * a["g_tax_rate"]
        ni = nit * (1 - a["bk_nci_share"])
        ri = ni - coe * eq
        pv += ri / (1 + coe) ** t
        loans *= 1 + a["bk_loan_growth"]
        dep *= 1 + a["bk_deposit_growth"]
        eq += ni - (a["fin_dividend_payout"] + a["fin_buyback_pct_ni"]) * max(0.0, ni)
        liquid = dep + borr + other_l + eq + nci - loans - other_a
        ta = loans + liquid + other_a
    value = book + pv + ri * (1 + g) / (coe - g) / (1 + coe) ** n
    return value, value / a["val_shares_diluted"]


def build(tmp, history, name, extra=(), changes=None):
    """history -> answers (-> edited answers) -> workbook; returns paths and verify result."""
    out = os.path.join(tmp, name)
    assert history_to_answers.main([history, "--out", out] + list(extra)) == 0
    ans = [os.path.join(out, f) for f in os.listdir(out) if f.endswith("_bank_filings.yaml")][0]
    if changes:
        with open(ans, encoding="utf-8") as f:
            text = f.read()
        for key, val in changes.items():
            text, n = re.subn(r"(?m)^%s: .*$" % key, "%s: %s" % (key, val), text)
            assert n == 1, key
        with open(ans, "w", encoding="utf-8") as f:
            f.write(text)
    xlsx = os.path.join(out, "model.xlsx")
    code, text = run(["engine/build_model.py", SCHEMA, ans, xlsx])
    assert code == 0, text
    vcode, vtext = run(["engine/verify_model.py", SCHEMA, xlsx])
    from openpyxl import load_workbook
    with open(ans, encoding="utf-8") as f:
        answers = yaml.safe_load(f)
    return answers, xlsx, load_workbook(xlsx), evaluate(xlsx), vcode, vtext


FC_ROWS = range(5, 10)          # 3 actual years in rows 2-4, then 5 forecast years


class BankModelUS(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        hist = os.path.join(FIX, "bank_jpm_history_trim.json")
        cls.plain = build(cls.tmp.name, hist, "plain")
        cls.priced = build(cls.tmp.name, hist, "priced",
                           ["--price", str(PRICE), "--price-date", "2026-10-30"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_verify_model_passes(self):
        for _, _, _, _, code, text in (self.plain, self.priced):
            self.assertEqual(code, 0, text)
            self.assertIn("Hardcoded numbers in calc sheets: 0", text)
            self.assertIn("PASS  Balance_Sheet: BS Check (m)", text)
            self.assertIn("PASS  Liquidity: CF Check (m)", text)
            self.assertIn("Circular references\n   none", text)

    def test_forecast_balance_check_is_zero(self):
        _, _, wb, vals, _, _ = self.plain
        checks = column(wb, vals, "Balance_Sheet", "BS Check (m)", FC_ROWS)
        self.assertEqual(len(checks), 5)
        self.assertTrue(all(abs(x) < 1e-6 for x in checks), checks)
        flows = column(wb, vals, "Liquidity", "CF Check (m)", FC_ROWS)
        self.assertTrue(all(abs(x) < 1e-6 for x in flows), flows)

    def test_enter_a_price_until_typed(self):
        _, _, wb, vals, _, _ = self.plain
        for label in ("Market capitalisation", "P/B", "P/E", "Residual-income value vs"):
            self.assertEqual(valuation(wb, vals, label), "enter a price", label)
        self.assertIsInstance(valuation(wb, vals, "Residual-income value per share"), float)
        a, _, wb, vals, _, _ = self.priced
        mcap = valuation(wb, vals, "Market capitalisation")
        self.assertAlmostEqual(mcap, PRICE * a["val_shares_diluted"], places=6)
        self.assertAlmostEqual(valuation(wb, vals, "P/B"), mcap / a["hist_3_equity_parent"], places=9)
        self.assertAlmostEqual(valuation(wb, vals, "P/E"), mcap / a["hist_3_net_income"], places=9)

    def test_residual_income_matches_independent_calc(self):
        for a, _, wb, vals, _, _ in (self.plain, self.priced):
            value, per_share = independent_ri(a)
            self.assertAlmostEqual(valuation(wb, vals, "Residual-income equity value"), value,
                                   delta=1e-6)
            self.assertAlmostEqual(valuation(wb, vals, "Residual-income value per share"),
                                   per_share, delta=1e-6)

    def test_us_loans_are_not_netted_again(self):
        a, _, wb, vals, _, _ = self.plain
        self.assertEqual(a["hist_3_loan_allowance_netted"], 0.0)
        self.assertEqual(a["hist_3_loans"], 1467664.0)     # JPM FY2025, net of the allowance
        self.assertEqual(column(wb, vals, "Balance_Sheet", "Net Loans (m)", [4]), [1467664.0])

    def test_drivers_from_own_history_with_derivations(self):
        import start_here
        a = self.plain[0]
        # credit cost = sum(provision FY2023-25) / sum(opening net loans FY2022-24)
        self.assertAlmostEqual(a["bk_credit_cost"],
                               (9320 + 10678 + 14212) / (1115921 + 1301286 + 1323643), places=9)
        how = start_here._derivations(a["data_notes"])
        for key in ("bk_loan_growth", "bk_deposit_growth", "bk_asset_yield", "bk_funding_cost",
                    "bk_fee_income_pct_assets", "bk_cost_income", "bk_credit_cost", "g_tax_rate",
                    "fin_dividend_payout", "fin_buyback_pct_ni", "bk_target_equity_assets",
                    "val_cost_of_equity", "val_terminal_growth"):
            self.assertIn(key, how)
        self.assertIn("opening", how["bk_asset_yield"])
        self.assertIn("not derived from the filing", how["val_cost_of_equity"])
        notes = "\n".join(a["data_notes"])
        self.assertIn("CET1", notes)
        self.assertIn("No interest-rate sensitivity", notes)
        self.assertNotIn("Securities firm on the bank chart", notes)

    def test_liquid_assets_negative_is_flagged(self):
        _, _, wb, vals, code, text = build(self.tmp.name, os.path.join(FIX, "bank_jpm_history_trim.json"),
                                           "hot", changes={"bk_loan_growth": "0.5",
                                                           "bk_target_equity_assets": "0.2"})
        self.assertEqual(code, 0, text)          # still balances: the flag is not a plug
        liquid = column(wb, vals, "Balance_Sheet", "Liquid Assets (m)", FC_ROWS)
        flags = column(wb, vals, "Balance_Sheet", "Liquid Assets Flag", FC_ROWS)
        self.assertLess(min(liquid), 0)
        for x, f in zip(liquid, flags):
            self.assertEqual(f.startswith("NEGATIVE"), x < 0, (x, f))
        cap = column(wb, vals, "Balance_Sheet", "Capital vs Target", FC_ROWS)
        self.assertEqual(set(cap), {"below target"})

    def test_two_year_history_is_refused(self):
        import json
        with open(os.path.join(FIX, "bank_jpm_history_trim.json"), encoding="utf-8") as f:
            h = json.load(f)
        h["years"] = h["years"][-2:]
        h["ticker"] = "TWO"
        path = os.path.join(self.tmp.name, "TWO_history.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(h, f)
        self.assertEqual(history_to_answers.main([path, "--out", self.tmp.name]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "two_bank_filings.yaml")))


def raw(history, line, fy):
    """A line straight from the history JSON, in millions; 0 when not tagged."""
    import json
    with open(history, encoding="utf-8") as f:
        x = json.load(f)["lines"].get(line, {}).get(fy)
    return 0.0 if x is None else x["val"] / 1e6


FIRST_YEAR = ["Yield on Opening Earning Assets", "Cost of Opening Interest-bearing Liab",
              "NIM: Yield less Cost of Funds", "NII / Opening Earning Assets", "Cost-to-Income",
              "Credit Cost (on Opening Net Loans)", "ROE (on Opening Equity to Owners)"]


def first_year_by_hand(history, fy, prev, gross):
    """R20: the first actual year's ratios from the history JSON alone."""
    r = lambda line, y: raw(history, line, y)  # noqa: E731
    loans = r("loans", prev) - (r("loan_allowance", prev) if gross else 0.0)
    earning = loans + sum(r(k, prev) for k in ("cash", "reverse_repos", "trading_assets",
                                               "securities"))
    ibl = sum(r(k, prev) for k in ("deposits", "negotiable_cds", "repo_borrowings",
                                   "short_term_borrowings", "long_term_debt"))
    y, c = r("interest_income", fy) / earning, r("interest_expense", fy) / ibl
    nii = r("interest_income", fy) - r("interest_expense", fy)
    return [y, c, y - c, nii / earning, r("opex", fy) / r("total_revenue", fy),
            r("provision", fy) / loans, r("net_income", fy) / r("equity_parent", prev)]


class FirstActualYear(unittest.TestCase):
    """R20: the first actual year's ratios on opening balances read the filed balance
    sheet one year earlier; with no earlier balance sheet they are blank."""

    @classmethod
    def setUpClass(cls):
        import json
        cls.tmp = tempfile.TemporaryDirectory()
        cls.jpm = os.path.join(FIX, "bank_jpm_history_trim.json")
        cls.mufg = os.path.join(FIX, "bank_8306_history_trim.json")
        cls.us = build(cls.tmp.name, cls.jpm, "us")
        cls.jp = build(cls.tmp.name, cls.mufg, "jp")
        with open(cls.jpm, encoding="utf-8") as f:       # drop FY2022: no earlier balance sheet
            h = json.load(f)
        h["years"] = [y for y in h["years"] if str(y["fy"]) != "2022"]
        for line in h["lines"].values():
            line.pop("2022", None)
        cls.short = os.path.join(cls.tmp.name, "jpm_3y.json")
        with open(cls.short, "w", encoding="utf-8") as f:
            json.dump(h, f)
        cls.none = build(cls.tmp.name, cls.short, "none")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, res, history, fy, prev, gross):
        a, _, wb, vals, code, text = res
        self.assertEqual(code, 0, text)
        self.assertEqual(a["hist_open_fy"], int(prev))
        got = [column(wb, vals, "PnL", h, [2])[0] for h in FIRST_YEAR]
        for h, x, want in zip(FIRST_YEAR, got, first_year_by_hand(history, fy, prev, gross)):
            self.assertAlmostEqual(x, want, places=12, msg=h)
        self.assertEqual(got[2], got[0] - got[1])
        self.assertIn("Opening balance (FY%s year end" % prev,
                      wb["Assumptions"]["B%d" % next(
                          r for r in range(2, wb["Assumptions"].max_row + 1)
                          if wb["Assumptions"]["C%d" % r].value == "hist_open_earning")].value)

    def test_us_bank_first_year_from_the_prior_year_end(self):
        self.check(self.us, self.jpm, "2023", "2022", False)

    def test_japan_bank_first_year_from_the_prior_year_end(self):
        self.check(self.jp, self.mufg, "2024", "2023", True)

    def test_no_earlier_balance_sheet_leaves_the_ratios_blank(self):
        a, _, wb, vals, code, text = self.none
        self.assertEqual(code, 0, text)
        self.assertFalse([k for k in a if k.startswith("hist_open_")])
        ws = wb["PnL"]
        heads = {c.value: c.column_letter for c in ws[1]}
        for h in FIRST_YEAR:
            if h != "Cost-to-Income":
                self.assertIsNone(ws["%s2" % heads[h]].value, h)
        self.assertIsNotNone(ws["%s2" % heads["Cost-to-Income"]].value)
        self.assertTrue(any(str(n).startswith("Opening balance: none.") for n in a["data_notes"]))
        self.assertIsInstance(column(wb, vals, "PnL", FIRST_YEAR[0], [3])[0], float)


class DistributionCap(unittest.TestCase):
    """The shared default cap on dividends + buybacks (history_to_answers.cap_distributions)."""

    def test_below_cap_is_untouched(self):
        vals = {"fin_dividend_payout": 0.27, "fin_buyback_pct_ni": 0.36}
        why = {"fin_dividend_payout": "d", "fin_buyback_pct_ni": "b"}
        history_to_answers.cap_distributions(vals, why)
        self.assertEqual((vals, why), ({"fin_dividend_payout": 0.27, "fin_buyback_pct_ni": 0.36},
                                       {"fin_dividend_payout": "d", "fin_buyback_pct_ni": "b"}))

    def test_buybacks_scaled_down_above_cap(self):
        # Goldman Sachs FY2023-25: dividends 34.94% + buybacks 65.44% = 100.38%
        vals = {"fin_dividend_payout": 0.3494, "fin_buyback_pct_ni": 0.6544}
        why = {"fin_dividend_payout": "d", "fin_buyback_pct_ni": "b"}
        history_to_answers.cap_distributions(vals, why)
        self.assertEqual(vals["fin_dividend_payout"], 0.3494)
        self.assertAlmostEqual(vals["fin_buyback_pct_ni"], 1 - 0.3494, places=12)
        self.assertIn("actual 3-year ratio 65.4400%", why["fin_buyback_pct_ni"])
        self.assertIn("100.38% of net income", why["fin_buyback_pct_ni"])
        self.assertEqual(why["fin_dividend_payout"], "d")

    def test_dividends_alone_above_cap(self):
        vals = {"fin_dividend_payout": 1.3, "fin_buyback_pct_ni": 0.2}
        why = {"fin_dividend_payout": "d", "fin_buyback_pct_ni": "b"}
        history_to_answers.cap_distributions(vals, why)
        self.assertEqual((vals["fin_dividend_payout"], vals["fin_buyback_pct_ni"]), (1.0, 0.0))
        self.assertIn("dividends are capped at 100%", why["fin_dividend_payout"])


class BankModelJapan(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.res = build(cls.tmp.name, os.path.join(FIX, "bank_8306_history_trim.json"), "mufg")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_verify_model_passes(self):
        code, text = self.res[4], self.res[5]
        self.assertEqual(code, 0, text)

    def test_gross_loans_are_netted_and_labelled(self):
        a, _, wb, vals, _, _ = self.res
        # MUFG FY2026: 貸出金 133,799,490 less 貸倒引当金 1,229,947 (¥m)
        self.assertEqual((a["hist_3_loans"], a["hist_3_loan_allowance_netted"]),
                         (133799490.0, 1229947.0))
        self.assertEqual(column(wb, vals, "Balance_Sheet", "Net Loans (m)", [4]),
                         [133799490.0 - 1229947.0])
        self.assertIn("Net loans = loans as printed (貸出金", "\n".join(a["data_notes"]))
        # loan growth is on net loans, FY2023 to FY2026
        net23, net26 = 109146272 - 1245727, 133799490 - 1229947
        self.assertAlmostEqual(a["bk_loan_growth"], (net26 / net23) ** (1 / 3) - 1, places=9)
        # deposits include 譲渡性預金
        self.assertEqual(a["hist_3_deposits"], 239439246.0 + 17601483.0)

    def test_residual_income_matches_independent_calc(self):
        a, _, wb, vals, _, _ = self.res
        value, per_share = independent_ri(a)
        self.assertAlmostEqual(valuation(wb, vals, "Residual-income equity value"), value,
                               delta=1e-6)
        self.assertAlmostEqual(valuation(wb, vals, "Residual-income value per share"),
                               per_share, delta=1e-6)

    def test_forecast_balance_check_is_zero(self):
        _, _, wb, vals, _, _ = self.res
        checks = column(wb, vals, "Balance_Sheet", "BS Check (m)", FC_ROWS)
        self.assertTrue(all(abs(x) < 1e-6 for x in checks), checks)


if __name__ == "__main__":
    unittest.main()

"""Insurer model: history -> answers -> workbook -> verify_model.py.

Offline only. Starts from trimmed history files written by filings_map.py:
  tests/fixtures/insurer_trv_history_trim.json   Travelers FY2022-FY2025 (US GAAP, non-life)
  tests/fixtures/insurer_met_history_trim.json   MetLife FY2022-FY2025 (US GAAP, life)
  tests/fixtures/insurer_8750_history_trim.json  Dai-ichi Life FY2023-FY2026 (J-GAAP, life)
  tests/fixtures/insurer_8766_history_trim.json  TEST FIXTURE: Tokio Marine's J-GAAP report
      S100TOZP alone (FY2023-FY2024, non-life). The live tool builds 8766 from its IFRS
      report (tests/test_ifrs_insurer.py). This J-GAAP history has 2 years and is refused by
      the 3-year rule, so the non-life J-GAAP path is built here with a copy of the schema
      set to 2 actual years.

Run: python -m unittest discover -s tests
"""
import json
import os
import re
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
FIX = os.path.join(ROOT, "tests", "fixtures")
SCHEMA = os.path.join("verticals", "listed_insurer.yaml")

import history_to_answers  # noqa: E402
from test_bank import column, evaluate, run, valuation  # noqa: E402

PRICE_US = 300.0      # test values typed as the user would, not market quotes
PRICE_JP = 1200.0
FC5 = range(5, 10)    # 3 actual years in rows 2-4, then 5 forecast years


def independent_ri(a):
    """Residual-income value from the answers file alone, by the stated rules."""
    n = max(int(k.split("_")[1]) for k in a if re.match(r"hist_\d+_fy$", k))
    h = lambda k: a["hist_%d_%s" % (n, k)]  # noqa: E731
    prem, fees = h("premiums"), h("fee_income")
    cash, inv = h("cash"), h("investments")
    other_a = h("total_assets") - cash - inv
    res, pab, debt = h("policy_reserves"), h("policyholder_account_balances"), h("debt")
    other_l = h("total_liabilities") - res - debt
    eq, nci = h("equity_parent"), h("total_equity") - h("equity_parent")
    coe, g, years = a["val_cost_of_equity"], a["val_terminal_growth"], a["g_forecast_years"]
    book, pv, ri = eq, 0.0, None
    for t in range(1, years + 1):
        p1 = prem * (1 + a["ins_premium_growth"])
        fees *= 1 + a["ins_premium_growth"]
        pre = (p1 + fees + inv * a["ins_investment_yield"] + p1 * (a["ins_other_income_pct"] + a["ins_inv_gains_pct"])
               - p1 * (a["ins_loss_ratio"] + a["ins_reserve_change_pct"] + a["ins_ph_dividend_pct"]
                       + a["ins_expense_ratio"]) - pab * a["ins_crediting_rate"])
        ni = (pre - max(0.0, pre) * a["g_tax_rate"]) * (1 - a["ins_nci_share"])
        ri = ni - coe * eq
        pv += ri / (1 + coe) ** t
        r1 = (res + p1 * a["ins_reserve_change_pct"] if a["ins_reserve_rollforward"] == 1
              else p1 * a["ins_reserves_pct_premiums"])
        pab = pab * r1 / res if res > 0 else 0.0
        res, prem = r1, p1
        eq += ni - (a["fin_dividend_payout"] + a["fin_buyback_pct_ni"]) * max(0.0, ni)
        inv = res + debt + other_l + eq + nci - cash - other_a
    value = book + pv + ri * (1 + g) / (coe - g) / (1 + coe) ** years
    return value, value / a["val_shares_diluted"]


def build(tmp, history, name, extra=(), changes=None, schema=SCHEMA):
    """history -> answers (-> edited answers) -> workbook; returns paths and verify result."""
    out = os.path.join(tmp, name)
    args = [history, "--out", out] + list(extra)
    if schema != SCHEMA:
        args += ["--schema", schema]
    assert history_to_answers.main(args) == 0
    ans = [os.path.join(out, f) for f in os.listdir(out) if f.endswith("_insurer_filings.yaml")][0]
    if changes:
        with open(ans, encoding="utf-8") as f:
            text = f.read()
        for key, val in changes.items():
            text, k = re.subn(r"(?m)^%s: .*$" % key, "%s: %s" % (key, val), text)
            assert k == 1, key
        with open(ans, "w", encoding="utf-8") as f:
            f.write(text)
    xlsx = os.path.join(out, "model.xlsx")
    code, text = run(["engine/build_model.py", schema, ans, xlsx])
    assert code == 0, text
    vcode, vtext = run(["engine/verify_model.py", schema, xlsx])
    from openpyxl import load_workbook
    with open(ans, encoding="utf-8") as f:
        answers = yaml.safe_load(f)
    return answers, xlsx, load_workbook(xlsx), evaluate(xlsx), vcode, vtext


class Checks:
    """Shared assertions; each subclass sets cls.res (and FC rows)."""
    FC = FC5

    def test_verify_model_passes(self):
        for res in self.all_res():
            code, text = res[4], res[5]
            self.assertEqual(code, 0, text)
            self.assertIn("Hardcoded numbers in calc sheets: 0", text)
            self.assertIn("PASS  Balance_Sheet: BS Check (m)", text)
            self.assertIn("PASS  Investment_Flows: CF Check (m)", text)
            self.assertIn("Circular references\n   none", text)

    def test_forecast_balance_check_is_zero(self):
        _, _, wb, vals, _, _ = self.res
        checks = column(wb, vals, "Balance_Sheet", "BS Check (m)", self.FC)
        self.assertEqual(len(checks), 5)
        self.assertTrue(all(abs(x) < 1e-6 for x in checks), checks)
        flows = column(wb, vals, "Investment_Flows", "CF Check (m)", self.FC)
        self.assertTrue(all(abs(x) < 1e-6 for x in flows), flows)

    def test_enter_a_price_until_typed(self):
        _, _, wb, vals, _, _ = self.res
        for label in ("Market capitalisation", "P/B", "P/E", "Residual-income value vs"):
            self.assertEqual(valuation(wb, vals, label), "enter a price", label)
        self.assertIsInstance(valuation(wb, vals, "Residual-income value per share"), float)

    def test_residual_income_matches_independent_calc(self):
        for a, _, wb, vals, _, _ in self.all_res():
            value, per_share = independent_ri(a)
            self.assertAlmostEqual(valuation(wb, vals, "Residual-income equity value"), value,
                                   delta=1e-6)
            self.assertAlmostEqual(valuation(wb, vals, "Residual-income value per share"),
                                   per_share, delta=1e-6)

    def test_not_modelled_lines_in_data_notes(self):
        import start_here
        a = self.res[0]
        notes = "\n".join(a["data_notes"])
        self.assertIn("Embedded value", notes)
        self.assertIn("IFRS 17 contractual service margin", notes)
        self.assertIn("Investment gains are not projected", notes)
        self.assertIn("Solvency ratios (ESR, SMR", notes)
        how = start_here._derivations(a["data_notes"])
        for key in ("ins_premium_growth", "ins_loss_ratio", "ins_reserve_change_pct",
                    "ins_expense_ratio", "ins_ph_dividend_pct", "ins_investment_yield",
                    "ins_inv_gains_pct", "ins_other_income_pct", "ins_crediting_rate", "ins_reserve_rollforward",
                    "ins_reserves_pct_premiums", "g_tax_rate", "fin_dividend_payout",
                    "fin_buyback_pct_ni", "ins_target_equity_assets", "val_cost_of_equity",
                    "val_terminal_growth"):
            self.assertIn(key, how)
        self.assertEqual(a["ins_inv_gains_pct"], 0.0)
        self.assertIn("volatile and not projected", how["ins_inv_gains_pct"])
        self.assertIn("Projected", how["ins_other_income_pct"])
        self.assertLessEqual(a["fin_dividend_payout"] + a["fin_buyback_pct_ni"], 1.0 + 1e-12)
        self.assertIn("opening investments", how["ins_investment_yield"])

    def test_first_actual_year_ratios_on_opening_balances(self):
        """R20: the first actual year reads the filed balance sheet a year earlier; with
        none in the history, its ratios on opening balances are blank."""
        a, _, wb, vals, _, _ = self.res
        heads = [c.value for c in wb["PnL"][1]]
        inv = next(h for h in heads if str(h).endswith("/ Opening Investments"))
        roe = "ROE (on Opening Equity to Owners)"
        loss = next(h for h in heads if str(h).startswith(("Loss Ratio", "Insurance Service")))
        self.assertAlmostEqual(column(wb, vals, "PnL", loss, [2])[0],
                               a["hist_1_claims"] / a["hist_1_premiums"], places=12)
        if a.get("hist_open_fy") is None:
            for h in (inv, roe):
                self.assertIsNone(wb["PnL"].cell(row=2, column=heads.index(h) + 1).value, h)
            self.assertTrue(any(str(n).startswith("Opening balance: none.")
                                for n in a["data_notes"]))
            return
        self.assertEqual(a["hist_open_fy"], a["hist_1_fy"] - 1)
        nii = a["hist_1_investment_income"] - a["hist_1_investment_expenses"]
        self.assertAlmostEqual(column(wb, vals, "PnL", inv, [2])[0],
                               nii / a["hist_open_investments"], places=12)
        self.assertAlmostEqual(column(wb, vals, "PnL", roe, [2])[0],
                               a["hist_1_net_income"] / a["hist_open_equity_parent"], places=12)
        hist = getattr(self, "hist", None)
        if hist and not hist.endswith("8766_history_trim.json"):
            import json
            with open(hist, encoding="utf-8") as f:
                h = json.load(f)
            prev = str(a["hist_open_fy"])
            self.assertEqual(a["hist_open_equity_parent"], h["lines"]["equity_parent"][prev]["val"] / 1e6)
            self.assertEqual(a["hist_open_investments"], h["lines"]["investments"][prev]["val"] / 1e6)

    def all_res(self):
        return [self.res]


class InsurerUSNonLife(Checks, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hist = os.path.join(FIX, "insurer_trv_history_trim.json")
        cls.res = build(cls.tmp.name, cls.hist, "plain")
        cls.priced = build(cls.tmp.name, cls.hist, "priced",
                           ["--price", str(PRICE_US), "--price-date", "2026-10-30"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def all_res(self):
        return [self.res, self.priced]

    def test_priced_multiples(self):
        a, _, wb, vals, _, _ = self.priced
        mcap = valuation(wb, vals, "Market capitalisation")
        self.assertAlmostEqual(mcap, PRICE_US * a["val_shares_diluted"], places=6)
        self.assertAlmostEqual(valuation(wb, vals, "P/B"), mcap / a["hist_3_equity_parent"], places=9)
        self.assertAlmostEqual(valuation(wb, vals, "P/E"), mcap / a["hist_3_net_income"], places=9)

    def test_drivers_by_hand(self):
        a = self.res[0]
        # loss ratio = claims FY2023-25 / premiums FY2023-25
        self.assertAlmostEqual(a["ins_loss_ratio"], (26215 + 27059 + 27221) / (37761 + 41941 + 43914),
                               places=9)
        # investment yield = NII FY2023-25 / opening investments FY2022-24
        self.assertAlmostEqual(a["ins_investment_yield"],
                               (2922 + 3590 + 3959) / (80454 + 88810 + 94223), places=9)
        self.assertAlmostEqual(a["ins_reserves_pct_premiums"], (65737 + 22431) / 43914, places=9)
        self.assertEqual(a["ins_reserve_rollforward"], 0)

    def test_life_rows_read_zero_and_are_labelled(self):
        a, _, wb, vals, _, _ = self.res
        self.assertEqual((a["ins_reserve_change_pct"], a["ins_crediting_rate"],
                          a["ins_ph_dividend_pct"]), (0.0, 0.0, 0.0))
        self.assertEqual(set(column(wb, vals, "PnL", "Interest Credited to Policyholders (m)",
                                    range(2, 10))), {0.0})
        notes = "\n".join(a["data_notes"])
        self.assertIn("Insurer type: non-life (inferred", notes)
        self.assertIn("interest_credited: 0 in every actual year, not applicable", notes)
        self.assertIn("future_policy_benefits: 0 in every actual year, not applicable", notes)
        self.assertIn("Premium basis: earned", notes)
        self.assertEqual(wb["PnL"]["C1"].value, "Premiums (m)")

    def test_negative_investments_are_flagged(self):
        _, _, wb, vals, code, text = build(
            self.tmp.name, self.hist, "hot",
            changes={"ins_expense_ratio": "1.0", "ins_reserves_pct_premiums": "0.01"})
        self.assertEqual(code, 0, text)          # still balances: the flag is not a plug
        inv = column(wb, vals, "Balance_Sheet", "Investments (m)", FC5)
        flags = column(wb, vals, "Balance_Sheet", "Investments Flag", FC5)
        self.assertLess(min(inv), 0)
        for x, f in zip(inv, flags):
            self.assertEqual(f.startswith("NEGATIVE"), x < 0, (x, f))
        self.assertEqual(set(column(wb, vals, "Balance_Sheet", "Capital vs Target", FC5)),
                         {"below target"})


class InsurerUSLife(Checks, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.res = build(cls.tmp.name, os.path.join(FIX, "insurer_met_history_trim.json"), "met")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_life_rows_used(self):
        a, _, wb, vals, _, _ = self.res
        # interest credited FY2023-25 / opening policyholder account balances FY2022-24
        self.assertAlmostEqual(a["ins_crediting_rate"],
                               (7860 + 8339 + 8950) / (210597 + 219269 + 221445), places=9)
        self.assertAlmostEqual(a["ins_ph_dividend_pct"],
                               (622 + 595 + 553) / (44283 + 44945 + 49779), places=9)
        ic = column(wb, vals, "PnL", "Interest Credited to Policyholders (m)", FC5)
        self.assertTrue(all(x > 0 for x in ic), ic)
        self.assertIn("Insurer type: life (inferred", "\n".join(a["data_notes"]))

    def test_other_income_split_from_gains(self):
        a = self.res[0]
        # FY2025: investment gains -1,145 + derivative gains -1,939; other income = OtherIncome 2,827
        self.assertEqual(a["hist_3_investment_gains"], -1145.0 - 1939.0)
        self.assertAlmostEqual(a["hist_3_other_income"], 2827.0, places=6)
        self.assertAlmostEqual(a["ins_other_income_pct"],
                               (2526 + 2601 + 2827) / (44283 + 44945 + 49779), places=9)

    def test_distributions_capped_at_profit(self):
        a = self.res[0]
        how = __import__("start_here")._derivations(a["data_notes"])
        # 3 years: dividends 49.05% + buybacks 97.98% of net income -> buybacks scaled down
        self.assertAlmostEqual(a["fin_dividend_payout"] + a["fin_buyback_pct_ni"], 1.0, places=12)
        self.assertAlmostEqual(a["fin_dividend_payout"],
                               (1566 + 1527 + 1509) / (1578 + 4426 + 3379), places=9)
        self.assertIn("DEFAULT CAPPED", how["fin_buyback_pct_ni"])
        self.assertIn("buybacks 97.98%), above the 100% cap", how["fin_buyback_pct_ni"])
        self.assertNotIn("DEFAULT CAPPED", how["fin_dividend_payout"])

    def test_claims_liability_is_labelled_as_tagged(self):
        a = self.res[0]
        notes = "\n".join(a["data_notes"])
        self.assertIn("tagged part of the printed line 'Other policy-related balances'", notes)
        self.assertEqual(a["hist_3_policy_reserves"], 17130 + 208855 + 236857)


class InsurerJapanLife(Checks, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hist = os.path.join(FIX, "insurer_8750_history_trim.json")
        cls.res = build(cls.tmp.name, cls.hist, "daiichi")
        cls.priced = build(cls.tmp.name, cls.hist, "priced",
                           ["--price", str(PRICE_JP), "--price-date", "2026-10-30"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def all_res(self):
        return [self.res, self.priced]

    def test_reserves_roll_forward_with_the_reserve_change(self):
        a, _, wb, vals, _, _ = self.res
        self.assertEqual(a["ins_reserve_rollforward"], 1)
        self.assertAlmostEqual(a["ins_reserve_change_pct"],
                               (1877422 + 341899 + 1814937) / (7526357 + 6799366 + 6944066),
                               places=9)
        res = column(wb, vals, "Balance_Sheet", "Policy Reserves, Total (m)", range(4, 10))
        chg = column(wb, vals, "PnL", "Change in Policy Reserves (m)", FC5)
        for i, c in enumerate(chg):
            self.assertAlmostEqual(res[i + 1] - res[i], c, delta=1e-6)

    def test_other_income_is_projected(self):
        a, _, wb, vals, _, _ = self.res
        # その他経常収益 FY2024-26 / premiums FY2024-26
        self.assertAlmostEqual(a["ins_other_income_pct"],
                               (467827 + 548833 + 628896) / (7526357 + 6799366 + 6944066), places=6)
        self.assertEqual(a["hist_3_investment_gains"], 0.0)
        prem = column(wb, vals, "PnL", "Premiums (m)", FC5)
        oth = column(wb, vals, "PnL", "Other Income (m)", FC5)
        for p, o in zip(prem, oth):
            self.assertAlmostEqual(o, p * a["ins_other_income_pct"], delta=1e-6)
        self.assertEqual(set(column(wb, vals, "PnL", "Investment and Derivative Gains (m)", FC5)), {0.0})

    def test_japanese_lines(self):
        a = self.res[0]
        # net investment income = 資産運用収益 - 資産運用費用 (FY2026)
        self.assertEqual((a["hist_3_investment_income"], a["hist_3_investment_expenses"]),
                         (3735313.0, 867008.0))
        self.assertEqual(a["hist_3_policyholder_dividends"], 107500.0)   # 契約者配当準備金繰入額
        notes = "\n".join(a["data_notes"])
        self.assertIn("Insurer type: life (insurer_type in the history file", notes)
        self.assertIn("below extraordinary items, outside 経常費用", notes)

    def test_priced_multiples(self):
        a, _, wb, vals, _, _ = self.priced
        mcap = valuation(wb, vals, "Market capitalisation")
        self.assertAlmostEqual(mcap, PRICE_JP * a["val_shares_diluted"], places=4)
        self.assertAlmostEqual(valuation(wb, vals, "P/B"), mcap / a["hist_3_equity_parent"], places=9)


class InsurerJapanNonLifeFixture(Checks, unittest.TestCase):
    """Tokio Marine's J-GAAP report alone: 2 actual years, schema copy set to 2."""
    FC = range(4, 9)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hist = os.path.join(FIX, "insurer_8766_history_trim.json")
        with open(os.path.join(ROOT, SCHEMA), encoding="utf-8") as f:
            text = f.read()
        text, k = re.subn(r"(?m)^  years: 3 .*$", "  years: 2", text)
        assert k == 1
        cls.schema = os.path.join(cls.tmp.name, "insurer_2y.yaml")
        with open(cls.schema, "w", encoding="utf-8") as f:
            f.write(text)
        cls.res = build(cls.tmp.name, cls.hist, "tokio", schema=cls.schema)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_three_year_template_refuses_two_years(self):
        out = os.path.join(self.tmp.name, "refused")
        self.assertEqual(history_to_answers.main([self.hist, "--out", out]), 2)
        self.assertFalse(os.path.exists(os.path.join(out, "8766_insurer_filings.yaml")))

    def test_written_premium_label(self):
        a, _, wb, _, _, _ = self.res
        self.assertEqual(wb["PnL"]["C1"].value, "Premiums Written (m)")
        heads = [c.value for c in wb["PnL"][1]]
        self.assertIn("Loss Ratio (Claims / Premiums Written)", heads)
        how = __import__("start_here")._derivations(a["data_notes"])
        self.assertIn("premiums written", how["ins_premium_growth"])
        self.assertIn("WRITTEN", how["ins_premium_growth"])
        notes = "\n".join(a["data_notes"])
        self.assertIn("Japanese non-life premiums are premiums WRITTEN", notes)
        self.assertIn("Insurer type: non-life", notes)
        self.assertIn("TEST FIXTURE", notes)

    def test_non_life_lines(self):
        a = self.res[0]
        M = 1.0
        self.assertEqual(a["hist_2_premiums"], (4824986 + 1049852) * M)
        self.assertEqual(a["hist_2_reserve_change"], (213594 + 392343) * M)
        self.assertEqual(a["hist_2_policy_reserves"], 22031174 * M)
        self.assertEqual(a["ins_reserve_rollforward"], 1)
        self.assertEqual(a["ins_crediting_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()

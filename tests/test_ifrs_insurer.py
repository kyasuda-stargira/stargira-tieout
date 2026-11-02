"""IFRS 17 non-life insurers: routing, maps/ifrs_insurer.yaml, the
tie-out, the 2-actual-year allowance and its limits, and the IFRS 17 workbook.

Offline only. Fixtures are EDINET type-5 CSV zips trimmed to the DEI, the share-count rows,
the non-life summary marker and the elements whose LOCAL name maps/ifrs_insurer.yaml names:
  edinet_8766_S100YLS8_trim.zip  Tokio Marine, years to 2026-03-31 (IFRS 17; prefix E03847)
  edinet_8630_S100YC6Z_trim.zip  SOMPO, years to 2026-03-31 (IFRS 17; DEI code cte)
  edinet_8725_S100YNCJ_trim.zip  MS&AD, years to 2026-03-31 (IFRS 17)
  edinet_8766_S100TOZP_trim.zip  Tokio Marine's earlier J-GAAP report (never mixed in)

Run: python -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fetch_filings  # noqa: E402
import filings_map  # noqa: E402
import history_to_answers  # noqa: E402
from schemas import load_schema  # noqa: E402
from test_bank import column, evaluate, run, valuation  # noqa: E402
# The browser tier (engine/export_json.py, web/) is not in the public copy; its
# checks below are skipped there.
BROWSER = os.path.exists(os.path.join(ROOT, "engine", "export_json.py"))
if BROWSER:
    from test_export_json import NODE  # noqa: E402
    from test_export_json import run as run_plain  # noqa: E402
else:
    NODE = None
from test_filings_fin_jp import FIX, Offline, TOKIO_JG, ZIPS, history, rewrite, set_code, set_value  # noqa: E402

TOKIO = "edinet_8766_S100YLS8_trim.zip"
SOMPO = "edinet_8630_S100YC6Z_trim.zip"
MSAD = "edinet_8725_S100YNCJ_trim.zip"
IFRS_SCHEMA = os.path.join("verticals", "listed_insurer_ifrs.yaml")
M = 1000000
FC5 = range(4, 9)     # 2 actual years in rows 2-3, then 5 forecast years


def v(h, key, fy):
    return h["lines"][key][str(fy)]["val"]


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class Routing(Offline):

    def test_three_groups_route_to_the_ifrs_insurer_chart(self):
        for fx, code in ((TOKIO, "8766"), (SOMPO, "8630"), (MSAD, "8725")):
            h = history([self.fx(fx)], self.tmp, code)
            self.assertEqual((h["sector"], h["insurer_type"], h["basis"]),
                             ("insurer", "non_life", "ifrs17"), code)
            self.assertEqual([y["fy"] for y in h["years"]], [2025, 2026], code)
            self.assertEqual({y["standard"] for y in h["years"]}, {"IFRS"}, code)
            self.assertIn("maps/ifrs_insurer.yaml", h["routing"], code)
            self.assertTrue(h["element_prefixes"]["InsuranceRevenueIFRS"].startswith(
                "jpcrp030000-asr_E"), code)

    def test_sompo_routes_by_its_ifrs17_lines_not_its_code(self):
        h = history([self.fx(SOMPO)], self.tmp, "8630")
        self.assertIn("detected as a financial company", h["routing"])
        self.assertIn("InsuranceRevenueIFRS and InsuranceContractLiabilitiesLiabilitiesIFRS", h["routing"])

    def test_without_ifrs17_lines_refused(self):
        src = self.edited(SOMPO, lambda r: None if r[0].endswith(":InsuranceRevenueIFRS") else r)
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp, "8630")
        self.assertIn("not supported yet", str(e.exception))

    def test_ifrs_securities_code_refused(self):
        src = self.edited(MSAD, set_code("SEC"))
        with self.assertRaises(filings_map.Unsupported):
            history([src], self.tmp, "8725")

    def test_jgaap_report_left_out_never_mixed(self):
        h = history([self.fx(TOKIO), self.fx(TOKIO_JG)], self.tmp, "8766")
        self.assertEqual({y["accn"] for y in h["years"]}, {"S100YLS8"})
        self.assertEqual(h["standard"], "IFRS (jpigp_cor + company elements)")


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class MapValues(Offline):
    """Each value checked against the figure printed in the filing's own statements
    (also re-read from the raw CSV)."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        try:
            cls.h = {code: history([os.path.join(FIX, fx)], tmp, code)
                     for fx, code in ((TOKIO, "8766"), (SOMPO, "8630"), (MSAD, "8725"))}
        finally:
            shutil.rmtree(tmp)

    def test_fy2026_lines(self):
        want = {"8766": (7693560, 6114387, -429502, 25240, 750700, 531255, 33002651, 20219118),
                "8630": (5372921, 4459715, -324914, 344907, 843226, 640086, 18603704, 10737677),
                "8725": (6436026, 5422775, -487807, 261099, 703521, 510612, 29592153, 18604850)}
        keys = ("premiums", "claims", "reinsurance_result", "finance_result", "pretax_income",
                "net_income", "total_assets", "policy_reserves")
        for code, vals in want.items():
            for key, x in zip(keys, vals):
                self.assertEqual(v(self.h[code], key, 2026), x * M, (code, key))

    def test_local_names_that_differ_by_group(self):
        tag = lambda code, key: self.h[code]["lines"][key]["2026"]["tag"]  # noqa: E731
        self.assertEqual(tag("8630", "claims"), "InsuranceServiceExpenseIFRS")
        self.assertEqual(tag("8725", "claims"), "InsuranceServiceExpensesIFRS")
        self.assertEqual(tag("8725", "reinsurance_result"), "CededReinsuranceResultIFRS")
        self.assertEqual(tag("8766", "investment_income"), "InvestmentGainsAndLossesIFRS")
        self.assertEqual(tag("8630", "investment_income"), "InvestmentGainsLossesIFRS")

    def test_msad_net_insurance_finance_expenses_is_filed_negative(self):
        h = self.h["8725"]
        self.assertEqual(h["lines"]["insurance_finance_result"]["2026"]["tag"],
                         "NetInsuranceFinanceExpensesIFRS")
        self.assertEqual(v(h, "insurance_finance_result", 2026), -670831 * M)
        # 931,931 - 670,831 = 261,100: the filed net financial result 261,099, to the ¥1m
        self.assertLessEqual(abs(v(h, "investment_income", 2026) + v(h, "insurance_finance_result", 2026)
                                 - v(h, "finance_result", 2026)), M)

    def test_other_income_and_expenses(self):
        h = self.h["8766"]
        self.assertEqual(v(h, "other_income", 2026), (379540 + 8310) * M)
        self.assertEqual(v(h, "opex", 2026), (723908 + 59559 + 28593) * M)
        self.assertEqual(v(self.h["8630"], "other_net", 2026), -89972 * M)   # SOMPO's own subtotal

    def test_investments_are_the_tagged_lines(self):
        self.assertEqual(v(self.h["8766"], "investments", 2026),
                         (21062726 + 2852633 + 68979) * M)
        self.assertEqual(v(self.h["8630"], "investments", 2026), (12451875 + 545958 + 36065) * M)

    def test_tie_out(self):
        status = {c: [y["status"] for y in h["years"]] for c, h in self.h.items()}
        self.assertEqual(status, {"8766": ["tied", "tied"], "8630": ["tied", "flagged"],
                                  "8725": ["tied", "tied"]})
        flag = self.h["8630"]["flags"]
        self.assertEqual(len(flag), 1)
        self.assertEqual((flag[0]["fy"], flag[0]["check"]), (2026, "pl"))
        self.assertIn("off by 2,000,000 (within the rounding of the 4 filed figures", flag[0]["reason"])
        cash = self.h["8725"]["checks"]["2026"]["cash"]
        self.assertEqual(cash["status"], "pass")
        self.assertEqual(v(self.h["8725"], "other_cash_change", 2026), -7061 * M)

    def test_opening_balance_sheet_where_filed(self):
        for code in ("8766", "8725"):
            op = self.h[code]["opening_balance"]
            self.assertEqual((op["fy"], op["end"], op["balance"]["status"]), (2024, "2024-03-31", "pass"))
        self.assertEqual(self.h["8766"]["opening_balance"]["lines"]["total_assets"]["val"], 31141365 * M)
        self.assertNotIn("opening_balance", self.h["8630"])
        self.assertTrue(any("no balance sheet is filed for the year end before FY2025" in w
                            for w in self.h["8630"]["warnings"]))

    def test_two_year_warning_in_history(self):
        for h in self.h.values():
            self.assertTrue(h["warnings"][0].startswith("ONLY 2 ACTUAL YEARS"))
            self.assertIn("**Only 2 actual years (IFRS 17).**", filings_map.summary_md(h))


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class FlagNotPlug(Offline):

    def test_planted_insurance_revenue_flags_the_service_result(self):
        src = self.edited(TOKIO, set_value("jpcrp030000-asr_E03847-000:InsuranceRevenueIFRS",
                                           "CurrentYearDuration", 5 * M))
        h = history([src], self.tmp, "8766")
        self.assertEqual(v(h, "premiums", 2026), (7693560 + 5) * M)
        self.assertEqual(h["years"][1]["status"], "flagged")
        # the filed lines already miss their subtotal by ¥1m (rounding), so +¥5m reads 6
        self.assertTrue(any(f["check"] == "pl" and "insurance service result off by 6,000,000"
                            in f["reason"] and "within the rounding" not in f["reason"]
                            for f in h["flags"]))


def ifrs_history(tmp, fx, code):
    manifest = fetch_filings.fetch_jp(code, tmp, from_files=[os.path.join(FIX, fx)])
    out = os.path.join(tmp, "hist")
    with contextlib.redirect_stdout(io.StringIO()):
        assert filings_map.main(["jp", manifest, "--ticker", code, "--out", out]) == 0
    return os.path.join(out, "%s_history.json" % code)


def answers_for(hist_path, out, extra=()):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        code = history_to_answers.main([hist_path, "--out", out] + list(extra))
    return code, buf.getvalue()


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class TwoYearAllowance(unittest.TestCase):
    """Only IFRS 17 insurer histories build with 2 actual years."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.saved = fetch_filings._http_get
        fetch_filings._http_get = lambda url, ua: (_ for _ in ()).throw(AssertionError("network"))
        cls.hist = ifrs_history(cls.tmp, TOKIO, "8766")

    @classmethod
    def tearDownClass(cls):
        fetch_filings._http_get = cls.saved
        shutil.rmtree(cls.tmp)

    def test_ifrs_insurer_builds_with_two_years_and_says_so(self):
        out = os.path.join(self.tmp, "ok")
        code, text = answers_for(self.hist, out)
        self.assertEqual(code, 0, text)
        self.assertIn("listed_insurer_ifrs.yaml", text)
        self.assertIn("WARNING: ONLY 2 ACTUAL YEARS (IFRS 17)", text)
        path = os.path.join(out, "8766_insurer_filings.yaml")
        with open(path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("# WARNING: ONLY 2 ACTUAL YEARS (IFRS 17)", body)
        self.assertIn("verticals/listed_insurer_ifrs.yaml", body.splitlines()[2])
        a = yaml.safe_load(body)
        self.assertEqual(sorted({int(k.split("_")[1]) for k in a
                                 if k.startswith("hist_") and k.split("_")[1].isdigit()}), [1, 2])
        # R20: the IFRS opening balance sheet (FY2024 year end) is the first year's opening.
        with open(self.hist, encoding="utf-8") as f:
            op = json.load(f)["opening_balance"]
        self.assertEqual(a["hist_open_fy"], op["fy"])
        self.assertEqual(a["hist_open_investments"], op["lines"]["investments"]["val"] / 1e6)
        self.assertEqual(a["hist_open_equity_parent"], op["lines"]["equity_parent"]["val"] / 1e6)
        self.assertEqual(a["hist_open_policyholder_account_balances"], 0.0)
        notes = a["data_notes"]
        self.assertEqual(notes[0], "## Warning: only 2 actual years (IFRS 17)")
        derivs = [n for n in notes if n.startswith(("ins_loss_ratio:", "g_tax_rate:",
                                                     "ins_investment_yield:"))]
        self.assertEqual(len(derivs), 3)
        self.assertTrue(all("2-year" in n for n in derivs), derivs)
        self.assertIn("1-year CAGR", [n for n in notes if n.startswith("ins_premium_growth:")][0])
        self.assertIn("IFRS opening balance sheet", [n for n in notes
                                                    if n.startswith("ins_investment_yield:")][0])

    def test_jgaap_two_year_insurer_still_refused(self):
        code, text = answers_for(os.path.join(FIX, "insurer_8766_history_trim.json"),
                                 os.path.join(self.tmp, "jg"))
        self.assertEqual(code, 2, text)
        self.assertIn("only 2 fiscal year(s)", text)
        code, text = answers_for(os.path.join(FIX, "insurer_8766_history_trim.json"),
                                 os.path.join(self.tmp, "jg2"), ["--schema", IFRS_SCHEMA])
        self.assertEqual(code, 2, text)
        self.assertIn("only for IFRS 17 insurer histories", text)

    def test_two_year_bank_still_refused(self):
        with open(os.path.join(FIX, "bank_8306_history_trim.json"), encoding="utf-8") as f:
            h = json.load(f)
        keep = [str(y["fy"]) for y in h["years"][-2:]]
        h["years"] = h["years"][-2:]
        h["lines"] = {k: {fy: x for fy, x in by.items() if fy in keep} for k, by in h["lines"].items()}
        path = os.path.join(self.tmp, "bank2.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(h, f)
        code, text = answers_for(path, os.path.join(self.tmp, "b2"))
        self.assertEqual(code, 2, text)
        self.assertIn("the bank model shows 3", text)
        code, text = answers_for(path, os.path.join(self.tmp, "b3"), ["--schema", IFRS_SCHEMA])
        self.assertEqual(code, 2, text)

    def test_ifrs_history_never_on_the_three_year_schema(self):
        code, text = answers_for(self.hist, os.path.join(self.tmp, "x3"),
                                 ["--schema", os.path.join("verticals", "listed_insurer.yaml")])
        self.assertEqual(code, 2, text)
        self.assertIn("not the IFRS 17 template", text)

    def test_mixed_standards_refused(self):
        with open(self.hist, encoding="utf-8") as f:
            h = json.load(f)
        h["years"][0]["standard"] = "Japan GAAP"
        path = os.path.join(self.tmp, "mixed.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(h, f)
        code, text = answers_for(path, os.path.join(self.tmp, "mx"))
        self.assertEqual(code, 2, text)
        self.assertIn("mixes accounting standards (FY2025 Japan GAAP, FY2026 IFRS)", text)

    def test_build_refuses_answers_for_another_year_count(self):
        out = os.path.join(self.tmp, "ok2")
        self.assertEqual(answers_for(self.hist, out)[0], 0)
        ans = os.path.join(out, "8766_insurer_filings.yaml")
        code, text = run(["engine/build_model.py", os.path.join("verticals", "listed_insurer.yaml"),
                          ans, os.path.join(self.tmp, "wrong.xlsx")])
        self.assertEqual(code, 2, text)
        self.assertIn("REFUSED: the answers file has actual years [1, 2] but the schema", text)


class Schema(unittest.TestCase):

    def test_ifrs_schema_extends_the_insurer_schema(self):
        base = load_schema(os.path.join(ROOT, "verticals", "listed_insurer.yaml"))
        s = load_schema(os.path.join(ROOT, IFRS_SCHEMA))
        self.assertEqual((s["history"]["years"], base["history"]["years"]), (2, 3))
        self.assertEqual(s["meta"]["basis"], "ifrs17")
        ids = [a["id"] for a in s["assumptions"]]
        self.assertEqual(ids[ids.index("ins_loss_ratio") + 1], "ins_reinsurance_pct")
        self.assertEqual(ids, [a["id"] for a in base["assumptions"]][:ids.index("ins_reinsurance_pct")]
                         + ["ins_reinsurance_pct"]
                         + [a["id"] for a in base["assumptions"]][ids.index("ins_reinsurance_pct"):])
        fields = [f["id"] for f in s["history"]["fields"]]
        self.assertEqual(fields[fields.index("claims") + 1], "reinsurance_expense")
        lab = {f["id"]: f["label"] for f in s["history"]["fields"]}
        self.assertEqual(lab["premiums"], "Insurance revenue (IFRS 17)")
        self.assertIn("incl. the CSM", lab["policy_reserves"])
        # untouched entries keep the base wording
        self.assertEqual(lab["tax"], {f["id"]: f["label"] for f in base["history"]["fields"]}["tax"])


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class Workbook(unittest.TestCase):
    """All three groups: build, verify, labels, RI recompute, export and the browser."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.saved = fetch_filings._http_get
        fetch_filings._http_get = lambda url, ua: (_ for _ in ()).throw(AssertionError("network"))
        from openpyxl import load_workbook
        cls.res = {}
        for fx, code in ((TOKIO, "8766"), (SOMPO, "8630"), (MSAD, "8725")):
            out = os.path.join(cls.tmp, code)
            hist = ifrs_history(out, fx, code)
            assert answers_for(hist, out)[0] == 0
            ans = os.path.join(out, "%s_insurer_filings.yaml" % code)
            xlsx = os.path.join(out, "model.xlsx")
            c, text = run(["engine/build_model.py", IFRS_SCHEMA, ans, xlsx])
            assert c == 0, text
            vcode, vtext = run(["engine/verify_model.py", IFRS_SCHEMA, xlsx])
            js = os.path.join(out, "model.json")
            if BROWSER:
                c, text = run(["engine/export_json.py", IFRS_SCHEMA, xlsx, js])
                assert c == 0, text
            with open(ans, encoding="utf-8") as f:
                a = yaml.safe_load(f)
            cls.res[code] = (a, xlsx, load_workbook(xlsx), evaluate(xlsx), vcode, vtext, js)

    @classmethod
    def tearDownClass(cls):
        fetch_filings._http_get = cls.saved
        shutil.rmtree(cls.tmp)

    def test_verify_model_passes(self):
        for code, r in self.res.items():
            self.assertEqual(r[4], 0, r[5])
            self.assertIn("Hardcoded numbers in calc sheets: 0", r[5])
            self.assertIn("PASS  Balance_Sheet: BS Check (m)", r[5])
            self.assertIn("PASS  Investment_Flows: CF Check (m)", r[5])

    def test_forecast_checks_are_zero(self):
        for code, (_, _, wb, vals, *_rest) in self.res.items():
            bs = column(wb, vals, "Balance_Sheet", "BS Check (m)", FC5)
            cf = column(wb, vals, "Investment_Flows", "CF Check (m)", FC5)
            self.assertTrue(all(abs(x) < 1e-6 for x in bs + cf), (code, bs, cf))

    def test_ifrs17_labels(self):
        _, _, wb, *_ = self.res["8766"]
        pnl = [c.value for c in wb["PnL"][1]]
        for h in ("Insurance Revenue (m)", "Insurance Service Expenses (m)",
                  "Net Expense from Reinsurance Contracts Held (m)", "Net Financial Result (m)",
                  "Actual: Net Insurance Finance Expenses (m)",
                  "Combined Ratio (All Underwriting Costs / Insurance Revenue)"):
            self.assertIn(h, pnl)
        self.assertNotIn("Premiums (m)", pnl)
        self.assertIn("Insurance Contract Liabilities incl. CSM (m)", [c.value for c in wb["Balance_Sheet"][1]])
        dash = [c.value for c in wb["Dashboard"][1]]
        self.assertIn("Insurance Revenue (m/yr)", dash)
        self.assertIn("Net financial result (m/yr)", dash)
        start = " ".join(str(c.value) for row in wb["Start_Here"].iter_rows() for c in row if c.value)
        self.assertIn("Only 2 actual years (IFRS 17)", start)
        self.assertIn("Charts of insurance revenue", start)
        self.assertIn("last two years", start)
        notes = " ".join(str(c.value) for row in wb["Data_Notes"].iter_rows() for c in row if c.value)
        self.assertIn("include the contractual service margin (CSM", notes)
        self.assertIn("not modelled separately", notes)
        self.assertIn("Embedded value is not modelled", notes)
        self.assertIn("ONLY 2 ACTUAL YEARS (IFRS 17)", notes)
        assumptions = {r[2]: r[1] for r in wb["Assumptions"].iter_rows(values_only=True) if r[2]}
        self.assertEqual(assumptions["hist_2_premiums"], "Actual year 2 of 2: Insurance revenue (IFRS 17)")

    def test_reinsurance_row_feeds_the_operating_result(self):
        a, _, wb, vals, *_ = self.res["8725"]
        self.assertAlmostEqual(a["hist_2_reinsurance_expense"], 487807.0)
        prem = column(wb, vals, "PnL", "Insurance Revenue (m)", FC5)
        ri = column(wb, vals, "PnL", "Net Expense from Reinsurance Contracts Held (m)", FC5)
        for p, x in zip(prem, ri):
            self.assertAlmostEqual(x, p * a["ins_reinsurance_pct"], delta=1e-6)
        # actual years: the operating result + other items = the filed pre-tax profit
        op = column(wb, vals, "PnL", "Operating Result (m)", [2, 3])
        oth = column(wb, vals, "PnL", "Other Items in Pre-tax (m)", [2, 3])
        self.assertEqual([round(o + x, 6) for o, x in zip(op, oth)], [458500.0, 703521.0])
        self.assertTrue(all(abs(x) <= 2 for x in oth), oth)    # only the ¥1m rounding

    def test_residual_income_matches_an_independent_recompute(self):
        for code, (a, _, wb, vals, *_rest) in self.res.items():
            n = 2
            h = lambda k: a["hist_%d_%s" % (n, k)]  # noqa: E731
            prem, inv, cash = h("premiums"), h("investments"), h("cash")
            other_a = h("total_assets") - cash - inv
            res, debt = h("policy_reserves"), h("debt")
            other_l = h("total_liabilities") - res - debt
            eq, nci = h("equity_parent"), h("total_equity") - h("equity_parent")
            coe, g, years = a["val_cost_of_equity"], a["val_terminal_growth"], a["g_forecast_years"]
            book, pv, ri = eq, 0.0, None
            for t in range(1, years + 1):
                p1 = prem * (1 + a["ins_premium_growth"])
                pre = (p1 + inv * a["ins_investment_yield"] + p1 * a["ins_other_income_pct"]
                       - p1 * (a["ins_loss_ratio"] + a["ins_reinsurance_pct"] + a["ins_expense_ratio"]))
                ni = (pre - max(0.0, pre) * a["g_tax_rate"]) * (1 - a["ins_nci_share"])
                ri = ni - coe * eq
                pv += ri / (1 + coe) ** t
                res, prem = p1 * a["ins_reserves_pct_premiums"], p1
                eq += ni - (a["fin_dividend_payout"] + a["fin_buyback_pct_ni"]) * max(0.0, ni)
                inv = res + debt + other_l + eq + nci - cash - other_a
            value = book + pv + ri * (1 + g) / (coe - g) / (1 + coe) ** years
            self.assertAlmostEqual(valuation(wb, vals, "Residual-income equity value"), value,
                                   delta=1e-6, msg=code)
            self.assertEqual(valuation(wb, vals, "P/B"), "enter a price", code)

    @unittest.skipUnless(BROWSER, "browser tier not in this copy")
    def test_export_has_the_reinsurance_row(self):
        for code, r in self.res.items():
            with open(r[6], encoding="utf-8") as f:
                d = json.load(f)
            self.assertEqual(d["history"]["years"], 2)
            self.assertIn("reinsurance", d["reference"]["years"][0]["PnL"])
            self.assertIn("ins_reinsurance_pct", [x["id"] for x in d["inputs"]])

    @unittest.skipUnless(BROWSER and NODE, "browser tier not in this copy, or node not installed")
    def test_javascript_model_matches_workbook(self):
        paths = [r[6] for r in self.res.values()]
        code, text = run_plain([NODE, "web/test_model.mjs"] + paths)
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count("check against Excel: PASS"), len(paths), text)
        self.assertNotIn(": false", text)


if __name__ == "__main__":
    unittest.main()

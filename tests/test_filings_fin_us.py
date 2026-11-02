"""US banks and insurers: routing, the sector maps and their tie-out.

Offline only. Fixtures in tests/fixtures/ are trimmed SEC companyfacts files
(10-K facts for the tags the sector maps use, full-year periods only):
  fin_jpm_trim.json  JPMorgan Chase, FY2020-FY2025 (bank)
  fin_gs_trim.json   Goldman Sachs, FY2023-FY2025 (broker-dealer holding company;
                     refused until a broker-dealer template exists)
  fin_trv_trim.json  Travelers, FY2023-FY2025 (non-life insurer)
  fin_all_trim.json  Allstate, FY2023-FY2025 (non-life insurer with no claims tag in
                     companyfacts: claims are derived)

Run: python -m unittest discover -s tests
"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
FIX = os.path.join(ROOT, "tests", "fixtures")

import fetch_filings  # noqa: E402
import filings_map  # noqa: E402
import history_to_answers  # noqa: E402

CMAP = filings_map.load_map()
BANK = filings_map.load_map(os.path.join(ROOT, "engine", "maps", "us_gaap_bank.yaml"))
INSURER = filings_map.load_map(os.path.join(ROOT, "engine", "maps", "us_gaap_insurer.yaml"))
M = 1000000


def load(name):
    return filings_map.load_facts(os.path.join(FIX, name))


def shutil_copy(src, dest):
    with open(src, "rb") as a, open(dest, "wb") as b:
        b.write(a.read())


def build(name, ticker, cmap=CMAP):
    return filings_map.build_history(load(name), ticker, cmap)


class Routing(unittest.TestCase):

    def test_bank(self):
        h = build("fin_jpm_trim.json", "JPM")
        self.assertEqual(h["sector"], "bank")
        self.assertIn("bank chart", h["routing"])
        self.assertIn("total_revenue", h["lines"])
        self.assertNotIn("revenue", h["lines"])            # not the corporate chart

    def test_insurer(self):
        h = build("fin_trv_trim.json", "TRV")
        self.assertEqual(h["sector"], "insurer")
        self.assertIn("PremiumsEarnedNet", h["routing"])
        self.assertIn("policy_reserves", h["lines"])

    # Securities firms: refused until a broker-dealer template exists.
    def test_broker_dealer_refused_by_sic(self):
        data = load("fin_gs_trim.json")
        data["sic"] = {"cik": data["cik"], "sic": "6211",
                       "sicDescription": "Security Brokers, Dealers & Flotation Companies"}
        with self.assertRaises(filings_map.Unsupported) as e:
            filings_map.build_history(data, "GS", CMAP)
        self.assertEqual(str(e.exception), (
            "not supported yet: this is a securities firm (SEC SIC code 6211, Security Brokers, "
            "Dealers & Flotation Companies). Tie-Out has no broker-dealer template yet; the bank "
            "template's drivers do not fit it."))

    def test_broker_dealer_refused_by_balance_sheet_without_sic(self):
        # FY2025 10-K: deposits 501,422 and loans 237,734 of total assets 1,809,320 ($m).
        with self.assertRaises(filings_map.Unsupported) as e:
            build("fin_gs_trim.json", "GS")
        self.assertEqual(str(e.exception), (
            "not supported yet: this is a securities firm (no SEC SIC code saved; in FY2025 its "
            "deposits are 28% of total assets and its loans 13%, below the 40% and 25% that mark "
            "a bank). Tie-Out has no broker-dealer template yet; the bank template's drivers do "
            "not fit it."))

    def test_bank_sic_decides_over_the_balance_sheet(self):
        data = load("fin_gs_trim.json")
        data["sic"] = {"cik": data["cik"], "sic": "6021", "sicDescription": "National Commercial Banks"}
        self.assertEqual(filings_map.build_history(data, "GS", CMAP)["sector"], "bank")

    def test_bank_is_never_refused_as_a_broker(self):
        for sic in (None, {"sic": "6021", "sicDescription": "National Commercial Banks"}):
            data = load("fin_jpm_trim.json")
            if sic:
                data["sic"] = dict(sic, cik=data["cik"])
            self.assertEqual(filings_map.build_history(data, "JPM", CMAP)["sector"], "bank")

    def test_fallback_thresholds(self):
        rule = CMAP["unsupported"]["broker_dealer"]

        def hist(deposits, loans, assets=100):
            lines = {"total_assets": {"2025": {"val": assets}}, "deposits": {"2025": {"val": deposits}}}
            if loans is not None:
                lines["loans"] = {"2025": {"val": loans}}
            return {"years": [{"fy": 2025}], "lines": lines}
        reason = filings_map.broker_dealer_reason
        self.assertIsNotNone(reason(None, hist(30, 15), CMAP))
        self.assertIsNotNone(reason(None, hist(30, None), CMAP))   # loans untagged
        self.assertIsNone(reason(None, hist(52, 26), CMAP))        # Citi-like
        self.assertIsNone(reason(None, hist(30, 30), CMAP))        # low deposits, a real loan book
        self.assertIsNone(reason(None, hist(45, 10), CMAP))
        self.assertEqual((rule["max_deposits_to_assets"], rule["max_loans_to_assets"]), (0.40, 0.25))

    def test_cli_reads_the_sic_file_beside_companyfacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            facts = os.path.join(tmp, "GS_companyfacts.json")
            shutil_copy(os.path.join(FIX, "fin_gs_trim.json"), facts)
            cik = load("fin_gs_trim.json")["cik"]
            for sic_cik, expect in ((cik, "SEC SIC code 6211"), (cik + 1, "no SEC SIC code saved")):
                with open(os.path.join(tmp, "GS_sic.json"), "w") as f:
                    json.dump({"cik": sic_cik, "sic": "6211",
                               "sicDescription": "Security Brokers, Dealers & Flotation Companies"}, f)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = filings_map.main(["us", facts, "--ticker", "GS", "--out", tmp])
                self.assertEqual(code, 2)
                self.assertIn("GS: not supported yet: this is a securities firm (%s" % expect,
                              buf.getvalue())     # another CIK's SIC file is not used
            self.assertFalse(os.path.exists(os.path.join(tmp, "GS_history.json")))

    def test_fetch_saves_the_sic_beside_companyfacts(self):
        facts = load("fin_gs_trim.json")
        answers = {fetch_filings.SEC_TICKERS_URL: {"0": {"cik_str": 886982, "ticker": "GS",
                                                         "title": "GOLDMAN SACHS GROUP INC"}},
                   fetch_filings.SEC_FACTS_URL.format(cik=886982): facts,
                   fetch_filings.SEC_SUBMISSIONS_URL.format(cik=886982): {
                       "name": "GOLDMAN SACHS GROUP INC", "sic": "6211",
                       "sicDescription": "Security Brokers, Dealers & Flotation Companies"}}
        saved, gap = fetch_filings._http_get, fetch_filings.MIN_GAP_SECONDS
        seen = []
        fetch_filings._http_get = lambda url, ua: seen.append(url) or json.dumps(answers[url]).encode()
        fetch_filings.MIN_GAP_SECONDS = 0
        try:
            with tempfile.TemporaryDirectory() as out, contextlib.redirect_stdout(io.StringIO()):
                fetch_filings.fetch_us("GS", out, user_agent="Example Ltd ops@example.com")
                with open(os.path.join(out, "GS_sic.json")) as f:
                    sic = json.load(f)
        finally:
            fetch_filings._http_get, fetch_filings.MIN_GAP_SECONDS = saved, gap
        self.assertEqual(seen, list(answers))                 # one submissions call, after the facts
        self.assertEqual((sic["cik"], sic["sic"]), (886982, "6211"))

    def test_from_file_copies_the_sic_file_offline(self):
        saved = fetch_filings._http_get
        fetch_filings._http_get = lambda url, ua: (_ for _ in ()).throw(AssertionError("network"))
        try:
            with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
                facts = os.path.join(src, "GS_companyfacts.json")
                shutil_copy(os.path.join(FIX, "fin_gs_trim.json"), facts)
                self.assertTrue(fetch_filings.fetch_us("GS", out, from_file=facts))
                self.assertFalse(os.path.exists(os.path.join(out, "GS_sic.json")))  # SIC optional
                with open(os.path.join(src, "GS_sic.json"), "w") as f:
                    json.dump({"cik": 886982, "sic": "6211"}, f)
                fetch_filings.fetch_us("GS", out, from_file=facts)
                self.assertTrue(os.path.exists(os.path.join(out, "GS_sic.json")))
        finally:
            fetch_filings._http_get = saved

    def test_neither_is_refused(self):
        data = load("fin_gs_trim.json")
        del data["facts"]["us-gaap"]["Deposits"]
        with self.assertRaises(filings_map.Unsupported) as e:
            filings_map.build_history(data, "GS", CMAP)
        self.assertIn("not supported yet", str(e.exception))
        self.assertIn("neither the bank nor the insurer chart fits", str(e.exception))

    def test_both_is_refused_not_guessed(self):
        data = load("fin_gs_trim.json")
        data["facts"]["us-gaap"]["PremiumsEarnedNet"] = copy.deepcopy(
            load("fin_trv_trim.json")["facts"]["us-gaap"]["PremiumsEarnedNet"])
        with self.assertRaises(filings_map.Unsupported) as e:
            filings_map.build_history(data, "GS", CMAP)
        self.assertIn("not routed rather than guessed: no SEC SIC code is saved", str(e.exception))
        self.assertIn("would settle it", str(e.exception))

    # Both lines filed: a saved SIC code breaks the tie (Citi, C, SIC 6021).
    def both(self, sic):
        data = load("fin_jpm_trim.json")
        data["facts"]["us-gaap"]["PremiumsEarnedNet"] = copy.deepcopy(
            load("fin_trv_trim.json")["facts"]["us-gaap"]["PremiumsEarnedNet"])
        if sic:
            data["sic"] = {"cik": data["cik"], "sic": sic[0], "sicDescription": sic[1]}
        return data

    def test_both_with_bank_sic_routes_as_bank(self):
        for code in ("6021", "6022", "6029", "6035", "6036"):
            h = filings_map.build_history(self.both((code, "Banks")), "C", CMAP)
            self.assertEqual(h["sector"], "bank")
            self.assertIn("SEC SIC code %s (Banks) says bank" % code, h["routing"])

    def test_both_with_insurer_sic_routes_as_insurer(self):
        h = filings_map.build_history(self.both(("6311", "Life Insurance")), "X", CMAP)
        self.assertEqual(h["sector"], "insurer")
        self.assertIn("SEC SIC code 6311 (Life Insurance) says insurer", h["routing"])

    def test_both_without_sic_or_with_another_code_is_refused(self):
        for sic, why in ((None, "no SEC SIC code is saved"),
                         (("6199", "Finance Services"), "its SEC SIC code 6199 does not name"),
                         (("6211", "Brokers"), "its SEC SIC code 6211 does not name")):
            with self.assertRaises(filings_map.Unsupported) as e:
                filings_map.build_history(self.both(sic), "C", CMAP)
            self.assertIn("not routed rather than guessed: " + why, str(e.exception))
            self.assertIn("A bank (6021, 6022, 6029, 6035, 6036) or insurer (63xx) SIC code",
                          str(e.exception))

    def test_cli_writes_sector(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = filings_map.main(["us", os.path.join(FIX, "fin_trv_trim.json"),
                                     "--ticker", "TRV", "--out", tmp])
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "TRV_history.md")) as f:
                self.assertIn("Sector: insurer", f.read())


class BankMap(unittest.TestCase):

    def setUp(self):
        self.h = build("fin_jpm_trim.json", "JPM")

    def test_cash_is_two_parts(self):
        cash = self.h["lines"]["cash"]["2025"]
        self.assertEqual(cash["val"], (21742 + 321596) * M)
        self.assertEqual(cash["tag"], "CashAndDueFromBanks + InterestBearingDepositsInBanks")
        c = self.h["checks"]["2025"]["cash"]
        self.assertEqual(c["status"], "pass")
        self.assertEqual(c["tests"][-1]["left"], 343338 * M)    # cash-flow closing cash

    def test_cash_check_fails_on_one_part_only(self):
        data = load("fin_jpm_trim.json")
        del data["facts"]["us-gaap"]["InterestBearingDepositsInBanks"]
        h = filings_map.build_history(data, "JPM", CMAP)
        c = h["checks"]["2025"]["cash"]
        self.assertEqual(c["status"], "flag")
        self.assertIn("321,596,000,000", c["reason"])

    def test_interest_expense_falls_back_before_2022(self):
        ie = self.h["lines"]["interest_expense"]
        self.assertEqual(ie["2021"]["tag"], "InterestExpense")
        self.assertEqual(ie["2021"]["val"], 5553 * M)
        self.assertEqual(ie["2022"]["tag"], "InterestExpenseOperating")
        self.assertEqual(ie["2025"]["val"], 97898 * M)

    def test_loans_are_the_balance_sheet_line(self):
        # 10-K FY2025 balance sheet: "Loans, net of allowance for loan losses" 1,467,664
        self.assertEqual(self.h["lines"]["loans"]["2025"]["val"], 1467664 * M)

    def test_net_revenue_derived_and_pl_foots(self):
        rev = self.h["lines"]["total_revenue"]["2025"]
        self.assertEqual(rev["val"], (95443 + 87004) * M)
        self.assertTrue(rev["tag"].startswith("derived: "))
        pl = self.h["checks"]["2025"]["pl"]
        self.assertEqual(pl["status"], "pass")
        self.assertTrue(pl["tests"][0]["ok"])                   # 182,447 - 14,212 - 95,640

    def test_all_years_tie(self):
        self.assertEqual([y["status"] for y in self.h["years"]], ["tied"] * 6)


class FlagNotPlug(unittest.TestCase):

    def test_planted_balance_mismatch(self):
        data = load("fin_jpm_trim.json")
        for f in data["facts"]["us-gaap"]["Assets"]["units"]["USD"]:
            if f["end"] == "2024-12-31":
                f["val"] += 7 * M
        h = filings_map.build_history(data, "JPM", CMAP)
        y = [y for y in h["years"] if y["fy"] == 2024][0]
        self.assertEqual(y["status"], "flagged")
        self.assertIn("off by 7,000,000", h["checks"]["2024"]["balance"]["reason"])
        self.assertEqual(h["lines"]["total_assets"]["2024"]["val"], 4002814 * M + 7 * M)
        self.assertEqual(set(h["lines"]),                       # no plug line appeared
                         {k for k, v in BANK["lines"].items() if not v.get("omit_if_empty")})

    def test_planted_pl_mismatch_flags_the_year(self):
        data = load("fin_trv_trim.json")
        for f in data["facts"]["us-gaap"]["BenefitsLossesAndExpenses"]["units"]["USD"]:
            if f["end"] == "2025-12-31":
                f["val"] += 50 * M
        h = filings_map.build_history(data, "TRV", CMAP)
        self.assertEqual(h["checks"]["2025"]["pl"]["status"], "flag")
        self.assertIn("off by -50,000,000", h["checks"]["2025"]["pl"]["reason"])
        self.assertEqual(h["years"][-1]["status"], "flagged")
        self.assertTrue(any(f["fy"] == 2025 and f["check"] == "pl" for f in h["flags"]))

    def test_disposal_group_cash_used_only_when_it_ties(self):
        h = build("fin_trv_trim.json", "TRV")
        c = h["checks"]["2025"]["cash"]
        self.assertEqual(c["status"], "pass")
        self.assertIn("disposal group", c["note"])
        data = load("fin_trv_trim.json")
        for f in data["facts"]["us-gaap"]["DisposalGroupIncludingDiscontinuedOperationCash"][
                "units"]["USD"]:
            f["val"] += 5 * M
        h = filings_map.build_history(data, "TRV", CMAP)
        c = h["checks"]["2025"]["cash"]
        self.assertEqual(c["status"], "flag")
        self.assertIn("off by 171,000,000", c["reason"])


class InsurerMap(unittest.TestCase):

    def test_reserves_and_opex(self):
        h = build("fin_trv_trim.json", "TRV")
        self.assertEqual(h["lines"]["policy_reserves"]["2025"]["val"], (65737 + 22431) * M)
        self.assertEqual(h["lines"]["opex"]["2025"]["val"], (41032 - 27221) * M)
        self.assertEqual([y["status"] for y in h["years"]], ["tied"] * 3)


    def test_tagged_claims_never_take_the_fallback(self):
        h = build("fin_trv_trim.json", "TRV")
        self.assertEqual(h["lines"]["claims"]["2025"]["tag"], "PolicyholderBenefitsAndClaimsIncurredNet")
        self.assertNotIn("derived_from", h["lines"]["claims"]["2025"])
        for helper in ("other_cost_and_expense", "interest_expense", "gain_on_disposal"):
            self.assertNotIn(helper, h["lines"])        # TRV's history is unchanged


class InsurerDerivedClaims(unittest.TestCase):
    """Allstate files P&C claims only with a ProductOrServiceAxis member and A&H benefits
    under its own element, so companyfacts has no claims tag. FY2025 10-K
    (0000899051-26-000031): 56,132 - 8,389 - 8,977 - 399 = 38,367 = claims 37,454 + A&H
    benefits 656 + pension remeasurement (35) + restructuring 61 + intangibles 231."""

    def setUp(self):
        self.h = build("fin_all_trim.json", "ALL")

    def test_claims_derived_to_the_traced_figure(self):
        c = self.h["lines"]["claims"]["2025"]
        self.assertEqual(c["val"], 38367 * M)
        self.assertEqual(c["val"], (37454 + 656 - 35 + 61 + 231) * M)
        self.assertEqual(c["derived_from"], {"total_expenses": 56132 * M,
                                             "acquisition_costs": 8389 * M,
                                             "other_cost_and_expense": 8977 * M,
                                             "interest_expense": 399 * M})
        self.assertIn("DERIVED", c["label"])
        self.assertIn("small other expense lines", c["label"])

    def test_opex_does_not_double_count(self):
        L = self.h["lines"]
        for fy in ("2023", "2024", "2025"):
            parts = sum(L[k][fy]["val"] for k in ("acquisition_costs", "other_cost_and_expense",
                                                  "interest_expense"))
            self.assertEqual(L["opex"][fy]["val"], parts)
            self.assertEqual(L["claims"][fy]["val"] + L["opex"][fy]["val"],
                             L["total_expenses"][fy]["val"])

    def test_note_names_the_tags_companyfacts_lacks(self):
        w = [x for x in self.h["warnings"] if x.startswith("line claims:")]
        self.assertEqual(len(w), 1)
        self.assertIn("PropertyLiabilityAndCasualtyInsuranceSegmentMember", w[0])
        self.assertIn("all:AccidentAndHealthInsuranceContractBenefitsNetOfReinsuranceRecoveries", w[0])

    def test_gain_on_disposal_ties_pre_tax(self):
        pl = self.h["checks"]["2025"]["pl"]
        self.assertEqual(pl["status"], "pass")
        t = pl["tests"][0]
        self.assertIn("+ gain on disposal of operations", t["test"])
        self.assertEqual(t["right"], 13156 * M)          # 67,685 - 56,132 + 1,603
        self.assertEqual([y["status"] for y in self.h["years"]], ["tied"] * 3)

    def test_without_the_gain_line_fy2025_flags(self):
        data = load("fin_all_trim.json")
        del data["facts"]["us-gaap"]["DisposalGroupNotDiscontinuedOperationGainLossOnDisposal"]
        h = filings_map.build_history(data, "ALL", CMAP)
        self.assertEqual(h["checks"]["2025"]["pl"]["status"], "flag")
        self.assertIn("off by -1,603,000,000", h["checks"]["2025"]["pl"]["reason"])


def replace_tag(data, old, parts):
    """Move tag `old`'s 10-K facts to the tags in `parts` ({tag: share of the value,
    or a function of the fact}), and delete `old`: a CECL-style filing with no total."""
    g = data["facts"]["us-gaap"]
    facts = g.pop(old)["units"]["USD"]
    for tag, f in parts.items():
        g[tag] = {"units": {"USD": [dict(x, val=f(x)) for x in facts]}}


class BankProvisionParts(unittest.TestCase):
    """Citi, PNC and BAC tag no total provision after CECL; the bank map sums the
    standard parts and keeps the sum only where the pre-tax footing then ties."""

    LOANS = "FinancingReceivableExcludingAccruedInterestCreditLossExpenseReversal"
    UNFUNDED = "OffBalanceSheetCreditLossLiabilityCreditLossExpenseReversal"

    def test_parts_sum_when_no_total_is_tagged(self):
        data = load("fin_jpm_trim.json")
        replace_tag(data, "ProvisionForLoanLeaseAndOtherLosses",
                    {self.LOANS: lambda f: f["val"] - 80 * M, self.UNFUNDED: lambda f: 80 * M})
        h = filings_map.build_history(data, "JPM", CMAP)
        p = h["lines"]["provision"]["2025"]
        self.assertEqual(p["val"], 14212 * M)
        self.assertEqual(p["tag"], self.LOANS + " + " + self.UNFUNDED)
        self.assertEqual([y["status"] for y in h["years"]], ["tied"] * 6)

    def test_parts_that_do_not_foot_are_dropped_and_flagged(self):
        # One part of the total is filed only under the company's own tag (as at PNC).
        data = load("fin_jpm_trim.json")
        replace_tag(data, "ProvisionForLoanLeaseAndOtherLosses",
                    {self.LOANS: lambda f: f["val"] - 22 * M})
        h = filings_map.build_history(data, "JPM", CMAP)
        self.assertNotIn("2025", h["lines"]["provision"])
        pl = h["checks"]["2025"]["pl"]
        self.assertEqual(pl["status"], "flag")
        self.assertIn("provision not in the data", pl["reason"])
        self.assertIn("the tagged parts (%s) add to 14,190,000,000" % self.LOANS, pl["reason"])
        self.assertIn("off by 22,000,000", pl["reason"])

    def test_a_tagged_total_wins_over_the_parts(self):
        data = load("fin_jpm_trim.json")
        data["facts"]["us-gaap"][self.LOANS] = copy.deepcopy(
            data["facts"]["us-gaap"]["ProvisionForLoanLeaseAndOtherLosses"])
        for f in data["facts"]["us-gaap"][self.LOANS]["units"]["USD"]:
            f["val"] -= 5 * M
        h = filings_map.build_history(data, "JPM", CMAP)
        self.assertEqual(h["lines"]["provision"]["2025"]["tag"], "ProvisionForLoanLeaseAndOtherLosses")


class BankGainInsideRevenue(unittest.TestCase):
    """Citi's gain on disposal sits inside its net revenue: the bank footing adds the
    gain only when that ties, and leaves it out (saying so) when only that ties."""

    GAIN = "DisposalGroupNotDiscontinuedOperationGainLossOnDisposal"

    def plant(self, gain_m, pretax_up_m):
        data = load("fin_jpm_trim.json")
        g = data["facts"]["us-gaap"]
        g[self.GAIN] = {"units": {"USD": [
            dict(f, val=gain_m * M) for f in g["ProvisionForLoanLeaseAndOtherLosses"]["units"]["USD"]
            if f["end"] == "2025-12-31"]}}
        for f in g["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"]["units"]["USD"]:
            if f["end"] == "2025-12-31":
                f["val"] += pretax_up_m * M
        return filings_map.build_history(data, "JPM", CMAP)["checks"]["2025"]["pl"]

    def test_gain_inside_revenue_is_left_out(self):
        pl = self.plant(404, 0)           # pre-tax unchanged: the gain is already in revenue
        self.assertEqual(pl["status"], "pass")
        t = pl["tests"][0]
        self.assertEqual(t["left_out"], ["gain_on_disposal"])
        self.assertIn("(left out: gain on disposal of operations, already inside net revenue "
                      "in this filing, 404,000,000)", t["test"])
        self.assertNotIn("+ gain on disposal", t["test"])

    def test_gain_outside_revenue_is_added(self):
        pl = self.plant(404, 404)         # pre-tax includes the gain on top of revenue
        self.assertEqual(pl["status"], "pass")
        t = pl["tests"][0]
        self.assertNotIn("left_out", t)
        self.assertIn("+ gain on disposal of operations", t["test"])

    def test_neither_way_ties_flags_with_both_differences(self):
        pl = self.plant(404, 100)
        self.assertEqual(pl["status"], "flag")
        self.assertIn("off by 304,000,000 (-100,000,000 with gain_on_disposal left out)", pl["reason"])

    def test_insurer_footing_has_no_inside_rule(self):
        # Allstate's gain is outside revenue (InsurerDerivedClaims); the insurer
        # footing keeps adding it and never leaves it out.
        self.assertFalse(any(ft.get("inside_if_ties") for ft in INSURER["footings"]))


class CoreLineGuard(unittest.TestCase):
    """history_to_answers refuses (exit 2) a financial model whose core lines are missing,
    instead of building it on template placeholders."""

    def run_answers(self, h):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "%s_history.json" % h["ticker"])
        with open(path, "w") as f:
            json.dump(h, f)
        return history_to_answers.main([path, "--out", tmp.name]), os.listdir(tmp.name)

    def test_derived_claims_build(self):
        rc, files = self.run_answers(build("fin_all_trim.json", "ALL"))
        self.assertEqual(rc, 0)
        self.assertIn("all_insurer_filings.yaml", files)

    def test_insurer_without_claims_is_refused(self):
        data = load("fin_all_trim.json")
        del data["facts"]["us-gaap"]["OtherCostAndExpenseOperating"]   # no fallback either
        h = filings_map.build_history(data, "ALL", CMAP)
        self.assertEqual(h["lines"]["claims"], {})
        rc, files = self.run_answers(h)
        self.assertEqual(rc, 2)
        self.assertEqual(files, ["ALL_history.json"])          # no answers file written

    def test_bank_without_interest_expense_is_refused(self):
        h = build("fin_jpm_trim.json", "JPM")
        h["lines"]["interest_expense"] = {}
        rc, _ = self.run_answers(h)
        self.assertEqual(rc, 2)

    def test_bank_without_loans_is_refused(self):
        # WFC files its loan book only under its own tag after FY2021.
        h = build("fin_jpm_trim.json", "JPM")
        h["lines"]["loans"] = {}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc, files = self.run_answers(h)
        self.assertEqual(rc, 2)
        self.assertEqual(files, ["JPM_history.json"])
        self.assertIn("refused: no value for loans in any actual year", out.getvalue())
        self.assertIn("without the loan book, loan growth is the template placeholder", out.getvalue())

    def test_bank_without_deposits_is_refused(self):
        h = build("fin_jpm_trim.json", "JPM")
        h["lines"]["deposits"] = {}
        with contextlib.redirect_stdout(io.StringIO()):
            rc, _ = self.run_answers(h)
        self.assertEqual(rc, 2)

    def test_bank_loan_growth_placeholder_is_refused(self):
        # Loans missing at the start of the growth window: loan growth would be the
        # template placeholder.
        h = build("fin_jpm_trim.json", "JPM")
        del h["lines"]["loans"]["2022"]
        with contextlib.redirect_stdout(io.StringIO()):
            rc, _ = self.run_answers(h)
        self.assertEqual(rc, 2)

    def test_driver_placeholder_from_a_missing_core_line_is_refused(self):
        # Premiums filed in FY2024-FY2025 but not FY2023: premium growth (FY2023 to
        # FY2025) would be the template placeholder, so the build is refused.
        h = build("fin_trv_trim.json", "TRV")
        del h["lines"]["premiums"]["2023"]
        rc, _ = self.run_answers(h)
        self.assertEqual(rc, 2)

    def test_placeholder_cost_of_equity_is_not_refused(self):
        rc, _ = self.run_answers(build("fin_jpm_trim.json", "JPM"))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

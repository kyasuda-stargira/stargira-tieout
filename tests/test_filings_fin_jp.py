"""Japanese banks, insurers and securities firms: routing, the sector maps and
their tie-out (filings_map.py jp).

Offline only. Fixtures in tests/fixtures/ are EDINET type-5 CSV zips trimmed to
the DEI, the share-count rows, the cash note and the elements the sector maps use:
  edinet_8306_S100YJQO_trim.zip  MUFG, year to 2026-03-31 (bank, DEI code bnk)
  edinet_5831_S100Y9RT_trim.zip  Shizuoka FG, year to 2026-03-31 (bank; 現金預け金 is not all cash)
  edinet_8750_S100YC7A_trim.zip  Dai-ichi Life, year to 2026-03-31 (life insurer, ins)
  edinet_8766_S100TOZP_trim.zip  Tokio Marine, year to 2024-03-31 (non-life, J-GAAP, INS)
  edinet_8766_S100YLS8_trim.zip  Tokio Marine, year to 2026-03-31 (the same company under IFRS 17;
                                 trimmed for maps/ifrs_insurer.yaml, see test_ifrs_insurer.py)
  edinet_8601_S100YCMP_trim.zip  Daiwa Securities, year to 2026-03-31 (securities firm, SEC;
                                 refused until a broker-dealer template exists)

Run: python -m unittest discover -s tests
"""
import contextlib
import csv
import glob
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
FIX = os.path.join(ROOT, "tests", "fixtures")

import fetch_filings  # noqa: E402
import filings_map  # noqa: E402

# The EDINET zip fixtures are not in the public copy, because the plugin directory holds
# any zip file for a reviewer. The checks that read them are skipped there.
ZIPS = bool(glob.glob(os.path.join(FIX, "*.zip")))
MUFG = "edinet_8306_S100YJQO_trim.zip"
SHIZUOKA = "edinet_5831_S100Y9RT_trim.zip"
DAIICHI = "edinet_8750_S100YC7A_trim.zip"
TOKIO_JG = "edinet_8766_S100TOZP_trim.zip"
TOKIO_IFRS = "edinet_8766_S100YLS8_trim.zip"
DAIWA = "edinet_8601_S100YCMP_trim.zip"
M = 1000000
CODE = filings_map.DEI_INDUSTRY


def rewrite(src, out_dir, change):
    """Copy a fixture zip into out_dir, passing each CSV row through change(row),
    which returns the row (edited or not), a list of rows, or None to drop it."""
    dest = os.path.join(out_dir, os.path.basename(src))
    with zipfile.ZipFile(src) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-16")
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    out = [rows[0]]
    for r in rows[1:]:
        r2 = change(r)
        if r2 is None:
            continue
        out.extend(r2 if r2 and isinstance(r2[0], list) else [r2])
    buf = io.StringIO()
    csv.writer(buf, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(out)
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr(name, buf.getvalue().encode("utf-16"))
    return dest


def set_code(code):
    def change(r):
        if r[0] == "jpdei_cor:" + CODE:
            r = r[:8] + [code] + r[9:]
        return r
    return change


def set_value(element, context, delta):
    def change(r):
        if r[0] == element and r[2] == context:
            r = r[:8] + [str(int(r[8]) + delta)] + r[9:]
        return r
    return change


def history(files, tmp, code):
    manifest = fetch_filings.fetch_jp(code, tmp, from_files=files)
    data, cmap = filings_map.load_edinet(manifest)
    return filings_map.build_history(data, code, cmap)


def v(h, key, fy):
    return h["lines"][key][str(fy)]["val"]


class Offline(unittest.TestCase):
    """Every test here runs with the network replaced by a failure."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = fetch_filings._http_get

        def no_network(url, user_agent):
            raise AssertionError("network used")
        fetch_filings._http_get = no_network

    def tearDown(self):
        fetch_filings._http_get = self.saved
        shutil.rmtree(self.tmp)

    def fx(self, name):
        return os.path.join(FIX, name)

    def edited(self, name, change):
        d = tempfile.mkdtemp(dir=self.tmp)
        return rewrite(self.fx(name), d, change)


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class Routing(Offline):

    def test_bank_by_lower_case_code(self):
        h = history([self.fx(MUFG)], self.tmp, "8306")
        self.assertEqual(h["sector"], "bank")
        self.assertIn("maps/jgaap_bank.yaml", h["routing"])
        self.assertIn("DEI industry code is bnk", h["routing"])

    def test_upper_case_codes(self):
        for name, code, sector, where in ((MUFG, "BNK", "bank", "jgaap_bank.yaml"),
                                          (DAIICHI, "INS", "insurer", "jgaap_insurer_life.yaml")):
            src = self.edited(name, set_code(code))
            h = history([src], tempfile.mkdtemp(dir=self.tmp), "9999")
            self.assertEqual(h["sector"], sector, name)
            self.assertIn(where, h["routing"])
            self.assertIn("DEI industry code is %s" % code, h["routing"])

    def test_suffix_fallback_without_a_financial_code(self):
        for name, where, suffix in ((MUFG, "jgaap_bank.yaml", "BNK"),
                                    (DAIICHI, "jgaap_insurer_life.yaml", "INS")):
            src = self.edited(name, set_code("cte"))
            h = history([src], tempfile.mkdtemp(dir=self.tmp), "9999")
            self.assertIn(where, h["routing"], name)
            self.assertIn("no financial DEI industry code (cte)", h["routing"])
            self.assertIn("end in %s" % suffix, h["routing"])

    def test_daiwa_refused_by_its_code(self):
        # Securities firms are refused until a broker-dealer template exists.
        for code in ("SEC", "sec"):
            src = self.edited(DAIWA, set_code(code))
            with self.assertRaises(filings_map.Unsupported) as e:
                history([src], tempfile.mkdtemp(dir=self.tmp), "8601")
            self.assertEqual(str(e.exception), (
                "not supported yet: this is a securities firm (DEI industry code %s). Tie-Out "
                "has no broker-dealer template yet; the bank template's drivers do not fit it."
                % code))

    def test_daiwa_refused_by_its_suffix(self):
        # It has current assets, so the old "no CurrentAssets" test alone would miss it,
        # and without the code it must not fall to the corporate chart.
        src = self.edited(DAIWA, set_code("cte"))
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp, "8601")
        msg = str(e.exception)
        self.assertTrue(msg.startswith("not supported yet: this is a securities firm (no financial "
                                       "DEI industry code (cte), but "), msg)
        self.assertIn("of its statement elements end in SEC). Tie-Out has no broker-dealer "
                      "template yet", msg)

    def test_daiwa_cli_exits_2_and_writes_nothing(self):
        manifest = fetch_filings.fetch_jp("8601", self.tmp, from_files=[self.fx(DAIWA)])
        out = os.path.join(self.tmp, "out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(filings_map.main(["jp", manifest, "--ticker", "8601", "--out", out]), 2)
        self.assertIn("8601: not supported yet: this is a securities firm", buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(out, "8601_history.json")))

    def test_securities_map_kept_but_unused(self):
        jgaap = filings_map.load_map(os.path.join(ROOT, "engine", "maps", "jgaap.yaml"))
        sec = [r for r in jgaap["unsupported"]["route"] if r["suffix"] == "SEC"]
        self.assertEqual(len(sec), 1)
        self.assertIn("refuse", sec[0])
        self.assertNotIn("map", sec[0])
        path = os.path.join(ROOT, "engine", "maps", "jgaap_securities.yaml")
        with open(path, encoding="utf-8") as f:
            self.assertIn("UNUSED, waiting for a broker-dealer template", f.read())

    def test_life_and_non_life_by_premium_line(self):
        life = history([self.fx(DAIICHI)], self.tmp, "8750")
        self.assertEqual((life["sector"], life["insurer_type"]), ("insurer", "life"))
        nonlife = history([self.fx(TOKIO_JG)], tempfile.mkdtemp(dir=self.tmp), "8766")
        self.assertEqual((nonlife["sector"], nonlife["insurer_type"]), ("insurer", "non_life"))
        self.assertIn("NetPremiumsWrittenOIINS (non-life format)", nonlife["routing"])
        self.assertTrue(any("WRITTEN" in n for n in nonlife["chart_notes"]))

    def test_insurer_with_both_premium_lines_refused(self):
        def add_nonlife(r):
            if r[0] == "jppfs_cor:InsurancePremiumsAndOtherOIINS":
                return [r, ["jppfs_cor:NetPremiumsWrittenOIINS"] + r[1:]]
            return r
        src = self.edited(DAIICHI, add_nonlife)
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp, "8750")
        self.assertIn("files both", str(e.exception))

    def test_code_and_suffix_that_disagree_refused(self):
        src = self.edited(DAIICHI, set_code("bnk"))
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp, "8750")
        self.assertIn("rather than guessed", str(e.exception))
        self.assertIn("end in INS", str(e.exception))

    def test_cli_names_the_sector(self):
        manifest = fetch_filings.fetch_jp("8306", self.tmp, from_files=[self.fx(MUFG)])
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(filings_map.main(["jp", manifest, "--ticker", "8306", "--out", out]), 0)
        with open(os.path.join(out, "8306_history.md"), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("Sector: bank.", md)
        self.assertIn("## How the chart reads this filing", md)


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class BankMap(Offline):

    def test_credit_cost_is_the_sum_of_tagged_parts(self):
        h = history([self.fx(MUFG)], self.tmp, "8306")
        # FY2026: 貸倒引当金繰入額 176,952 - 償却債権取立益 96,758
        self.assertEqual(v(h, "provision", 2026), (176952 - 96758) * M)
        self.assertEqual(h["lines"]["provision"]["2026"]["tag"],
                         "ProvisionOfAllowanceForLoanLossesOEBNK - RecoveriesOfWrittenOffClaimsOIBNK")
        # FY2025: a net release, 貸倒引当金戻入益 76,843 + 償却債権取立益 112,203
        self.assertEqual(v(h, "provision", 2025), -(76843 + 112203) * M)

    def test_revenue_trap_and_net_revenue(self):
        h = history([self.fx(MUFG)], self.tmp, "8306")
        self.assertEqual(v(h, "revenue_tagged", 2026), 14620843 * M)   # 経常収益 (revenue)
        self.assertEqual(v(h, "ordinary_profit", 2026), 3410192 * M)   # 経常利益 (profit)
        self.assertEqual(v(h, "net_interest_income", 2026), (8723940 - 5717866) * M)
        # net revenue = 経常利益 + 営業経費 + credit costs
        self.assertEqual(v(h, "total_revenue", 2026), (3410192 + 3489570 + 80194) * M)
        self.assertEqual(h["checks"]["2026"]["pl"]["status"], "pass")

    def test_mufg_cash_is_cash_and_due_from_banks(self):
        h = history([self.fx(MUFG)], self.tmp, "8306")
        self.assertEqual(v(h, "cash", 2026), 90045500 * M)
        self.assertEqual(v(h, "end_cash_cf", 2026), 90045500 * M)
        self.assertEqual(h["checks"]["2026"]["cash"]["status"], "pass")

    def test_part_of_due_from_banks_is_not_cash_note_used_only_if_it_foots(self):
        h = history([self.fx(SHIZUOKA)], self.tmp, "5831")
        self.assertEqual(v(h, "cash", 2026), 862461 * M)
        self.assertEqual(v(h, "end_cash_cf", 2026), 755943 * M)
        self.assertEqual(h["checks"]["2026"]["cash"]["status"], "pass")
        self.assertIn("cash reconciliation note", h["checks"]["2026"]["cash"]["note"])
        # Break the note's last line: the year flags instead.
        bad = self.edited(SHIZUOKA, lambda r: r[:8] + [r[8].replace("755,943", "755,944")] + r[9:]
                          if filings_map.CASH_NOTE in r[0] else r)
        h = history([bad], tempfile.mkdtemp(dir=self.tmp), "5831")
        self.assertEqual(h["checks"]["2026"]["cash"]["status"], "flag")

    def test_rounding_sized_cash_gap_still_flags_with_reason(self):
        h = history([self.fx(MUFG)], self.tmp, "8306")
        c = h["checks"]["2025"]["cash"]
        self.assertEqual(c["status"], "flag")
        self.assertIn("within the rounding of the four filed figures", c["reason"])


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class InsurerMap(Offline):

    def test_life_lines(self):
        h = history([self.fx(DAIICHI)], self.tmp, "8750")
        self.assertEqual(v(h, "premiums", 2026), 6944066 * M)          # 保険料等収入
        self.assertEqual(v(h, "claims", 2026), 6447114 * M)            # 保険金等支払金
        self.assertEqual(v(h, "admin_expenses", 2026), 1048234 * M)    # 事業費
        self.assertEqual(v(h, "total_revenue", 2026), 11308275 * M)    # 経常収益
        self.assertEqual(v(h, "policyholder_dividend_provision", 2026), 107500 * M)
        self.assertEqual(v(h, "policy_reserves", 2026), 61255107 * M)  # 保険契約準備金
        # opex = 経常費用 - claims - reserve provision - investment expenses
        self.assertEqual(v(h, "opex", 2026), (10554587 - 6447114 - 1814937 - 867008) * M)
        self.assertEqual(h["checks"]["2026"]["pl"]["status"], "pass")
        self.assertEqual(h["years"][-1]["status"], "tied")

    def test_reserve_change_is_provision_minus_reversal(self):
        h = history([self.fx(DAIICHI)], self.tmp, "8750")
        self.assertEqual(v(h, "reserve_change", 2026), 1814937 * M)    # no reversal filed
        spec = filings_map.load_map(os.path.join(ROOT, "engine", "maps", "jgaap_insurer_life.yaml"))
        line = {"val": 0, "accn": "S1", "form": "x", "filed": "2026-06-01"}
        lines = {"reserve_provision": {2026: dict(line, val=500)},
                 "reserve_reversal": {2026: dict(line, val=180)}}
        d = filings_map.derive_line(lines, spec["lines"]["reserve_change"], 2026)
        self.assertEqual(d["val"], 320)
        self.assertEqual(d["tag"], "derived: reserve_provision - reserve_reversal")
        lines = {"reserve_reversal": {2026: dict(line, val=180)}}     # a release only
        self.assertEqual(filings_map.derive_line(lines, spec["lines"]["reserve_change"], 2026)["val"], -180)

    def test_non_life_lines(self):
        h = history([self.fx(TOKIO_JG)], self.tmp, "8766")
        self.assertEqual(v(h, "premiums", 2024), (4824986 + 1049852) * M)   # written, not earned
        self.assertEqual(v(h, "claims", 2024), (2578456 + 179667 + 563195) * M)
        self.assertEqual(v(h, "reserve_provision", 2024), (213594 + 392343) * M)
        self.assertEqual(v(h, "policy_reserves", 2024), 22031174 * M)
        self.assertEqual(h["checks"]["2024"]["pl"]["status"], "pass")

    def test_cash_note_with_ditto_marks_and_run_together_columns(self):
        h = history([self.fx(DAIICHI)], self.tmp, "8750")      # "591,000 〃" rows
        self.assertEqual(h["checks"]["2026"]["cash"]["status"], "pass")
        h = history([self.fx(TOKIO_JG)], tempfile.mkdtemp(dir=self.tmp), "8766")
        self.assertEqual(h["checks"]["2024"]["cash"]["status"], "pass")   # "871,993896,898"
        cols = filings_map.parse_cash_note("（単位：百万円）現金871,993896,898定期△103,228△110,552"
                                           "現金同等物768,765786,346")
        self.assertEqual([[x for _, x in c] for c in cols],
                         [[871993 * M, -103228 * M, 768765 * M], [896898 * M, -110552 * M, 786346 * M]])


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class FlagNotPlug(Offline):

    def test_planted_balance_mismatch(self):
        src = self.edited(MUFG, set_value("jppfs_cor:Assets", "CurrentYearInstant", 7 * M))
        h = history([src], self.tmp, "8306")
        b = h["checks"]["2026"]["balance"]
        self.assertEqual(b["status"], "flag")
        self.assertEqual(b["tests"][0]["diff"], 7 * M)
        self.assertEqual(v(h, "total_assets", 2026), 431731548 * M + 7 * M)   # kept as filed
        self.assertEqual(h["years"][-1]["status"], "flagged")

    def test_planted_pl_mismatch(self):
        src = self.edited(DAIICHI, set_value("jppfs_cor:OperatingExpensesINS", "CurrentYearDuration", 50 * M))
        h = history([src], self.tmp, "8750")
        pl = h["checks"]["2026"]["pl"]
        self.assertEqual(pl["status"], "flag")
        self.assertIn("経常収益 - 経常費用 = 経常利益", pl["reason"])
        self.assertEqual(v(h, "ordinary_profit", 2026), 753688 * M)   # not re-computed
        src = self.edited(MUFG, set_value("jppfs_cor:ExtraordinaryLoss", "CurrentYearDuration", 3 * M))
        h = history([src], tempfile.mkdtemp(dir=self.tmp), "8306")
        self.assertIn("特別損失", h["checks"]["2026"]["pl"]["reason"])


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class TokioMarineDecision(Offline):
    """Latest report IFRS, earlier report J-GAAP: the IFRS
    non-life insurer routes to maps/ifrs_insurer.yaml with the IFRS years only (never
    mixed). An IFRS bank, or an IFRS insurer that is not non-life, is still refused."""

    def test_ifrs_insurer_routed_with_ifrs_years_only(self):
        h = history([self.fx(TOKIO_IFRS), self.fx(TOKIO_JG)], self.tmp, "8766")
        self.assertEqual((h["sector"], h["insurer_type"], h["basis"]), ("insurer", "non_life", "ifrs17"))
        self.assertEqual([y["fy"] for y in h["years"]], [2025, 2026])
        self.assertEqual({y["accn"] for y in h["years"]}, {"S100YLS8"})
        self.assertEqual({y["standard"] for y in h["years"]}, {"IFRS"})
        self.assertIn("maps/ifrs_insurer.yaml", h["routing"])
        self.assertTrue(any("S100TOZP" in w and "never mixed" in w for w in h["warnings"]))

    def test_upper_case_code_on_ifrs_without_the_non_life_line_refused(self):
        src = self.edited(TOKIO_IFRS, lambda r: None if "SummaryOfBusinessResultsINS" in r[0] else r)
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp, "8766")
        self.assertIn("DEI industry code INS", str(e.exception))
        self.assertIn("only IFRS map for financial companies is for non-life insurers", str(e.exception))

    def test_ifrs_bank_refused_with_the_existing_message(self):
        src = self.edited(TOKIO_IFRS, set_code("BNK"))
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src, self.fx(TOKIO_JG)], self.tmp, "8766")
        msg = str(e.exception)
        self.assertIn("not supported yet", msg)
        self.assertIn("S100YLS8) is under IFRS", msg)
        self.assertIn("2 year(s) of P&L and cash flow (FY2025, FY2026)", msg)
        self.assertIn("3 balance sheet(s)", msg)
        self.assertIn("S100TOZP are under Japan GAAP", msg)
        self.assertIn("not mixed", msg)

    def test_cli_exit_2_for_an_ifrs_bank_and_nothing_written(self):
        src = self.edited(TOKIO_IFRS, set_code("bnk"))
        manifest = fetch_filings.fetch_jp("8766", self.tmp, from_files=[src])
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(filings_map.main(["jp", manifest, "--ticker", "8766", "--out", out]), 2)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()

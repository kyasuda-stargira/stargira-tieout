"""Japan filings track: fetch_filings.py jp (offline paths) and filings_map.py jp.

Offline only. Fixtures in tests/fixtures/ are EDINET type-5 CSV zips trimmed to
the DEI, the share-count rows, the cash note and the mapped elements. Banks,
insurers and securities firms are tested in test_filings_fin_jp.py.
  edinet_7974_S100Y9NX_trim.zip  Nintendo, 有価証券報告書 for the year to 2026-03-31 (J-GAAP)
  edinet_7974_S100TMMG_trim.zip  Nintendo, year to 2024-03-31 (J-GAAP)
  edinet_4502_S100YB5L_trim.zip  Takeda, year to 2026-03-31 (IFRS)

Run: python -m unittest discover -s tests
"""
import contextlib
import glob
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
FIX = os.path.join(ROOT, "tests", "fixtures")

import fetch_filings  # noqa: E402
import filings_map  # noqa: E402
import history_to_answers  # noqa: E402

# The EDINET zip fixtures are not in the public copy, because the plugin directory holds
# any zip file for a reviewer. The checks that read them are skipped there.
ZIPS = bool(glob.glob(os.path.join(FIX, "*.zip")))
NIN = ["edinet_7974_S100Y9NX_trim.zip", "edinet_7974_S100TMMG_trim.zip"]
TAKEDA = ["edinet_4502_S100YB5L_trim.zip"]
KEY = "TESTKEY-0123456789abcdef"


def edited(src, out_dir, *pairs):
    """Copy a fixture zip into out_dir with text replacements in its CSV."""
    dest = os.path.join(out_dir, os.path.basename(src))
    with zipfile.ZipFile(src) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-16")
    for old, new in pairs:
        if old not in text:
            raise AssertionError("fixture has no %r" % old)
        text = text.replace(old, new)
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr(name, text.encode("utf-16"))
    return dest


def history(files, tmp, code="7974"):
    """--from-file fetch then map, all offline. Returns the history dict."""
    manifest = fetch_filings.fetch_jp(code, tmp, from_files=files)
    data, cmap = filings_map.load_edinet(manifest)
    return filings_map.build_history(data, code, cmap)


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

    def fx(self, names):
        return [os.path.join(FIX, n) for n in names]


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class StandardDetection(Offline):

    def test_jgaap_and_ifrs_detected_from_elements(self):
        h = history(self.fx(NIN), self.tmp)
        self.assertTrue(h["standard"].startswith("J-GAAP"))
        h = history(self.fx(TAKEDA), self.tmp, "4502")
        self.assertTrue(h["standard"].startswith("IFRS"))

    def test_us_gaap_filer_refused(self):
        src = edited(self.fx(NIN)[0], self.tmp, ('"Japan GAAP"', '"US GAAP"'))
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp)
        self.assertIn("not supported yet", str(e.exception))
        self.assertIn("US GAAP", str(e.exception))

    def test_dei_that_disagrees_with_the_elements_is_refused(self):
        src = edited(self.fx(NIN)[0], self.tmp, ('"Japan GAAP"', '"IFRS"'))
        with self.assertRaises(filings_map.Unsupported) as e:
            history([src], self.tmp)
        self.assertIn("rather than guessed", str(e.exception))


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class Map(Offline):

    def test_nintendo_years_values_and_doc_ids(self):
        h = history(self.fx(NIN), self.tmp)
        self.assertEqual([y["fy"] for y in h["years"]], [2023, 2024, 2025, 2026])
        self.assertEqual([y["accn"] for y in h["years"]],
                         ["S100TMMG", "S100TMMG", "S100Y9NX", "S100Y9NX"])
        self.assertEqual(h["currency"], "JPY")
        self.assertEqual(h["filing_id_label"], "docID")
        rev = h["lines"]["revenue"]
        self.assertEqual(rev["2026"]["val"], 2313051000000)
        self.assertEqual(rev["2026"]["tag"], "NetSales")
        self.assertEqual(rev["2025"]["accn"], "S100Y9NX")     # prior-year column of the newest report
        self.assertEqual(rev["2024"]["accn"], "S100TMMG")
        self.assertEqual(h["lines"]["ebit"]["2026"]["val"], 360117000000)
        self.assertEqual(h["lines"]["net_income"]["2026"]["val"], 424056000000)
        self.assertEqual(h["lines"]["total_assets"]["2026"]["val"], 3805312000000)
        self.assertEqual(h["years"][0]["unit"], 1000000)
        self.assertIn("prior-year figures", h["years"][2]["basis"])

    def test_payments_turn_positive(self):
        h = history(self.fx(NIN), self.tmp)
        self.assertEqual(h["lines"]["capex"]["2026"]["val"], 27169000000)
        self.assertEqual(h["lines"]["dividends_paid"]["2026"]["val"], 147858000000)

    def test_share_count_is_issued_less_treasury(self):
        so = history(self.fx(NIN), self.tmp)["shares_outstanding"]
        self.assertEqual(so["val"], 1287260000 - 134431200)
        self.assertEqual(so["accn"], "S100Y9NX")
        self.assertIn("treasury", so["basis"])

    def test_parts_sum_when_no_total(self):
        spec = {"kind": "stock", "tags": ["Inventories"],
                "parts": [["MerchandiseAndFinishedGoods"], ["WorkInProcess"]]}
        facts = {k: {"units": {"JPY": [{"end": "2026-03-31", "val": v, "accn": "S1", "form": "x",
                                         "filed": "2026-06-01"}]}}
                 for k, v in (("MerchandiseAndFinishedGoods", 5), ("WorkInProcess", 7))}
        cmap = {"meta": {"forms": ["x"], "unit": "JPY", "flow_days": [350, 380]}}
        v = filings_map.pick_line(facts, spec, "2026-03-31", cmap)
        self.assertEqual(v["val"], 12)
        self.assertEqual(v["tag"], "MerchandiseAndFinishedGoods + WorkInProcess")

    def test_ifrs_takeda(self):
        h = history(self.fx(TAKEDA), self.tmp, "4502")
        self.assertEqual(h["lines"]["revenue"]["2026"]["val"], 4505720000000)
        self.assertEqual(h["lines"]["net_income"]["2026"]["val"], -152390000000)
        self.assertEqual(h["lines"]["capex"]["2026"]["val"], 176003000000)
        self.assertEqual(h["years"][-1]["status"], "tied")
        self.assertTrue(any("only 2 fiscal year" in w for w in h["warnings"]))

    def test_credit_wording(self):
        h = history(self.fx(NIN), self.tmp)
        self.assertTrue(h["credit"][0].startswith("出典：EDINET閲覧（提出）サイト（"))
        self.assertIn("WZEK0040.aspx?S100Y9NX", h["credit"][0])
        self.assertIn("PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）",
                      h["credit"][0])
        self.assertIn("をもとにStargira Tie-Out（Stargira Advisors Ltd）により加工", h["credit"][1])


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class TieOut(Offline):

    def test_nintendo_ties_out_through_its_cash_note(self):
        h = history(self.fx(NIN), self.tmp)
        self.assertEqual([y["status"] for y in h["years"]], ["tied"] * 4)
        c = h["checks"]["2026"]["cash"]
        self.assertIn("cash reconciliation note", c["note"])
        last = c["tests"][-1]
        self.assertEqual(last["left"], 1316680000000)                 # 現金及び現金同等物
        self.assertEqual(last["right"], 1791802000000 - 775400000000 + 300278000000)

    def test_planted_mismatch_is_flagged_not_plugged(self):
        src = edited(self.fx(NIN)[0], self.tmp,
                     ('"jppfs_cor:Assets"\t"資産"\t"CurrentYearInstant"\t"当期末"\t"連結"\t"時点"\t'
                      '"JPY"\t"円"\t"3805312000000"',
                      '"jppfs_cor:Assets"\t"資産"\t"CurrentYearInstant"\t"当期末"\t"連結"\t"時点"\t'
                      '"JPY"\t"円"\t"3810312000000"'))
        h = history([src, self.fx(NIN)[1]], self.tmp)
        y = [y for y in h["years"] if y["fy"] == 2026][0]
        self.assertEqual(y["status"], "flagged")
        self.assertIn("off by 5,000,000,000", h["checks"]["2026"]["balance"]["reason"])
        self.assertEqual(h["lines"]["total_assets"]["2026"]["val"], 3810312000000)
        self.assertEqual(h["checks"]["2025"]["balance"]["status"], "pass")

    def test_note_that_does_not_foot_is_flagged(self):
        src = edited(self.fx(NIN)[0], self.tmp, ("△775,400百万円", "△775,500百万円"))
        h = history([src, self.fx(NIN)[1]], self.tmp)
        c = h["checks"]["2026"]["cash"]
        self.assertEqual(c["status"], "flag")
        self.assertIn("does not foot", c["reason"])
        self.assertIn("475,122,000,000", c["reason"])               # the gap, reported
        self.assertEqual(h["checks"]["2025"]["cash"]["status"], "pass")

    def test_rounding_gap_of_one_unit_uses_the_filed_total(self):
        lines = {"total_assets": {"2026": {"val": 15511506000000}},
                 "total_liabilities": {"2026": {"val": 8080858000000}},
                 "total_equity": {"2026": {"val": 7430649000000}},
                 "liabilities_and_equity": {"2026": {"val": 15511506000000}}}
        y = {"fy": 2026, "unit": 1000000}
        hist = history_to_answers.History({"years": [y], "lines": lines})
        tl, note = history_to_answers._rounding_fix(hist, y, 15511506.0, 8080858.0, 7430649.0)
        self.assertEqual(tl, 8080857.0)
        self.assertIn("rounding", note)
        lines["total_liabilities"]["2026"]["val"] = 8080860000000     # 3 units: not rounding
        self.assertIsNone(history_to_answers._rounding_fix(hist, y, 15511506.0, 8080860.0, 7430649.0))


@unittest.skipUnless(ZIPS, "filing zips not in this copy")
class AnswersFile(Offline):

    def test_answers_carry_jpy_and_the_edinet_credit(self):
        manifest = fetch_filings.fetch_jp("7974", self.tmp, from_files=self.fx(NIN))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(filings_map.main(["jp", manifest, "--ticker", "7974",
                                               "--out", self.tmp]), 0)
            self.assertEqual(history_to_answers.main(
                [os.path.join(self.tmp, "7974_history.json")]), 0)
        with open(os.path.join(self.tmp, "7974_filings.yaml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('g_reporting_ccy: "JPY"', text)
        self.assertIn("PDL1.0", text.split("g_data_source:")[1].split("\n")[0])
        self.assertIn("Stargira Tie-Out（Stargira Advisors Ltd）により加工", text)
        self.assertIn('hist_3_accn: "S100Y9NX"', text)
        self.assertIn('hist_3_form: "有価証券報告書"', text)
        self.assertIn("hist_3_revenue: 2313051.0", text)


class FetchOffline(Offline):

    @unittest.skipUnless(ZIPS, "filing zips not in this copy")
    def test_from_file_needs_no_key_and_no_network(self):
        os.environ.pop(fetch_filings.EDINET_KEY_ENV, None)
        with contextlib.redirect_stdout(io.StringIO()):
            code = fetch_filings.main(["jp", "7974", "--out", self.tmp, "--from-file"]
                                      + self.fx(NIN))
        self.assertEqual(code, 0)
        with open(os.path.join(self.tmp, "7974_edinet.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual([x["docID"] for x in m["filings"]], ["S100Y9NX", "S100TMMG"])

    def test_missing_key_message(self):
        os.environ.pop(fetch_filings.EDINET_KEY_ENV, None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = fetch_filings.main(["jp", "7974", "--out", self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("EDINET_API_KEY", err.getvalue())
        self.assertIn("register", err.getvalue())
        self.assertIn("DATA-SOURCES", err.getvalue())

    def test_key_never_in_error_text(self):
        os.environ[fetch_filings.EDINET_KEY_ENV] = KEY
        try:
            def failing(url, user_agent):
                raise OSError("HTTP Error 500 for %s (key %s)" % (url, KEY))
            fetch_filings._http_get = failing
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = fetch_filings.main(["jp", "7974", "--out", self.tmp])
            self.assertEqual(code, 1)
            self.assertNotIn(KEY, err.getvalue())
            self.assertIn("<redacted>", err.getvalue())
            with self.assertRaises(fetch_filings.FetchError) as e:
                fetch_filings.edinet_json(fetch_filings.EDINET_LIST_URL.format(date="2026-06-25"), KEY)
            self.assertNotIn(KEY, str(e.exception))
            self.assertIsNone(e.exception.__cause__)
            self.assertNotIn(KEY, fetch_filings.redact("x?Subscription-Key=%s&y=1" % KEY))
        finally:
            os.environ.pop(fetch_filings.EDINET_KEY_ENV, None)

    def test_error_status_inside_http_200(self):
        body = json.dumps({"StatusCode": 401, "message": "Access denied due to invalid "
                           "subscription key."}).encode()
        fetch_filings._http_get = lambda url, ua: body
        with self.assertRaises(fetch_filings.FetchError) as e:
            fetch_filings.edinet_json(fetch_filings.EDINET_LIST_URL.format(date="2026-06-25"), KEY)
        self.assertIn("refused the API key", str(e.exception))
        self.assertNotIn(KEY, str(e.exception))
        ok = {"metadata": {"status": "200", "message": "OK"}, "results": []}
        self.assertIsNone(fetch_filings.edinet_status_error(ok))
        self.assertIn("404", fetch_filings.edinet_status_error(
            {"metadata": {"status": "404", "message": "Not Found"}}))

    def test_scan_finds_latest_report_and_caches_past_days_without_the_key(self):
        seen = []

        def fake(url, ua):
            seen.append(url)
            day = url.split("date=")[1][:10]
            results = []
            if day == "2026-06-25":
                results = [{"docID": "S100Y9NX", "secCode": "79740", "docTypeCode": "120",
                            "csvFlag": "1", "withdrawalStatus": "0", "periodEnd": "2026-03-31",
                            "submitDateTime": "2026-06-25 15:30"},
                           {"docID": "S100ZZZZ", "secCode": "79740", "docTypeCode": "140",
                            "csvFlag": "1", "withdrawalStatus": "0"}]
            return json.dumps({"metadata": {"status": "200"}, "results": results}).encode()
        fetch_filings._http_get = fake
        fetch_filings._last_call[0] = 0.0
        cache = os.path.join(self.tmp, "edinet_lists")
        today = date(2026, 6, 27)
        days = [date(2026, 6, 27), date(2026, 6, 26), date(2026, 6, 25), date(2026, 6, 24)]
        r = fetch_filings.find_report("79740", days, KEY, cache, today)
        self.assertEqual(r["docID"], "S100Y9NX")
        self.assertEqual(len(seen), 3)                       # stopped once found
        self.assertTrue(all(KEY in u for u in seen))         # the key goes only into the call
        self.assertEqual(sorted(os.listdir(cache)), ["2026-06-25.json", "2026-06-26.json"])
        for name in os.listdir(cache):
            with open(os.path.join(cache, name), encoding="utf-8") as f:
                self.assertNotIn(KEY, f.read())
        seen.clear()
        fetch_filings.find_report("79740", days[1:], KEY, cache, today)
        self.assertEqual(seen, [])                           # past days come from the cache

    def test_securities_code(self):
        self.assertEqual(fetch_filings.edinet_sec_code("7974"), "79740")
        for bad in ("797", "79740", "abcd"):
            with self.assertRaises(fetch_filings.FetchError):
                fetch_filings.edinet_sec_code(bad)

    def test_edinet_host_allowed_others_refused(self):
        fetch_filings.check_host("https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-06-25")
        for url in ("http://api.edinet-fsa.go.jp/api/v2/documents.json",
                    "https://api.edinet-fsa.go.jp.example.com/x",
                    "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100Y9NX"):
            with self.assertRaises(fetch_filings.FetchError):
                fetch_filings.check_host(url)

    def test_rate_gap(self):
        calls = []
        fetch_filings._http_get = lambda url, ua: calls.append(
            fetch_filings.time.monotonic()) or b'{"metadata": {"status": "200"}}'
        fetch_filings._last_call[0] = 0.0
        for _ in range(3):
            fetch_filings.edinet_json(fetch_filings.EDINET_LIST_URL.format(date="2026-06-25"), KEY)
        gaps = [b - a for a, b in zip(calls, calls[1:])]
        self.assertTrue(all(g >= 0.99 for g in gaps), gaps)


if __name__ == "__main__":
    unittest.main()


class CompanyCashElement(unittest.TestCase):
    """A company's own '...CCE' element counts in the cash roll-forward only when it
    makes the year tie (Daikin's 連結子会社の決算期変更 line, 2026-11-02)."""

    def setUp(self):
        self.cmap = filings_map.load_map(os.path.join(ROOT, "engine", "maps", "jgaap.yaml"))
        self.year = {"fy": 2025, "end": "2025-03-31"}

    def run_check(self, ext_val):
        f = lambda v: {2025: {"val": v, "tag": "t"}}  # noqa: E731
        lines = {"cfo": f(100), "cfi": f(-40), "cff": f(-10), "net_change_cash": f(50),
                 "end_cash_cf": {2025: {"val": 1040, "tag": "CashAndCashEquivalents"}},
                 "cash": f(1040)}
        fact = {"start": "2024-04-01", "end": "2025-03-31", "val": ext_val, "accn": "S1",
                "form": self.cmap["meta"]["forms"][0], "filed": "2025-06-20"}
        unit = self.cmap["meta"]["unit"]
        gaap = {filings_map.EXT_CCE + "OwnChangeCCE": {"units": {unit: [fact]}}}
        return filings_map.check_cash(gaap, lines, self.year, 1000, None, self.cmap, 1)

    def test_closes_gap(self):
        r = self.run_check(-10)      # 1000 + 50 - 10 = 1040
        self.assertEqual(r["status"], "pass")
        self.assertIn("OwnChangeCCE", r["note"])

    def test_does_not_close_gap(self):
        r = self.run_check(-30)      # 1000 + 50 - 30 = 1020, not 1040
        self.assertEqual(r["status"], "flag")

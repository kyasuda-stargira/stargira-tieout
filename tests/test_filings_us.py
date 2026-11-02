"""US filings track: fetch_filings.py (offline paths) and filings_map.py.

Offline only. Fixtures in tests/fixtures/ are trimmed SEC companyfacts files
(10-K facts for the mapped tags only): aapl_trim.json (Apple FY2018-FY2021) and
jpm_trim.json (JPMorgan, a few tags, FY2023-FY2025).

Run: python -m unittest discover -s tests
"""
import copy
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

CMAP = filings_map.load_map()


def load(name):
    return filings_map.load_facts(os.path.join(FIX, name))


def fact(val, end, accn, filed, start=None, form="10-K", fy=None, fp="FY"):
    f = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp,
         "form": form, "filed": filed}
    if start:
        f["start"] = start
    return f


def synthetic_restated():
    """Two 10-Ks. The second restates FY2023 revenue (100 -> 110).

    Each 10-K also carries a quarter tagged fy/fp FY (as real 10-Ks do), which
    must never be read as the year.
    """
    k23, k24 = "0000000001-24-000001", "0000000001-25-000001"
    revenue = [
        fact(100, "2023-12-31", k23, "2024-02-01", "2023-01-01", fy=2023),
        fact(30, "2023-12-31", k23, "2024-02-01", "2023-10-01", fy=2023),   # Q4 in the 10-K
        fact(110, "2023-12-31", k24, "2025-02-01", "2023-01-01", fy=2024),  # restated
        fact(120, "2024-12-31", k24, "2025-02-01", "2024-01-01", fy=2024),
        fact(35, "2024-12-31", k24, "2025-02-01", "2024-10-01", fy=2024),   # Q4 in the 10-K
        fact(999, "2024-12-31", "0000000001-24-000009", "2024-11-01",
             "2024-01-01", form="10-Q", fy=2024, fp="Q3"),                  # not a 10-K
    ]
    assets = [fact(500, "2023-12-31", k23, "2024-02-01", fy=2023),
              fact(600, "2024-12-31", k24, "2025-02-01", fy=2024)]
    return {"cik": 1, "entityName": "Synthetic Co",
            "facts": {"us-gaap": {"Revenues": {"units": {"USD": revenue}},
                                  "Assets": {"units": {"USD": assets}},
                                  "AssetsCurrent": {"units": {"USD": assets}}}}}


class AnnualPeriods(unittest.TestCase):

    def test_restated_year_uses_latest_filing_and_full_year(self):
        h = filings_map.build_history(synthetic_restated(), "SYN", CMAP)
        self.assertEqual([y["fy"] for y in h["years"]], [2023, 2024])
        self.assertEqual([y["accn"] for y in h["years"]],
                         ["0000000001-24-000001", "0000000001-25-000001"])
        rev = h["lines"]["revenue"]
        self.assertEqual(rev["2023"]["val"], 110)               # restated, not 100 or 30
        self.assertEqual(rev["2023"]["accn"], "0000000001-25-000001")
        self.assertEqual(rev["2024"]["val"], 120)               # year, not Q4 or the 10-Q
        self.assertTrue(any("only 2 fiscal year" in w for w in h["warnings"]))

    def test_apple_years_and_accession_numbers(self):
        h = filings_map.build_history(load("aapl_trim.json"), "AAPL", CMAP)
        self.assertEqual([y["fy"] for y in h["years"]], [2018, 2019, 2020, 2021])
        self.assertEqual(h["years"][-1]["accn"], "0000320193-21-000105")
        self.assertEqual(h["lines"]["revenue"]["2021"]["val"], 365817000000)
        self.assertEqual(h["years"][0]["unit"], 1000000)
        for line in h["lines"].values():
            for v in line.values():
                self.assertIn(v["form"], ("10-K", "10-K/A"))
                self.assertTrue(v["accn"])


class TieOut(unittest.TestCase):

    def setUp(self):
        self.data = load("aapl_trim.json")

    def test_apple_ties_out(self):
        h = filings_map.build_history(self.data, "AAPL", CMAP)
        for fy in ("2018", "2019", "2020"):
            c = h["checks"][fy]
            self.assertEqual([c[k]["status"] for k in ("balance", "cash", "net_income")],
                             ["pass"] * 3, fy)
        self.assertEqual(h["years"][0]["status"], "tied")

    def set_assets(self, end, delta):
        data = copy.deepcopy(self.data)
        for f in data["facts"]["us-gaap"]["Assets"]["units"]["USD"]:
            if f["end"] == end:
                f["val"] += delta
        return data

    def test_planted_mismatch_is_flagged_not_plugged(self):
        h = filings_map.build_history(self.set_assets("2020-09-26", 5000000000), "AAPL", CMAP)
        y = [y for y in h["years"] if y["fy"] == 2020][0]
        self.assertEqual(y["status"], "flagged")
        self.assertEqual(h["checks"]["2020"]["balance"]["status"], "flag")
        self.assertIn("off by 5,000,000,000", h["checks"]["2020"]["balance"]["reason"])
        self.assertTrue(any(f["fy"] == 2020 and f["check"] == "balance" for f in h["flags"]))
        # nothing was forced: assets keep the planted figure, no new line appeared
        self.assertEqual(h["lines"]["total_assets"]["2020"]["val"], 323888000000 + 5000000000)
        self.assertEqual(set(h["lines"]), set(CMAP["lines"]))

    def test_one_unit_difference_is_within_tolerance(self):
        h = filings_map.build_history(self.set_assets("2020-09-26", 1000000), "AAPL", CMAP)
        self.assertEqual(h["checks"]["2020"]["balance"]["status"], "pass")
        h = filings_map.build_history(self.set_assets("2020-09-26", 2000000), "AAPL", CMAP)
        self.assertEqual(h["checks"]["2020"]["balance"]["status"], "flag")


class RestrictedCash(unittest.TestCase):

    def test_reconciled_when_tagged_flagged_when_not(self):
        h = filings_map.build_history(load("aapl_trim.json"), "AAPL", CMAP)
        c19 = h["checks"]["2019"]["cash"]
        self.assertEqual(c19["status"], "pass")
        self.assertIn("reconciled through restricted cash", c19["note"])
        last = c19["tests"][-1]
        self.assertEqual(last["left"], 50224000000)              # cash-flow closing cash
        self.assertEqual(last["right"], 48844000000 + 23000000 + 1357000000)
        c21 = h["checks"]["2021"]["cash"]
        self.assertEqual(c21["status"], "flag")
        self.assertIn("tags no restricted-cash balance", c21["reason"])
        self.assertIn("989,000,000", c21["reason"])


class BankDetection(unittest.TestCase):
    """Banks are routed, no longer refused (tests/test_filings_fin_us.py covers
    routing in full); the switch still builds them on the corporate chart."""

    def test_bank_routed(self):
        h = filings_map.build_history(load("jpm_trim.json"), "JPM", CMAP)
        self.assertEqual(h["sector"], "bank")
        self.assertIn("Deposits", h["routing"])

    def test_rule_can_be_switched_off(self):
        cmap = copy.deepcopy(CMAP)
        cmap["unsupported"]["enabled"] = False
        h = filings_map.build_history(load("jpm_trim.json"), "JPM", cmap)
        self.assertEqual(len(h["years"]), 3)
        self.assertEqual(h["sector"], "corporate")

    def test_cli_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = filings_map.main(["us", os.path.join(FIX, "jpm_trim.json"),
                                     "--ticker", "JPM", "--out", tmp])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(os.listdir(tmp)), ["JPM_history.json", "JPM_history.md"])


class FetchOffline(unittest.TestCase):

    def setUp(self):
        self.saved = fetch_filings._http_get

        def no_network(url, user_agent):
            raise AssertionError("network used: %s" % url)
        fetch_filings._http_get = no_network

    def tearDown(self):
        fetch_filings._http_get = self.saved

    def test_from_file_uses_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = fetch_filings.main(["us", "aapl", "--out", tmp,
                                       "--from-file", os.path.join(FIX, "aapl_trim.json")])
            self.assertEqual(code, 0)
            path = os.path.join(tmp, "AAPL_companyfacts.json")
            with open(path) as f:
                self.assertEqual(json.load(f)["cik"], 320193)
            self.assertEqual(filings_map.main(["us", path, "--ticker", "AAPL", "--out", tmp]), 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "AAPL_history.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "AAPL_history.md")))

    def test_user_agent_required(self):
        with self.assertRaises(SystemExit):
            fetch_filings.main(["us", "AAPL", "--out", tempfile.gettempdir()])
        with self.assertRaises(fetch_filings.FetchError):
            fetch_filings.get_json(fetch_filings.SEC_TICKERS_URL, "no email here")

    def test_only_sec_hosts_allowed(self):
        for url in ("https://example.com/x.json", "http://data.sec.gov/x.json",
                    "https://data.sec.gov.example.com/x.json"):
            with self.assertRaises(fetch_filings.FetchError):
                fetch_filings.check_host(url)
        fetch_filings.check_host("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")

    def test_ticker_to_cik(self):
        tickers = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                   "1": {"cik_str": 18230, "ticker": "CAT", "title": "CATERPILLAR INC"}}
        self.assertEqual(fetch_filings.cik_for_ticker(tickers, "cat"), (18230, "CATERPILLAR INC"))
        with self.assertRaises(fetch_filings.FetchError):
            fetch_filings.cik_for_ticker(tickers, "ZZZZ")

    def test_rate_gap(self):
        calls = []
        fetch_filings._http_get = lambda url, ua: calls.append(
            fetch_filings.time.monotonic()) or b"{}"
        fetch_filings._last_call[0] = 0.0
        for _ in range(3):
            fetch_filings.get_json(fetch_filings.SEC_TICKERS_URL, "Test Ltd t@example.com")
        gaps = [b - a for a, b in zip(calls, calls[1:])]
        self.assertTrue(all(g >= 0.99 for g in gaps), gaps)


R_PAGE = """<html><body><table class="report" id="id1">
<tr><th class="tl" colspan="1" rowspan="2"><div><strong>CASH FLOWS STATEMENTS - USD ($)<br> $ in Millions</strong></div></th>
<th class="th" colspan="2">12 Months Ended</th></tr>
<tr><th class="th"><div>Sep. 25, 2021</div></th><th class="th"><div>Sep. 26, 2020</div></th></tr>
<tr class="re"><td class="pl"><a onclick="Show.showAR( this, 'defref_us-gaap_NetCashProvidedByUsedInOperatingActivitiesAbstract', window );"><strong>Operations</strong></a></td>
<td class="text">&#160;<span></span></td><td class="text">&#160;<span></span></td></tr>
<tr class="ro"><td class="pl"><a onclick="Show.showAR( this, 'defref_us-gaap_NetIncomeLoss', window );">Net income</a></td>
<td class="nump">$ 94,680<span></span></td><td class="nump">$ 57,411<span></span></td></tr>
<tr class="re"><td class="pl custom"><a onclick="Show.showAR( this, 'defref_aapl_DepreciationAmortizationAndOther', window );">Depreciation, amortization, and other</a></td>
<td class="nump">11,284<span></span></td><td class="nump">11,056<span></span></td></tr>
<tr class="ro"><td class="pl custom"><a onclick="Show.showAR( this, 'defref_aapl_OtherNoncashItems', window );">Other</a></td>
<td class="num">(4,921)<span></span></td><td class="nump">97<span></span></td></tr>
</table>
<table class="authRefData" id="defref_x"><tr><td>Name: aapl_DepreciationAmortizationAndOther</td></tr></table>
</body></html>"""

FILING_SUMMARY = b"""<?xml version="1.0" encoding="utf-8"?><FilingSummary><MyReports>
<Report><HtmlFileName>R1.htm</HtmlFileName><ShortName>Cover Page</ShortName><MenuCategory>Cover</MenuCategory></Report>
<Report><HtmlFileName>R2.htm</HtmlFileName><ShortName>CONSOLIDATED STATEMENTS OF OPERATIONS</ShortName><MenuCategory>Statements</MenuCategory></Report>
<Report><HtmlFileName>R4.htm</HtmlFileName><ShortName>CONSOLIDATED BALANCE SHEETS</ShortName><MenuCategory>Statements</MenuCategory></Report>
<Report><HtmlFileName>R5.htm</HtmlFileName><ShortName>CONSOLIDATED BALANCE SHEETS (Parenthetical)</ShortName><MenuCategory>Statements</MenuCategory></Report>
<Report><HtmlFileName>R7.htm</HtmlFileName><ShortName>CONSOLIDATED STATEMENTS OF CASH FLOWS</ShortName><MenuCategory>Statements</MenuCategory></Report>
<Report><HtmlFileName>R9.htm</HtmlFileName><ShortName>Cash Flow Details</ShortName><MenuCategory>Details</MenuCategory></Report>
</MyReports></FilingSummary>"""


def no_da(data):
    """Apple's trimmed facts with its D&A tag removed (a filer like Microsoft)."""
    data = copy.deepcopy(data)
    del data["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]
    return data


def statements(rows):
    page = fetch_filings.parse_r_page(R_PAGE)
    page["rows"] = [r for r in page["rows"] if r["element"] in rows]
    return {"cik": 320193, "accn": "0000320193-21-000105", "form": "10-K",
            "filed": "2021-10-29", "report_date": "2021-09-25",
            "source": "https://www.sec.gov/Archives/edgar/data/320193/000032019321000105/",
            "pages": {"cf": dict(page, title="CONSOLIDATED STATEMENTS OF CASH FLOWS",
                                 file="R7.htm")}}


class StatementPages(unittest.TestCase):
    """A line a company files only under its own tag, read from the 10-K's
    rendered statement page (fetch_filings.py step 4)."""

    def test_r_page_rows_as_printed(self):
        p = fetch_filings.parse_r_page(R_PAGE)
        self.assertEqual(p["scale"], 1000000)
        self.assertEqual(p["ends"], ["2021-09-25", "2020-09-26"])
        self.assertEqual(p["months"], [12, 12])
        rows = {r["element"]: r for r in p["rows"]}
        self.assertEqual(sorted(rows), ["aapl:DepreciationAmortizationAndOther",
                                        "aapl:OtherNoncashItems", "us-gaap:NetIncomeLoss"])
        self.assertEqual(rows["us-gaap:NetIncomeLoss"]["values"], [94680, 57411])
        self.assertFalse(rows["us-gaap:NetIncomeLoss"]["custom"])
        self.assertTrue(rows["aapl:DepreciationAmortizationAndOther"]["custom"])
        self.assertEqual(rows["aapl:OtherNoncashItems"]["values"], [-4921, 97])

    def test_filing_summary_primary_statements_only(self):
        self.assertEqual(fetch_filings.statement_pages(FILING_SUMMARY), {
            "pl": ("CONSOLIDATED STATEMENTS OF OPERATIONS", "R2.htm"),
            "bs": ("CONSOLIDATED BALANCE SHEETS", "R4.htm"),
            "cf": ("CONSOLIDATED STATEMENTS OF CASH FLOWS", "R7.htm")})

    def test_needed_only_when_a_line_has_no_standard_tag(self):
        self.assertEqual(fetch_filings.lines_needing_statements(load("aapl_trim.json"))[0], [])
        self.assertEqual(fetch_filings.lines_needing_statements(no_da(load("aapl_trim.json")))[0],
                         ["da"])

    def test_own_line_fills_the_years_it_prints(self):
        data = no_da(load("aapl_trim.json"))
        data["statements"] = statements(["aapl:DepreciationAmortizationAndOther",
                                         "aapl:OtherNoncashItems", "us-gaap:NetIncomeLoss"])
        h = filings_map.build_history(data, "AAPL", CMAP)
        da = h["lines"]["da"]
        self.assertEqual(sorted(da), ["2020", "2021"])
        self.assertEqual(da["2021"]["val"], 11284000000)
        self.assertEqual(da["2021"]["tag"], "aapl:DepreciationAmortizationAndOther")
        text = " ".join(h["warnings"])
        self.assertIn("FY2020, FY2021 taken from the company's own line", text)
        self.assertIn("aapl:OtherNoncashItems", text)  # listed among the company's own tags
        self.assertEqual([y["status"] for y in h["years"]],
                         [y["status"] for y in filings_map.build_history(
                             load("aapl_trim.json"), "AAPL", CMAP)["years"]])

    def test_two_fitting_rows_are_not_used(self):
        data = no_da(load("aapl_trim.json"))
        st = statements(["aapl:DepreciationAmortizationAndOther"])
        twin = dict(st["pages"]["cf"]["rows"][0], element="aapl:DepreciationAndAmortizationOfSoftware")
        st["pages"]["cf"]["rows"].append(twin)
        data["statements"] = st
        h = filings_map.build_history(data, "AAPL", CMAP)
        self.assertEqual(h["lines"]["da"], {})
        self.assertIn("not read from the 10-K: 2 of the company's own rows fit",
                      " ".join(h["warnings"]))

    def test_fetch_reads_statements_only_from_sec(self):
        facts = no_da(load("aapl_trim.json"))
        folder = "https://www.sec.gov/Archives/edgar/data/320193/000032019321000105/"
        bodies = {
            fetch_filings.SEC_TICKERS_URL: {"0": {"cik_str": 320193, "ticker": "AAPL",
                                                  "title": "Apple Inc."}},
            fetch_filings.SEC_FACTS_URL.format(cik=320193): facts,
            fetch_filings.SEC_SUBMISSIONS_URL.format(cik=320193): {
                "name": "Apple Inc.", "sic": "3571", "sicDescription": "Electronic Computers",
                "filings": {"recent": {"form": ["10-Q", "10-K"],
                                       "accessionNumber": ["x", "0000320193-21-000105"],
                                       "reportDate": ["2021-12-25", "2021-09-25"],
                                       "filingDate": ["2022-01-28", "2021-10-29"]}}},
            folder + "FilingSummary.xml": FILING_SUMMARY,
        }
        for name in ("R2.htm", "R4.htm", "R7.htm"):
            bodies[folder + name] = R_PAGE.encode()
        calls = []

        def fake(url, ua):
            calls.append(url)
            body = bodies[url]
            return body if isinstance(body, bytes) else json.dumps(body).encode()
        saved = fetch_filings._http_get, fetch_filings.MIN_GAP_SECONDS
        fetch_filings._http_get, fetch_filings.MIN_GAP_SECONDS = fake, 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fetch_filings.fetch_us("aapl", tmp, "Test Ltd t@example.com")
                with open(os.path.join(tmp, "AAPL_statements.json")) as f:
                    st = json.load(f)
                self.assertEqual(st["accn"], "0000320193-21-000105")
                self.assertEqual(st["lines_missing"], ["da"])
                path = os.path.join(tmp, "AAPL_companyfacts.json")
                self.assertEqual(filings_map.main(["us", path, "--ticker", "AAPL", "--out", tmp]), 0)
                with open(os.path.join(tmp, "AAPL_history.json")) as f:
                    self.assertEqual(json.load(f)["lines"]["da"]["2021"]["val"], 11284000000)
                calls.clear()
                with open(path, "w") as f:
                    json.dump(load("aapl_trim.json"), f)
                bodies[fetch_filings.SEC_FACTS_URL.format(cik=320193)] = load("aapl_trim.json")
                fetch_filings.fetch_us("aapl", tmp, "Test Ltd t@example.com")
                self.assertFalse([u for u in calls if "/Archives/" in u])  # D&A tagged: no pages
        finally:
            fetch_filings._http_get, fetch_filings.MIN_GAP_SECONDS = saved
        self.assertTrue(all(u.startswith(("https://www.sec.gov/", "https://data.sec.gov/"))
                            for u in calls))


if __name__ == "__main__":
    unittest.main()

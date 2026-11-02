#!/usr/bin/env python3
"""
Fetch a listed company's filed statements (XBRL facts) and save them locally.

This is the ONLY file in the repo allowed to use the network (a check
script enforces it). It talks only to the hosts in
ALLOWED_HOSTS and refuses any other URL.

Usage:
  fetch_filings.py us <TICKER> --out <dir> --user-agent "<Firm> <email>"
  fetch_filings.py us <TICKER> --out <dir> --from-file <companyfacts.json>

US (SEC EDGAR):
  1. ticker -> CIK via https://www.sec.gov/files/company_tickers.json
  2. https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  3. https://data.sec.gov/submissions/CIK##########.json, for the SIC code and the
     list of 10-K filings
  4. Only when a line the map marks `extension` (D&A) has no standard tag in one of
     the last 3 years: the latest 10-K's FilingSummary.xml and the SEC's rendered
     pages of its statements (R2.htm ...) from www.sec.gov/Archives. companyfacts
     holds standard tags only, so a line a company files under its own tag (for
     example Microsoft's "Depreciation, amortization, and other") is missing there.
  Saves <out>/<TICKER>_companyfacts.json (raw, unchanged),
  <out>/<TICKER>_sic.json (CIK, name, SIC code and its description; filings_map.py
  uses the SIC to refuse securities firms) and, after step 4,
  <out>/<TICKER>_statements.json (each statement's rows as printed: element,
  label, values). Every fact keeps
  its accession number (accn). The SEC asks for a User-Agent naming the firm
  and a contact email; there is no default, the user must give one.
  At most 1 call per second (the SEC's own ceiling is 10).

--from-file skips the network entirely: the user downloads the file by hand
and this script only checks it and copies it into --out. A <TICKER>_sic.json or
<TICKER>_statements.json next to a <TICKER>_companyfacts.json is copied too;
without the SIC file, filings_map.py falls back to the balance-sheet test for
securities firms.

Japan (EDINET API v2, Financial Services Agency):
  fetch_filings.py jp <CODE> --out <dir> [--filings 2]
  fetch_filings.py jp <CODE> --out <dir> --from-file <S100XXXX.zip> [...]
  1. The document list (documents.json?type=2) is per submission day. Each past
     day's list is cached in <out>/edinet_lists/ (public, shared by companies).
     The scan runs back from today to the latest 有価証券報告書 (docTypeCode
     120) for the code (EDINET's secCode is the 4-character code plus "0"), then
     around the same date two years earlier for the report before.
  2. documents/<docID>?type=5 downloads the CSV form of the XBRL (a zip).
  Saves <out>/<CODE>_<docID>.zip per report and <out>/<CODE>_edinet.json, the
  list of reports. Two reports two years apart hold four years of statements.
  The API key comes from the environment variable EDINET_API_KEY (each user
  registers for their own, see docs/DATA-SOURCES.md 2.2). The key is never
  printed, logged or saved: it is removed from every error message. EDINET
  answers errors with HTTP 200 and the status in the JSON body; that is checked.
  At most 1 call per second.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

ALLOWED_HOSTS = {"www.sec.gov", "data.sec.gov", "api.edinet-fsa.go.jp"}
MIN_GAP_SECONDS = 1.0
TIMEOUT_SECONDS = 60

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

_last_call = [0.0]


class FetchError(Exception):
    pass


# network ----------------------------------------------------------------------
def check_host(url):
    """Refuse any URL that is not https on an allowed host."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise FetchError("refused: %s is not on the allowed host list %s"
                         % (url, sorted(ALLOWED_HOSTS)))


def _http_get(url, user_agent):
    """One GET. Kept separate so tests can replace it to prove no network use."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read()


def get_json(url, user_agent):
    """Rate-limited, host-checked GET returning parsed JSON."""
    return json.loads(get_bytes(url, user_agent).decode("utf-8"))


def get_bytes(url, user_agent):
    """Rate-limited, host-checked GET returning the raw body."""
    check_host(url)
    if not user_agent or "@" not in user_agent:
        raise FetchError('a User-Agent naming your firm and a contact email is '
                         'required, e.g. --user-agent "Example Ltd ops@example.com"')
    wait = _last_call[0] + MIN_GAP_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        body = _http_get(url, user_agent)
    finally:
        _last_call[0] = time.monotonic()
    return body


# US: SEC EDGAR ----------------------------------------------------------------
def cik_for_ticker(tickers, ticker):
    """tickers: parsed company_tickers.json. Return (cik int, company title)."""
    want = ticker.upper()
    for row in tickers.values():
        if str(row.get("ticker", "")).upper() == want:
            return int(row["cik_str"]), row.get("title", "")
    raise FetchError("ticker %s is not in the SEC ticker list" % ticker)


def check_companyfacts(data):
    """Raise unless data looks like an SEC companyfacts file."""
    if not isinstance(data, dict) or "facts" not in data or "cik" not in data:
        raise FetchError("not an SEC companyfacts file (no 'cik'/'facts' keys)")
    if "us-gaap" not in data["facts"]:
        raise FetchError("companyfacts file has no us-gaap facts")


def fetch_us(ticker, out_dir, user_agent=None, from_file=None):
    """Save <out>/<TICKER>_companyfacts.json. Return its path."""
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, "%s_companyfacts.json" % ticker.upper())
    sic_dest = os.path.join(out_dir, "%s_sic.json" % ticker.upper())
    st_dest = os.path.join(out_dir, "%s_statements.json" % ticker.upper())
    if from_file:
        with open(from_file, encoding="utf-8") as f:
            check_companyfacts(json.load(f))
        if os.path.abspath(from_file) != os.path.abspath(dest):
            shutil.copyfile(from_file, dest)
            stem = (from_file[:-len("_companyfacts.json")]
                    if from_file.endswith("_companyfacts.json") else None)
            for suffix, target in (("_sic.json", sic_dest), ("_statements.json", st_dest)):
                if stem and os.path.exists(stem + suffix):
                    shutil.copyfile(stem + suffix, target)
        return dest
    tickers = get_json(SEC_TICKERS_URL, user_agent)
    cik, title = cik_for_ticker(tickers, ticker)
    print("%s -> CIK %010d (%s)" % (ticker.upper(), cik, title))
    data = get_json(SEC_FACTS_URL.format(cik=cik), user_agent)
    check_companyfacts(data)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f)
    url = SEC_SUBMISSIONS_URL.format(cik=cik)
    sub = get_json(url, user_agent)
    sic = {"cik": cik, "name": sub.get("name"), "sic": sub.get("sic"),
           "sicDescription": sub.get("sicDescription"), "source": url}
    print("%s -> SIC %s (%s)" % (ticker.upper(), sic["sic"], sic["sicDescription"]))
    with open(sic_dest, "w", encoding="utf-8") as f:
        json.dump(sic, f, indent=1)
    missing, years = lines_needing_statements(data)
    if missing:
        try:
            st = fetch_statements(cik, sub, years[-1]["end"], user_agent)
        except (FetchError, OSError, ValueError, ET.ParseError) as e:
            print("warning: %s has no standard tag for %s, and its 10-K statements could "
                  "not be read (%s); the line stays missing" % (ticker.upper(), ", ".join(missing), e))
        else:
            st["lines_missing"] = missing
            with open(st_dest, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=1)
            print("%s -> 10-K %s statements (%s): no standard tag for %s"
                  % (ticker.upper(), st["accn"], ", ".join(sorted(st["pages"])), ", ".join(missing)))
    return dest


# US: the 10-K's rendered statements (only when a line has no standard tag) ----
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"
RECENT_YEARS = 3                 # the actual years a listed model shows
STATEMENT_KINDS = [              # FilingSummary ShortName -> kind; first match wins
    ("cf", ("CASH FLOW",)),
    ("oci", ("COMPREHENSIVE",)),
    ("equity", ("EQUITY", "STOCKHOLDERS", "SHAREHOLDERS")),
    ("bs", ("BALANCE SHEET", "FINANCIAL POSITION", "FINANCIAL CONDITION")),
    ("pl", ("INCOME", "OPERATIONS", "EARNINGS")),
]
R_DATE = re.compile(r"^([A-Z][a-z]{2})\.? (\d{1,2}), (\d{4})$")
R_MONTHS = re.compile(r"^(\d+) Months Ended$")
R_SCALE = re.compile(r"\$ in (Thousands|Millions|Billions)")
SCALES = {"Thousands": 1000, "Millions": 1000000, "Billions": 1000000000}


def lines_needing_statements(data):
    """(map lines marked `extension` with no standard-tag value in one of the last
    RECENT_YEARS fiscal years, the fiscal years). Corporate route only: a bank or
    insurer goes to its own map, which has no such line."""
    import filings_map
    cmap = filings_map.load_map()
    gaap = data["facts"]["us-gaap"]
    years = filings_map.fiscal_years(gaap, cmap)
    if not years or filings_map.unsupported_reason(gaap, years, cmap):
        return [], years
    recent = years[-RECENT_YEARS:]
    return [k for k, spec in cmap["lines"].items() if spec.get("extension") and any(
        filings_map.pick_line(gaap, spec, y["end"], cmap) is None for y in recent)], years


def fetch_statements(cik, sub, end, user_agent):
    """The 10-K for the year ending `end`: its FilingSummary.xml, then the SEC's
    rendered page of each primary statement, parsed into rows as printed."""
    recent = (sub.get("filings") or {}).get("recent") or {}
    hit = next(((a, f) for form, a, r, f in zip(recent.get("form", []),
                                                 recent.get("accessionNumber", []),
                                                 recent.get("reportDate", []),
                                                 recent.get("filingDate", []))
                if form == "10-K" and r == end), None)
    if hit is None:
        raise FetchError("no 10-K for the year ended %s in the SEC submissions list" % end)
    accn, filed = hit
    folder = SEC_ARCHIVE_URL.format(cik=cik, folder=accn.replace("-", ""))
    pages = statement_pages(get_bytes(folder + "FilingSummary.xml", user_agent))
    if "cf" not in pages:
        raise FetchError("FilingSummary.xml of %s names no cash flow statement" % accn)
    out = {"cik": cik, "accn": accn, "form": "10-K", "filed": filed, "report_date": end,
           "source": folder, "pages": {}}
    for kind, (title, name) in sorted(pages.items()):
        page = parse_r_page(get_bytes(folder + name, user_agent).decode("utf-8", "replace"))
        out["pages"][kind] = dict(page, title=title, file=name, url=folder + name)
    return out


def statement_pages(summary_xml):
    """FilingSummary.xml -> {kind: (ShortName, HtmlFileName)} for the primary
    statements (MenuCategory Statements, not the parenthetical pages)."""
    out = {}
    for rep in ET.fromstring(summary_xml).iter("Report"):
        name, page = rep.findtext("ShortName") or "", rep.findtext("HtmlFileName")
        if rep.findtext("MenuCategory") != "Statements" or not page:
            continue
        upper = name.upper()
        if "PARENTHETICAL" in upper:
            continue
        for kind, words in STATEMENT_KINDS:
            if any(w in upper for w in words):
                out.setdefault(kind, (name, page))
                break
    return out


class _RPage(HTMLParser):
    """Collects the cells of the first <table class="report"> of an R page."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.depth, self.done, self.rows, self.cell = 0, False, [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self.depth or (not self.done and "report" in (a.get("class") or "").split()):
                self.depth += 1
            return
        if not self.depth:
            return
        if tag == "tr" and self.depth == 1:
            self.rows.append([])
        elif tag in ("th", "td") and self.depth == 1 and self.rows:
            self.cell = {"tag": tag, "class": (a.get("class") or "").split(),
                         "colspan": int(a.get("colspan") or 1), "text": "", "ref": None}
            self.rows[-1].append(self.cell)
        elif tag == "a" and self.cell is not None:
            m = re.search(r"defref_([A-Za-z0-9-]+)_(\w+)", a.get("onclick") or "")
            if m:
                self.cell["ref"] = "%s:%s" % m.groups()

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
            self.done = self.done or self.depth == 0
        elif tag in ("th", "td"):
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


def r_number(text):
    """'$ 133,749' -> 133749; '(1,234)' -> -1234; blank -> None."""
    t = text.replace("$", "").replace(",", "").replace("\xa0", "").strip()
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    try:
        n = float(t)
    except ValueError:
        return None
    n = int(n) if n == int(n) else n
    return -n if neg else n


def parse_r_page(html):
    """An SEC rendered statement page -> {scale, ends, months, rows}. Each row:
    {element, label, custom, values}; values line up with `ends` (period ends,
    ISO dates) and `months` (the period length, None on a balance sheet)."""
    p = _RPage()
    p.feed(html)
    head = [c for r in p.rows for c in r if c["tag"] == "th"]
    if not head:
        raise ValueError("no statement table on the page")
    title = " ".join(head[0]["text"].split())
    m = R_SCALE.search(title)
    scale = SCALES[m.group(1)] if m else 1
    ends, months = [], []
    for c in head[1:]:
        text = " ".join(c["text"].split())
        dm, mm = R_DATE.match(text), R_MONTHS.match(text)
        if dm:
            ends.append(datetime.strptime(" ".join(dm.groups()), "%b %d %Y").date().isoformat())
        elif mm:
            months.extend([int(mm.group(1))] * c["colspan"])
    if months and len(months) != len(ends):
        raise ValueError("period headers do not line up with the date columns")
    rows = []
    for r in p.rows:
        if not r or r[0]["tag"] != "td" or not r[0]["ref"]:
            continue
        values = [r_number(c["text"]) for c in r[1:1 + len(ends)]]
        if all(v is None for v in values):
            continue
        rows.append({"element": r[0]["ref"], "label": " ".join(r[0]["text"].split()),
                     "custom": "custom" in r[0]["class"], "values": values})
    return {"scale": scale, "ends": ends, "months": months or [None] * len(ends),
            "rows": rows}


# JP: EDINET -------------------------------------------------------------------
EDINET_API = "https://api.edinet-fsa.go.jp/api/v2/"
EDINET_LIST_URL = EDINET_API + "documents.json?date={date}&type=2"
EDINET_DOC_URL = EDINET_API + "documents/{doc_id}?type=5"
EDINET_KEY_ENV = "EDINET_API_KEY"
EDINET_ANNUAL = "120"            # docTypeCode of 有価証券報告書 (annual securities report)
EDINET_UA = "Stargira Tie-Out (fetch_filings.py)"
LATEST_SCAN_DAYS = 400           # how far back from today to look for the latest report
OLDER_SCAN_DAYS = 75             # either side of the expected date for an older report
DOC_ID = re.compile(r"S1[0-9A-Z]{6}")
NO_KEY_MESSAGE = (
    "no EDINET API key: set the environment variable %s to your own key. EDINET "
    "issues a key to each user who registers on its site (an email address, a "
    "password and a phone number for the security code); Stargira cannot register "
    "for you. How to register: docs/DATA-SOURCES.md section 2.2. Then run, for "
    "example:  export %s=<your key>" % (EDINET_KEY_ENV, EDINET_KEY_ENV))


def edinet_key():
    key = os.environ.get(EDINET_KEY_ENV, "").strip()
    if not key:
        raise FetchError(NO_KEY_MESSAGE)
    return key


def redact(text, key=None):
    """Remove the API key from any text before it is printed or raised."""
    text = re.sub(r"(Subscription-Key=)[^&\s'\"]*", r"\1<redacted>", str(text))
    if key:
        text = text.replace(key, "<redacted>")
    return text


def _edinet_get(url, key):
    """Rate-limited, host-checked GET with the key added. Errors never carry the key."""
    check_host(url)
    sep = "&" if "?" in url else "?"
    full = url + sep + urllib.parse.urlencode({"Subscription-Key": key})
    wait = _last_call[0] + MIN_GAP_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        return _http_get(full, EDINET_UA)
    except Exception as e:      # the message may hold the full URL with the key
        raise FetchError("EDINET call failed for %s: %s" % (url, redact(e, key))) from None
    finally:
        _last_call[0] = time.monotonic()


def edinet_status_error(body):
    """EDINET answers errors with HTTP 200 and the real status in the JSON body.

    Return a plain message for an error body, or None for a good one."""
    if not isinstance(body, dict):
        return "the answer is not a JSON object"
    meta = body.get("metadata") or {}
    status = str(meta.get("status") or body.get("StatusCode") or body.get("statusCode") or "")
    if status in ("", "200"):
        return None
    msg = meta.get("message") or body.get("message") or ""
    if status == "401":
        return ("EDINET refused the API key (status 401: %s). Check %s holds your own "
                "current key; reissuing a key cancels the old one" % (msg, EDINET_KEY_ENV))
    if status == "429":
        return "EDINET says too many requests (status 429); wait a while and run again"
    return "EDINET returned status %s: %s" % (status, msg)


def edinet_json(url, key):
    raw = _edinet_get(url, key)
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise FetchError("EDINET answer for %s is not JSON" % url) from None
    err = edinet_status_error(body)
    if err:
        raise FetchError(redact(err, key))
    return body


def day_list(day, key, cache_dir, today):
    """The filings submitted on one day (type=2 list). Past days are cached on disk.

    The cache keeps only the results (public filing metadata), never the request."""
    path = os.path.join(cache_dir, "%s.json" % day.isoformat())
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["results"]
    body = edinet_json(EDINET_LIST_URL.format(date=day.isoformat()), key)
    results = body.get("results") or []
    if day < today:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": day.isoformat(), "results": results}, f, ensure_ascii=False)
    return results


def edinet_sec_code(code):
    code = str(code).strip()
    if not re.fullmatch(r"[0-9][0-9A-Z][0-9][0-9A-Z]", code):
        raise FetchError("a Japanese securities code has 4 characters, e.g. 7974 (got %r)" % code)
    return code + "0"


def annual_reports(results, sec5):
    """Type-120 filings for this securities code with CSV data, not withdrawn."""
    return [r for r in results if r.get("secCode") == sec5
            and r.get("docTypeCode") == EDINET_ANNUAL
            and str(r.get("csvFlag")) == "1" and str(r.get("withdrawalStatus", "0")) == "0"]


def find_report(sec5, days, key, cache_dir, today, want_period_end=None):
    """Scan the given days in order; return the first annual report found, or None."""
    for day in days:
        hits = annual_reports(day_list(day, key, cache_dir, today), sec5)
        if want_period_end:
            hits = [r for r in hits if r.get("periodEnd") == want_period_end]
        if hits:
            return sorted(hits, key=lambda r: r.get("submitDateTime") or "")[-1]
    return None


def years_back(day, n):
    try:
        return day.replace(year=day.year - n)
    except ValueError:              # 29 February
        return day.replace(year=day.year - n, day=28)


def check_csv_zip(path):
    """Raise unless path is an EDINET type-5 zip holding an annual-report CSV."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        raise FetchError("%s is not a zip file (EDINET CSV downloads are zips)" % path) from None
    if not any(n.endswith(".csv") and "jpcrp" in os.path.basename(n) for n in names):
        raise FetchError("%s holds no jpcrp*.csv (not an EDINET type-5 CSV download)" % path)


def fetch_jp(code, out_dir, from_files=None, doc_ids=None, filings=2, today=None):
    """Save each annual report's CSV zip as <out>/<CODE>_<docID>.zip and a manifest
    <out>/<CODE>_edinet.json. Return the manifest path."""
    sec5 = edinet_sec_code(code)
    code = code.upper()
    os.makedirs(out_dir, exist_ok=True)
    manifest = {"code": code, "sec_code": sec5, "source": "EDINET API v2, document type 5 (CSV)",
                "filings": []}
    if from_files:
        doc_ids = list(doc_ids or [])
        for i, src in enumerate(from_files):
            doc_id = doc_ids[i] if i < len(doc_ids) else None
            if not doc_id:
                m = DOC_ID.search(os.path.basename(src))
                if not m:
                    raise FetchError("cannot tell the EDINET docID of %s from its name; "
                                     "pass --doc-id S100XXXX for it" % src)
                doc_id = m.group(0)
            dest = os.path.join(out_dir, "%s_%s.zip" % (code, doc_id))
            if src.lower().endswith(".csv"):
                with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
                    z.write(src, "XBRL_TO_CSV/" + os.path.basename(src))
            else:
                check_csv_zip(src)
                if os.path.abspath(src) != os.path.abspath(dest):
                    shutil.copyfile(src, dest)
            check_csv_zip(dest)
            manifest["filings"].append({"docID": doc_id, "file": os.path.basename(dest),
                                        "from_file": os.path.basename(src)})
    else:
        key = edinet_key()
        today = today or date.today()
        cache = os.path.join(out_dir, "edinet_lists")
        latest = find_report(sec5, [today - timedelta(days=i) for i in range(LATEST_SCAN_DAYS)],
                             key, cache, today)
        if latest is None:
            raise FetchError("no 有価証券報告書 with CSV data for securities code %s in the "
                             "last %d days of EDINET filings" % (code, LATEST_SCAN_DAYS))
        found = [latest]
        submitted = date.fromisoformat(latest["submitDateTime"][:10])
        period_end = date.fromisoformat(latest["periodEnd"])
        for n in range(2, 2 * filings - 1, 2):     # two years apart: each report holds 2 years
            expect = years_back(submitted, n)
            days = [expect]
            for i in range(1, OLDER_SCAN_DAYS + 1):
                days += [expect - timedelta(days=i), expect + timedelta(days=i)]
            days = [x for x in days if x < today]
            older = find_report(sec5, days, key, cache, today,
                                years_back(period_end, n).isoformat())
            if older is None:
                print("note: no 有価証券報告書 for the year ended %s found within %d days of %s"
                      % (years_back(period_end, n), OLDER_SCAN_DAYS, expect))
                break
            found.append(older)
        for r in found:
            print("%s -> %s, %s, period %s to %s, filed %s" % (
                code, r["docID"], r.get("filerName"), r.get("periodStart"), r.get("periodEnd"),
                r.get("submitDateTime")))
            raw = _edinet_get(EDINET_DOC_URL.format(doc_id=r["docID"]), key)
            if raw[:2] != b"PK":            # an error comes back as JSON, still HTTP 200
                try:
                    err = edinet_status_error(json.loads(raw.decode("utf-8")))
                except ValueError:
                    err = None
                raise FetchError(redact("download of %s failed: %s" % (
                    r["docID"], err or "the answer is not a zip file"), key))
            dest = os.path.join(out_dir, "%s_%s.zip" % (code, r["docID"]))
            with open(dest, "wb") as f:
                f.write(raw)
            check_csv_zip(dest)
            keep = ("docID", "edinetCode", "secCode", "filerName", "docDescription",
                    "periodStart", "periodEnd", "submitDateTime", "docTypeCode")
            entry = {k: r.get(k) for k in keep}
            entry["file"] = os.path.basename(dest)
            manifest["filings"].append(entry)
    path = os.path.join(out_dir, "%s_edinet.json" % code)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return path


# CLI --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="market")
    us = sub.add_parser("us", help="US filer, SEC EDGAR companyfacts")
    us.add_argument("ticker")
    us.add_argument("--out", required=True, help="folder for the raw JSON")
    us.add_argument("--user-agent", help='"<Firm> <contact email>" (required unless --from-file)')
    us.add_argument("--from-file", help="use this downloaded companyfacts JSON; no network")
    jp = sub.add_parser("jp", help="Japanese filer, EDINET 有価証券報告書 (CSV)")
    jp.add_argument("code", help="4-character securities code, e.g. 7974")
    jp.add_argument("--out", required=True, help="folder for the downloaded zips")
    jp.add_argument("--filings", type=int, default=2,
                    help="annual reports to fetch, two years apart (default 2 = 4 years)")
    jp.add_argument("--from-file", nargs="+",
                    help="use these saved type-5 zips (or their jpcrp CSV); no network")
    jp.add_argument("--doc-id", nargs="+", help="the docID of each --from-file, if its "
                    "file name does not contain it")
    args = ap.parse_args(argv)
    if args.market not in ("us", "jp"):
        ap.error("choose a market: us or jp")
    try:
        if args.market == "jp":
            if not 1 <= args.filings <= 5:
                ap.error("--filings must be 1 to 5")
            path = fetch_jp(args.code, args.out, args.from_file, args.doc_id, args.filings)
        else:
            if not args.from_file and not args.user_agent:
                ap.error('--user-agent "<Firm> <contact email>" is required (the SEC asks '
                         'for one); or pass --from-file to skip the network')
            path = fetch_us(args.ticker, args.out, args.user_agent, args.from_file)
    except (FetchError, OSError, ValueError) as e:
        print("error: %s" % redact(e, os.environ.get(EDINET_KEY_ENV, "").strip() or None),
              file=sys.stderr)
        return 1
    print("saved %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

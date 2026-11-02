# Data sources for "from filings" mode

Checked 1–2 November 2026. Every URL below was loaded in that session, and the text beside it matches what the page said then.

**In short:**
- **US statements** come free from the SEC's `companyfacts` API. No key is needed. Send a User-Agent that names the firm and a contact email, and stay well under 10 requests a second. SEC content may be reused; citing the SEC is requested.
- **Japanese statements** come from the EDINET API v2. **Every call needs an API key.** Each user must register for their own key (email, password and a phone number for SMS or voice checks). Reuse is allowed under PDL1.0, with a source credit and an "edited by" note. Scraping the website is banned; the API is the allowed route.
- **Share prices** are never fetched by the tool. The user looks them up on the exchange's own page (Nasdaq, NYSE or JPX). All three pages restrict use to the viewer's own, personal use and ban automated capture.
- **Share counts** come from the filings themselves, so the user only has to type the price.
- **Banks and insurers** use a different statement layout in both countries: no current/non-current split, and no "sales" or "cost of sales" lines.

Labels: **[D]** = read on the primary page this session. **[I]** = read on a third-party page. **[S]** = my judgment.

---

## 1. United States: SEC EDGAR

### 1.1 The API

- **Endpoint.** `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`. It "returns all the company concepts data for a company" in one call. The CIK (Central Index Key, the SEC's company number) is "10-digit … including leading zeros". [D] [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- **No key.** "These APIs do not require any authentication or API keys to access." [D] (same page)
- **Bulk file.** `companyfacts.zip` holds the same data for every company. It is republished nightly at about 3:00 a.m. ET. [D] (same page). The page links it as `https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip`. A header-only request on 2 November 2026 returned HTTP 200, 1,414,913,699 bytes (about 1.4 GB), last modified 31 Oct 2026. The file itself was not downloaded. [D]
- **Sibling endpoints** on the same page: `companyconcept` (one tag for one company) and `frames` (one tag across all companies for one period). [D]

### 1.2 Fair-access rules

- **Rate cap:** "Current max request rate: 10 requests/second." [D] [Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data). The Internet Security Policy says the same, and adds that the SEC "does not allow 'unclassified' bots". [D] [SEC privacy and security policy](https://www.sec.gov/privacy.htm)
- **User-Agent:** declare one in the request headers, in the form "Sample Company Name AdminContact@<sample company domain>.com". [D] [Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data). The developer FAQ links this to the "Undeclared Automated Tool" error. [D] [Webmaster FAQ](https://www.sec.gov/os/webmaster-faq)
- **Our setting:** `Stargira Advisors Ltd kyasuda@stargira.com`, at most 1 request a second (the tool's own limit). [S]
- **If you go over:** "SEC reserves the right to limit request rates to preserve fair access for all users." [D] [Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data)

### 1.3 Reuse terms

- "All Government-created content on sec.gov and EDGAR public filing content are free to access and reuse." The only exceptions named are things like stock photos. [D] [Webmaster FAQ](https://www.sec.gov/os/webmaster-faq)
- sec.gov information "may be copied or further distributed"; "Please consider appropriate citation to the SEC as the source." [D] [SEC privacy and security policy](https://www.sec.gov/privacy.htm)
- **So:** each model's Sources sheet should say "Source: SEC EDGAR XBRL data (companyfacts), accession no. …". [S]

### 1.4 Ticker → CIK map

- `https://www.sec.gov/files/company_tickers.json`, a "json file for ticker/CIK/Company name associations". A tab-separated twin, `ticker.txt`, also exists. The SEC warns: "We periodically update the file but do not guarantee accuracy or scope." [D] [Webmaster FAQ](https://www.sec.gov/os/webmaster-faq)

### 1.5 Live test (2 November 2026, 2 requests, 2 seconds apart)

Raw files are in the scratchpad only, not the repo.

| Call | HTTP | Size |
|---|---|---|
| `https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` (Apple) | 200, `application/json` | 271,819 bytes (compressed transfer) |
| `https://www.sec.gov/files/company_tickers.json` | 200, `application/json` | 219,935 bytes |

What the Apple response contains [D, from the response]:
- **Top-level keys:** `cik` (320193, a number with no leading zeros), `entityName` ("Apple Inc."), `facts`.
- **Taxonomies under `facts`:** `us-gaap` (503 tags) and `dei` (2 tags). `dei` (Document and Entity Information, the cover-page tags) holds only `EntityCommonStockSharesOutstanding` and `EntityPublicFloat`.
- **Each fact** has the fields `start` (only for flows such as revenue), `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`, and sometimes `frame`.
  - **Accession number:** all 25,135 facts carry `accn` (the filing's ID number, e.g. `0000320193-18-000145`). Each history column can therefore cite its filing.
  - **Form types seen:** 10-Q (15,304 facts), 10-K (8,503), 8-K (1,118), 10-K/A (210).
  - **Fiscal fields:** `fp` is FY, Q1, Q2 or Q3 (569 facts have none). Watch out: `fy`/`fp` describe the *filing*, not the fact's own period. A fact with `start` 2018-07-01 and `end` 2018-09-29 (one quarter) is tagged `fy 2018, fp FY` because it came from the 10-K. The build must pick annual values by `start`/`end` (about 12 months) and use `frame` where present. Only 9,802 of 25,135 facts have `frame`.
  - **Typical tags present:** `AssetsCurrent`, `LiabilitiesCurrent`, `Assets`, `StockholdersEquity`, `NetIncomeLoss`, `CashAndCashEquivalentsAtCarryingValue`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `WeightedAverageNumberOfDilutedSharesOutstanding`, `CommonStockSharesOutstanding`.
- **`dei:EntityCommonStockSharesOutstanding` is present** (unit `shares`, 70 values since 2009). This is the cover-page count, dated near the filing date, not at year end. Example: the 10-K filed 2025-10-31 gives the count as of 2025-10-17. The latest value is 14,594,180,000 shares as of 2026-07-17 (10-Q filed 2026-07-31).

The ticker file is a JSON object keyed "0", "1", … with `cik_str`, `ticker` and `title`. It has 10,423 rows; AAPL maps to 320193. [D, from the response]

---

### 1.6 A line under the company's own tag: the 10-K statement pages

- **The gap.** `companyfacts` holds standard taxonomies only, so a line a company files under its own tag is missing there. Microsoft files D&A as `msft:DepreciationAmortizationAndOther` ("Depreciation, amortization, and other"), so no standard D&A tag has a value in any year. [D]
- **The route.** For each 10-K, EDGAR publishes `FilingSummary.xml` (the list of statement pages) and the SEC's rendered page of each statement (`R2.htm` …) in the filing folder `https://www.sec.gov/Archives/edgar/data/<CIK>/<accession without dashes>/`. Each row names its element (`defref_msft_DepreciationAmortizationAndOther`), marks the company's own rows (class `custom`) and shows the values as printed, with the scale in the title (`$ in Millions`). [D] Microsoft FY2026 10-K 0001193125-26-323660: `FilingSummary.xml` 68,644 bytes, cash flow page `R6.htm` 92,655 bytes, fetched 2 November 2026.
- **When it is used.** Only when a line the map marks `extension` (D&A) has no standard-tag value in one of the last 3 years; then the latest 10-K's primary statements are read (for Microsoft, the summary and 5 statement pages: 6 more requests on top of the usual 3). [D]
- **Checked two ways (2 November 2026).** The rendered page and the filed 10-K document (`msft-20260630.htm`, inline XBRL) both give 38,534 / 29,433 / 20,958 ($m) for FY2026 / FY2025 / FY2024. [D]
- **Reprinted years differ.** Microsoft's FY2025 10-K (0000950170-25-100235) printed this line as 34,153 for FY2025 and 22,287 for FY2024. Its FY2026 10-K reprints them as 29,433 and 20,958. The tool takes all three years from the latest 10-K, so they share one basis. [D]

## 2. Japan: EDINET API v2 (Financial Services Agency)

Main sources:
- the terms of use, [EDINET 利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)
- the document list on [EDINET API関連資料](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html)
- the **EDINET API仕様書 (Version 2), dated June 2026**, at [ESE140206.pdf](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf). Its page numbers are cited below.

### 2.1 Endpoints

- **Base:** `https://api.edinet-fsa.go.jp/api/v2/`. Version 2 is written `v2`. GET only, over TLS 1.2 or later. [D] spec p.39–40
- **Document list (書類一覧API):** `…/api/v2/documents.json?date=YYYY-MM-DD&type=1|2&Subscription-Key=<key>`. [D] spec p.39–41
  - `date` is required. It must be today or earlier, and within the last 10 years.
  - `type=1` (the default) returns the count only. `type=2` returns the list of filings plus the count.
  - Useful fields in each result: `docID`, `edinetCode`, `secCode` (5-digit securities code), `docTypeCode`, `periodEnd`, `xbrlFlag`, `pdfFlag`, `csvFlag`. [D] spec p.48–50
- **Document download (書類取得API):** `…/api/v2/documents/<docID>?type=N&Subscription-Key=<key>`. [D] spec p.75–77. The `type` values are:
  - `1` = filing body + audit report, **including the XBRL files** (when `xbrlFlag` = 1)
  - `2` = the PDF
  - `3` = substitute documents and attachments
  - `4` = English-language files
  - `5` = **CSV**, the XBRL data converted to CSV (when `csvFlag` = 1)
- **How long filings stay available:** 有価証券報告書 (annual securities report) and 半期報告書 (half-year report) can be viewed for 10 years (5 + 5). 四半期報告書 (quarterly report) can be viewed for 10 years (3 + 7). [D] spec p.5
- **No browser calls:** "EDINET API では、クロスドメインの通信を許可しません" — the API refuses cross-domain calls, so JavaScript in a web page cannot reach it. [D] spec p.39, p.75.

### 2.2 API key: required

- "EDINET API への接続の際には、EDINET から発行されたキー（以下「API キー」という。）を使用した認証を行います。API キーの取得に際しては、アカウントの作成が必要となります。" (Every connection uses an EDINET-issued API key, and getting one requires an account.) [D] spec p.13
- `Subscription-Key` is marked required (○) on both endpoints. [D] spec p.40, p.76
- **Live test, 2 November 2026, one call, no key:** `GET https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-10-30&type=1` returned **HTTP 200** with the body `{"StatusCode": 401,"message": "Access denied due to invalid subscription key.Make sure to provide a valid key for an active subscription."}`. [D]
  - **So no call works without a key.**
  - **Coding note:** errors arrive with HTTP status 200, and the real status is in the JSON body. The spec says so too. For downloads, check `Content-Type` to tell a file from an error. [D] spec p.82, p.84
- **Other status codes:** 400, 401, 404, **429 "Too Many Requests"** ("十分な時間を空けて再試行してください。また、取得間隔を見直してください。" — wait well before retrying, and review the request interval), 500. [D] spec p.82–84

**How a user gets a key (user step; Stargira does not do this for them)** [D] spec p.13–20:
1. Allow pop-ups for `https://api.edinet-fsa.go.jp` in the browser (spec §2-2).
2. Open the EDINET viewing site. Click the login button ("EDINET APIの利用登録はこちらから", seen in the HTML of the [EDINET download page](https://disclosure2.edinet-fsa.go.jp/weee0010.aspx)). Then click 「今すぐサインアップ」 (sign up now).
3. Enter an email address and confirm it with the code sent by email. Set a password: 12–256 characters, using at least 3 of lower case, upper case, digits and symbols.
4. Set up multi-factor authentication with a **phone number**, by SMS code or automated voice call.
5. On the API key screen, register contact details. The key is then shown. Save it; reissuing a key cancels the old one.

### 2.3 Licence and credit

- **Licence:** website content may be used under 「公共データ利用規約（第1.0版）」（PDL1.0）. [D] [EDINET 利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)
- PDL1.0 allows commercial use ("商用利用も可能です") and is compatible with CC BY 4.0. [D] [PDL1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)
- **Credit wording EDINET asks for** [D] (EDINET terms):
  - Source credit: 「出典：EDINET閲覧（提出）サイト（当該ページのURL）、PDL1.0（規約原文ページのURL）」
  - Edited content: 「本コンテンツを編集・加工等して利用する場合は、上記出典とは別に、編集・加工等を行ったこと及びその主体を記載してください。」 (If you edit or process the content, say so, and say who did it, separately from the source credit.) Example: 「EDINET閲覧（提出）サイト（当該ページのURL）をもとに○○株式会社作成」
  - Do not present edited data as if it were the government's unedited original.
- **Suggested wording for Tie-Out models** [S]: 「出典：EDINET閲覧（提出）サイト（<書類のURL>）、PDL1.0（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）。EDINET閲覧（提出）サイトのデータをもとにStargira Tie-Out（Stargira Advisors Ltd）により加工」 (it names the tool and its maker as the processor, not Stargira as the maker of the model, because the user runs the tool and sets the inputs)
- **Exclusions:** the terms do not apply to logos, the EDINET taxonomy (covered by its own legal statement), the XBRL authoring tool and the large-shareholding Excel form. [D] (EDINET terms). PDL1.0 adds that content where a third party holds rights is not covered (§1.2.1). [D] (PDL1.0 page)
  - The EDINET terms say nothing specific about who holds copyright in company filings.
  - PDL1.0 says plain numbers and simple tables are not copyright works. [D] (PDL1.0 page)
  - **Practical reading** [S]: using the financial figures is fine. Reproducing a filing's narrative text in bulk is less clear.

### 2.4 Scraping ban and rate guidance

- **Ban:** 「ウェブサイトから情報を抽出するコンピュータソフトウェア技術（スクレイピング等）を利用して本ウェブサイトからコンテンツを機械的に取得することは禁止します。」 (Using scraping software to pull content from the site by machine is banned.) The exception is the API, or content the API cannot supply. 「本ウェブサイトのコンテンツを機械的に取得するには、API機能を利用してください」 (Use the API to retrieve content by machine.) [D] (EDINET terms)
- **Rate:** no number is given anywhere.
  - The FSA may limit access depending on load.
  - "短時間における大量のアクセス" (heavy access in a short time) is a banned act.
  - The API may stop or slow down without notice.
  - Using the API counts as agreeing to the terms; there is no separate sign-up for the terms. [D] (EDINET terms)
- **Our setting** [S]: at most 1 request a second, and back off on a 429.

### 2.5 Securities code → EDINET code

- The spec names fixed links for the code list. [D] spec p.86
  - Japanese: `https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip`
  - English: `https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelisteng/Edinetcode.zip` (header-only check 2 November 2026: HTTP 200, 572,821 bytes) [D]
- **Downloaded once on 2 November 2026** (HTTP 200, 573,797 bytes; scratchpad only) [D]:
  - One file, `EdinetcodeDlInfo.csv`, in **Shift-JIS (CP932)** encoding, with 11,432 entries.
  - **Columns:** ＥＤＩＮＥＴコード, 提出者種別, 上場区分, 連結の有無, 資本金, 決算日, 提出者名, 提出者名（英字）, 提出者名（ヨミ）, 所在地, 提出者業種, **証券コード**, 提出者法人番号.
  - **The securities code has 5 digits:** the 4-digit exchange code plus "0". Toyota is `72030` → `E02144`.
  - The 提出者業種 (industry) column lets the tool spot banks (銀行業) and insurers (保険業). [S]
- **Merged codes:** when a filer's two codes are merged, a separate list maps old to new. It is [ESE140190.csv](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140190.csv), listed on the [EDINET API関連資料](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html) page and in spec p.72. Fetched 2 November 2026: columns 集約処理日, 廃止EDINETコード, 継続EDINETコード (merge date, retired code, continuing code). [D]

---

## 3. Where the user looks up the price (the tool never fetches it)

| Exchange | Page loaded | What it shows | Delay stated | Terms that limit use |
|---|---|---|---|---|
| **Nasdaq** | [nasdaq.com/market-activity/stocks/aapl](https://www.nasdaq.com/market-activity/stocks/aapl) (reached through the site's own search) | Last price ($344.63), change, the date (Oct 30, 2026), "Market: Closed", volume, 52-week range. Also a "Historical NOCP" tab (Nasdaq Official Closing Price) | "Real-time Data is provided using Nasdaq Last Sale Data"; indices "delayed at least 1 minute" | A "personal, limited … license to use the Services solely for your personal, non-commercial use". It bans any automated or manual process "to capture data or content from the Service". [D] [Nasdaq legal](https://www.nasdaq.com/legal) |
| **NYSE** | [nyse.com/quote/XNYS:T](https://www.nyse.com/quote/XNYS:T) (AT&T; link taken from nyse.com's home page. Other stocks use `XNYS:<ticker>`) | Last price, change, volume, open/high/low, 52-week range, "As of Friday, October 30, 2026 07:00:00 PM ET" | "Quote data is delayed"; "Market Data is delayed by 15 minutes and is for informational and/or educational purposes only" | ICE's terms cover nyse.com. Use is "only for your own personal, non-commercial use". They ban passing content to "others in your company or organization", and ban "data mining, robots or similar data gathering or extraction methods". [D] [ICE terms of use](https://www.ice.com/privacy-security-center/terms-of-use) |
| **JPX (Tokyo)** | [株価検索](https://quote.jpx.co.jp/jpxhp/main/index.aspx?F=stock_search) → search by code → detail page, e.g. [7203 トヨタ自動車](https://quote.jpx.co.jp/jpxhp/main/index.aspx?f=stock_detail&qcode=7203) | 現在値 (current price) with time, 前日比 (change from the previous day), 前日終値 (previous close; ¥3,070 when checked), bid/ask with times, 52-week high/low, market value, **発行済株式数** (shares issued; 14,594,987,460) | "最低15分遅れ" (at least 15 minutes late); market value updates once a day at about 17:20 | The data comes from QUICK Corp. "情報は、利用者ご自身のためにのみ利用するものとし" (for the user's own use only). Reuse or redistribution for third parties is banned, and so is "情報の蓄積、編集および加工等" (storing, editing or processing the information). [D] (notice on the detail page). The site-wide terms (updated 2026/07/29) say all rights, including copyright, belong to JPX, and ask users to avoid "高頻度・高負荷に繋がる可能性のある自動取得等" (automated retrieval that could mean high frequency or high load). [D] [JPX サイトのご利用上の注意と免責事項](https://www.jpx.co.jp/term-of-use/index.html) |

Notes:
- **Licensing risk** [S]: all three restrict use to the viewer's own, personal or non-commercial use, and ban automated capture.
  - The tool should only *name* the page. The user looks up the price and types it in.
  - A professional user building a model at work may fall outside "personal, non-commercial" use (Nasdaq, ICE). That is the user's matter under those terms, but the skill should not call these pages "licensed for commercial use".
  - The JPX ban on storing and processing is the strictest. Typing one price into one's own model is ordinary personal reference, but the skill should not suggest copying JPX data in bulk.
- **Suggested form for the Valuation sheet** [S]: "Price: user, <date>, looked up on <exchange page>". Prefer the official close (Nasdaq "Historical NOCP"; NYSE "Last" after 4 p.m. ET; JPX 前日終値 or 終値).

### Where the share count comes from (no lookup needed)

- **US** [D, from the Apple response]: `dei:EntityCommonStockSharesOutstanding`, in the same `companyfacts` file. It is the cover-page count of shares outstanding, dated close to the filing date.
  - For per-share values at year end, `us-gaap:CommonStockSharesOutstanding` (balance-sheet date) is also in the file.
  - For EPS, use `us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding`.
- **Japan** [D, EDINET taxonomy element list [ESE140114.xlsx](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140114.xlsx), downloaded 2 November 2026]:
  - `jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults`: 発行済株式総数 (普通株式), in the 有価証券報告書's key financial data, at each year end.
  - `jpcrp_cor:NumberOfIssuedSharesAsOfFilingDateIssuedSharesTotalNumberOfSharesEtc`: 提出日現在発行数 (shares issued as of the filing date).
  - `jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc`: 自己株式等 所有株式数の合計 (treasury shares held).
  - **Shares outstanding = issued − treasury.** [S] Japanese filings report *issued* shares, which include treasury shares; US cover pages report shares *outstanding*.
  - The JPX detail page also shows 発行済株式数. It says changes can take "数週間" (several weeks) to appear. [D]

---

## 4. Banks and insurers: why the standard chart does not fit

**Japan** [D, EDINET 勘定科目リスト [ESE140115.xlsx](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140115.xlsx), downloaded 2 November 2026]:
- The account list has a separate sheet per industry: 一般商工業 (general), 銀行・信託業 (bk1), 銀行・信託業（特定取引勘定設置銀行） (bk2), 生命保険業 (in1), 損害保険業 (in2), and others.
- The general sheet has 流動資産/固定資産 (current/non-current assets), 売上高 (sales), 売上総利益 (gross profit) and 営業利益 (operating profit).
- **The bank and life-insurer sheets have none of these.**
  - The bank income statement starts with 経常収益 (`OrdinaryIncomeBNK`, ordinary income) and 経常費用 (ordinary expenses), with lines such as 資金運用収益 (interest and investment income) and 役務取引等収益 (fees and commissions).
  - The life-insurer income statement starts with 経常収益 (`OperatingIncomeINS`), including 保険料等収入 (premium income), and adds 契約者配当準備金繰入額 (transfer to the policyholder dividend reserve).

**United States** [I, Regulation S-X as reproduced by Cornell LII; the official eCFR page was not retrieved]:
- **Banks** ([§210.9-03](https://www.law.cornell.edu/cfr/text/17/210.9-03), [§210.9-04](https://www.law.cornell.edu/cfr/text/17/210.9-04)):
  - The balance sheet runs "Cash and due from banks", deposits, loans (net of the allowance), deposits taken, short- and long-term borrowings. It has no current/non-current split.
  - The income statement starts with "Interest and fees on loans", then net interest income and the provision for loan losses. There is no net sales or cost of goods sold.
- **Insurers** ([§210.7-03](https://www.law.cornell.edu/cfr/text/17/210.7-03), [§210.7-04](https://www.law.cornell.edu/cfr/text/17/210.7-04)):
  - Investments, deferred policy acquisition costs and policy liabilities, with no current/non-current split.
  - Revenue is "Premiums" and "Net investment income". Costs are "Benefits, claims, losses and settlement expenses". There is no net sales or cost of goods sold.
- **In the data** [S]: US banks' and insurers' `us-gaap` facts will mostly lack `AssetsCurrent`, `LiabilitiesCurrent` and a cost-of-revenue tag. This has not been checked against a live bank filing, because only one SEC call (Apple) was allowed in this step.

**For design choice 4** [S]: a working-capital-days, gross-margin and capex model has no inputs for these firms. A bank is valued on net interest margin, credit losses and capital; an insurer on premiums, the loss ratio and investment yield. So banks and insurers get their own templates (section 5 and `docs/MODELS.md`), and securities firms are refused until a broker-dealer template exists.
- Japan: detect by the EDINET code list's 提出者業種 column, or by `OrdinaryIncomeBNK`/`OperatingIncomeINS` in the filing.
- US: detect by the absence of `AssetsCurrent`, or by SIC code from the submissions API, which this step did not test.
  - **Tested since** [D, 8 live calls to `https://data.sec.gov/submissions/CIK##########.json`, 2026-11-02]: the submissions record gives the SIC code and its description, and the `name` confirms the CIK. Goldman Sachs (CIK 886982) and Morgan Stanley (895421) are SIC 6211, Security Brokers, Dealers & Flotation Companies. JPMorgan (19617), U.S. Bancorp (36104), PNC (713676), Wells Fargo (72971), Citigroup (831001) and Bank of America (70858) are SIC 6021, National Commercial Banks. `fetch_filings.py` now saves this as `<TICKER>_sic.json`: SIC 6211 refuses a securities firm, and a bank SIC code routes a company that files both deposits and premiums (Citi) to the bank chart.

---

## Notes for users

1. **EDINET API key.** Every EDINET call needs a `Subscription-Key`. Each user registers on the EDINET site (email, password, phone for SMS or voice checks) and sets the key as the environment variable `EDINET_API_KEY` (section 2.2). Without one, the Japan route runs only on a report downloaded by hand (`--from-file`).
2. **Price pages are personal-use only.** Nasdaq, NYSE (ICE) and JPX all limit use to the viewer's own, personal or non-commercial use. The skill names the pages; it does not say they are licensed for business use.
3. **EDINET credit.** Every Japanese model carries EDINET's source credit and the note 「…をもとにStargira Tie-Out（Stargira Advisors Ltd）により加工」 (section 2.3). Keep both when you share the model.

## Not retrieved

- **eCFR (ecfr.gov), the official Regulation S-X text:** redirected to an "unblock" page. The Cornell LII mirror was used instead, so §4's US part is marked [I].
- **The bulk `companyfacts.zip`:** only its headers were checked (about 1.4 GB); the contents were not downloaded.
- **A machine-readable EDINET rate limit:** none is published. The spec and terms give only "heavy access in a short time" and status 429.
- **A Japanese bank or insurer filing, and a US bank `companyfacts` file:** not fetched, so §4's claims about tags in actual filings are [S].
- **JPX site-wide terms by plain fetch:** returned HTTP 403. They were read in the browser instead (see §3).

---

## 5. Bank and insurer taxonomy

Checked 2 November 2026. Every URL below was loaded that day.

**In short:**
- **US GAAP** has a standard element for almost every bank and insurer line. Three lines have none: total fee and commission income, fee and commission expense, and an insurer's change in policy reserves on the income statement.
- **J-GAAP** has its own element sets for banks, life insurers, non-life insurers and securities firms. Their names end in `BNK`, `INS` or `SEC`. Totals and net income use the same elements as the corporate map.
- **IFRS on EDINET** has no bank or insurer elements at all. Such a company would use its own extension elements.
- **Shares** use the same elements as the corporate maps.

### 5.1 Sources

| Source | URL |
|---|---|
| FASB US GAAP taxonomy 2026, schema (17,182 elements) | [us-gaap-2026.xsd](https://xbrl.fasb.org/us-gaap/2026/elts/us-gaap-2026.xsd) |
| FASB 2026 deprecation linkbase | [us-gaap-depcon-def-2026.xml](https://xbrl.fasb.org/us-gaap/2026/elts/us-gaap-depcon-def-2026.xml) |
| EDINET document page; the 2026年版EDINETタクソノミ files were published 2025-11-11 | [WZEK0110](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html) |
| 勘定科目リスト (account list), one sheet per industry | [ESE140115.xlsx](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140115.xlsx) |
| タクソノミ要素リスト (element list, including the DEI cover tags) | [ESE140114.xlsx](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140114.xlsx) |
| 国際会計基準タクソノミ要素リスト (IFRS elements, `jpigp_cor`) | [ESE140184.xlsx](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140184.xlsx) |
| EDINETタクソノミの概要説明 (taxonomy overview, November 2025) | [ESE140108.pdf](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140108.pdf) |
| 提出者別タクソノミ作成ガイドライン (has the industry identifier table) | [ESE140110.pdf](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140110.pdf) |
| SEC companyfacts, Travelers and MetLife (saved outside the repo) | [CIK0000086312](https://data.sec.gov/api/xbrl/companyfacts/CIK0000086312.json), [CIK0001099219](https://data.sec.gov/api/xbrl/companyfacts/CIK0001099219.json) |

These were also searched for the allowed DEI industry-code values, and none lists them: [ESE140112](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140112.pdf), [ESE140124](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140124.pdf), [ESE140119](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140119.pdf), [ESE140109](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140109.pdf), [ESE140104](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140104.pdf), [ESE140113](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140113.pdf), [ESE140126](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140126.pdf).

The MUFG report (`output/filings/8306_S100YJQO.zip`) and JPMorgan's companyfacts (`output/filings/JPM_companyfacts.json`), both already in the repo, were used to check names against real filings.

### 5.2 Key elements

**US GAAP banks** [D, schema + JPMorgan 10-K facts]:
- Interest income `InterestIncomeOperating`; interest expense `InterestExpenseOperating`, then `InterestExpense`. JPMorgan moved from `InterestExpense` to `InterestExpenseOperating` in 2022, so the map needs both to reach older years.
- Net interest income `InterestIncomeExpenseNet`; non-interest income `NoninterestIncome`; trading `TradingGainsLosses`.
- Provision for credit losses `ProvisionForLoanLeaseAndOtherLosses`; operating expenses `NoninterestExpense`. After CECL (2020) Citi, PNC and Bank of America file the total under their own tags only, which companyfacts leaves out. The standard parts are `FinancingReceivableExcludingAccruedInterestCreditLossExpenseReversal` (or `ProvisionForLoanLossesExpensed`), `OffBalanceSheetCreditLossLiabilityCreditLossExpenseReversal`, the HTM securities credit-loss tags and, for Citi, `PolicyholderBenefitsAndClaimsIncurredNet`. They add to the printed total at Bank of America (FY2020-FY2025) and at Citi in FY2019 and FY2021. Citi's "other assets" line and part of PNC's total are under the companies' own tags only.
- Wells Fargo files its loan book ("Total loans", "Net loans") under its own tags only after FY2021, so a Wells Fargo model is refused: a bank with no loans or no deposits in any actual year is refused, like one with no interest income or expense.
- `Revenues` for a bank is revenue **net of** interest expense (JPMorgan FY2025: $95,443m + $87,004m = $182,447m).
- Balance sheet: `CashAndDueFromBanks`, `InterestBearingDepositsInBanks`, `Deposits`, `ShortTermBorrowings`, `Assets`, `Liabilities`, `StockholdersEquity`, `LiabilitiesAndStockholdersEquity`. Securities are filed by category (available-for-sale, held-to-maturity, trading). JPMorgan's loans, trading assets and long-term debt are not in companyfacts as one standard tag; step 3 must check them against the 10-K.

**US GAAP insurers** [D, schema + Travelers and MetLife 10-K facts]:
- `PremiumsEarnedNet`, `PolicyholderBenefitsAndClaimsIncurredNet`, `BenefitsLossesAndExpenses`, `NetInvestmentIncome`.
- Balance sheet: `Investments`, `LiabilityForClaimsAndClaimsAdjustmentExpense`, `UnearnedPremiums`, `LiabilityForFuturePolicyBenefits`, `PolicyholderContractDeposits`.
- The 2026 deprecation file retires the line-of-business versions (for example `PremiumsEarnedNetLife`). Map only the main elements.

**J-GAAP banks** [D, account list sheets bk1/bk2 + MUFG]:
- Revenue is 経常収益 `OrdinaryIncomeBNK`. Watch the names: `OrdinaryIncome` is 経常利益 (ordinary *profit*).
- Lines: 資金運用収益 `InterestIncomeOIBNK`, 資金調達費用 `InterestExpensesOEBNK`, 役務取引等収益 `FeesAndCommissionsOIBNK`, 営業経費 `GeneralAndAdministrativeExpensesOEBNK`.
- **Credit costs (与信関係費用) have no element.** The provision 貸倒引当金繰入額 `ProvisionOfAllowanceForLoanLossesOEBNK` and 貸出金償却 sit inside その他経常費用. A net reversal (貸倒引当金戻入益) and 償却債権取立益 sit inside その他経常収益, on the income side. The map must add up the parts.
- Net interest income (資金利益) has no element either: interest income less interest expense.
- Balance sheet: 現金預け金 `CashAndDueFromBanksAssetsBNK`, 有価証券 `SecuritiesAssetsBNK`, 貸出金 `LoansAndBillsDiscountedAssetsBNK`, 貸倒引当金 `AllowanceForLoanLossesAssetsBNK` (filed negative), 預金 `DepositsLiabilitiesBNK`, 譲渡性預金 (a separate line), 借用金 `BorrowedMoneyLiabilitiesBNK`.

**J-GAAP insurers** [D, account list sheets in1/in2; no insurer filing read yet]:
- Revenue is 経常収益 `OperatingIncomeINS`, although the English name says "operating income".
- Life: 保険料等収入 `InsurancePremiumsAndOtherOIINS`; 保険金等支払金 `InsuranceClaimsAndOtherOEINS`; 事業費 `ProjectExpensesINS`.
- Non-life: 正味収入保険料 `NetPremiumsWrittenOIINS` and 正味支払保険金 `NetLossPaidOEINS`. Both are written or paid figures; no earned-premium element exists.
- Reserve changes: 責任準備金等繰入額 `ProvisionOfPolicyReserveAndOtherOEINS`. A release is booked on the income side as 責任準備金等戻入額 `ReversalOfPolicyReserveAndOtherINS`.
- Balance sheet: 保険契約準備金 `ReserveForInsurancePolicyLiabilitiesLiabilitiesINS`, 支払備金 `OutstandingClaimsLiabilitiesINS`, 責任準備金 `PolicyReserveLiabilitiesINS`.

**J-GAAP securities firms** [D, account list sheet sec]: unlike banks and insurers, they keep the current/non-current split and 営業利益. Revenue is 営業収益 `OperatingRevenueSEC` and 純営業収益 `NetOperatingRevenueSEC`.

**IFRS** [D, ESE140108 §2-5-4-3 and ESE140184]: 「国際会計基準タクソノミでは業種別のタクソノミやパターン別リンクベースファイルは用意されていません」, so there are no industry taxonomies for IFRS. A search of all 1,933 element rows found no deposit, loan, credit-loss or insurance-contract element.

### 5.3 Cash and the tie-out

- **JPMorgan** [D]: the cash flow statement's closing cash equals `CashAndDueFromBanks` + `InterestBearingDepositsInBanks` exactly, in every year 2021–2025. The bank map's cash line must add the two.
- **MUFG** [D]: 現金及び現金同等物 equals 現金預け金 in both years, and the filing's note says so (「一致しております」). The existing cash rule works if the bank map's cash line is `CashAndDueFromBanksAssetsBNK`.
- **Other Japanese banks** [S]: the bank sheet has cash-flow lines for deposits excluding cash equivalents, and for deposits excluding those at the Bank of Japan. So some banks count only part of 預け金 as cash. For them the existing rule (use the note only if it foots, otherwise flag) applies unchanged. The same holds for insurers, whose balance sheet shows 現金及び預貯金.
- Both test filings already tie out with today's checks (CFO + CFI + CFF + FX = net change; opening + net change = closing). [D]

### 5.4 Industry codes

- **The account list's sheet codes** are `bk1`, `bk2`, `in1`, `in2`, `sec` (and `cai` for general companies). [D, ESE140115 and ESE140110]
- **The value in a filing's DEI** is a different code. MUFG files `bnk` (consolidated) and `cte` (non-consolidated). [D]
- No FSA document fetched lists the allowed DEI values. So `ins`, `in1`, `in2`, `bk1`, `bk2` and `sec` are not confirmed as DEI values.
- MUFG files `bnk` but uses the bk2 trading lines, so the DEI code cannot tell bk1 from bk2.
- **Route on the element suffixes as well as the code** [S]. A securities firm has `CurrentAssets`, so today's `require_absent` rule would not catch it if its DEI code were missing.

### 5.5 Not retrieved

- **The SEC `dei` taxonomy schema.** `dei:EntityCommonStockSharesOutstanding` is confirmed only from the three companyfacts files.
- **A J-GAAP insurer, a securities firm, or an IFRS bank or insurer filing.** Step 1 is fetching them; until then, those element names are confirmed in the taxonomy only.

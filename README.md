# Stargira Tie-Out: three-statement model builder for Claude Code

Ask Claude for a financial model. Stargira Tie-Out asks you a few questions, builds a
three-statement model (P&L, balance sheet and cash flow) in Excel, and checks that it
ties out in every period: the balance sheet balances and cash agrees.

## What it builds

- **A SaaS company**, a **power project** (a utility-scale solar plant with project
  finance), and a **business that earns on a balance it holds**, such as a lending book.
- **A listed company, US or Japan, built from its own filings** (SEC for the US, EDINET
  for Japan): the last three filed years as actuals, then a forecast from the company's
  own history.
- **A listed bank and a listed insurer (life or non-life), US or Japan,** from the same
  filings. Their statements have no cost of sales and no
  current/non-current split, so each has its own template. A bank is driven by loan and
  deposit growth, asset yield, funding cost, cost-to-income and credit cost (loan losses
  / loans). An insurer is driven by premium growth, the loss and expense ratios, the
  change in reserves and the investment yield. Both are valued on residual income: book
  equity plus the present value of future profit above the cost of equity.

**How a financial company is routed.** The tool picks the template itself. In the US,
deposits send a company to the bank template and premiums earned to the insurer
template. A company that files both goes where its SEC SIC code (industry code) says: a
bank code (6021, 6022, 6029, 6035, 6036) to the bank template, an insurer code (63xx) to
the insurer template. Citi (SIC 6021) files both and is built as a bank. In Japan, the
filing's EDINET industry code decides (in either case), with the element-name suffix
(BNK, INS) as the fallback.

**Securities firms are refused until a broker-dealer template exists**:
the bank template's drivers do not fit them. In the US, a company the bank route catches
is refused when its SEC SIC code is 6211 (security brokers and dealers), or, with no SIC
code saved, when its deposits are below 40% of total assets and its loans (when tagged)
below 25%. Goldman Sachs and Morgan Stanley are refused this way. In Japan, the EDINET
industry code `sec` (either case), or with no financial code the element-name suffix SEC,
is refused. Daiwa is refused this way.

**Japanese non-life insurers under IFRS 17** (Tokio Marine, SOMPO and MS&AD, which moved to
IFRS in the year to March 2026) go to an IFRS 17 version of the insurer template,
`verticals/listed_insurer_ifrs.yaml`. It shows insurance revenue, insurance service
expenses, the net reinsurance expense, the net financial result and insurance contract
liabilities (which include the CSM, not modelled separately). Their IFRS reports hold only
2 years, so these models have **2 actual years**, and every workbook, answers file and
history says so. J-GAAP and IFRS years are never mixed.

**What is refused, with the reason given:** a securities firm (above); any other Japanese
bank or insurer that files under IFRS (EDINET's IFRS taxonomy has no industry elements); Nomura (US
GAAP on EDINET); any other company with fewer than 3 years of history, such as SBI Shinsei
Bank; a history that mixes J-GAAP and IFRS years; a Japanese industry code and element suffix
that disagree; an insurer that files both life and non-life premium lines; and a US
company with neither of deposits and premiums earned, or with both and no bank or insurer
SIC code saved.

Every workbook opens on a Start_Here sheet: three steps, the headline results, a colour
key and a guide to every sheet. For a SaaS, power or balance model the steps are: name
the business and set the dates, check the yellow inputs, read the results (with the
balance-sheet and cash-flow checks, which must read 0). For a listed company, bank or
insurer they are: type the share price, check the inputs, read the results. A Dashboard
sheet of charts follows.

## Install and start

1. In Claude Code, run these two commands:

   ```text
   /plugin marketplace add kyasuda-stargira/stargira-tieout
   /plugin install stargira-tieout@stargira
   ```

2. It needs Python 3.9 or later with two packages: `pip install openpyxl pyyaml`.
   The `formulas` package is optional; the checker uses it if it is installed.
3. Ask Claude for a financial model, or run `/stargira-tieout:financial-model`.
   Anything you don't answer uses the template's default.

Everything runs on your own computer, and the plugin collects no data. The one
exception to "offline" is the listed-company route: when you run it, one script
downloads the company's filings from the SEC or EDINET (see [What runs where](#what-runs-where)
and the [privacy notice](https://www.stargira.com/privacy-tieout)). Questions or bugs: use the
[contact form](https://www.stargira.com/#contact).

Nobody reviews what the plugin builds: treat the result as your own draft. For a model
going to an investor or lender, Stargira Advisors offers a paid build with an
independent re-computation and a named reviewer's sign-off.

## How it works

The plugin builds each workbook from two small YAML files. Every number you can change
sits on one Assumptions sheet. Every other cell is a formula.

## The four rules every model follows

1. **The YAML is the source, the workbook is output.** A model is defined by a schema
   file (the list of inputs, with defaults) and an answers file (the values you
   override for one case). Rebuild the workbook instead of hand-editing it.
2. **No typed-in business numbers outside the Assumptions sheet.** Each input is one
   cell on Assumptions with its own named range (an Excel name such as
   `plant_capacity_mw` that points at that cell). Calculation sheets hold only
   formulas that refer to those names or to other cells.
3. **The statements tie out in every period.** Total assets minus total liabilities and
   equity is zero, and closing cash on the cash flow equals cash on the balance sheet.
   Each workbook carries a `BS Check` and a `CF Check` column so anyone can see this.
4. **Every build is logged.** Each project keeps a `BUILD_LOG.md` recording what changed
   and what the checks showed.

## What runs where

Everything runs on your own computer. The plugin is a set of local Python scripts
plus a skill file that tells Claude Code how to run them. Before first use you install
three Python packages with `pip install -r requirements.txt` (`openpyxl` and `pyyaml`;
`formulas` is optional). That install uses the network, and you run it yourself.

After that, exactly one script uses the network: `engine/fetch_filings.py`, for the
listed-company route. It calls only three hosts, at most once a second, and only when
you run it: `www.sec.gov` (the ticker-to-company list) and `data.sec.gov` (the company's
filed figures, and its submissions record for the SIC industry code) for US companies, and `api.edinet-fsa.go.jp` (the Financial Services
Agency's EDINET API) for Japanese companies. For the US it asks for three public files: the SEC's ticker list,
and the chosen company's filing data and submissions record by its SEC company number
(CIK). When a line the model needs has no standard tag in the company's last three
years (Microsoft files its D&A under its own tag), it also asks `www.sec.gov` for the
latest 10-K's filing summary and the SEC's rendered pages of its statements, and reads
that line as printed. The only thing of yours
it sends is the User-Agent you give it: your firm and contact email, which the SEC
requires from automated callers. As with any web request, the SEC also sees your IP
address. For Japan it asks EDINET for its daily lists of filings and the company's annual
reports (有価証券報告書), sending the EDINET API key you registered for yourself; EDINET
sees that key and your IP address.

To skip the network entirely, download the company's `companyfacts` file from the SEC
yourself and pass it with `--from-file`. A `<TICKER>_statements.json` saved by an earlier
run, next to that file, is used too.

Everything else stays offline. The other scripts read your answers file (and, for a
listed company, the saved filing data) and write an Excel workbook into a folder you
choose. They have no web, socket or email code; our build checks fail if network
code appears in any file other than `engine/fetch_filings.py`. The plugin
collects no data. The tool never fetches a share price: you type it in. Claude Code
itself is Anthropic's software; what it sends to Anthropic is covered by Anthropic's
own terms, not by this plugin.

**Data source.** US listed-company, bank and insurer models use data from the U.S.
Securities and Exchange Commission: SEC EDGAR XBRL data (companyfacts). Each model's
Data_Notes sheet cites the SEC as the source, with the company's CIK and the accession
number (the SEC's filing ID) of each actual year's annual report. Japanese models use
EDINET data under the PDL1.0 licence; Data_Notes carries EDINET's credit, the docID of
each 有価証券報告書, and a "processed with Stargira Tie-Out (Stargira Advisors Ltd)" line.

## Use it from Claude Code

The repo is also a Claude Code plugin. The manifest is `.claude-plugin/plugin.json`,
and the skill is `skill/SKILL.md`. To try it for one session:

```
pip install -r requirements.txt
claude --plugin-dir /path/to/this/repo
```

Then ask for a model, or run `/stargira-tieout:financial-model`. The skill asks a few
questions, builds the workbook and runs the checker.

Three tested example prompts, one per template, are in `docs/EXAMPLES.md`.

## How to run it

Needs Python 3.9+ with `openpyxl` and `pyyaml` (`pip install -r requirements.txt`).

```
python engine/build_model.py <schema.yaml> <answers.yaml>
```

This writes `output/<answers-file-name>.xlsx`. For example:

```
python engine/build_model.py verticals/saas.yaml answers/saas_base.yaml            # SaaS
python engine/build_model.py verticals/power_project.yaml answers/power_base.yaml  # power plant
python engine/build_model.py verticals/balance.yaml answers/balance_base.yaml      # balance type
```

The schema's `meta.archetypes` line picks the revenue logic: `subscription` (customers ×
price per customer), `capacity` (plant size × how much of the time it runs × price), or,
if absent, `balance`.

The answers file is a flat list of `id: value` lines. Any input you leave out takes the
default from the schema.

Then check the workbook:

```
python engine/verify_model.py <schema.yaml> <workbook.xlsx>
```

It runs four checks and exits with code 1 if any fails:

1. **No typed-in numbers.** It scans every formula outside Assumptions. The only numbers
   allowed are unit conversions such as 12 months a year or 10,000 basis points. The
   Timeline sheet may also hold period numbers.
2. **Named ranges match the schema.** Every input has one name, and no name exists
   without an input.
3. **No circular references.** No cell depends, through any chain of formulas, on
   itself.
4. **Statements tie out.** It calculates every `BS Check` and `CF Check` cell from the
   workbook's own formulas and requires zero in every period.

For check 4 it uses the `formulas` Python package if
installed, otherwise a small built-in evaluator (`engine/xl_eval.py`). The report says
which one ran.

## What is in this repo

| Path | What it is |
|---|---|
| `engine/build_model.py` | The builder, and the balance-type logic |
| `engine/verify_model.py` | The checker |
| `engine/xl_eval.py` | Built-in formula evaluator the checker falls back on |
| `engine/dashboard.py` | The Dashboard sheet: charts of the main figures over time |
| `engine/start_here.py` | The Start_Here sheet for every template, and, for listed companies, banks and insurers, the "where this comes from" notes on its Assumptions sheet |
| `engine/core.py` | Shared pieces for vertical plugins: timeline, scenario switch, curves |
| `engine/vertical_saas.py`, `engine/vertical_power.py`, `engine/vertical_listed.py` | Revenue and balance-sheet logic for each vertical |
| `engine/vertical_bank.py`, `engine/vertical_insurer.py` | The bank and insurer templates' logic: P&L drivers, the balancing line (liquid assets for a bank, investments for an insurer) and the capital check |
| `engine/fin_valuation.py` | The residual-income Valuation sheet shared by the bank and insurer templates |
| `engine/fetch_filings.py` | Downloads a company's filed figures from the SEC (US) or EDINET (Japan). The only script that uses the network |
| `engine/filings_map.py`, `engine/maps/` | Routes the company to the corporate, bank or insurer chart, maps the filed tags to that chart, rebuilds the annual history and ties it out (a failing year is flagged, never plugged). Maps: `us_gaap.yaml`, `us_gaap_bank.yaml`, `us_gaap_insurer.yaml`, `jgaap.yaml`, `jgaap_bank.yaml`, `jgaap_insurer_life.yaml`, `jgaap_insurer_nonlife.yaml`, `jgaap_securities.yaml` (kept but unused until a broker-dealer template exists), `ifrs.yaml`, `ifrs_insurer.yaml` (IFRS 17 non-life insurers, read by local element name) |
| `engine/history_to_answers.py` | Turns that history into an answers file for `listed_corporate.yaml`, `listed_bank.yaml`, `listed_insurer.yaml` or `listed_insurer_ifrs.yaml`, picked by the history's sector and basis |
| `verticals/saas.yaml`, `verticals/power_project.yaml`, `verticals/balance.yaml`, `verticals/listed_corporate.yaml`, `verticals/listed_bank.yaml`, `verticals/listed_insurer.yaml`, `verticals/listed_insurer_ifrs.yaml` | Schema templates with defaults (`listed_insurer_ifrs.yaml` extends the insurer template; `engine/schemas.py` reads both). `balance.yaml` is for income earned on a balance the business holds, such as yield on a lending book, with a capital requirement against it |
| `docs/DATA-SOURCES.md` | The SEC and EDINET terms, and where to look up a share price |
| `answers/` | Base cases (empty files mean "use every default") |
| `skill/SKILL.md` | The Claude Code skill that runs the builder and checker |
| `docs/ARCHITECTURE.md` | Design doc: the build stages, revenue archetypes, schema grammar and roadmap |
| `docs/MODELS.md` | Each model sheet by sheet, and the simplifications to review |
| `docs/EXAMPLES.md` | Three example prompts, one per template, using the sample answers files |
| `.claude-plugin/` | Plugin manifest and a one-plugin marketplace file |
| `requirements.txt` | Python packages |

Templates can carry two extra blocks. `scenarios` holds Base/Bear/Bull rows; each value
becomes an input named `scn_<axis>_<scenario>`, and the `g_scenario` input picks which
row is live. `curves` holds year-by-year paths; each point becomes inputs named
`crv_<curve>_<n>_year` and `crv_<curve>_<n>_<field>`, and the model draws straight
lines between points.

Period counts (for example `g_monthly_periods`) set how many rows the workbook has.
Changing them on the Assumptions sheet does nothing until you rebuild.

## Simplifications

These models are simple on purpose. Each item below is a choice a reviewer should know
about before relying on the numbers. `docs/MODELS.md` has more detail.

**All three models**

- Tax losses carry forward without limit. Some tax systems cap how much of a profit old
  losses can cover (the UK does so above a £5M allowance); the models do not.
- Tax defaults to 25% in all three templates, a placeholder (the UK main rate).
- The default inputs are placeholders, not market data. Change them to your own figures.

**SaaS**

- Services revenue is booked in the period the deal is won, not spread over onboarding.
- Commissions and capitalised development are written off at 1/N of the remaining
  balance each month (a declining balance), not in equal amounts.
- No fixed assets or capex. The only assets held over time are capitalised commissions
  and development cost.
- The ESOP (employee share option) pool input is labelled "pre-A", but the pool is set up
  before the seed round.
- Annual renewals are billed on each customer's anniversary, spread evenly through the
  year. There is no input to bill them all in one month.
- With the default timeline the quarterly years run Nov–Jan, Feb–Apr, May–Jul and Aug–Oct.
  The Q4 sales uplift (×1.15) lands on Nov–Jan, not Oct–Dec. Move the start date if you
  need calendar quarters.

**Power project**

- Output = capacity factor × availability. Enter the capacity factor *before* downtime,
  or set availability to 100% if your figure is already net.
- Tax is charged on book profit. Book depreciation is the only deduction: no separate tax
  depreciation, capital allowances or tax credits. The default rate is 25%, a placeholder.
- Once the debt is repaid, cash builds up. Dividends cannot exceed retained earnings, and
  the model has no shareholder loan or capital reduction to release that cash. It is paid
  out at the end and counted in the equity IRR.
- Equity goes in alongside debt during construction, not before it. Lenders often want
  equity first; drawing it pro rata makes equity IRR slightly higher.
- Gearing ("debt / total capex") is applied before interest during construction, which is
  then added to the loan. So debt at COD (commercial operation date) is a higher share of
  capex than the input.
- Interest uses yearly periods: charged on the opening balance, repaid at year end. Many
  project loans pay twice a year.

**Balance type**

- No loan book. There is no new lending, repayment, default, provision or funding cost; the
  balance follows straight lines between milestones, and the yield is treated as net revenue.
  The balance itself does not appear on the balance sheet. "Balance" here means a revenue
  base, not a modelled loan book.
- No capital ratio. The own-funds requirement is held as a buffer asset funded by equity;
  the model does not test equity against it.
- A launch balance above zero appears with no cash movement (the default is zero).
- Cash and the own-funds buffer earn nothing, and there is no interest expense.
- Perfect-foresight cash: each period's equity is sized from the next period's known cash
  flow. A real business would forecast this with error.
- A balance milestone after the last period is never reached. The build warns, and the KPI
  sheet's Timeline Check cell says so. With the defaults, milestone 3 (year 6, month 72) falls
  inside the 80-month timeline; after it the balance stays at $1,000M.

## Public vs held back

**Public:** the builder, the checker, the SaaS, power-project and balance-type templates,
the listed-company, bank and insurer templates with the filings route that feeds them,
and the Claude Code skill that drives them.

**Held back** (not in this repo): the structured-credit waterfall builder and template,
other specialist verticals, the browser scenario tool, and client project folders. These
support a paid service. That service also includes independent re-computation of the
model and a named person signing off on it.

Output from the public tools has passed the automated checks described above. Nobody
has reviewed it. Treat it as a draft until someone has.

## Support

Questions, bug reports and requests: use the contact form at
<https://stargira.com/#contact>.

## Licence

Apache License 2.0. See `LICENSE` and `NOTICE`. Copyright 2026 Stargira Advisors Ltd.
The licence covers the code and templates in this repo. You own the workbooks you build
with them.

# The models

What each model builds, sheet by sheet, and the simplifications a reviewer should know about.
It describes the workbooks the engine writes today (`output/*_base.xlsx`). The design behind
them is in [ARCHITECTURE.md](ARCHITECTURE.md).

| | SaaS | Power project | Balance |
|---|---|---|---|
| Revenue shape | customers × price per customer | plant capacity × how much it runs × price per MWh | a balance held × the yield on it, plus fees |
| Template | `verticals/saas.yaml` | `verticals/power_project.yaml` | `verticals/balance.yaml` |
| Logic | `engine/vertical_saas.py` | `engine/vertical_power.py` | `engine/build_model.py` |
| Inputs (named ranges) | 115 | 60 | 33 |
| Default timeline | 10 months, then 24 quarters | 8 construction quarters, then 25 operating years | 2 pre-launch months, 8 months, then 24 quarters |
| Scenario switch | yes (churn, price, new customers) | yes (tariff, capacity factor, capex) | no |
| Sheets | 16 | 17 | 12 |

## Shared rules

Every model follows the same rules, and `engine/verify_model.py` checks them on every build:

1. **Every input lives once, on the Assumptions sheet,** as a named range. Calculation sheets hold
   only formulas.
2. **No typed-in numbers in formulas,** except unit conversions (such as 12 months a year) and
   the period counts on the Timeline sheet.
3. **No circular references.**
4. **The balance sheet balances and the cash flow matches the balance-sheet cash** in every
   period. The check columns must equal zero.

Every workbook opens on **Start_Here** (three steps, the headline results, a colour key and
a guide to every sheet), then **Dashboard**, **Assumptions** and **Timeline**, and ends with
**KPI** and **_Manifest**. **Dashboard** charts the main figures over time: revenue, EBITDA,
net income, operating and free cash flow, cash and net debt, plus each model's own measures.
Flows are shown at an annual rate (period value × 12 ÷ months), because periods change length
from months to quarters to years. Balances are period-end values. Its data table (from column U)
is formulas only, linked to the other sheets. `_Manifest` records the template version, the answers file and the build time.
The input count above includes the scenario rows (`scn_…`) and the curve points (`crv_…`)
that the engine turns into inputs.

Changing a period count on the Assumptions sheet does nothing until you rebuild, because the
number of rows is fixed at build time.

---

## SaaS

A B2B software company selling subscriptions in two price tiers (A and B), funded by
equity rounds. The defaults describe a seed-to-Series-A company selling to mid-market customers.

**Inputs (13 sections):** Entity, Timeline, Acquisition, Churn, Pricing, COGS, S&M,
Headcount, R&D/G&A, Billings, Raises, WC (working capital), Cash. Headcount has the most
inputs (24): for each of five teams, a start month, a start and end headcount and a salary, plus on-costs, salary inflation and the ramp years.

**Sheets, in the order money flows:**

| Sheet | What it works out |
|---|---|
| Scenario | The live value of each scenario axis (Base, Bear or Bull) |
| Customers | Per tier: opening customers, new customers (with a Q4 uplift), churn, closing customers |
| Revenue | Per tier: MRR (monthly recurring revenue) opening, retained, expansion, new, closing |
| Headcount | Heads and cost for engineering, product, sales, customer success and G&A |
| OpEx | Hosting, support, licences and services (COGS); acquisition spend; marketing; R&D tools |
| Capitalised | Sales commissions and development cost held as assets, then written off over time |
| Working_Capital | Receivables, prepaid costs, payables, accruals, deferred revenue |
| Opening_Balances | Working capital already in place for the opening customers at model start |
| PnL | Revenue down to net income, with tax losses carried forward |
| Funding | Cash before and after the two planned rounds, plus any **unplanned funding** |
| Balance_Sheet | Assets, liabilities and equity, with the balance check |
| Cash_Flow | Operating, investing and financing cash flow, with the cash check |
| Cap_Table | Shares after each round, post-money valuations, the unplanned funding priced as extra shares, and ownership at the end of the model |
| KPI | ARR, MRR, NRR, GRR, logo churn, CAC payback, LTV/CAC, burn multiple, rule of 40, gross margin, magic number, runway, plus pass/fail flags |

**Simplifications to review:**

- Revenue is an MRR stock per tier. New customers add to it and churn removes the share that
  leaves. Expansion and the yearly price rise grow the customers who stay.
- All opening customers and opening ARR sit in Tier A.
- Newer customers churn faster. The model guesses how many customers are under a year old from
  the current rate of new sign-ups. It does not track each year's cohort separately.
- Deferred revenue = annual-prepay customers' share of MRR × MRR × 12 ÷ 2. That is, on
  average half a year of service is still owed. Their share of MRR is a little below their
  share of customers, because they get the prepay discount.
- The opening balance sheet holds the working capital of the opening customers. Receivables
  and deferred revenue come from the opening MRR. Prepaid costs, payables and accruals assume
  costs were already running at the first period's level. Opening equity is cash plus this net
  working capital, which sits in opening retained earnings.
- Capitalised commissions and development are written off at 1/N of the opening balance each
  month, where N is the write-off period.
- **The "unplanned funding" line is the funding gap.** If cash would fall below the target
  (the larger of the minimum and a set number of months of costs), this line tops it up.
  The cap table issues shares for the whole gap at one price: the Round 2 price times
  `cap_unplanned_price_mult` (default 1.0), whenever the money arrives.
- CAC payback and LTV/CAC count all acquisition cost: variable acquisition spend (commissions
  included), the fixed marketing budget and sales salaries. Customer-success salaries are left
  out, because they serve existing customers.
- With the default timeline, quarters start in November. A quarter that does not start in
  January, April, July or October is labelled by its months, such as "Nov 27–Jan 28". The Q4
  sales uplift applies to any quarter that starts in October, November or December.

## Power project

A single utility-scale solar plant that sells power under a PPA (a long-term contract at a
set price), with any unsold share at market prices, funded with project debt and equity.

**Inputs (10 sections):** Entity, Timeline, Plant, Revenue, Direct costs, Capex, Financing,
WC, Cash, KPIs. Two **curves** add year-by-year paths: tariff escalation and the merchant
price.

**Sheets, in the order money flows:**

| Sheet | What it works out |
|---|---|
| Scenario | The live value of each scenario axis |
| Capex | EPC, development, grid connection and contingency; total capex; debt at COD |
| Ops | Flags per period: operating or not, operating year, inside the debt tenor, inside depreciation life, tax holiday |
| Generation | Capacity factor, degradation, output in MWh, PPA and market prices, revenue. Output = capacity factor × availability, so the capacity factor input is the figure *before* downtime; enter a net figure only with availability at 100% |
| OpEx | Fixed and variable O&M, insurance, land lease, asset management |
| Assets | Construction in progress (CWIP), its transfer to plant (PP&E) at COD, depreciation |
| Debt | Construction loan draws and interest, then repayment; debt service and CFADS (cash available for debt service); the first operating year's working-capital build-up, left out of the DSCR test |
| Working_Capital | Receivables and payables |
| PnL | Revenue down to net income, with tax losses carried forward |
| Funding | Capex, debt reserve funding, debt draws and repayments, equity in, dividends |
| Balance_Sheet | Assets, liabilities and equity, with the balance check |
| Cash_Flow | Operating, investing and financing cash flow, with the cash check |
| Returns | Discount factors, present values for LCOE, project and equity cash flows, payback |
| KPI | One value each: LCOE, minimum and average DSCR, equity IRR, project IRR, equity payback, EBITDA margin, minimum DSCR against the target |

COD is the commercial operation date, when the plant starts selling power. DSCR (debt service
coverage ratio) is cash available for debt service ÷ debt service due.

**Simplifications to review:**

- Capex is spent evenly over the construction quarters. Debt funds it at the gearing ratio,
  and equity tops cash up to the minimum. Interest during construction is either added to the
  loan and the asset, or expensed.
- At COD, construction in progress becomes plant and is depreciated in equal amounts over its life.
- **Debt is not sized to a target DSCR.** The loan at COD is repaid over the tenor in proportion
  to each year's EBITDA. The target DSCR is only a test on the result.
- A debt service reserve account (DSRA) holds the next period's debt service for a set number
  of months.
- Dividends come from cash above the minimum, capped by retained earnings. They stop in any
  year whose DSCR is below the lock-up level. Cash left at the end counts as a final payout in
  the equity IRR.
- Operating costs start at COD. Nothing is spent on O&M during construction.
- The default tariff ($58/MWh) is a placeholder, not a market quote. It works only at the
  default 100 MW plant size; at $38 the base case lost money (minimum DSCR 0.76).
- **Tax defaults to 25%** (a placeholder, the UK main rate, as in the SaaS template). Tax is
  charged on book profit: book depreciation is the only deduction, with no separate tax
  depreciation, allowances or credits. Losses carry forward without limit.
- **The DSCR test leaves out the first year's working-capital build-up.** Receivables and
  payables start from zero at COD, so the first operating year pays a one-off $1.33M into
  working capital. The cash flow, CFADS and both IRRs still include it; the DSCR and lock-up
  tests do not. Check this against the CFADS definition in your loan terms. The base case's minimum DSCR is then
  1.37 (first operating year), against the 1.30 target. Counting it would give 1.17.

## Balance

A business that earns a yield on a balance it holds, such as a lending book or assets under
management, and must hold capital against that balance.

**Inputs (8 sections):** Entity, Timeline, Balance, Revenue, OpEx, Working Capital,
Cash & Capital, Own Funds. The balance is set by a launch value and three milestones
(year and value).

**Sheets, in the order money flows:**

| Sheet | What it works out |
|---|---|
| Balance_Ramp | Opening, closing and average balance each period (labelled AUM) |
| Revenue | Yield revenue, transaction fees, other revenue |
| OpEx | Monthly running cost and OpEx per period |
| Working_Capital | Receivables, prepaid costs, payables, accruals, and their change |
| PnL | Revenue down to net income, with tax losses carried forward (used without limit against later profits). Tax defaults to 25%, a placeholder; the base case pays none, because its losses are never used up |
| Forward_Cash | The own-funds buffer, the cash target, equity injections and dividends |
| Balance_Sheet | Cash, own-funds buffer, other assets, liabilities and equity, with the balance check |
| Cash_Flow | Operating, investing and financing cash flow, with the cash check |
| KPI | Balance, revenue, OpEx, EBITDA margin, net income, total equity injected, cash, runway on net burn (cash ÷ monthly operating cash outflow; "n/a" when operating cash flow is not negative) |

**Simplifications to review:**

- The balance moves in straight lines between the three milestones. There is no customer or
  cohort detail.
- A milestone after the last period is never reached: the balance stops where the timeline
  ends. The build prints a warning, and the KPI sheet's Timeline Check cell says so. With the
  defaults the timeline ends 80 months after launch and milestone 3 is at year 6 (month 72),
  so the balance reaches $1,000M and stays there for the last 8 months.
- EBITDA margin shows "n/a" while revenue is zero. In the first months after launch, tiny
  revenue still gives very large negative margins.
- **Perfect-foresight cash.** Each period ends with enough cash to cover the next period's
  operating shortfall, using that period's known cash flow. Equity goes in only when needed, and
  dividends are paid only above the target. A real business would forecast this with error.
- Injected and retained cash earns nothing. Only the balance earns a yield.
- No credit losses, provisions or funding cost. The yield is treated as net revenue. For a
  lender, model these outside or enter the yield net of them.
- The own-funds buffer = the larger of a fixed floor and a % of the closing balance. It is one
  capital requirement, funded by equity and held as a separate asset.
- OpEx moves in a straight line in time (months from launch) from its launch level to its final
  level, reached in the last period.
- No scenario switch.

## Financial companies: standard chart

Banks and insurers do not fit the listed-company chart: they have no current/non-current
split and no cost of sales. `engine/filings_map.py` routes them to their own chart instead of
refusing them. Every map for them (US GAAP now; J-GAAP and IFRS next) fills the keys below,
so the bank and insurer templates read the same keys whatever the filing standard. The
history file says which chart it uses in `sector` (`corporate`, `bank` or `insurer`).

**Routing (US).** The `unsupported` rule in `engine/maps/us_gaap.yaml` catches a company with
no `AssetsCurrent` and a bank or insurer tag. Its `route` list then decides, on 10-K values in
the years selected: `PremiumsEarnedNet` sends it to the insurer chart, `Deposits` to the bank
chart. A company with both goes where its saved SEC SIC code says (`<TICKER>_sic.json`, from
`fetch_filings.py`): a bank code (6021, 6022, 6029, 6035, 6036) to the bank chart, an insurer
code (63xx) to the insurer chart. Citi (SIC 6021) files both and goes to the bank chart. A
company with neither, or both and no deciding SIC code, is refused with the reason.
A securities firm is refused until a broker-dealer template exists. A
broker-dealer holding company (Goldman Sachs, Morgan Stanley) holds deposits, so the bank route
catches it first; `broker_dealer_reason()` then refuses it on SIC 6211 or, with no SIC code
saved, on deposits below 40% of total assets and loans (when tagged) below 25%.

**Routing (Japan).** `jp_route()` in `engine/filings_map.py` reads the EDINET DEI industry
code, ignoring case (EDINET writes `bnk` and `BNK`). A bank code sends the company to
`engine/maps/jgaap_bank.yaml`; an insurer code to `jgaap_insurer_life.yaml` (it files 保険料等収入)
or `jgaap_insurer_nonlife.yaml` (it files 正味収入保険料); a securities-firm code (`sec`) is
refused until a broker-dealer template exists; `jgaap_securities.yaml` is kept
but unused. With no financial code, the element suffix decides (BNK / INS, at least three
statement elements); the suffix SEC with a securities P&L line is refused. A code and a suffix that disagree,
or an insurer that files both premium lines, are refused. An IFRS non-life insurer that files
IFRS 17 lines goes to `engine/maps/ifrs_insurer.yaml` (see "Japan (IFRS 17)" below). Any other
IFRS bank, insurer or securities firm is refused: EDINET's IFRS taxonomy has no industry
elements. A US-GAAP filer on EDINET stays refused.

Terms: a *filed* line is one tag (or the sum of tagged parts) from the filing. A *derived* line is
worked out from other lines; its tag reads `derived: …`. The tie-out never uses a derived line
to close a gap: balance, cash and net income are checked on filed totals only.

### Bank chart (`engine/maps/us_gaap_bank.yaml`)

| Key | Meaning |
|---|---|
| `interest_income` | Gross interest (and dividend) income |
| `interest_expense` | Gross interest expense |
| `net_interest_income` | Interest income less interest expense, as filed |
| `noninterest_income` | All revenue other than net interest income: fees, trading, other |
| `total_revenue` | Net revenue = net interest income + noninterest income (derived). A bank's revenue is net of interest expense |
| `revenue_tagged` | The company's own revenue tag, for reference only (US Bancorp's is on a taxable-equivalent basis, $116M–352M a year above net revenue) |
| `provision` | Provision for credit losses (a release is negative) |
| `opex` | Noninterest (operating) expenses |
| `pretax_income`, `tax` | Profit before tax; income tax |
| `net_income_incl_nci`, `net_income_nci`, `net_income` | Net income including non-controlling interests; the NCI share; net income to the parent's shareholders |
| `cash` | Cash and due from banks + interest-bearing deposits with banks (the cash-flow statement's cash) |
| `reverse_repos` | Fed funds sold and securities bought under resale agreements |
| `trading_assets` | Trading assets, where tagged as one line |
| `securities` | Investment securities: available-for-sale + held-to-maturity |
| `loans` | Loans net of the allowance (the balance-sheet line) |
| `loan_allowance` | Allowance for loan losses (positive) |
| `total_assets` | Total assets |
| `deposits` | Deposits |
| `repo_borrowings` | Fed funds bought and securities sold under repurchase agreements |
| `short_term_borrowings`, `long_term_debt` | Borrowed money, short and long term |
| `total_liabilities`, `temporary_equity` | Total liabilities; mezzanine equity |
| `equity_parent`, `nci`, `total_equity`, `liabilities_and_equity` | Shareholders' equity (incl. preferred); NCI; total equity; total liabilities and equity |
| `retained_earnings` | Retained earnings |
| Cash flow | The listed-company keys: `cfo`, `capex`, `cfi`, `dividends_paid`, `buybacks`, `cff`, `fx_effect`, `net_change_cash`, `end_cash_cf` |

`fee_income` and `other_income` are optional detail keys for maps whose standard files them
(J-GAAP 役務取引等収益, その他業務収益). US GAAP has no fee total, so the US map leaves them out.
"Other assets" and "other liabilities" are not history lines: some banks file trading assets or
long-term debt only under their own tags (JPMorgan does), so the template works them out as the
total less the mapped lines and labels them so.

### Insurer chart (`engine/maps/us_gaap_insurer.yaml`)

| Key | Meaning |
|---|---|
| `premiums` | Premiums earned, net of reinsurance |
| `fee_income` | Insurance commissions and fees (life) |
| `investment_income` | Net investment income |
| `investment_gains`, `derivative_gains` | Net investment gains (losses); net derivative gains (losses) |
| `other_income` | Other revenues |
| `total_revenue` | Total revenues, as filed |
| `claims` | Benefits and claims incurred, net. US GAAP includes the change in reserves here. US: where no claims tag has a value that year, derived as total expenses − DAC amortisation − operating costs − interest expense − interest credited − policyholder dividends, labelled DERIVED (it includes small other expense lines; Allstate) |
| `reserve_change` | Change in policy reserves, only where filed as its own line (J-GAAP 責任準備金等繰入額). Not in the US map |
| `interest_credited`, `policyholder_dividends` | Interest credited to policyholder accounts; policyholder dividends (life) |
| `acquisition_costs` | Amortisation of deferred acquisition costs (detail; inside `opex`) |
| `total_expenses` | Total benefits, losses and expenses, as filed |
| `opex` | All other expenses = total expenses − claims − interest credited − policyholder dividends (derived). Holds acquisition and other operating costs |
| `gain_on_disposal` | US: gain (loss) on disposal of operations, outside revenues and expenses; in the pre-tax footing when filed, left out of the history when not |
| `pretax_income`, `tax`, `net_income_incl_nci`, `net_income_nci`, `net_income` | As for banks |
| `cash` | Cash and cash equivalents |
| `investments` | Total investments, as filed |
| `total_assets` | Total assets |
| `claims_reserve`, `unearned_premiums`, `future_policy_benefits`, `policyholder_account_balances` | The reserve lines, as filed |
| `policy_reserves` | The sum of the four reserve lines that are filed (derived) |
| `debt` | Short- and long-term debt, as one filed total |
| `total_liabilities`, `temporary_equity`, `equity_parent`, `nci`, `total_equity`, `liabilities_and_equity`, `retained_earnings` | As for banks |
| Cash flow | The listed-company keys |

### Japan (J-GAAP): how the keys are filled, and extra keys

The J-GAAP maps fill the keys above with these differences. Each history also carries
`chart_notes`, one line per difference, so the templates can label the lines.

- **Bank.** `total_revenue` (net revenue) is derived: 経常利益 + 営業経費 + credit costs.
  `revenue_tagged` is 経常収益 (gross ordinary income). `net_interest_income` is derived
  (資金運用収益 − 資金調達費用). `provision` is credit costs: 貸倒引当金繰入額 + 貸出金償却 −
  貸倒引当金戻入益 − 償却債権取立益, as tagged. `loans` is 貸出金 before the allowance.
  `long_term_debt` is 借用金 (all maturities) + 社債. `temporary_equity` stays empty.
- **Securities firm on the bank chart (waiting for the broker-dealer template; securities firms
  are refused today, and `jgaap_securities.yaml` is unused).** `total_revenue` is 純営業収益 (filed);
  `interest_income` / `interest_expense` are 金融収益 / 金融費用; `provision` stays empty (credit
  costs are inside SG&A); `deposits` stays empty unless filed under the bank element.
- **Insurer.** `premiums` is 保険料等収入 (life) or premiums WRITTEN, 正味収入保険料 + 生命保険料
  (non-life: J-GAAP has no earned-premium element). `claims` is claims PAID. `reserve_change` is
  derived: `reserve_provision` − `reserve_reversal`. `opex` is derived: 経常費用 − claims −
  reserve provision − investment expenses. `policy_reserves` is the filed total 保険契約準備金.
  `fee_income`, `investment_gains`, `derivative_gains`, `interest_credited`,
  `policyholder_dividends`, `unearned_premiums`, `future_policy_benefits`,
  `policyholder_account_balances` and `temporary_equity` stay empty: J-GAAP does not file them
  as separate lines.
- **All three.** `equity_parent` is derived (株主資本 + その他の包括利益累計額); J-GAAP has no
  single equity-to-owners total.

Extra keys (Japan only; a template may ignore them):

| Key | Chart | Meaning |
|---|---|---|
| `ordinary_expenses` | bank | 経常費用 |
| `ordinary_profit` | all | 経常利益 (ordinary profit) |
| `extraordinary_income`, `extraordinary_loss` | all | 特別利益, 特別損失 (inside pre-tax income) |
| `negotiable_cds` | bank | 譲渡性預金, filed apart from 預金 |
| `shareholders_equity`, `accumulated_oci`, `share_options` | all | 株主資本, その他の包括利益累計額, 新株予約権 (net assets = their sum + NCI) |
| `operating_profit`, `non_operating_income`, `non_operating_expenses` | bank (securities firm; unused until the broker-dealer template) | 営業利益, 営業外収益, 営業外費用 |
| `reserve_provision`, `reserve_reversal` | insurer | 責任準備金等繰入額 (+ 支払備金繰入額, non-life), in 経常費用; the reversals, in 経常収益 |
| `investment_expenses` | insurer | 資産運用費用 (incl. realised losses) |
| `admin_expenses` | insurer | 事業費 (life) or 営業費及び一般管理費 (non-life); detail inside `opex` |
| `policyholder_dividend_provision` | insurer (life) | 契約者配当準備金繰入額: below extraordinary items, outside `total_expenses` |
| `policy_reserve`, `dividend_reserve` | insurer | 責任準備金 (non-life: 責任準備金等), 契約者配当準備金 |

The history also records `insurer_type` (`life` / `non_life`) or `statement_format`
(`securities`, for the unused securities map) where it applies.

### Japan (IFRS 17): how the keys are filled, and extra keys

Tokio Marine (8766), SOMPO (8630) and MS&AD (8725) moved to IFRS in the year to March 2026.
`jp_ifrs_insurer()` routes an IFRS filer to `engine/maps/ifrs_insurer.yaml` when its latest
report files `InsuranceRevenueIFRS` and `InsuranceContractLiabilitiesLiabilitiesIFRS` (under
any prefix) and its summary of business results shows 正味収入保険料 (non-life). A bank or
securities-firm DEI code never routes there. The map reads each element by its **local name**
under `jpigp_cor` or the group's own prefix (EDINET's IFRS taxonomy has no insurer elements),
and lists every local name seen where the groups differ. The history records `basis: ifrs17`,
`standard: IFRS` on each year, `element_prefixes`, and, where filed, `opening_balance` (the
balance sheet a year before the first year, with its own balance check). Only the IFRS
report's years are used; a history that mixes standards is refused.

- `premiums` is IFRS 17 **insurance revenue**, gross of reinsurance (not written, not earned net).
- `claims` is **insurance service expenses**: incurred claims, attributable expenses and
  amortised acquisition cash flows, one line on the face of all three statements.
- `investment_income` is the net investment result as filed (interest + realised and unrealised
  gains − investment expenses). `investment_gains` stays empty: the gains are inside it.
- `other_income` = その他の収益 (+ SOMPO's その他の営業収益) + the equity-method share.
  `opex` is derived: non-attributable 一般管理費 + その他の費用 + 金融費用.
- `policy_reserves` is **insurance contract liabilities**, which include the CSM (not tagged
  on the face). `claims_reserve`, `unearned_premiums` and `reserve_change` stay empty.
- `investments` = investment securities + loans + other financial investments + investment
  property + reverse repos, as tagged.

Extra keys (IFRS 17 only):

| Key | Meaning |
|---|---|
| `reinsurance_result` | Net income or expense from reinsurance contracts held (negative = a net cost) |
| `insurance_service_result` | Insurance revenue − insurance service expenses + reinsurance result, as filed |
| `insurance_finance_result` | Net insurance finance income or expenses (negative = expense), as filed |
| `insurance_finance_issued`, `reinsurance_finance` | Its parts: finance expenses on contracts issued (positive = expense); finance income on reinsurance held |
| `finance_result` | Net financial result = `investment_income` + `insurance_finance_result`, as filed |
| `investment_interest`, `investment_other_gains`, `investment_costs` | Parts of the investment result (detail) |
| `admin_expenses`, `other_expenses`, `finance_costs` | Parts of `opex` |
| `other_net` | Other income and expenses, net: SOMPO's filed subtotal, else the sum of its tagged lines |
| `insurance_contract_assets`, `reinsurance_contract_assets`, `reinsurance_contract_liabilities`, `investment_contract_liabilities` | Balance-sheet lines outside policy reserves and investments |

P&L footings that flag the year: insurance revenue − insurance service expenses + reinsurance
result = insurance service result; investment result + insurance finance result = net
financial result; insurance service result + net financial result + other income and
expenses = profit before tax. A footing that misses by no more than the rounding of its own
figures still flags, and its reason says so. Reported only: the investment result's parts,
the insurance finance result's parts, other income − other expenses, pre-tax − tax, total
equity = owners + NCI.

### Checks added for financial companies

- **P&L footing (`pl`), flags the year:** banks, pre-tax income = net revenue − provision −
  noninterest expense (+ gain on disposal of operations, added only when that ties; Citi's
  gain sits inside its net revenue, so there it is left out and the test says so). Since
  CECL (2020) Citi, PNC and Bank of America tag their total provision under their own tags
  only; the bank map then sums the standard parts (loans, unfunded commitments, HTM
  securities, policyholder benefits) and keeps the sum only in a year where this footing
  ties. Otherwise the year shows no provision and its flag says what the parts added to;
  insurers, pre-tax income = total revenues − total benefits, losses and
  expenses.
- **Reported, never failed:** net interest income = interest income − interest expense; net
  revenue = the company's own revenue tag; insurer revenue = its parts; pre-tax income − tax =
  net income.
- **Japan, P&L footings that flag the year:** 経常収益 − 経常費用 = 経常利益 (banks, insurers);
  純営業収益 − SG&A = 営業利益 and 営業利益 + 営業外損益 = 経常利益 (securities firms, waiting for
  the broker-dealer template); 経常利益 +
  特別利益 − 特別損失 (− 契約者配当準備金繰入額, life) = 税金等調整前当期純利益. Reported only: pre-tax
  − tax = net income; net assets = equity to owners + share options + NCI; 保険契約準備金 = its
  parts.
- **Cash held for sale:** when a disposal group's cash is outside balance-sheet cash but inside
  the cash-flow statement's cash, the roll-forward adds the tagged amounts, only if that makes
  the year tie, and says so.

### Bank template (`verticals/listed_bank.yaml`, `engine/vertical_bank.py`)

`history_to_answers.py` picks it when the history's `sector` is `bank`, and writes
`<ticker>_bank_filings.yaml`. Three actual years (grey inputs), then 5–10 forecast years.

- **Lines.** Net loans = loans as filed less the allowance where the filing prints loans before
  it (Japan; the whole 貸倒引当金 line, labelled). Liquid assets = cash + reverse repos + trading
  assets + securities. Deposits include 譲渡性預金. Borrowings = repos + short-term borrowings +
  long-term debt. Other assets and other liabilities = the filed totals less those lines.
- **Drivers** (each from the last three years; how is in Data_Notes): loan and deposit growth;
  yield on opening earning assets; cost of opening interest-bearing liabilities; noninterest
  income / opening total assets; cost-to-income; credit cost / opening net loans; tax; NCI
  share; payout; buybacks. Cost of equity and long-run growth are placeholders. The target
  equity-to-assets ratio is a typed-in check, not a driver.
- **Forecast.** Loans and deposits grow; borrowings, other lines and NCI stay flat; equity to
  owners rolls forward. Liquid assets balance the sheet; `Liquidity` rebuilds them from the
  year's flows (`CF Check`), and a flag shows if they turn negative.
- **Valuation.** Residual income (`engine/fin_valuation.py`, shared with the insurer
  template): book + Σ (NI − CoE × opening equity) / (1 + CoE)^t + terminal value. P/B, P/E
  and value vs price read "enter a price" until a price is typed.
- **Opening balance.** The first actual year's ratios on opening balances (yield, cost of
  funds, NIM, NII margin, credit cost, ROE) read the balance sheet a year earlier: a few grey
  filed inputs, `hist_open_<field>` (net loans, deposits, total assets, interest-earning
  assets, interest-bearing liabilities, equity to owners), labelled "Opening balance (FY…
  year end)". They feed only those ratios, never the forecast or the defaults. With no
  earlier balance sheet in the history they stay empty, the ratios read blank, and Data_Notes
  says why.
- **Refused:** a history with fewer than 3 years (as for the listed model).
- The workbook opens on Start_Here, and its Dashboard charts net interest income and net revenue, profit, loans and deposits, equity to assets against target, and ROE.

### Insurer template (`verticals/listed_insurer.yaml`, `engine/vertical_insurer.py`)

`history_to_answers.py` picks it when the history's `sector` is `insurer`, and writes
`<ticker>_insurer_filings.yaml`. One template serves non-life and life: the type comes from the
history's `insurer_type` (Japan) or, for a US filing, from whether it tags future policy
benefits, policyholder account balances or interest credited. A row that does not apply reads
0 and Data_Notes lists it as not applicable. Three actual years, then 5–10 forecast years.

- **Lines.** Net investment income = investment income less investment expenses (Japan files
  both gross). Investment and derivative gains are the tagged gain lines (US; Japan has none
  apart). Other income = total revenue less premiums, fees, investment income, gains and
  reserve reversals (Japan: その他経常収益). The change in reserves is Japan's provision less
  reversal (US: inside claims). Japan life policyholder dividends (契約者配当準備金繰入額) sit
  below extraordinary items in the filing; the model shows them as an underwriting cost, and
  extraordinary items fall in "other items in pre-tax". Policy reserves are the tagged parts
  (US, "as tagged") or 保険契約準備金 as filed (Japan). Japanese non-life premiums are WRITTEN,
  and the workbook labels them so.
- **Drivers** (each from the last three years; how is in Data_Notes): premium growth; loss
  ratio; reserve change / premiums; expense ratio (acquisition + other operating expenses);
  policyholder dividends / premiums; net investment income / opening investments; interest
  credited / opening policyholder account balances; other income / premiums (projected,
  because its matching costs stay in expenses); tax; NCI share; payout; buybacks. The
  investment and derivative gains ratio is 0 by default (volatile, not projected; the
  history's ratio is in its derivation line). Default dividends + buybacks are capped at 100%
  of net income (buybacks scaled down; the actual ratio stays in the derivation line), in the
  bank template too. Cost of equity and long-run growth are placeholders. The target
  equity-to-assets ratio is a typed-in check.
- **Forecast.** Premiums grow, fees with them; underwriting costs are ratios to premiums.
  Reserves roll forward with the reserve change when the filing shows it (Japan), or hold
  their ratio to premiums (US); policyholder account balances keep their share. Cash, debt,
  other lines and NCI stay flat; equity to owners rolls forward. Investments balance the
  sheet; `Investment_Flows` rebuilds them from the year's flows (`CF Check`), and a flag shows
  if they turn negative.
- **Not modelled:** embedded value, the IFRS 17 contractual service margin, investment gains
  (unless typed), solvency ratios (ESR, SMR, RBC: typed in, not filed).
- **Valuation.** Residual income, as for banks (`engine/fin_valuation.py`).
- **Opening balance.** As for banks: the first actual year's investment yield, crediting
  rate and ROE read `hist_open_<field>` inputs (investments, policyholder account balances,
  equity to owners) from the year before (IFRS 17: the report's opening balance sheet). Empty,
  with blank ratios and a Data_Notes line, when the history holds no earlier balance sheet
  (SOMPO 8630 files none).
- **Refused:** a history with fewer than 3 years, except an IFRS 17 insurer (below).
- The workbook opens on Start_Here, and its Dashboard charts premiums, the combined ratio, investment income, profit, equity and ROE.
- **IFRS 17 version (`verticals/listed_insurer_ifrs.yaml`).** It extends the insurer template
  (`engine/schemas.py` merges the two) and is used only for a history with `basis: ifrs17`;
  `history_to_answers.py` refuses any other history on it, and an IFRS 17 history on the
  3-year template. Differences: **2 actual years** (the warning is in the
  history `.md`, the answers file, Data_Notes and Start_Here, and each "how derived" line
  that uses 2 years says "2-year"); the IFRS 17 labels (insurance revenue, insurance service
  expenses, net financial result, insurance contract liabilities incl. the CSM); one more
  cost row and driver, the net expense from reinsurance contracts held
  (`hist_<n>_reinsurance_expense`, `ins_reinsurance_pct`). The investment-expense row holds
  the net insurance finance expenses, so net investment income is the filed net financial
  result and the yield default is net financial result / opening investments (the FY-1
  opening investments come from the IFRS opening balance sheet where filed). Reserves are
  held at their ratio to insurance revenue (no reserve-change line). Not modelled: the CSM
  as its own line, the risk adjustment, embedded value, insurance finance taken to OCI.
  `build_model.py` refuses an answers file whose actual-year count differs from the schema's.

---

## Known gaps in the paperwork

- The header of `verticals/balance.yaml` says "7 domains, 28 assumptions". The template has
  8 sections and 33 inputs.
- The balance sheets label the balance "AUM" even when it is a lending book.
- `_Manifest` reports the generator as v0.2 for all three models, whatever the template version.

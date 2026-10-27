# The three models

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

Every workbook opens on **Dashboard**, then **Assumptions** and **Timeline**, and ends with
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

---

## Known gaps in the paperwork

- The header of `verticals/balance.yaml` says "7 domains, 28 assumptions". The template has
  8 sections and 33 inputs.
- The balance sheets label the balance "AUM" even when it is a lending book.
- `_Manifest` reports the generator as v0.2 for all three models, whatever the template version.

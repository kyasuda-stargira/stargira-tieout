# Financial Model Generator

Builds a three-statement financial model (P&L, balance sheet, cash flow) as an Excel
workbook from two small YAML files. Every number you can change sits on one
Assumptions sheet. Every other cell is a formula.

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
`formulas` is optional). That install is the only step that uses the network, and you
run it yourself.

After that, the scripts read your answers file and write an Excel workbook into a folder
you choose. The code makes no network calls: it has no web, socket or email code, and it
sends nothing anywhere. It collects no data. Claude Code itself is Anthropic's software;
what it sends to Anthropic is covered by Anthropic's own terms, not by this plugin.

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
| `engine/core.py` | Shared pieces for vertical plugins: timeline, scenario switch, curves |
| `engine/vertical_saas.py`, `engine/vertical_power.py` | Revenue and balance-sheet logic for each vertical |
| `verticals/saas.yaml`, `verticals/power_project.yaml`, `verticals/balance.yaml` | Schema templates with defaults. `balance.yaml` is for income earned on a balance the business holds, such as yield on a lending book, with a capital requirement against it |
| `answers/` | Base cases (empty files mean "use every default") |
| `skill/SKILL.md` | The Claude Code skill that runs the builder and checker |
| `docs/ARCHITECTURE.md` | Design doc: the build stages, revenue archetypes, schema grammar and roadmap |
| `docs/MODELS.md` | The three models sheet by sheet, and the simplifications to review |
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
and the Claude Code skill that drives them.

**Held back** (not in this repo): the structured-credit waterfall builder and template,
other specialist verticals, and client project folders. These
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

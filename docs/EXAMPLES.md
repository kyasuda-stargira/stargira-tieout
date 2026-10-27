# Example prompts

Three prompts that show what the plugin does, one per template. Each uses a sample
answers file from `answers/` as its data. Those files hold no overrides, so every
input is the template default. Run them in an empty folder with the plugin loaded
(for example `claude --plugin-dir /path/to/this/repo`).

Each prompt was run on 2026-10-25 in a fresh, empty folder with `claude -p` and this
plugin loaded. Each built a workbook, and the checker ended with `RESULT: PASS`.

## 1. SaaS

> Build a three-statement model of a SaaS business with /stargira-tieout:financial-model.
> Use the plugin's sample answers file answers/saas_base.yaml as the case and keep every
> template default (no interview needed). Save the workbook under ./models/, run the
> checker, and give me the checker's result line plus the five most important KPIs.

What to expect: a 34-period model (10 months, then 24 quarters) from January 2027.
ARR grows from $0.25M to about $22.8M. The planned seed and Series A rounds leave a
funding gap of about $9.0M, which the cap table prices as extra shares.

## 2. Power project

> Build a three-statement model of a 100 MW solar project with
> /stargira-tieout:financial-model. Use the plugin's sample answers file
> answers/power_base.yaml as the case and keep every template default (no interview
> needed). Save the workbook under ./models/, run the checker, and tell me the
> checker's result line, the minimum DSCR against its target, LCOE and equity IRR.

What to expect: 8 construction quarters, then 25 operating years. LCOE about
$44.96/MWh and equity IRR about 11.8%. The minimum DSCR is about 1.37, in the first
operating year, which meets the 1.30 target.

## 3. Balance type (a lending business)

> Build a three-statement model of a lending business that earns a yield on its loan
> balance, with /stargira-tieout:financial-model. Use the plugin's sample answers file
> answers/balance_base.yaml as the case and keep every template default (no interview
> needed). Save the workbook under ./models/, run the checker, and tell me the
> checker's result line, the closing balance and any warnings from the build.

What to expect: no build warnings. The balance reaches $1,000M at milestone 3 (year 6)
and ends there. The KPI sheet's Timeline Check cell reads "OK".

All figures above are from template defaults. They are placeholders, not market data,
and nobody has reviewed these models.

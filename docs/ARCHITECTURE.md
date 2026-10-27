# Generic Financial Projection Generator — Architecture v1.1

**Date:** v1.0 2026-07-16; v1.1 updated 2026-10-20 to match the code
**Lineage:** Generalised from a proven multi-entity consolidated model (v0.3+) and a
single-entity reference engine (v0.2), both built for a `balance`-archetype financial
issuer; this document defines the vertical-agnostic architecture so the same machinery can
produce a model for any business — power projects, SaaS, logistics, funds, manufacturing.

---

## 1. Design invariants (carried over from the reference implementation, non-negotiable)

1. **Source of truth = schema + answers, never the xlsx.** The workbook is *output*. Rebuild,
   don't hand-edit.
2. **Zero hardcoded business numbers outside the Assumptions sheet(s).** Every input lands on
   an Assumptions sheet as a named-range cell; every calculation-sheet cell is an Excel
   *formula* referencing named ranges or other cells. A numeric literal in a calc sheet is a
   build failure, enforced by lint (§7).
3. **One named range per input.** The SaaS and power templates prefix names by section
   (`rev_*`, `capex_*`, `fin_*`, `g_*`) so they stay unique and self-describing. The balance
   template uses plain names (`balance_launch`, `asset_yield_rate`).
4. **Balance Sheet and Cash Flow reconcile to zero every period** (within 1e-9), checked by
   `engine/verify_model.py` after every build. The checker also rejects circular references.
5. **Every build logged** in the project's `BUILD_LOG.md`: date, template and version,
   answers files, outputs, and the checker's result lines. The workbook has no log sheet;
   its `_Manifest` sheet records the template version, answers file and build time.

## 2. Pipeline overview

| Stage | Name | What happens |
|---|---|---|
| 0 | Interview | Claude asks what the business sells and picks the template (driver archetype) |
| 1 | Schema | The template's assumption tables, as YAML |
| 2 | Defaults and answers | Defaults sit in the template; the user's numbers go in `answers/<case>.yaml`, or later on the Assumptions sheet |
| 3 | Build | Schema + answers → xlsx via openpyxl: one named range per input, formulas only |
| 4 | Verify | No-hardcode lint, named-range audit, no circular references, BS and CF checks at zero |

In practice the skill (`skill/SKILL.md`) runs stages 0 and 2 as a conversation. For
stage 1 it copies a template from `verticals/` rather than generating a new schema.

Each stage writes an artefact to disk, so any stage can be re-run independently:

```
projects/<project_name>/
  schema.yaml           # stage 1+2: a copy of verticals/<template>.yaml, defaults included (the contract)
  answers/<case>.yaml   # stage 2: per-case overrides (base / bear / client-X)
  output/<case>.xlsx    # stage 3 artefact, checked in stage 4
  BUILD_LOG.md          # one entry per build, with the checker's result
```

The engine is not copied into each project. Every project is built by the shared
`engine/build_model.py` and checked by `engine/verify_model.py`. The interview is not saved
as a file; the answers file holds its result.

## 3. Stage 0 — Interview (nature of business → driver archetype)

Claude asks a short interview. Its first job is to pick the template, by matching the
business to a **driver archetype**. Its second is to collect the few numbers that matter
most; everything else keeps the template default. The questions as asked are in
`skill/SKILL.md` (Step 1).

The archetypes:

| Archetype        | Revenue formula shape                          | Example verticals                     | Built? |
|------------------|------------------------------------------------|---------------------------------------|--------|
| `balance`        | balance × rate (yield / fee on the balance)    | fund, bank, lender, e-money issuer    | yes (`balance.yaml`) |
| `capacity`       | capacity × utilisation × unit price            | power project, hotel, shipping, mining| yes (`power_project.yaml`) |
| `subscription`   | subscribers × ARPU (cohort adds/churn)         | SaaS, telecom, media                  | yes (`saas.yaml`) |
| `units`          | units sold × price (± volume growth curve)     | manufacturing, hardware, retail       | not yet |
| `take_rate`      | GMV/volume × take-rate bps                     | marketplace, PSP, exchange            | not yet |
| `contract`       | milestone/percent-complete on backlog          | EPC, construction, defence            | not yet |

The plan allows a business to stack several archetypes (a payments business = `balance` NIM
+ `take_rate` fees). The engine does not do this yet: it picks one plugin from the first
matching archetype. Smaller second revenue lines sit inside a template instead. The power
template carries a capacity payment, and the balance template a transaction fee.

What the design asks the interview to settle, and where each answer lands today:

1. **What the business sells, and to whom** → picks the template.
2. **Capex-heavy with a construction period?** → the power template (quarterly construction,
   then annual operations: default 8 quarters + 25 years).
3. **How it is financed** → project debt with DSCR tests (power), staged equity rounds with a
   cap table (SaaS), or equity injected as needed (balance).
4. **Timeline** → monthly then quarterly for SaaS (default 10 months + 24 quarters) and
   balance (2 pre-launch months + 8 months + 24 quarters).
5. **Currency and tax** → the Entity section of each template.
6. **Scenario** → which of Base, Bear or Bull to show first (SaaS and power). The axes are
   fixed by the template: churn, ARPU and new customers for SaaS; tariff, capacity factor and
   capex for power. The balance template has no scenario switch.

The interview is not saved as a separate file. Its result is the answers file
(section 5) and the template chosen.

## 4. Stage 1 — Schema (assumption tables)

The schema is a YAML file listing every assumption as
`{ id, label, section, unit, default }` — one grammar shared by every vertical
template. Units come from a closed set:
`usd_millions | percent | bps | ratio | integer | date | text | per_unit`.

Today the schema is not generated. The skill copies a ready-made template from
`verticals/` into the project. Two optional blocks add inputs automatically:

- `scenarios:` — Base/Bear/Bull rows; each value becomes an input `scn_<axis>_<scenario>`,
  and `g_scenario` picks the live row.
- `curves:` — year-by-year paths; each point becomes inputs `crv_<curve>_<n>_year` and
  `crv_<curve>_<n>_<field>`, with straight lines between points.

The SaaS and power templates list the **module blocks** they are made of in `meta.modules`.
The list documents the template; the engine does not read it. The balance template has no
list, but its sections cover the same ground. The modules:

| Module               | Used by | Contents (as built)                                              |
|----------------------|---------|------------------------------------------------------------------|
| `entity_timeline`    | all     | entity, currency, tax, start or financial-close date, period counts |
| `scenario`           | SaaS, power | Base/Bear/Bull rows scaling the template's chosen drivers     |
| `revenue_<archetype>`| all     | driver milestones or ramp, plus rates, prices and escalators     |
| `opex_headcount`     | SaaS    | per team: start month, start and end headcount, salary; on-costs and salary inflation |
| `opex_direct`        | power   | fixed and variable O&M, insurance, land lease, asset management, escalation |
| `capex_depreciation` | power   | capex items, contingency, depreciation life and salvage; capex is spent evenly over construction |
| `debt`               | power   | gearing, rate, tenor, interest during construction, DSRA months, a DSCR test |
| `raises_captable`    | SaaS    | two rounds: close month, size, pre-money, fees; founder shares and ESOP |
| `working_capital`    | all     | receivable and payable days; prepaid and accrued items where used |
| `cash_policy`        | all     | minimum cash; SaaS adds a buffer of months of costs; balance uses perfect foresight |
| `curves`             | power   | tariff escalation and merchant price paths                       |

Rule: **milestone/curve inputs, not point estimates.** Growth is expressed as milestones
(start and end values, or `milestone1_year/milestone1_value…`) or year-indexed curves, never
a single growth-rate cell, so the Assumptions sheet stays tweakable without touching formulas.

## 5. Stage 2 — Defaults

Every schema item ships with a **default** so the model builds and reconciles on
first run, before the user has touched anything:

- Values come from (in priority order): what the user said in the interview → the template
  default in `verticals/*.yaml`. Some defaults carry a YAML comment saying where they came
  from or that they are placeholders; most do not. The skill does not research benchmarks at
  build time.
- Per-case overrides live in `answers/<case>.yaml` (flat `id: value` map). `base.yaml` may be
  empty — template defaults ARE the base case. Other cases are extra answer files, or, for
  SaaS and power, the in-workbook scenario switch.
- Unknown ids in an answers file trigger a warning and are ignored.
- After the first build, the user can change numbers **directly on the Assumptions sheet**
  (every input is one editable named cell); the workbook recalculates because calculation
  sheets hold only formulas. For lasting changes, copy the change into the answers YAML and
  rebuild. Period counts are the exception: they fix the number of rows, so changing them
  needs a rebuild.

## 6. Stage 3 — Build engine (shared core + vertical plugin)

The engine lives in `engine/`. One command builds any model:

```
python engine/build_model.py verticals/<vertical>.yaml answers/<case>.yaml [output.xlsx]
```

| File | Role |
|---|---|
| `build_model.py` | The command line, the shared sheets and the balance model. It reads the template and answers, writes `Assumptions` (one named range per input) and `_Manifest`, and picks the plugin. With no plugin, it builds the balance model itself |
| `core.py` | Shared building blocks for plugins: the `Timeline`, the `Scenario` sheet, curves, and helpers to declare and fill period sheets |
| `vertical_saas.py` | Plugin for the `subscription` archetype |
| `vertical_power.py` | Plugin for the `capacity` archetype |

**How the plugin is picked.** `meta.archetypes` in the template decides:
`subscription` → `vertical_saas.py`, `capacity` → `vertical_power.py`, anything else
→ the balance model in `build_model.py`.

**The plugin contract.** A plugin exposes one function, `build(wb, schema, values)`, and
builds every sheet between `Assumptions` and `_Manifest`, three statements included. It uses
`core.Model` like this:

1. `declare(sheet, cols)` lists every period sheet's columns up front. Any sheet can then
   refer to any other, whatever order they are built in.
2. `fill(sheet, rowfn)` writes the sheet row by row. `rowfn(r)` returns a formula for every
   declared column. A missing formula stops the build. Row r is the same period on every sheet.
3. `static_sheet(name, rows)` writes a single-value sheet, such as `Capex`, `Cap_Table` or the
   power `KPI`.

So the shared code covers inputs, timeline, scenarios, curves and sheet layout. Each plugin
writes its own revenue, costs, PnL, balance sheet and cash flow. This differs from the v1.0
plan, which had one generic three-statement engine with plugins supplying only revenue and
special balance-sheet lines.

**Sheets every model has:** `Assumptions` first; `Timeline` next; `KPI`; `_Manifest` last.
The sheets in between depend on the vertical; `MODELS.md` lists them sheet by sheet.
There are no `Charts` or `Build_Log` sheets, and no segments or consolidation layer yet.

## 7. Stage 4 — Verify (self-check before handing over)

`engine/verify_model.py <schema.yaml> <workbook.xlsx>` must end with `RESULT: PASS` before the
model is handed over. It runs four checks:

1. **No-hardcode lint:** every sheet except Assumptions and `_Manifest` may hold only
   formulas. Numbers inside formulas must be unit conversions from an allowed list. On the
   Timeline sheet, whole-number period indices and month counts are allowed and reported.
2. **Named-range audit:** every schema input has exactly one named range pointing at one cell
   on Assumptions; no name exists that is not an input; no `#REF!`. Inputs no formula uses are
   listed as warnings.
3. **No circular references,** judged from the formula text the way Excel judges it.
4. **Reconciliation:** every check column ("BS Check", "CF Check") is evaluated from the
   workbook's own formulas and must be zero (within 1e-9) in every period. Evaluation uses the
   `formulas` package if installed, else the built-in evaluator in `engine/xl_eval.py`.

The checker does not recompute the model independently in Python; it evaluates the
formulas the builder wrote. An independent recomputation is part of the paid verified
build, not the public checker.

The hidden `_Manifest` sheet records the generator name, template version, vertical, answers
file path and build time. The checker does not test it, and it holds no answers-file hash or
engine git state.

## 8. Worked example — Power Project (see `verticals/power_project.yaml`)

Interview → `capacity` archetype, capex-heavy, project-debt financed, quarterly then annual
timeline. The template's input sections:

1. Entity (name, currency, tax rate, tax holiday from COD, scenario switch)
2. Timeline (financial close date; 8 construction quarters; 25 operating years)
3. Plant (capacity in MW, year-1 capacity factor, annual degradation rate, availability)
4. Revenue (PPA share; PPA tariff in $/MWh; merchant price; capacity payment per MW-year)
5. Direct costs (fixed O&M per MW-year, variable O&M per MWh, insurance % of capex, land
   lease, asset management, cost escalation)
6. Capex (EPC per MW, development % of EPC, grid connection, contingency; depreciation life
   and salvage)
7. Financing (gearing, rate, tenor from COD, whether interest during construction is added to
   the loan, DSRA months, target DSCR as a test)
8. Working capital (receivable and payable days)
9. Cash (minimum cash; dividend lock-up DSCR)
10. KPIs (discount rate for LCOE)

Plus a scenario block scaling {tariff, capacity factor, capex} and two curves (tariff
escalation, merchant price path).

The KPI sheet gives LCOE, minimum and average DSCR, equity IRR, project IRR, equity payback
and EBITDA margin, all from formulas.

The plugin, `engine/vertical_power.py`, writes the power-specific sheets. The core of
the `Generation` sheet, per period:

- MWh = operating flag × capacity × 8,760 × (months ÷ 12) × capacity factor ×
  (1 − degradation)^(operating year − 1) × availability. The capacity factor is the
  figure before downtime; availability takes the downtime off once.
- revenue = MWh × PPA share × tariff[year] + MWh × (1 − PPA share) × merchant price[year]
  + capacity payment

At COD, construction in progress moves to plant (PP&E). The loan is repaid in proportion
to EBITDA over the tenor; it is not sculpted to the target DSCR or repaid as an annuity.
`docs/MODELS.md` lists every sheet and simplification.

## 9. Roadmap

Done:

- v1.0 (2026-07-16): this architecture, the vertical template library and the interview skill.
- v1 build (2026-10-18 to 19): the engine split into a shared core and plugins (section 6);
  SaaS and power plugins; the balance model kept in `build_model.py`; the checker; the
  skill rewritten to call the engine; packaged as a Claude Code plugin.

Next:

- More verticals, each as a template plus a plugin that passes the checker.
- Segment and consolidation support (several entities, with eliminations), lifted from the
  consolidated reference model into the shared core.

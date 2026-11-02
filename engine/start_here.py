"""
Start-here guide for every model (listed company, bank, insurer, and the SaaS,
power-project and balance-type templates), and a clearer Assumptions sheet for
models built from filings.

A first-time user opens the workbook and needs to know three things: what to
type, where to type it, and where the answer is. This module adds:
  - a "Start_Here" sheet, first in the workbook: the three steps with links,
    the headline results (linked to Valuation), a colour key and a sheet guide;
  - on Assumptions, a "Where this comes from" column, the filed actual years
    in grey (leave them alone) and the share price cell picked out.

Every number on Start_Here is a formula linked to another sheet, so the
no-hardcode lint still holds. What differs by template (the step 2 text, the
results and the sheet guide) is in KINDS; the rest is shared.
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.hyperlink import Hyperlink

from build_model import BORDER, INPUT_FILL, PCT1, SECTION_FILL, USD2, _style_header

ACTUAL_FILL = PatternFill("solid", fgColor="E7E6E6")   # filed figures: leave alone
PRICE_FILL = PatternFill("solid", fgColor="FFD966")    # the one cell every user must fill
TITLE_FONT = Font(bold=True, size=16)
BOLD = Font(bold=True)
LINK_FONT = Font(color="0563C1", underline="single")
WRAP = Alignment(wrap_text=True, vertical="top")

# Start_Here results: (label, Valuation row label, number format[, "price"]).
# A fourth item "price" makes the cell read "enter a price" until one is typed.
RESULTS = [
    ("Share price you typed", "Share price", "0.00"),
    ("Implied value per share (DCF)", "Implied value per share", "0.00"),
    ("Implied value vs your price", "Implied value vs typed price", PCT1),
    ("Market capitalisation (m)", "Market capitalisation (m)", USD2),
    ("Enterprise value (m)", "Enterprise value = market cap + net debt (m)", USD2),
    ("EV / EBITDA", "EV / EBITDA", "0.00"),
    ("P/E", "P/E", "0.00"),
]

SHEETS = [
    ("Dashboard", "Charts of revenue, profit, cash flow, cash and net debt, actual and forecast."),
    ("Assumptions", "Every input. Yellow cells are yours to change; grey cells are the filed actual years."),
    ("Timeline", "One row per year: actual (A) or forecast (F), with each filing's ID."),
    ("PnL", "Income statement: revenue down to net income."),
    ("Working_Capital", "Receivables, inventory and payables, from the days on Assumptions."),
    ("Fixed_Assets", "Property, plant and equipment: capex in, depreciation out."),
    ("Funding", "Debt, interest, dividends, buybacks and the credit line that keeps cash at the minimum."),
    ("Balance_Sheet", "Assets, liabilities and equity; the check column must read zero."),
    ("Cash_Flow", "Operating, investing and financing cash flow; the check column must read zero."),
    ("DCF", "Free cash flow per forecast year and its present value."),
    ("Valuation", "Market value at your price, multiples, and the DCF value per share."),
    ("Data_Notes", "Where every actual figure came from, tie-out flags, and what the model leaves out."),
]


FIN_RESULTS = [
    ("Share price you typed", "Share price", "0.00", "price"),
    ("Residual-income value per share", "Residual-income value per share", "0.00"),
    ("Residual-income value vs your price", "Residual-income value vs typed price", PCT1),
    ("Market capitalisation (m)", "Market capitalisation (m)", USD2),
    ("P/B (price / book value)", "P/B (price / book value per share)", "0.00"),
    ("P/E", "P/E", "0.00"),
    ("ROE, last actual year", "ROE, last actual year", PCT1),
]

FIN_SHEETS_HEAD = [
    ("Assumptions", "Every input. Yellow cells are yours to change; grey cells are the filed actual years."),
    ("Timeline", "One row per year: actual (A) or forecast (F), with each filing's ID."),
]
FIN_SHEETS_TAIL = [
    ("Residual_Income", "Net income to owners less the cost of equity on opening equity, "
                        "and its present value, per forecast year."),
    ("Valuation", "Market value at your price, P/B, P/E, ROE, and the residual-income value per share."),
    ("Data_Notes", "Where every actual figure came from, tie-out flags, and what the model leaves out."),
]
BANK_SHEETS = (
    [("Dashboard", "Charts of net interest income, profit, loans and deposits, capital and ROE, "
                   "actual and forecast.")] + FIN_SHEETS_HEAD + [
    ("PnL", "Income statement: interest income and expense, fees, costs, credit losses, "
            "down to net income; yield, NIM, cost-to-income and ROE."),
    ("Equity", "Equity to owners: opening, plus net income, less dividends and buybacks."),
    ("Balance_Sheet", "Loans, liquid assets, deposits, borrowings and equity; the check column "
                      "must read zero. Also equity to assets against your target."),
    ("Liquidity", "Liquid assets rebuilt from the year's flows; the check column must read zero."),
    ] + FIN_SHEETS_TAIL)
INSURER_SHEETS = (
    [("Dashboard", "Charts of premiums, combined ratio, investment income, profit, equity and ROE, "
                   "actual and forecast.")] + FIN_SHEETS_HEAD + [
    ("PnL", "Income statement: premiums, investment income, claims, reserves and expenses, "
            "down to net income; loss, expense and combined ratios and ROE."),
    ("Equity", "Equity to owners: opening, plus net income, less dividends and buybacks."),
    ("Balance_Sheet", "Investments, policy reserves, debt and equity; the check column must "
                      "read zero. Also equity to assets against your target."),
    ("Investment_Flows", "Investments rebuilt from the year's flows; the check column must read zero."),
    ] + FIN_SHEETS_TAIL)

# IFRS 17 insurers (verticals/listed_insurer_ifrs.yaml): the same guide with the IFRS 17
# names, found by the PnL header "Insurance Revenue (m)".
INSURER_IFRS_SHEETS = (
    [("Dashboard", "Charts of insurance revenue, combined ratio, net financial result, profit, "
                   "equity and ROE, actual and forecast.")] + FIN_SHEETS_HEAD + [
    ("PnL", "Income statement: insurance revenue, insurance service expenses, the reinsurance "
            "expense, the net financial result and other expenses, down to net income; ratios "
            "and ROE."),
    ("Equity", "Equity to owners: opening, plus net income, less dividends and buybacks."),
    ("Balance_Sheet", "Investments, insurance contract liabilities (incl. the CSM), debt and "
                      "equity; the check column must read zero. Also equity to assets against "
                      "your target."),
    ("Investment_Flows", "Investments rebuilt from the year's flows; the check column must read zero."),
    ] + FIN_SHEETS_TAIL)
INSURER_IFRS_INPUTS = ("Insurance revenue growth, the insurance service expense, reinsurance and "
                       "other expense ratios, the net financial result yield, tax, payouts, "
                       "capital target, cost of equity. ")
IFRS_WARNING = ("Only 2 actual years (IFRS 17): this group moved to IFRS in the year to March "
                "2026, so every default comes from 1 or 2 years, not 3. Treat them as rough.")

# What differs by template: the step 2 text, the results and the sheet guide.
KINDS = {
    "listed": ("Growth, margins, tax, working capital, dividends, discount rate. ", RESULTS, SHEETS),
    "bank": ("Loan and deposit growth, yield and cost of funds, fees, cost-to-income, credit "
             "cost, tax, payouts, capital target, cost of equity. ", FIN_RESULTS, BANK_SHEETS),
    "insurer": ("Premium growth, loss and expense ratios, reserves, investment yield, tax, "
                "payouts, capital target, cost of equity. ", FIN_RESULTS, INSURER_SHEETS),
}


def _link(c, sheet, cell, text):
    """A plain-text cell that jumps to another sheet (no HYPERLINK formula, so
    every evaluator reads it)."""
    c.value = text
    c.hyperlink = Hyperlink(ref=c.coordinate, location=f"'{sheet}'!{cell}")
    c.font = LINK_FONT


def _find_row(ws, col, wanted):
    """First row whose label contains `wanted` (Valuation's price label is a
    formula, so it cannot be matched exactly)."""
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=col).value
        if isinstance(v, str) and wanted in v:
            return r
    raise KeyError(f"{ws.title}: no row {wanted!r}")


def _derivations(data_notes):
    """{input id: how its default was derived}, from the answers file's notes."""
    out, on = {}, False
    for line in data_notes:
        text = str(line)
        if text.startswith("## "):
            on = text.startswith("## How each default")
            continue
        if on and ": " in text:
            ids, how = text.split(": ", 1)
            for i in ids.split(","):
                out[i.strip()] = how
    return out


def annotate_assumptions(wb, data_notes):
    """Add 'Where this comes from', grey out filed actuals, pick out the price cell."""
    ws = wb["Assumptions"]
    how = _derivations(data_notes)
    ws.column_dimensions["F"].width = 90
    _style_header(ws.cell(row=1, column=6, value="Where this comes from / what to do"))
    price_row = None
    for r in range(2, ws.max_row + 1):
        aid = ws.cell(row=r, column=3).value
        if not aid:
            if ws.cell(row=r, column=2).value:          # section row
                ws.cell(row=r, column=6).fill = SECTION_FILL
            continue
        vcell, ncell = ws.cell(row=r, column=5), ws.cell(row=r, column=6)
        ncell.border = BORDER
        ncell.alignment = WRAP
        if aid.startswith("hist_"):
            vcell.fill = ACTUAL_FILL
            ncell.value = ("Not filed: the history holds no earlier balance sheet, so the first "
                           "actual year's ratios on opening balances are blank (Data_Notes)."
                           if aid.startswith("hist_open_") and vcell.value is None else
                           "Filed figure. Leave it as it is.")
            continue
        if aid == "val_share_price":
            price_row = r
            vcell.fill = PRICE_FILL
            vcell.font = BOLD
            ncell.value = ("TYPE THE SHARE PRICE HERE, in the reporting currency. "
                           "The tool never fetches a price. Look it up on the exchange's "
                           "quote page (links on the Valuation sheet).")
        elif aid == "val_price_date":
            ncell.value = "The date of that price."
        elif aid in how:
            ncell.value = "Default: " + how[aid] + ". Change it if you see it differently."
        elif aid in ("g_entity_name", "g_ticker", "g_reporting_ccy", "g_data_source"):
            vcell.fill = ACTUAL_FILL
            ncell.value = "From the filing."
    return price_row


def build(wb, data_notes, kind="listed"):
    inputs_text, results, sheets = KINDS[kind]
    ifrs = kind == "insurer" and "Insurance Revenue (m)" in [c.value for c in wb["PnL"][1]]
    if ifrs:
        inputs_text, sheets = INSURER_IFRS_INPUTS, INSURER_IFRS_SHEETS
    price_row = annotate_assumptions(wb, data_notes)
    val = wb["Valuation"]

    ws = wb.create_sheet("Start_Here", 0)
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 90
    ws.sheet_view.showGridLines = False

    r = 1
    ws.cell(row=r, column=2, value='=g_entity_name & " (" & g_ticker & ")"').font = TITLE_FONT
    r += 1
    ws.cell(row=r, column=2,
            value=('="A forecast and valuation built from the company\'s own filings. '
                   'Amounts in millions of " & g_reporting_ccy & "."'))
    if ifrs:
        r += 1
        ws.cell(row=r, column=2, value=IFRS_WARNING).font = BOLD
    r += 2

    ws.cell(row=r, column=2, value="How to use it in three steps").font = BOLD
    r += 1
    steps = [
        ("Step 1. Type the share price",
         ("Assumptions", f"E{price_row}", "Go to the share price cell"),
         "The orange cell on Assumptions. Add the date of the price in the row below it. "
         "Until you do, the market value and multiples read \"enter a price\"."),
        ("Step 2. Check the yellow inputs",
         ("Assumptions", "E2", "Go to Assumptions"),
         inputs_text + "Each one starts "
         "from the company's own last %s years; column F says how. Change any you see "
         "differently. Leave the grey rows alone: they are the filed figures."
         % ("two" if ifrs else "three")),
        ("Step 3. Read the results",
         ("Valuation", "A1", "Go to Valuation"),
         "The headline numbers are below. Valuation shows the working, and Dashboard "
         "shows the charts."),
    ]
    for label, link, text in steps:
        c = ws.cell(row=r, column=2, value=label)
        c.font = BOLD
        c.alignment = WRAP
        _link(ws.cell(row=r, column=3), *link)
        ws.cell(row=r, column=3).alignment = WRAP
        ws.cell(row=r, column=4, value=text).alignment = WRAP
        ws.row_dimensions[r].height = 46
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Results (update as you change the inputs)").font = BOLD
    r += 1
    for label, val_label, fmt, *price in results:
        vr = _find_row(val, 1, val_label)
        ws.cell(row=r, column=2, value=label).border = BORDER
        f = f"=Valuation!B{vr}"
        if price:
            f = f'=IF(Valuation!B{vr}>0, Valuation!B{vr}, "enter a price")'
        c = ws.cell(row=r, column=3, value=f)
        c.number_format = fmt
        c.border = BORDER
        c.alignment = Alignment(horizontal="right")
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Colour key").font = BOLD
    r += 1
    for fill, text in [(PRICE_FILL, "The share price: you must type it."),
                       (INPUT_FILL, "Your inputs: change them freely."),
                       (ACTUAL_FILL, "Filed figures from the company's reports: leave them alone."),
                       (None, "White cells are formulas: don't type over them.")]:
        c = ws.cell(row=r, column=2, value="")
        c.border = BORDER
        if fill:
            c.fill = fill
        ws.cell(row=r, column=3, value=text)
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="What each sheet does").font = BOLD
    r += 1
    for name, text in sheets:
        _link(ws.cell(row=r, column=2), name, "A1", name)
        ws.cell(row=r, column=3, value=text)
        r += 1
    r += 1
    ws.cell(row=r, column=2,
            value=("Before you rely on the numbers, read Data_Notes: it lists the years whose "
                   "history did not tie out, lines the filing does not tag, and what the "
                   "forecast leaves out."))

    wb.active = 0
    for other in wb.worksheets[1:]:
        other.sheet_view.tabSelected = False


# The three base templates (SaaS, power project, balance type): no filings and no
# share price. Every input starts at the template's default, and the results are
# formulas over the model's own sheets. A result is (label, how, sheet, column
# header or KPI row label, number format); `how` is last (the last period), min,
# sum, maxabs (the largest gap either way: a check that must read 0) or kpi (the
# KPI sheet's value for that row).
CHECKS = [("Balance-sheet check, largest gap in any period (must read 0)", "maxabs",
           "Balance_Sheet", "BS Check", USD2),
          ("Cash-flow check, largest gap in any period (must read 0)", "maxabs",
           "Cash_Flow", "CF Check", USD2)]
BASE = {
    "saas": {
        "name": "g_entity_name",
        "what": "A three-statement model of a SaaS company: customers and their subscriptions "
                "drive revenue, and equity rounds fund the losses. Amounts in $ millions.",
        "step1": ("Section 1: the company name, currency, tax rate and scenario. Section 2: the "
                  "start date, how many monthly then quarterly periods, and the customers and "
                  "ARR you start with."),
        "inputs": ("Sections 3 to 13: customer adds, churn, pricing, cost of sales, sales and "
                   "marketing, headcount and salaries, R&D and G&A, billing terms, funding "
                   "rounds, working capital and the minimum cash balance. The scenario cell "
                   "(1 = Base, 2 = Bear, 3 = Bull) moves churn, pricing and customer adds "
                   "together. "),
        "results": [("ARR at the end of the model ($M)", "last", "KPI", "ARR ($M)", USD2),
                    ("Lowest cash balance in any period ($M)", "min", "Balance_Sheet", "Cash ($M)", USD2),
                    ("Funding the model had to add, beyond your rounds ($M)", "sum", "Funding",
                     "Unplanned Funding ($M)", USD2),
                    ("Customers at the end of the model", "last", "Customers", "Customers Close", "#,##0"),
                    ("Net income, whole model ($M)", "sum", "PnL", "Net Income ($M)", USD2)] + CHECKS,
        "sheets": [
            ("Dashboard", "Charts of revenue, profit, cash flow, cash, ARR, customers and headcount."),
            ("Assumptions", "Every input. Yellow cells are yours to change."),
            ("Timeline", "One row per period: monthly first, then quarterly."),
            ("Scenario", "What the scenario cell does to churn, pricing and customer adds."),
            ("Customers", "Customers per price tier: opening, added, churned, closing."),
            ("Revenue", "Monthly recurring revenue per tier, down to ARR and revenue."),
            ("Headcount", "Staff and their cost, per team."),
            ("OpEx", "Cost of sales, sales and marketing, R&D and G&A."),
            ("Capitalised", "Sales commissions and development spend held as assets, then written off."),
            ("Working_Capital", "Receivables, prepaid costs, payables, accruals and deferred revenue."),
            ("Opening_Balances", "The balance sheet the model starts from."),
            ("PnL", "Income statement: revenue down to net income, with tax losses carried forward."),
            ("Funding", "Cash before and after your rounds, and any extra funding the model adds to "
                        "keep cash at the minimum."),
            ("Balance_Sheet", "Assets, liabilities and equity; the check column must read zero."),
            ("Cash_Flow", "Operating, investing and financing cash flow; the check column must read zero."),
            ("Cap_Table", "Who owns what after each funding round."),
            ("KPI", "ARR, retention, churn, payback, burn and runway, period by period, with flags."),
        ],
    },
    "power": {
        "name": "g_entity_name",
        "what": "A three-statement model of a utility-scale solar plant with project finance: "
                "build, then decades of output sold under a contract and at market prices, paid "
                "for with debt and equity. Amounts in $ millions.",
        "step1": ("Section 1: the project name, currency, tax rate and scenario. Section 2: the "
                  "financial close date, how many quarters of construction and how many "
                  "operating years."),
        "inputs": ("Sections 3 to 10: plant size and output, the contract tariff and market "
                   "prices, operating costs, the build cost, the loan (size, rate, tenor, target "
                   "DSCR), working capital and cash. The scenario cell (1 = Base, 2 = Bear, "
                   "3 = Bull) moves the main risks together. "),
        "results": [("Equity IRR", "kpi", "KPI", "Equity IRR", PCT1),
                    ("Project IRR (after tax, before financing)", "kpi", "KPI", "Project IRR", PCT1),
                    ("Minimum DSCR (cash for debt service / debt service)", "kpi", "KPI",
                     "Minimum DSCR", "0.00"),
                    ("Minimum DSCR vs your target", "kpi", "KPI", "Minimum DSCR vs target", "@"),
                    ("LCOE ($/MWh, cost per unit of power over the plant's life)", "kpi", "KPI",
                     "LCOE", "0.00"),
                    ("Equity payback (years from commercial operation)", "kpi", "KPI",
                     "Equity payback", "0.0")] + CHECKS,
        "sheets": [
            ("Dashboard", "Charts of revenue, profit, cash flow, cash, net debt and DSCR."),
            ("Assumptions", "Every input. Yellow cells are yours to change."),
            ("Timeline", "One row per period: construction quarters, then operating years."),
            ("Scenario", "What the scenario cell does to the main risks."),
            ("Capex", "The build cost and how it is spent."),
            ("Ops", "Flags per period: operating or not, inside the loan's tenor, in a tax holiday."),
            ("Generation", "Output, the contract and market prices, and revenue."),
            ("OpEx", "Operating and maintenance, insurance, land lease and asset management costs."),
            ("Assets", "Construction in progress, then the plant and its depreciation."),
            ("Debt", "The construction loan, then the term loan: interest, repayments and DSCR."),
            ("Working_Capital", "Receivables and payables."),
            ("PnL", "Income statement: revenue down to net income, with tax losses carried forward."),
            ("Funding", "Debt draws, equity in and dividends out."),
            ("Balance_Sheet", "Assets, liabilities and equity; the check column must read zero."),
            ("Cash_Flow", "Operating, investing and financing cash flow; the check column must read zero."),
            ("Returns", "Project and equity cash flows behind the IRRs, payback and LCOE."),
            ("KPI", "The headline returns and debt cover ratios."),
        ],
    },
    "balance": {
        "name": "entity_name",
        "what": "A three-statement model of a business that earns on a balance it holds, such "
                "as a lending book: the balance earns a yield and fees, costs come out, and the "
                "business keeps a capital buffer. Amounts in $ millions.",
        "step1": ("Section 1: the company name, currencies and year end. Section 2: the launch "
                  "date and how many periods the model runs."),
        "inputs": ("Sections 3 to 8: how the balance grows, the yield and fees it earns, "
                   "operating costs, working capital, the cash policy and capital injections, "
                   "and the own-funds buffer. "),
        "results": [("Balance (AUM) at the end of the model ($M)", "last", "KPI", "AUM ($M)", USD2),
                    ("Revenue, whole model ($M)", "sum", "PnL", "Revenue ($M)", USD2),
                    ("Net income, whole model ($M)", "sum", "PnL", "Net Income ($M)", USD2),
                    ("Lowest cash balance in any period ($M)", "min", "Balance_Sheet", "Cash ($M)", USD2),
                    ("Own-funds buffer at the end ($M)", "last", "Balance_Sheet", "Own-Funds Buf ($M)", USD2),
                    ("Capital injected, whole model ($M)", "last", "KPI", "Cum Inject ($M)", USD2)] + CHECKS,
        "sheets": [
            ("Dashboard", "Charts of revenue, profit, cash flow, cash, the balance and runway."),
            ("Assumptions", "Every input. Yellow cells are yours to change."),
            ("Timeline", "One row per period."),
            ("Balance_Ramp", "The balance (AUM) at the start, end and average of each period."),
            ("Revenue", "Yield, fee and other revenue."),
            ("OpEx", "Operating costs."),
            ("Working_Capital", "Receivables, prepaid costs, payables and accruals."),
            ("PnL", "Income statement: revenue down to net income, with tax losses carried forward."),
            ("Forward_Cash", "The cash policy: the buffer, capital injections and dividends."),
            ("Balance_Sheet", "Assets, liabilities and equity; the check column must read zero."),
            ("Cash_Flow", "Operating, investing and financing cash flow; the check column must read zero."),
            ("KPI", "The balance, revenue, costs, profit, cash and runway, period by period."),
        ],
    },
}


def _column(ws, header):
    """Column letter of the first row-1 header that starts with `header`."""
    for c in ws[1]:
        if isinstance(c.value, str) and c.value.startswith(header):
            return c.column_letter
    raise KeyError(f"{ws.title}: no column {header!r}")


def _result_formula(wb, how, sheet, key):
    ws = wb[sheet]
    if how == "kpi":
        return f"='{sheet}'!B{_find_row(ws, 1, key)}"
    col, last = _column(ws, key), ws.max_row
    rng = f"'{sheet}'!{col}2:{col}{last}"
    return {"last": f"='{sheet}'!{col}{last}", "min": f"=MIN({rng})", "sum": f"=SUM({rng})",
            "maxabs": f"=MAX(MAX({rng}),-MIN({rng}))"}[how]


def build_base(wb, kind):
    """Start_Here for a SaaS, power-project or balance-type model."""
    spec = BASE[kind]
    ws = wb.create_sheet("Start_Here", 0)
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 52
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 90
    ws.sheet_view.showGridLines = False

    r = 1
    ws.cell(row=r, column=2, value="=" + spec["name"]).font = TITLE_FONT
    r += 1
    ws.cell(row=r, column=2, value=spec["what"])
    r += 2

    ws.cell(row=r, column=2, value="How to use it in three steps").font = BOLD
    r += 1
    steps = [
        ("Step 1. Name the business and set the dates",
         ("Assumptions", "E2", "Go to Assumptions"), spec["step1"]),
        ("Step 2. Check the yellow inputs",
         ("Assumptions", "E2", "Go to Assumptions"),
         spec["inputs"] + "Each one starts at the template's default: a starting point, not a "
         "view of your business. Change any that don't fit."),
        ("Step 3. Read the results",
         ("Dashboard", "A1", "Go to Dashboard"),
         "The headline numbers are below and update as you change the inputs. Dashboard "
         "shows the charts and KPI the ratios. Both checks must read 0; if one doesn't, an "
         "input has broken the model."),
    ]
    for label, link, text in steps:
        c = ws.cell(row=r, column=2, value=label)
        c.font = BOLD
        c.alignment = WRAP
        _link(ws.cell(row=r, column=3), *link)
        ws.cell(row=r, column=3).alignment = WRAP
        ws.cell(row=r, column=4, value=text).alignment = WRAP
        ws.row_dimensions[r].height = 62
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Results (update as you change the inputs)").font = BOLD
    r += 1
    for label, how, sheet, key, fmt in spec["results"]:
        ws.cell(row=r, column=2, value=label).border = BORDER
        c = ws.cell(row=r, column=3, value=_result_formula(wb, how, sheet, key))
        c.number_format = fmt
        c.border = BORDER
        c.alignment = Alignment(horizontal="right")
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Colour key").font = BOLD
    r += 1
    for fill, text in [(INPUT_FILL, "Your inputs, on Assumptions: change them freely."),
                       (None, "White cells are formulas: don't type over them.")]:
        c = ws.cell(row=r, column=2, value="")
        c.border = BORDER
        if fill:
            c.fill = fill
        ws.cell(row=r, column=3, value=text)
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="What each sheet does").font = BOLD
    r += 1
    for name, text in spec["sheets"]:
        if name in wb.sheetnames:
            _link(ws.cell(row=r, column=2), name, "A1", name)
            ws.cell(row=r, column=3, value=text)
            r += 1

    wb.active = 0
    for other in wb.worksheets[1:]:
        other.sheet_view.tabSelected = False

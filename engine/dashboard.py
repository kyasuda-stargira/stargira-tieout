"""
Dashboard sheet: charts of the main figures over time, for every vertical.

The sheet holds a small linked table (one row per period, formulas only) and
line charts drawn from it. Flows (revenue, profit, cash flow) are shown as an
annual rate (period value × 12 / months), because periods change length from
monthly to quarterly to annual and raw period totals would jump at each change.
Balances (cash, net debt, ARR) are period-end values. The x-axis is a date axis,
so a quarter takes three times the width of a month.

Columns are found by their header text, so the same code serves the SaaS,
power, balance, listed, bank and insurer builders. The listed, bank and insurer
models are currency-neutral: their headers say "(m)" (millions of the
reporting currency) where the others say "($M)", so for them every "$M" in the
headers and titles below reads "m". The bank and insurer have no EBITDA or cash
flow statement, so they replace the common charts with their own (FIN_KINDS),
and like the listed model they are annual and labelled by fiscal year.
"""

from __future__ import annotations

from openpyxl.chart import LineChart, Reference
from openpyxl.chart.axis import DateAxis
from openpyxl.utils import get_column_letter

from build_model import BORDER, PCT1, _style_header

USD = "#,##0.00"
NUM1 = "#,##0.0"
# Categorical slots 1-3 (fixed order) from the shared chart palette.
SERIES_COLORS = ["2A78D6", "EB6834", "1BAF7A"]
TABLE_COL = 21          # data table starts in column U, right of the charts
CHART_W, CHART_H = 16, 7.5   # cm
ROWS_PER_CHART = 16


def _find(wb, sheet, header):
    ws = wb[sheet]
    for c in ws[1]:
        if c.value == header:
            return c.column_letter
    raise KeyError(f"{sheet}: no column headed {header!r}")


# Each vertical: list of (column header on Dashboard, number format, formula
# builder), then charts as (title, [Dashboard headers]). A formula builder gets
# (col, r) where col(sheet, header) returns the source column letter.
def _annual(sheet, header):
    return lambda col, r: f"{sheet}!{col(sheet, header)}{r}*12/Timeline!{col('Timeline', 'Months')}{r}"


def _end(sheet, header):
    return lambda col, r: f"{sheet}!{col(sheet, header)}{r}"


def _ratio(sheet, header):
    # KPI cells hold "" when undefined; NA() leaves a gap instead of a zero.
    return lambda col, r: (f'IF(ISNUMBER({sheet}!{col(sheet, header)}{r}), '
                           f'{sheet}!{col(sheet, header)}{r}, NA())')


def _fcf(col, r):
    return (f"(Cash_Flow!{col('Cash_Flow', 'CF from Ops ($M)')}{r}"
            f"+Cash_Flow!{col('Cash_Flow', 'CF from Inv ($M)')}{r})"
            f"*12/Timeline!{col('Timeline', 'Months')}{r}")


COMMON = [
    ("Revenue ($M/yr)", USD, _annual("PnL", "Revenue ($M)")),
    ("EBITDA ($M/yr)", USD, _annual("PnL", "EBITDA ($M)")),
    ("Net income ($M/yr)", USD, _annual("PnL", "Net Income ($M)")),
    ("Operating cash flow ($M/yr)", USD, _annual("Cash_Flow", "CF from Ops ($M)")),
    ("Free cash flow ($M/yr)", USD, _fcf),
    ("Cash ($M)", USD, _end("Balance_Sheet", "Cash ($M)")),
]
COMMON_CHARTS = [
    ("Revenue, EBITDA and net income ($M a year)",
     ["Revenue ($M/yr)", "EBITDA ($M/yr)", "Net income ($M/yr)"]),
    ("Operating and free cash flow ($M a year)",
     ["Operating cash flow ($M/yr)", "Free cash flow ($M/yr)"]),
    ("Cash and net debt ($M, period end)", ["Cash ($M)", "Net debt ($M)"]),
]


def _net_debt(debt_header):
    if debt_header is None:
        return lambda col, r: f"-Balance_Sheet!{col('Balance_Sheet', 'Cash ($M)')}{r}"
    return lambda col, r: (f"Balance_Sheet!{col('Balance_Sheet', debt_header)}{r}"
                           f"-Balance_Sheet!{col('Balance_Sheet', 'Cash ($M)')}{r}")


def _cum(sheet, header):
    def f(col, r, first):
        cur = f"{sheet}!{col(sheet, header)}{r}"
        return cur if r == first else f"{{prev}}+{cur}"
    f.cumulative = True
    return f


def _arr_per_head(col, r):
    heads = f"Headcount!{col('Headcount', 'Total Heads')}{r}"
    return f"IF({heads}=0, NA(), Revenue!{col('Revenue', 'ARR ($M)')}{r}*1000/{heads})"


VERTICALS = {
    "saas": ([("Net debt ($M)", USD, _net_debt(None)),
              ("ARR ($M)", USD, _end("Revenue", "ARR ($M)")),
              ("Customers", "#,##0", _end("Customers", "Customers Close")),
              ("Headcount", "#,##0", _end("Headcount", "Total Heads")),
              ("ARR per head ($k)", "#,##0", _arr_per_head),
              ("Unplanned funding, cumulative ($M)", USD, _cum("Funding", "Unplanned Funding ($M)")),
              ("NRR (annualised)", PCT1, _ratio("KPI", "NRR (annualised)")),
              ("Gross margin", PCT1, _ratio("KPI", "Gross Margin")),
              ("CAC payback (months)", NUM1, _ratio("KPI", "CAC Payback (months)"))],
             [("ARR ($M, period end)", ["ARR ($M)"]),
              ("Customers and headcount (period end)", ["Customers", "Headcount"]),
              ("ARR per head ($k)", ["ARR per head ($k)"]),
              ("Unplanned funding, cumulative ($M)", ["Unplanned funding, cumulative ($M)"]),
              ("NRR and gross margin", ["NRR (annualised)", "Gross margin"]),
              ("CAC payback (months)", ["CAC payback (months)"])]),
    "power": ([("Net debt ($M)", USD, _net_debt("Debt ($M)")),
               ("DSCR", "0.00", _ratio("Debt", "DSCR"))],
              [("DSCR (cash available ÷ debt service)", ["DSCR"])]),
    "balance": ([("Net debt ($M)", USD, _net_debt(None)),
                 ("AUM ($M)", USD, _end("KPI", "AUM ($M)")),
                 ("Runway on net burn (months)", NUM1, _ratio("KPI", "Runway on Net Burn (mo)"))],
                [("AUM ($M, period end)", ["AUM ($M)"]),
                 ("Runway on net burn (months)", ["Runway on net burn (months)"])]),
    "listed": ([("Net debt ($M)", USD,
                 lambda col, r: (f"Balance_Sheet!{col('Balance_Sheet', 'Debt ($M)')}{r}"
                                 f"+Balance_Sheet!{col('Balance_Sheet', 'Revolver ($M)')}{r}"
                                 f"-Balance_Sheet!{col('Balance_Sheet', 'Cash ($M)')}{r}"))],
               []),
}
BANK = ([("Net interest income ($M/yr)", USD, _annual("PnL", "Net Interest Income (m)")),
         ("Net revenue ($M/yr)", USD, _annual("PnL", "Net Revenue (m)")),
         ("Net income to owners ($M/yr)", USD, _annual("PnL", "Net Income to Owners (m)")),
         ("Net loans ($M)", USD, _end("Balance_Sheet", "Net Loans (m)")),
         ("Deposits ($M)", USD, _end("Balance_Sheet", "Deposits incl. CDs (m)")),
         ("Equity to owners / assets", PCT1, _ratio("Balance_Sheet", "Equity to Owners / Assets")),
         ("Target equity / assets", PCT1, _ratio("Balance_Sheet", "Target Equity / Assets")),
         ("ROE", PCT1, _ratio("PnL", "ROE (on Opening Equity to Owners)"))],
        [("Net interest income and net revenue ($M a year)",
          ["Net interest income ($M/yr)", "Net revenue ($M/yr)"]),
         ("Net income to owners ($M a year)", ["Net income to owners ($M/yr)"]),
         ("Net loans and deposits ($M, year end)", ["Net loans ($M)", "Deposits ($M)"]),
         ("Equity to owners / assets against target",
          ["Equity to owners / assets", "Target equity / assets"]),
         ("ROE (on opening equity to owners)", ["ROE"])])
# {prem} reads "Premiums", "Premiums Written" or "Insurance Revenue" (IFRS 17), as the
# insurer's PnL header does; {nii} / {nii_col} read "Net investment income" or, for
# IFRS 17, "Net financial result" (investment result less insurance finance expenses).
INSURER = ([("{prem} ($M/yr)", USD, _annual("PnL", "{prem} (m)")),
            ("Combined ratio", PCT1,
             _ratio("PnL", "Combined Ratio (All Underwriting Costs / {prem})")),
            ("{nii} ($M/yr)", USD, _annual("PnL", "{nii_col} (m)")),
            ("Net income to owners ($M/yr)", USD, _annual("PnL", "Net Income to Owners (m)")),
            ("Equity to owners ($M)", USD, _end("Balance_Sheet", "Equity to Owners (m)")),
            ("ROE", PCT1, _ratio("PnL", "ROE (on Opening Equity to Owners)"))],
           [("{prem} ($M a year)", ["{prem} ($M/yr)"]),
            ("Combined ratio (all underwriting costs / {prem_lc})", ["Combined ratio"]),
            ("{nii} ($M a year)", ["{nii} ($M/yr)"]),
            ("Net income to owners ($M a year)", ["Net income to owners ($M/yr)"]),
            ("Equity to owners ($M, year end)", ["Equity to owners ($M)"]),
            ("ROE (on opening equity to owners)", ["ROE"])])
VERTICALS["bank"] = BANK
VERTICALS["insurer"] = INSURER
FIN_KINDS = ("bank", "insurer")             # no common charts: their own replace them
FY_KINDS = ("listed", "bank", "insurer")    # annual; labelled FY2025A / FY2026F
UNIT_TEXT = {k: "m" for k in FY_KINDS}      # replaces "$M" in headers for currency-neutral models


def _premium_header(wb):
    """'Premiums Written' when the insurer files premiums written, 'Insurance Revenue'
    for an IFRS 17 insurer, else 'Premiums'."""
    heads = [c.value for c in wb["PnL"][1]]
    for name in ("Premiums Written", "Insurance Revenue"):
        if name + " (m)" in heads:
            return name
    return "Premiums"


def build(wb, kind):
    extra_cols, extra_charts = VERTICALS[kind]
    unit = UNIT_TEXT.get(kind, "$M")
    prem = _premium_header(wb) if kind == "insurer" else ""
    ifrs = prem == "Insurance Revenue"
    nii_col = "Net Financial Result" if ifrs else "Net Investment Income"

    def u(text):
        return (text.replace("$M", unit).replace("{prem}", prem)
                .replace("{prem_lc}", prem.lower()).replace("{nii_col}", nii_col)
                .replace("{nii}", nii_col.capitalize()))

    common = ([], []) if kind in FIN_KINDS else (COMMON, COMMON_CHARTS)
    cols = [(u(h), fmt, fn) for h, fmt, fn in common[0] + extra_cols]
    charts = [(u(t), [u(h) for h in hs]) for t, hs in common[1] + extra_charts]
    last = wb["Timeline"].max_row
    first = 2

    def col(sheet, header):
        return _find(wb, sheet, u(header))

    ws = wb.create_sheet("Dashboard", 0)
    start_col = col("Timeline", "Period Start")
    label_col = col("Timeline", "Period Label")
    heads = [("Period start", "mmm yyyy", lambda c, r: f"Timeline!{start_col}{r}"),
             ("Period", None, lambda c, r: f"Timeline!{label_col}{r}")]
    if kind in FY_KINDS:
        # Fiscal years can start in any month, so a date axis would label each
        # year by the calendar year it starts in (Apple's FY2023 shows as 2022).
        # Label by fiscal year instead: FY2025A for actuals, FY2026F for forecasts.
        year_col, type_col = col("Timeline", "Year"), col("Timeline", "Type")
        heads.append(("Fiscal year", None,
                      lambda c, r: (f'"FY"&Timeline!{year_col}{r}'
                                    f'&IF(Timeline!{type_col}{r}="actual","A","F")')))
    heads += cols
    where = {}
    for i, (h, fmt, fn) in enumerate(heads):
        ci = TABLE_COL + i
        letter = get_column_letter(ci)
        where[h] = ci
        ws.column_dimensions[letter].width = max(12, len(h) + 2)
        _style_header(ws.cell(row=1, column=ci, value=h))
        for r in range(first, last + 1):
            if getattr(fn, "cumulative", False):
                f = fn(col, r, first).replace("{prev}", f"{letter}{r-1}")
            else:
                f = fn(col, r)
            c = ws.cell(row=r, column=ci, value="=" + f)
            c.border = BORDER
            if fmt:
                c.number_format = fmt

    dates = Reference(ws, min_col=where["Period start"], min_row=first, max_row=last)
    if kind in FY_KINDS:
        years = Reference(ws, min_col=where["Fiscal year"], min_row=first, max_row=last)
    for n, (title, series) in enumerate(charts):
        ch = LineChart()
        ch.title = title
        ch.title.overlay = False   # unset, Excel draws the title over the plot
        ch.width, ch.height = CHART_W, CHART_H
        ch.legend = ch.legend if len(series) > 1 else None
        if ch.legend is not None:
            ch.legend.position = "b"
            ch.legend.overlay = False   # unset, Excel draws it over the axis labels
        for k, h in enumerate(series):
            ch.add_data(Reference(ws, min_col=where[h], min_row=1, max_row=last),
                        titles_from_data=True)
            s = ch.series[-1]
            s.smooth = False
            s.graphicalProperties.line.solidFill = SERIES_COLORS[k]
            s.graphicalProperties.line.width = 25400   # 2 pt
        if kind in FY_KINDS:
            ch.set_categories(years)
        else:
            ch.set_categories(dates)
            ch.x_axis = DateAxis(crossAx=100)
            ch.x_axis.number_format = "yyyy"
            ch.x_axis.majorTimeUnit = "years"
            if kind == "power":   # 25+ years: label every fifth
                ch.x_axis.majorUnit = 5
        ch.x_axis.tickLblPos = "low"   # keep year labels below negative values
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        ch.y_axis.crossAx = ch.x_axis.axId   # 500 for the date axis, 10 for the text axis
        fmt = next(f for h2, f, _ in heads if h2 == series[0])
        if fmt:
            # Listed models run to hundreds of thousands of millions: no decimals on the axis.
            ch.y_axis.number_format = "#,##0" if kind in FY_KINDS and fmt == USD else fmt
            # Ratios that barely move (a combined ratio of 95.4-95.7%) get axis steps
            # finer than 0.1%, so one decimal would print the same label twice.
            if kind in ("bank", "insurer") and fmt == PCT1:
                ch.y_axis.number_format = "0.00%"
        anchor_col = "A" if n % 2 == 0 else "K"
        ws.add_chart(ch, f"{anchor_col}{1 + (n // 2) * ROWS_PER_CHART}")
    ws.sheet_view.zoomScale = 90
    wb.active = 0

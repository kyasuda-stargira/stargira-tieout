"""
Valuation pieces shared by the financial-company templates (vertical_bank.py now;
vertical_insurer.py can reuse them).

  - priced_if(): the "enter a price" rule. A row that needs the share price
    reads "enter a price" until one is typed (val_share_price > 0), the same IF
    pattern as vertical_listed.py.
  - RI_COLUMNS / ri_row(): one forecast row of a residual-income sheet.
  - build_ri_valuation(): the Valuation sheet (market value, book value and
    returns, residual-income value per share, price-lookup links).

Residual income (RI) in forecast year t is net income to owners less the cost
of equity times OPENING equity to owners:
    RI_t = NI_t - CoE x E_(t-1)
    value = E_0 + sum_t RI_t / (1+CoE)^t + [RI_N x (1+g) / (CoE - g)] / (1+CoE)^N
with E_0 the last actual year's equity to owners, end-of-year discounting and
g the long-run growth of residual income after the last forecast year.
Inputs named here: val_cost_of_equity, val_terminal_growth, val_share_price,
val_price_date, val_price_source, val_shares_diluted, val_shares_source,
g_reporting_ccy, g_forecast_years.
"""

from __future__ import annotations

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from build_model import BORDER, PCT1, SECTION_FILL, USD2, _style_header
from vertical_listed import PRICE_LOOKUP_LINES

X2 = "0.00"
X4 = "0.0000"
PRICED = "val_share_price>0"


def priced_if(expr, guard=PRICED):
    """expr once a price is typed, else the text "enter a price"."""
    return f'IF({guard}, {expr}, "enter a price")'


RI_COLUMNS = [
    ("open_equity", "Opening Equity to Owners (m)", USD2),
    ("ni", "Net Income to Owners (m)", USD2),
    ("roe", "ROE on Opening Equity", PCT1),
    ("charge", "Equity Charge = CoE x Opening Equity (m)", USD2),
    ("ri", "Residual Income (m)", USD2),
    ("period", "Discount Period (yrs)", "0"),
    ("df", "Discount Factor", X4),
    ("pv", "PV of Residual Income (m)", USD2),
]


def ri_row(col, open_equity, ni, period):
    """Formulas for one forecast row. col(key) gives this row's cell on the RI
    sheet; open_equity, ni and period are cell references elsewhere."""
    return {
        "open_equity": open_equity,
        "ni": ni,
        "roe": f"IF({col('open_equity')}>0, {col('ni')}/{col('open_equity')}, 0)",
        "charge": f"val_cost_of_equity*{col('open_equity')}",
        "ri": f"{col('ni')}-{col('charge')}",
        "period": period,
        "df": f"1/(1+val_cost_of_equity)^{col('period')}",
        "pv": f"{col('ri')}*{col('df')}",
    }


def write_rows(ws, rows, start=2):
    """rows: (key, label, formula with {key} placeholders, number format, note);
    key "section" writes a bold band. Returns {key: $B$row}."""
    ref = {}
    r = start
    for key, label, formula, fmt, note in rows:
        if key == "section":
            for col in (1, 2, 3):
                ws.cell(row=r, column=col).fill = SECTION_FILL
            ws.cell(row=r, column=1, value=label).font = Font(bold=True)
            r += 1
            continue
        ref[key] = f"$B${r}"
        ws.cell(row=r, column=1, value=label).border = BORDER
        c = ws.cell(row=r, column=2, value="=" + formula.format(**ref))
        c.border = BORDER
        if fmt:
            c.number_format = fmt
        if note:
            ws.cell(row=r, column=3, value=note).border = BORDER
        r += 1
    return ref, r


def build_ri_valuation(wb, book, open_book, ni_last, ri_rng, pv_rng, df_rng, book_note):
    """Valuation sheet for a financial company.
    book, open_book, ni_last: cell references (last actual equity to owners, the
    year before's, last actual net income to owners). ri_rng, pv_rng, df_rng:
    forecast-year ranges on the residual-income sheet."""
    ws = wb.create_sheet("Valuation")
    for col, (h, w) in enumerate([("Item", 62), ("Value", 18), ("Note", 96)], 1):
        ws.column_dimensions[get_column_letter(col)].width = w
        _style_header(ws.cell(row=1, column=col, value=h))
    rows = [
        ("section", "Market value", None, None, None),
        ("price", '=IF(val_share_price>0, "Share price (source: " & val_price_source & ", " & '
                  'TEXT(val_price_date, "yyyy-mm-dd") & ")", "Share price (not entered yet)")',
         "val_share_price", X2,
         "Typed by the user. 0 means not entered yet. Quote it in the reporting currency."),
        ("ccy", "Reporting currency", "g_reporting_ccy", None, None),
        ("shares", '="Shares (millions; source: " & val_shares_source & ")"', "val_shares_diluted",
         "#,##0.000",
         "Default: the count in the latest annual filing (US: cover-page shares outstanding; "
         "Japan: issued shares less treasury shares). Not diluted."),
        ("mcap", "Market capitalisation (m)", priced_if("{price}*{shares}"), USD2, None),
        ("section", "Book value and returns (last actual year)", None, None, None),
        ("book", "Equity to owners, last actual year end (m)", book, USD2, book_note),
        ("bvps", "Book value per share", 'IF({shares}>0, {book}/{shares}, "n/a")', X2, None),
        ("ni", "Net income to owners, last actual year (m)", ni_last, USD2, None),
        ("roe", "ROE, last actual year (on opening equity to owners)",
         f'IF({open_book}>0, {{ni}}/{open_book}, "n/a")', PCT1, None),
        ("pb", "P/B (price / book value per share)",
         priced_if('IF({book}>0, {mcap}/{book}, "n/a")'), X2, None),
        ("pe", "P/E", priced_if('IF({ni}>0, {mcap}/{ni}, "n/a")'), X2, None),
        ("section", "Residual-income value", None, None, None),
        ("coe", "Cost of equity", "val_cost_of_equity", PCT1,
         "Input, not derived from the filing. Change it to your own estimate."),
        ("g", "Long-run growth of residual income", "val_terminal_growth", PCT1,
         "Input; must be below the cost of equity."),
        ("sum_pv", "PV of forecast residual income (m)", f"SUM({pv_rng})", USD2,
         "Residual income = net income to owners less cost of equity x opening equity; "
         "end-of-year discounting."),
        ("tv", "Terminal value of residual income at the last forecast year end (m)",
         f'IF({{coe}}>{{g}}, INDEX({ri_rng}, g_forecast_years)*(1+{{g}})/({{coe}}-{{g}}), '
         f'"cost of equity must be above long-run growth")', USD2,
         "Last forecast year's residual income, growing at the long-run rate."),
        ("pv_tv", "PV of terminal value (m)",
         f'IF(ISNUMBER({{tv}}), {{tv}}*INDEX({df_rng}, g_forecast_years), "n/a")', USD2, None),
        ("value", "Residual-income equity value = book + PV of residual income (m)",
         'IF(ISNUMBER({pv_tv}), {book}+{sum_pv}+{pv_tv}, "n/a")', USD2, None),
        ("per_share", "Residual-income value per share",
         'IF(AND(ISNUMBER({value}), {shares}>0), {value}/{shares}, "n/a")', X2, None),
        ("implied_pb", "Implied P/B at that value",
         'IF(AND(ISNUMBER({value}), {book}>0), {value}/{book}, "n/a")', X2, None),
        ("upside", "Residual-income value vs typed price",
         priced_if('IF(ISNUMBER({per_share}), {per_share}/{price}-1, "n/a")'), PCT1, None),
    ]
    ref, r = write_rows(ws, rows)
    r += 1
    for line in PRICE_LOOKUP_LINES:
        ws.cell(row=r, column=1, value=line)
        r += 1
    ws.freeze_panes = "A2"
    return ref

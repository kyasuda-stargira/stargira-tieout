"""
Listed-corporate vertical. Reads a schema built on verticals/listed_corporate.yaml.

A forward model seeded from a company's own filed history. Annual periods run
down the rows: the last `history.years` actual years (inputs on the Assumptions
sheet, hist_<n>_<field>), then g_forecast_years forecast years.

Sheets: Timeline, PnL, Working_Capital, Fixed_Assets, Funding, Balance_Sheet,
Cash_Flow, DCF, Valuation, Data_Notes (plus Dashboard and Assumptions from
build_model.py).

Modelling choices (each a simplification worth knowing about):
  - Actual years show the filed figures as inputs. Their check cells are left
    blank: the history was tied out by filings_map.py, and its result per year
    is on the Timeline (Tie-out Status) and the Data_Notes sheet. The check
    columns cover every forecast year.
  - The last actual year's balance sheet is the opening position. "Other
    assets" and "other liabilities" are the filed totals less the named lines;
    they, and debt, stay flat in the forecast (debt is refinanced at maturity).
  - Revenue grows at one rate. Cost of revenue, EBIT, D&A and capex follow
    revenue as fixed ratios. D&A sits inside cost of revenue and operating
    costs, as US and Japanese filers usually report it, so EBITDA = EBIT + D&A.
  - D&A (amortisation included) runs down net PP&E; capex adds to it.
  - Receivables follow revenue; inventory and payables follow cost of revenue,
    each in days of the year.
  - Interest = opening debt x rate (no interest on cash), so nothing circular.
  - Tax = rate x pre-tax profit when positive; no loss carry-forward.
  - Dividends and buybacks are shares of positive net income, paid whatever
    the cash position.
  - Cash policy: cash never closes below a floor (fin_min_cash_pct x cash at
    the last actual year end). A revolving credit facility (revolver) draws
    whatever keeps cash at the floor and repays from cash above it. Revolver
    interest = opening revolver balance x fin_revolver_rate, so the draw never
    depends on its own interest: nothing is circular. The facility has no
    limit, so a large draw is a sign the distribution inputs are too high.
  - Non-controlling interests are not split out of the forecast profit.
  - Valuation: market cap = typed price x shares; net debt = debt less cash and
    short-term investments at the last actual year end (leases, long-term
    investments and pensions are not in it); DCF on unlevered free cash flow
    with end-of-year discounting and a Gordon-growth terminal value.

Nothing in this file names a currency or a filing taxonomy: amounts are in
millions of g_reporting_ccy, and the filing type and ID come in as text inputs.
"""

from __future__ import annotations

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from build_model import BORDER, CHECK_GOOD, PCT1, SECTION_FILL, USD2, _style_header
from core import Model

X2 = "0.00"
X4 = "0.0000"

# Where to look up a share price, per market (shared with vertical_bank.py).
PRICE_LOOKUP_LINES = [
    "Where to look up the price (the tool never fetches one; you type it in):",
    "  US shares on Nasdaq: https://www.nasdaq.com/market-activity/stocks/<ticker> "
    "(the 'Historical NOCP' tab gives the official close)",
    "  US shares on the NYSE: https://www.nyse.com/quote/XNYS:<ticker>",
    "  Japanese shares on JPX: https://quote.jpx.co.jp/jpxhp/main/index.aspx?F=stock_search "
    "(search by securities code; use 終値 or 前日終値)",
    "These quote pages are licensed for your own personal, non-commercial use only and "
    "ban automated capture. Type one price; do not copy their data in bulk.",
    "Shares: the default is the count in the filing itself (US: the cover page; Japan: "
    "issued shares less treasury shares), so no lookup is needed.",
]


def build(wb, schema, v, data_notes=()):
    n_hist = int(schema["history"]["years"])
    n_fc = int(v["g_forecast_years"])
    if not 5 <= n_fc <= 10:
        raise ValueError("g_forecast_years must be 5 to 10, got %s" % n_fc)

    m = Model(wb, schema, v)
    first = 2
    last_hist = first + n_hist - 1          # row of the last actual year
    first_fc = last_hist + 1
    m.first, m.last = first, last_hist + n_fc
    X = m.ref

    def H(r, field):
        return f"hist_{r - first + 1}_{field}"

    def is_hist(r):
        return r <= last_hist

    def prev(sheet, key, r):
        return X(sheet, key, r - 1)

    def delta(sheet, key, r):
        return f"({X(sheet, key, r)}-{X(sheet, key, r - 1)})"

    def fc_rng(sheet, key):
        c = m.cols[sheet][key]
        return f"{sheet}!${c}${first_fc}:${c}${m.last}"

    build_timeline(wb, m, first, last_hist)

    # ── declare every period sheet ───────────────────────────────────────────
    m.declare("PnL", [
        ("revenue", "Revenue (m)", USD2), ("growth", "Revenue Growth", PCT1),
        ("cogs", "Cost of Revenue (m)", USD2), ("gross", "Gross Profit (m)", USD2),
        ("opex", "Other Operating Costs incl. D&A (m)", USD2), ("ebit", "EBIT (m)", USD2),
        ("da", "D&A (m)", USD2), ("ebitda", "EBITDA (m)", USD2),
        ("interest", "Interest (m)", USD2),
        ("rev_interest", "Revolver Interest (m)", USD2), ("pretax", "Pre-tax (m)", USD2),
        ("tax", "Tax (m)", USD2), ("net_income", "Net Income (m)", USD2)])
    m.declare("Working_Capital", [
        ("receivables", "Receivables (m)", USD2), ("inventory", "Inventory (m)", USD2),
        ("payables", "Payables (m)", USD2), ("nwc", "Net Working Capital (m)", USD2)])
    m.declare("Fixed_Assets", [
        ("open", "PP&E Open (m)", USD2), ("capex", "Capex (m)", USD2),
        ("da", "D&A (m)", USD2), ("close", "PP&E Close (m)", USD2)])
    m.declare("Funding", [
        ("cash_open", "Cash Open (m)", USD2), ("ni", "Net Income (m)", USD2),
        ("da", "D&A (m)", USD2), ("d_nwc", "Increase in NWC (m)", USD2),
        ("capex", "Capex (m)", USD2), ("dividends", "Dividends (m)", USD2),
        ("buybacks", "Buybacks (m)", USD2),
        ("cash_before", "Cash before Revolver (m)", USD2), ("min_cash", "Minimum Cash (m)", USD2),
        ("rev_open", "Revolver Open (m)", USD2), ("rev_draw", "Revolver Draw (m)", USD2),
        ("rev_repay", "Revolver Repay (m)", USD2), ("rev_close", "Revolver Close (m)", USD2),
        ("cash_close", "Cash Close (m)", USD2)])
    m.declare("Balance_Sheet", [
        ("cash", "Cash (m)", USD2), ("receivables", "Receivables (m)", USD2),
        ("inventory", "Inventory (m)", USD2), ("ppe", "PP&E Net (m)", USD2),
        ("other_assets", "Other Assets (m)", USD2), ("total_assets", "Total Assets (m)", USD2),
        ("payables", "Payables (m)", USD2), ("debt", "Debt (m)", USD2),
        ("revolver", "Revolver (m)", USD2), ("other_liab", "Other Liabilities (m)", USD2), ("total_liab", "Total Liab (m)", USD2),
        ("equity", "Total Equity (m)", USD2), ("total_le", "Total L+E (m)", USD2),
        ("balance_check", "BS Check (m)", USD2)])
    m.declare("Cash_Flow", [
        ("ni", "Net Income (m)", USD2), ("da", "D&A (m)", USD2),
        ("d_wc", "Working Capital (m)", USD2), ("d_other", "Other Assets/Liab (m)", USD2),
        ("cfo", "CF from Ops (m)", USD2), ("capex", "Capex (m)", USD2),
        ("cfi", "CF from Inv (m)", USD2), ("debt_net", "Debt Net (m)", USD2),
        ("revolver_net", "Revolver Net (m)", USD2),
        ("dividends", "Dividends (m)", USD2), ("buybacks", "Buybacks (m)", USD2),
        ("cff", "CF from Fin (m)", USD2), ("net_change", "Net Change in Cash (m)", USD2),
        ("opening", "Open Cash (m)", USD2), ("closing", "Close Cash (m)", USD2),
        ("cf_check", "CF Check (m)", USD2)])
    m.declare("DCF", [
        ("nopat", "EBIT after Tax (m)", USD2), ("da", "D&A (m)", USD2),
        ("capex", "Capex (m)", USD2), ("d_nwc", "Increase in NWC (m)", USD2),
        ("ufcf", "Unlevered FCF (m)", USD2), ("period", "Discount Period (yrs)", "0"),
        ("df", "Discount Factor", X4), ("pv", "PV of FCF (m)", USD2)])

    def fill(sheet, rowfn):
        """Like Model.fill, but actual-year rows may leave columns blank."""
        cols = m.specs[sheet]
        ws = wb.create_sheet(sheet)
        heads = [("Period", 8), ("Label", 40)] + [(h, max(12, len(h) + 2)) for _, h, _ in cols]
        for i, (h, w) in enumerate(heads, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
            _style_header(ws.cell(row=1, column=i, value=h))
        for r in range(first, m.last + 1):
            ws.cell(row=r, column=1, value=f"=Timeline!A{r}").border = BORDER
            ws.cell(row=r, column=2, value=f"=Timeline!C{r}").border = BORDER
            f = rowfn(r)
            missing = [k for k, _, _ in cols if k not in f]
            if missing and not is_hist(r):
                raise KeyError(f"{sheet} row {r}: no formula for {missing}")
            for i, (k, _, fmt) in enumerate(cols, 3):
                c = ws.cell(row=r, column=i)
                c.border = BORDER
                if k in f:
                    c.value = "=" + f[k]
                    c.number_format = fmt or USD2
                    if k.endswith("_check"):
                        c.fill = CHECK_GOOD
        ws.freeze_panes = "C2"

    # ── P&L ──────────────────────────────────────────────────────────────────
    def pnl(r):
        P = lambda k: X("PnL", k, r)  # noqa: E731
        growth = {} if r == first else {"growth": f"{P('revenue')}/{prev('PnL', 'revenue', r)}-1"}
        if is_hist(r):
            return dict(growth, **{
                "revenue": H(r, "revenue"), "cogs": H(r, "cogs"),
                "gross": f"{P('revenue')}-{P('cogs')}", "opex": f"{P('gross')}-{P('ebit')}",
                "ebit": H(r, "ebit"), "da": H(r, "da"), "ebitda": f"{P('ebit')}+{P('da')}",
                "interest": H(r, "interest"), "pretax": H(r, "pretax"), "tax": H(r, "tax"),
                "net_income": H(r, "net_income")})
        return dict(growth, **{
            "revenue": f"{prev('PnL', 'revenue', r)}*(1+rev_growth)",
            "cogs": f"{P('revenue')}*(1-gross_margin)",
            "gross": f"{P('revenue')}-{P('cogs')}",
            "opex": f"{P('gross')}-{P('ebit')}",
            "ebit": f"{P('revenue')}*ebit_margin",
            "da": f"{P('revenue')}*da_pct_rev",
            "ebitda": f"{P('ebit')}+{P('da')}",
            "interest": f"{prev('Balance_Sheet', 'debt', r)}*fin_interest_rate",
            "rev_interest": f"{X('Funding', 'rev_open', r)}*fin_revolver_rate",
            "pretax": f"{P('ebit')}-{P('interest')}-{P('rev_interest')}",
            "tax": f"MAX(0, {P('pretax')})*g_tax_rate",
            "net_income": f"{P('pretax')}-{P('tax')}"})
    fill("PnL", pnl)

    # ── Working capital ──────────────────────────────────────────────────────
    def working_capital(r):
        W = lambda k: X("Working_Capital", k, r)  # noqa: E731
        nwc = {"nwc": f"{W('receivables')}+{W('inventory')}-{W('payables')}"}
        if is_hist(r):
            return dict(nwc, receivables=H(r, "receivables"), inventory=H(r, "inventory"),
                        payables=H(r, "payables"))
        rev, cogs = X("PnL", "revenue", r), X("PnL", "cogs", r)
        return dict(nwc, receivables=f"{rev}*wc_receivable_days/g_days_in_year",
                    inventory=f"{cogs}*wc_inventory_days/g_days_in_year",
                    payables=f"{cogs}*wc_payable_days/g_days_in_year")
    fill("Working_Capital", working_capital)

    # ── Fixed assets ─────────────────────────────────────────────────────────
    def fixed_assets(r):
        F = lambda k: X("Fixed_Assets", k, r)  # noqa: E731
        opening = {} if r == first else {"open": prev("Fixed_Assets", "close", r)}
        if is_hist(r):
            return dict(opening, capex=H(r, "capex"), da=H(r, "da"), close=H(r, "ppe"))
        return dict(opening, capex=f"{X('PnL', 'revenue', r)}*capex_pct_rev",
                    da=X("PnL", "da", r), close=f"{F('open')}+{F('capex')}-{F('da')}")
    fill("Fixed_Assets", fixed_assets)

    # ── Funding: cash after operations and distributions, then the revolver ──
    # The draw and repayment depend on cash before the revolver, which includes
    # revolver interest on the OPENING balance only, so nothing is circular.
    def funding(r):
        if is_hist(r):
            return {}
        F = lambda k: X("Funding", k, r)  # noqa: E731
        ni = X("PnL", "net_income", r)
        return {
            "cash_open": prev("Balance_Sheet", "cash", r), "ni": ni, "da": X("PnL", "da", r),
            "d_nwc": delta("Working_Capital", "nwc", r), "capex": X("Fixed_Assets", "capex", r),
            "dividends": f"fin_dividend_payout*MAX(0, {ni})",
            "buybacks": f"fin_buyback_pct_ni*MAX(0, {ni})",
            "cash_before": (f"{F('cash_open')}+{F('ni')}+{F('da')}-{F('d_nwc')}-{F('capex')}"
                            f"-{F('dividends')}-{F('buybacks')}"),
            "min_cash": f"{H(last_hist, 'cash')}*fin_min_cash_pct",
            "rev_open": prev("Balance_Sheet", "revolver", r),
            "rev_draw": f"MAX(0, {F('min_cash')}-{F('cash_before')})",
            "rev_repay": f"MIN({F('rev_open')}, MAX(0, {F('cash_before')}-{F('min_cash')}))",
            "rev_close": f"{F('rev_open')}+{F('rev_draw')}-{F('rev_repay')}",
            "cash_close": f"{F('cash_before')}+{F('rev_draw')}-{F('rev_repay')}"}
    fill("Funding", funding)

    # ── Balance sheet ────────────────────────────────────────────────────────
    def balance_sheet(r):
        B = lambda k: X("Balance_Sheet", k, r)  # noqa: E731
        totals = {
            "total_assets": "+".join(B(k) for k in ("cash", "receivables", "inventory", "ppe",
                                                    "other_assets")),
            "total_liab": f"{B('payables')}+{B('debt')}+{B('revolver')}+{B('other_liab')}",
            "total_le": f"{B('total_liab')}+{B('equity')}"}
        if is_hist(r):
            return dict(totals, cash=H(r, "cash"), receivables=H(r, "receivables"),
                        inventory=H(r, "inventory"), ppe=H(r, "ppe"),
                        other_assets=H(r, "other_assets"), payables=H(r, "payables"),
                        debt=H(r, "debt"), revolver="0", other_liab=H(r, "other_liabilities"),
                        equity=H(r, "equity"))
        return dict(totals,
                    cash=X("Funding", "cash_close", r),
                    receivables=X("Working_Capital", "receivables", r),
                    inventory=X("Working_Capital", "inventory", r),
                    ppe=X("Fixed_Assets", "close", r),
                    other_assets=prev("Balance_Sheet", "other_assets", r),
                    payables=X("Working_Capital", "payables", r),
                    debt=prev("Balance_Sheet", "debt", r),
                    revolver=X("Funding", "rev_close", r),
                    other_liab=prev("Balance_Sheet", "other_liab", r),
                    equity=(f"{prev('Balance_Sheet', 'equity', r)}+{X('PnL', 'net_income', r)}"
                            f"-{X('Funding', 'dividends', r)}-{X('Funding', 'buybacks', r)}"),
                    balance_check=f"{B('total_assets')}-{B('total_le')}")
    fill("Balance_Sheet", balance_sheet)

    # ── Cash flow: rebuilt from balance-sheet movements ──────────────────────
    def cash_flow(r):
        C = lambda k: X("Cash_Flow", k, r)  # noqa: E731
        if is_hist(r):
            return {"ni": H(r, "net_income"), "da": H(r, "da"), "cfo": H(r, "cfo"),
                    "capex": f"-{H(r, 'capex')}", "cfi": H(r, "cfi"),
                    "dividends": f"-{H(r, 'dividends')}", "buybacks": f"-{H(r, 'buybacks')}",
                    "cff": H(r, "cff")}
        D = lambda k: delta("Balance_Sheet", k, r)  # noqa: E731
        da = X("PnL", "da", r)
        return {
            "ni": X("PnL", "net_income", r), "da": da,
            "d_wc": f"-({D('receivables')}+{D('inventory')}-{D('payables')})",
            "d_other": f"{D('other_liab')}-{D('other_assets')}",
            "cfo": f"{C('ni')}+{C('da')}+{C('d_wc')}+{C('d_other')}",
            "capex": f"-({D('ppe')}+{da})",
            "cfi": C("capex"),
            "debt_net": D("debt"),
            "revolver_net": D("revolver"),
            "dividends": f"-{X('Funding', 'dividends', r)}",
            "buybacks": f"-{X('Funding', 'buybacks', r)}",
            "cff": f"{C('debt_net')}+{C('revolver_net')}+{C('dividends')}+{C('buybacks')}",
            "net_change": f"{C('cfo')}+{C('cfi')}+{C('cff')}",
            "opening": prev("Balance_Sheet", "cash", r),
            "closing": f"{C('opening')}+{C('net_change')}",
            "cf_check": f"{C('closing')}-{X('Balance_Sheet', 'cash', r)}"}
    fill("Cash_Flow", cash_flow)

    # ── DCF (forecast years only) ────────────────────────────────────────────
    def dcf(r):
        if is_hist(r):
            return {}
        Dc = lambda k: X("DCF", k, r)  # noqa: E731
        return {
            "nopat": f"{X('PnL', 'ebit', r)}*(1-g_tax_rate)", "da": X("PnL", "da", r),
            "capex": X("Fixed_Assets", "capex", r), "d_nwc": X("Funding", "d_nwc", r),
            "ufcf": f"{Dc('nopat')}+{Dc('da')}-{Dc('capex')}-{Dc('d_nwc')}",
            "period": X("Timeline", "pid", r),
            "df": f"1/(1+val_wacc)^{Dc('period')}",
            "pv": f"{Dc('ufcf')}*{Dc('df')}"}
    fill("DCF", dcf)

    build_valuation(wb, m, last_hist, fc_rng)
    build_data_notes(wb, data_notes)


def build_timeline(wb, m, first, last_hist):
    """Timeline for an annual model seeded from filed history: one row per actual
    year (labelled with its filing ID and tie-out status), then forecast years.
    Shared with vertical_bank.py."""
    def H(r, field):
        return f"hist_{r - first + 1}_{field}"

    def is_hist(r):
        return r <= last_hist

    tl_cols = [("pid", "Period ID"), ("start", "Period Start"), ("label", "Period Label"),
               ("year", "Year"), ("type", "Type"), ("months", "Months"),
               ("end", "Period End"), ("status", "Tie-out Status")]
    m.cols["Timeline"] = {k: get_column_letter(i) for i, (k, _) in enumerate(tl_cols, 1)}
    ws = wb.create_sheet("Timeline")
    for i, (_, h) in enumerate(tl_cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = 16
        _style_header(ws.cell(row=1, column=i, value=h))
    ws.column_dimensions["C"].width = 52
    for r in range(first, m.last + 1):
        if is_hist(r):
            row = {
                "pid": r - last_hist,
                "start": f"=EDATE({H(r, 'end')}, -12)+1",
                "label": (f'="FY" & {H(r, "fy")} & " Actual (" & {H(r, "form")} & ", filing ID " '
                          f'& {H(r, "accn")} & ")"'),
                "year": f"={H(r, 'fy')}",
                "type": '="actual"',
                "end": f"={H(r, 'end')}",
                "status": f"={H(r, 'status')}",
            }
        else:
            row = {
                "pid": r - last_hist,
                "start": f"=G{r - 1}+1",
                "label": f'="FY" & D{r} & " Forecast"',
                "year": f"=D{r - 1}+1",
                "type": '="forecast"',
                "end": f"=EDATE(G{r - 1}, 12)",
                "status": '="forecast"',
            }
        row["months"] = 12
        for i, (k, _) in enumerate(tl_cols, 1):
            c = ws.cell(row=r, column=i, value=row[k])
            c.border = BORDER
            if k in ("start", "end"):
                c.number_format = "yyyy-mm-dd"
    ws.freeze_panes = "A2"


def build_valuation(wb, m, last_hist, fc_rng):
    """Price x shares, EV, multiples and an implied DCF value per share."""
    X = m.ref
    ws = wb.create_sheet("Valuation")
    for col, (h, w) in enumerate([("Item", 58), ("Value", 18), ("Note", 90)], 1):
        ws.column_dimensions[get_column_letter(col)].width = w
        _style_header(ws.cell(row=1, column=col, value=h))
    bs = lambda k: X("Balance_Sheet", k, last_hist)  # noqa: E731
    pl = lambda k: X("PnL", k, last_hist)  # noqa: E731
    priced = "val_share_price>0"
    ref = {}
    rows = [
        ("section", "Market value (last actual year end balance sheet)", None, None, None),
        ("price", '=IF(val_share_price>0, "Share price (source: " & val_price_source & ", " & '
                  'TEXT(val_price_date, "yyyy-mm-dd") & ")", "Share price (not entered yet)")',
         "val_share_price", X2,
         "Typed by the user. 0 means not entered yet. Quote it in the reporting currency."),
        ("ccy", "Reporting currency", "g_reporting_ccy", None, None),
        ("shares", '="Shares (millions; source: " & val_shares_source & ")"', "val_shares_diluted", "#,##0.000",
         "Default: the count in the latest annual filing (US: cover-page shares outstanding; "
         "Japan: issued shares less treasury shares). Not diluted. Replace with a diluted "
         "count if you have one."),
        ("mcap", "Market capitalisation (m)",
         f'IF({priced}, {{price}}*{{shares}}, "enter a price")', USD2, None),
        ("debt", "Debt (m)", bs("debt"), USD2,
         "Short-term borrowings plus current and long-term debt as tagged. Leases are not in it."),
        ("cash", "Cash and short-term investments (m)", bs("cash"), USD2,
         "Long-term investments are not in it."),
        ("net_debt", "Net debt (m)", "{debt}-{cash}", USD2, None),
        ("ev", "Enterprise value = market cap + net debt (m)",
         f'IF({priced}, {{mcap}}+{{net_debt}}, "enter a price")', USD2, None),
        ("section", "Multiples on the last actual year", None, None, None),
        ("rev", "Revenue, last actual year (m)", pl("revenue"), USD2, None),
        ("ebitda", "EBITDA, last actual year (m)", pl("ebitda"), USD2, None),
        ("ni", "Net income, last actual year (m)", pl("net_income"), USD2, None),
        ("ev_sales", "EV / Sales",
         f'IF({priced}, IF({{rev}}>0, {{ev}}/{{rev}}, "n/a"), "enter a price")', X2, None),
        ("ev_ebitda", "EV / EBITDA",
         f'IF({priced}, IF({{ebitda}}>0, {{ev}}/{{ebitda}}, "n/a"), "enter a price")', X2, None),
        ("pe", "P/E",
         f'IF({priced}, IF({{ni}}>0, {{mcap}}/{{ni}}, "n/a"), "enter a price")', X2, None),
        ("section", "Implied value from the DCF", None, None, None),
        ("wacc", "Discount rate (WACC)", "val_wacc", PCT1, "Input, not derived from the filing."),
        ("tg", "Terminal growth", "val_terminal_growth", PCT1, "Input; must be below WACC."),
        ("sum_pv", "PV of forecast unlevered FCF (m)", f"SUM({fc_rng('DCF', 'pv')})", USD2,
         "End-of-year discounting."),
        ("tv", "Terminal value at the last forecast year end (m)",
         f'IF({{wacc}}>{{tg}}, INDEX({fc_rng("DCF", "ufcf")}, g_forecast_years)*(1+{{tg}})'
         f'/({{wacc}}-{{tg}}), "WACC must be above terminal growth")', USD2,
         "Gordon growth on the last forecast year's unlevered FCF."),
        ("pv_tv", "PV of terminal value (m)",
         f'IF(ISNUMBER({{tv}}), {{tv}}*INDEX({fc_rng("DCF", "df")}, g_forecast_years), "n/a")',
         USD2, None),
        ("ev_dcf", "DCF enterprise value (m)",
         'IF(ISNUMBER({pv_tv}), {sum_pv}+{pv_tv}, "n/a")', USD2, None),
        ("eq_dcf", "DCF equity value = DCF EV less net debt (m)",
         'IF(ISNUMBER({ev_dcf}), {ev_dcf}-{net_debt}, "n/a")', USD2, None),
        ("per_share", "Implied value per share",
         'IF(AND(ISNUMBER({eq_dcf}), {shares}>0), {eq_dcf}/{shares}, "n/a")', X2, None),
        ("upside", "Implied value vs typed price",
         f'IF(AND({priced}, ISNUMBER({{per_share}})), {{per_share}}/{{price}}-1, "enter a price")',
         PCT1, None),
    ]
    r = 2
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
    r += 1
    for line in PRICE_LOOKUP_LINES:
        ws.cell(row=r, column=1, value=line)
        r += 1
    ws.freeze_panes = "A2"
    return ref


def build_data_notes(wb, data_notes):
    """Source credit, per-year tie-out status, flags and warnings, never hidden."""
    ws = wb.create_sheet("Data_Notes")
    ws.column_dimensions["A"].width = 160
    _style_header(ws.cell(row=1, column=1, value="Data notes"))
    ws.cell(row=2, column=1, value=('=g_entity_name & " (" & g_ticker & "). Amounts in millions of " '
                                    '& g_reporting_ccy & ". Source of the actuals: " & g_data_source'))
    r = 3
    for line in data_notes:
        text = str(line)
        if text.startswith("="):       # keep notes as text, never formulas
            text = " " + text
        c = ws.cell(row=r, column=1, value=text)
        if text.startswith("## "):
            c.value = text[3:]
            c.font = Font(bold=True)
            c.fill = SECTION_FILL
        r += 1

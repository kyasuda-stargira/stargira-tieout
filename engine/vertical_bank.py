"""
Listed-bank vertical. Reads a schema built on verticals/listed_bank.yaml.

A forward model of a bank (or a securities firm filed on the bank chart), seeded
from its own filed history. Annual periods run down the rows: the last
`history.years` actual years (inputs on Assumptions, hist_<n>_<field>), then
g_forecast_years forecast years. The Timeline is the listed model's.

Sheets: Timeline, PnL, Equity, Balance_Sheet, Liquidity, Residual_Income,
Valuation, Data_Notes (plus Assumptions from build_model.py).

Modelling choices (each a simplification worth knowing about):
  - Actual years show the filed figures as inputs. Their check cells are left
    blank: the history was tied out by filings_map.py (status per year on the
    Timeline and Data_Notes). The check columns cover every forecast year.
  - Net loans = loans as filed less the allowance where the filing prints loans
    before it (Japan: 貸出金 less the whole 貸倒引当金 line). US loans are filed
    net, so nothing is deducted. One formula set serves both.
  - Liquid assets = cash + reverse repos + trading assets + investment
    securities. With net loans they are the interest-earning assets. Other
    assets = total assets less both, as filed.
  - Interest-bearing liabilities = deposits (incl. negotiable CDs) + borrowings
    (repos, short-term borrowings, long-term debt, as tagged). Other liabilities
    = total liabilities (incl. mezzanine equity) less both.
  - Forecast: net loans and deposits grow at their rates; borrowings, other
    assets, other liabilities and non-controlling interests stay flat. Equity
    to owners rolls forward with net income to owners less dividends and
    buybacks. Liquid assets are the balancing line (total liabilities + equity
    less loans and other assets), so the balance sheet balances by
    construction; the Liquidity sheet rebuilds them from the year's flows as
    an independent check, and a flag column says if they turn negative.
  - Interest income = opening earning assets x yield; interest expense =
    opening interest-bearing liabilities x cost of funds; noninterest income =
    opening total assets x a ratio; provision = opening net loans x credit
    cost. All on OPENING balances, so nothing is circular.
  - The first actual year's opening balances are filed inputs of their own
    (hist_open_<field>, the year end before it, from the history). They feed
    only that year's ratios. Where the history holds no earlier balance sheet
    they are empty and those ratios are left blank.
  - Opex = cost-to-income ratio x net revenue. Tax = rate x pre-tax profit when
    positive. Extraordinary and other items below the operating line are 0 in
    the forecast.
  - The NCI share of net income is a fixed proportion; it is treated as paid
    out to the minority holders, so NCI equity stays flat.
  - Capital: equity to owners / total assets against a typed-in target, shown
    as a check (flag), not used to balance anything.
  - Valuation: residual income (fin_valuation.py), not a free-cash-flow DCF.
"""

from __future__ import annotations

from openpyxl.utils import get_column_letter

from build_model import BORDER, CHECK_GOOD, PCT1, USD2, _style_header
from core import Model
from fin_valuation import RI_COLUMNS, build_ri_valuation, ri_row
from vertical_listed import build_data_notes, build_timeline

LIQUID_PARTS = ("cash", "reverse_repos", "trading_assets", "securities")


def fill_rows(wb, m, first, last_hist, sheet, rowfn, hist_only=()):
    """Like Model.fill, but actual-year rows may leave columns blank, and the
    hist_only columns are blank in forecast years. Shared with vertical_insurer.py."""
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
        missing = [k for k, _, _ in cols if k not in f and k not in hist_only]
        if missing and r > last_hist:
            raise KeyError(f"{sheet} row {r}: no formula for {missing}")
        for i, (k, _, fmt) in enumerate(cols, 3):
            c = ws.cell(row=r, column=i)
            c.border = BORDER
            if k in f:
                c.value = "=" + f[k]
                if fmt:
                    c.number_format = fmt
                if k.endswith("_check"):
                    c.fill = CHECK_GOOD
    ws.freeze_panes = "C2"


def build(wb, schema, v, data_notes=()):
    n_hist = int(schema["history"]["years"])
    n_fc = int(v["g_forecast_years"])
    if not 5 <= n_fc <= 10:
        raise ValueError("g_forecast_years must be 5 to 10, got %s" % n_fc)

    m = Model(wb, schema, v)
    first = 2
    last_hist = first + n_hist - 1
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

    def per(num, den):
        """num / den, or 0 when den is not positive (no #DIV/0!)."""
        return f"IF({den}>0, {num}/{den}, 0)"

    def opening(field):
        """The first actual year's opening balance input, or None when not filed."""
        name = f"hist_open_{field}"
        return name if v.get(name) is not None else None

    build_timeline(wb, m, first, last_hist)

    m.declare("PnL", [
        ("interest_income", "Interest Income (m)", USD2),
        ("interest_expense", "Interest Expense (m)", USD2),
        ("nii", "Net Interest Income (m)", USD2),
        ("nonint", "Noninterest Income (m)", USD2),
        ("revenue", "Net Revenue (m)", USD2),
        ("opex", "Operating Expenses (m)", USD2),
        ("provision", "Provision for Credit Losses (m)", USD2),
        ("other_items", "Other Items in Pre-tax (m)", USD2),
        ("pretax", "Pre-tax (m)", USD2), ("tax", "Tax (m)", USD2),
        ("ni_total", "Net Income incl. NCI (m)", USD2),
        ("nci_share", "NCI Share of Net Income (m)", USD2),
        ("ni_owners", "Net Income to Owners (m)", USD2),
        ("yield", "Yield on Opening Earning Assets", PCT1),
        ("cost_funds", "Cost of Opening Interest-bearing Liab", PCT1),
        ("nim", "NIM: Yield less Cost of Funds", PCT1),
        ("nii_margin", "NII / Opening Earning Assets", PCT1),
        ("cost_income", "Cost-to-Income", PCT1),
        ("credit_cost", "Credit Cost (on Opening Net Loans)", PCT1),
        ("roe", "ROE (on Opening Equity to Owners)", PCT1)])
    m.declare("Equity", [
        ("open", "Equity to Owners Open (m)", USD2),
        ("ni", "Net Income to Owners (m)", USD2),
        ("dividends", "Dividends (m)", USD2), ("buybacks", "Buybacks (m)", USD2),
        ("other", "Other Movements incl. OCI (m)", USD2),
        ("close", "Equity to Owners Close (m)", USD2),
        ("nci", "NCI and Other Equity (m)", USD2)])
    m.declare("Balance_Sheet", [
        ("cash", "Actual: Cash and Due from Banks (m)", USD2),
        ("reverse_repos", "Actual: Reverse Repos (m)", USD2),
        ("trading_assets", "Actual: Trading Assets (m)", USD2),
        ("securities", "Actual: Investment Securities (m)", USD2),
        ("loans_filed", "Actual: Loans as Filed (m)", USD2),
        ("allowance", "Actual: Allowance Deducted (m)", USD2),
        ("loans", "Net Loans (m)", USD2),
        ("liquid", "Liquid Assets (m)", USD2),
        ("other_assets", "Other Assets (m)", USD2),
        ("total_assets", "Total Assets (m)", USD2),
        ("deposits", "Deposits incl. CDs (m)", USD2),
        ("borrowings", "Borrowings (m)", USD2),
        ("other_liab", "Other Liabilities (m)", USD2),
        ("total_liab", "Total Liab (m)", USD2),
        ("equity_owners", "Equity to Owners (m)", USD2),
        ("nci", "NCI and Other Equity (m)", USD2),
        ("total_equity", "Total Equity (m)", USD2),
        ("total_le", "Total L+E (m)", USD2),
        ("balance_check", "BS Check (m)", USD2),
        ("earning", "Interest-earning Assets (m)", USD2),
        ("ibl", "Interest-bearing Liabilities (m)", USD2),
        ("eq_assets", "Equity to Owners / Assets", PCT1),
        ("target", "Target Equity / Assets", PCT1),
        ("capital_flag", "Capital vs Target", None),
        ("liquid_flag", "Liquid Assets Flag", None)])
    m.declare("Liquidity", [
        ("open", "Liquid Assets Open (m)", USD2),
        ("ni", "Net Income to Owners (m)", USD2),
        ("dividends", "Dividends (m)", USD2), ("buybacks", "Buybacks (m)", USD2),
        ("equity_other", "Other Equity Movements (m)", USD2),
        ("d_nci", "Change in NCI (m)", USD2),
        ("d_deposits", "Change in Deposits (m)", USD2),
        ("d_borrowings", "Change in Borrowings (m)", USD2),
        ("d_other_liab", "Change in Other Liabilities (m)", USD2),
        ("d_loans", "Change in Net Loans (m)", USD2),
        ("d_other_assets", "Change in Other Assets (m)", USD2),
        ("close", "Liquid Assets Close, from Flows (m)", USD2),
        ("bs", "Liquid Assets on Balance Sheet (m)", USD2),
        ("cf_check", "CF Check (m)", USD2)])
    m.declare("Residual_Income", RI_COLUMNS)

    def fill(sheet, rowfn, hist_only=()):
        fill_rows(wb, m, first, last_hist, sheet, rowfn, hist_only)

    B = lambda k, r: X("Balance_Sheet", k, r)  # noqa: E731

    # ── P&L ──────────────────────────────────────────────────────────────────
    def pnl(r):
        P = lambda k: X("PnL", k, r)  # noqa: E731
        f = {"nii": f"{P('interest_income')}-{P('interest_expense')}",
             "revenue": f"{P('nii')}+{P('nonint')}",
             "nci_share": f"{P('ni_total')}-{P('ni_owners')}"} if is_hist(r) else {}
        if r > first:
            f.update({
                "yield": per(P("interest_income"), B("earning", r - 1)),
                "cost_funds": per(P("interest_expense"), B("ibl", r - 1)),
                "nim": f"{P('yield')}-{P('cost_funds')}",
                "nii_margin": per(P("nii"), B("earning", r - 1)),
                "cost_income": per(P("opex"), P("revenue")),
                "credit_cost": per(P("provision"), B("loans", r - 1)),
                "roe": per(P("ni_owners"), X("Equity", "close", r - 1))})
        else:
            # First actual year: the opening balances are the hist_open_ inputs. A ratio
            # whose opening balance is not filed is left blank (Data_Notes says why).
            f["cost_income"] = per(P("opex"), P("revenue"))
            for key, num, base in [("yield", "interest_income", "earning"),
                                   ("cost_funds", "interest_expense", "ibl"),
                                   ("nii_margin", "nii", "earning"),
                                   ("credit_cost", "provision", "loans"),
                                   ("roe", "ni_owners", "equity_parent")]:
                if opening(base):
                    f[key] = per(P(num), opening(base))
            if "yield" in f and "cost_funds" in f:
                f["nim"] = f"{P('yield')}-{P('cost_funds')}"
        if is_hist(r):
            f.update({
                "interest_income": H(r, "interest_income"),
                "interest_expense": H(r, "interest_expense"),
                "nonint": H(r, "noninterest_income"), "opex": H(r, "opex"),
                "provision": H(r, "provision"),
                "other_items": f"{P('pretax')}-({P('revenue')}-{P('provision')}-{P('opex')})",
                "pretax": H(r, "pretax"), "tax": H(r, "tax"),
                "ni_total": H(r, "net_income_total"), "ni_owners": H(r, "net_income")})
            return f
        f.update({
            "interest_income": f"{B('earning', r - 1)}*bk_asset_yield",
            "interest_expense": f"{B('ibl', r - 1)}*bk_funding_cost",
            "nii": f"{P('interest_income')}-{P('interest_expense')}",
            "nonint": f"{B('total_assets', r - 1)}*bk_fee_income_pct_assets",
            "revenue": f"{P('nii')}+{P('nonint')}",
            "opex": f"{P('revenue')}*bk_cost_income",
            "provision": f"{B('loans', r - 1)}*bk_credit_cost",
            "other_items": "0",
            "pretax": f"{P('revenue')}-{P('provision')}-{P('opex')}+{P('other_items')}",
            "tax": f"MAX(0, {P('pretax')})*g_tax_rate",
            "ni_total": f"{P('pretax')}-{P('tax')}",
            "nci_share": f"{P('ni_total')}*bk_nci_share",
            "ni_owners": f"{P('ni_total')}-{P('nci_share')}"})
        return f
    fill("PnL", pnl)

    # ── Equity roll-forward ──────────────────────────────────────────────────
    def equity(r):
        E = lambda k: X("Equity", k, r)  # noqa: E731
        f = {"ni": X("PnL", "ni_owners", r)}
        if r > first:
            f["open"] = prev("Equity", "close", r)
        if is_hist(r):
            f.update({"dividends": H(r, "dividends"), "buybacks": H(r, "buybacks"),
                      "close": H(r, "equity_parent"),
                      "nci": f"{H(r, 'total_equity')}-{H(r, 'equity_parent')}"})
            if r > first:
                f["other"] = f"{E('close')}-{E('open')}-{E('ni')}+{E('dividends')}+{E('buybacks')}"
            return f
        f.update({"dividends": f"fin_dividend_payout*MAX(0, {E('ni')})",
                  "buybacks": f"fin_buyback_pct_ni*MAX(0, {E('ni')})",
                  "other": "0",
                  "close": f"{E('open')}+{E('ni')}-{E('dividends')}-{E('buybacks')}+{E('other')}",
                  "nci": prev("Equity", "nci", r)})
        return f
    fill("Equity", equity)

    # ── Balance sheet ────────────────────────────────────────────────────────
    def balance_sheet(r):
        b = lambda k: B(k, r)  # noqa: E731
        f = {"equity_owners": X("Equity", "close", r), "nci": X("Equity", "nci", r),
             "total_equity": f"{b('equity_owners')}+{b('nci')}",
             "total_le": f"{b('total_liab')}+{b('total_equity')}",
             "earning": f"{b('loans')}+{b('liquid')}",
             "ibl": f"{b('deposits')}+{b('borrowings')}",
             "eq_assets": per(b("equity_owners"), b("total_assets")),
             "target": "bk_target_equity_assets"}
        if is_hist(r):
            f.update({k: H(r, k) for k in LIQUID_PARTS})
            f.update({
                "loans_filed": H(r, "loans"), "allowance": H(r, "loan_allowance_netted"),
                "loans": f"{b('loans_filed')}-{b('allowance')}",
                "liquid": "+".join(b(k) for k in LIQUID_PARTS),
                "total_assets": H(r, "total_assets"),
                "other_assets": f"{b('total_assets')}-{b('loans')}-{b('liquid')}",
                "deposits": H(r, "deposits"), "borrowings": H(r, "borrowings"),
                "total_liab": H(r, "total_liabilities"),
                "other_liab": f"{b('total_liab')}-{b('deposits')}-{b('borrowings')}"})
            return f
        f.update({
            "loans": f"{prev('Balance_Sheet', 'loans', r)}*(1+bk_loan_growth)",
            "other_assets": prev("Balance_Sheet", "other_assets", r),
            "liquid": f"{b('total_le')}-{b('loans')}-{b('other_assets')}",
            "total_assets": f"{b('loans')}+{b('liquid')}+{b('other_assets')}",
            "deposits": f"{prev('Balance_Sheet', 'deposits', r)}*(1+bk_deposit_growth)",
            "borrowings": prev("Balance_Sheet", "borrowings", r),
            "other_liab": prev("Balance_Sheet", "other_liab", r),
            "total_liab": f"{b('deposits')}+{b('borrowings')}+{b('other_liab')}",
            "balance_check": f"{b('total_assets')}-{b('total_le')}",
            "capital_flag": (f'IF({b("eq_assets")}<{b("target")}, "below target", '
                             f'"at or above target")'),
            "liquid_flag": (f'IF({b("liquid")}<0, "NEGATIVE: loans and other assets exceed '
                            f'funding; change the growth inputs", "ok")')})
        return f
    fill("Balance_Sheet", balance_sheet,
         hist_only=LIQUID_PARTS + ("loans_filed", "allowance"))

    # ── Liquidity: liquid assets rebuilt from the year's flows (the check) ───
    def liquidity(r):
        if is_hist(r):
            return {}
        L = lambda k: X("Liquidity", k, r)  # noqa: E731
        return {
            "open": prev("Balance_Sheet", "liquid", r),
            "ni": X("Equity", "ni", r), "dividends": X("Equity", "dividends", r),
            "buybacks": X("Equity", "buybacks", r), "equity_other": X("Equity", "other", r),
            "d_nci": delta("Balance_Sheet", "nci", r),
            "d_deposits": delta("Balance_Sheet", "deposits", r),
            "d_borrowings": delta("Balance_Sheet", "borrowings", r),
            "d_other_liab": delta("Balance_Sheet", "other_liab", r),
            "d_loans": delta("Balance_Sheet", "loans", r),
            "d_other_assets": delta("Balance_Sheet", "other_assets", r),
            "close": (f"{L('open')}+{L('ni')}-{L('dividends')}-{L('buybacks')}+{L('equity_other')}"
                      f"+{L('d_nci')}+{L('d_deposits')}+{L('d_borrowings')}+{L('d_other_liab')}"
                      f"-{L('d_loans')}-{L('d_other_assets')}"),
            "bs": B("liquid", r),
            "cf_check": f"{L('close')}-{L('bs')}"}
    fill("Liquidity", liquidity)

    # ── Residual income (forecast years only) ────────────────────────────────
    def residual_income(r):
        if is_hist(r):
            return {}
        return ri_row(lambda k: X("Residual_Income", k, r), prev("Equity", "close", r),
                      X("PnL", "ni_owners", r), X("Timeline", "pid", r))
    fill("Residual_Income", residual_income)

    build_ri_valuation(
        wb, book=B("equity_owners", last_hist), open_book=B("equity_owners", last_hist - 1),
        ni_last=X("PnL", "ni_owners", last_hist),
        ri_rng=fc_rng("Residual_Income", "ri"), pv_rng=fc_rng("Residual_Income", "pv"),
        df_rng=fc_rng("Residual_Income", "df"),
        book_note="Shareholders' equity of the parent as filed (US: incl. preferred stock; "
                  "Japan: 株主資本 + その他の包括利益累計額). Non-controlling interests and share "
                  "options are not in it.")
    build_data_notes(wb, data_notes)

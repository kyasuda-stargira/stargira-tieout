"""
Listed-insurer vertical. Reads a schema built on verticals/listed_insurer.yaml.

A forward model of a non-life or life insurer, seeded from its own filed
history. Annual periods run down the rows: the last `history.years` actual years
(inputs on Assumptions, hist_<n>_<field>), then g_forecast_years forecast years.
The Timeline is the listed model's.

Sheets: Timeline, PnL, Equity, Balance_Sheet, Investment_Flows,
Residual_Income, Valuation, Data_Notes (plus Assumptions from build_model.py).

Modelling choices (each a simplification worth knowing about):
  - Actual years show the filed figures as inputs. Their check cells are left
    blank: the history was tied out by filings_map.py. The check columns cover
    every forecast year.
  - One template for non-life and life. A row that does not apply (reserve
    change where the filing has no such line, interest credited and
    policyholder account balances at a non-life insurer) reads 0.
  - P&L: net investment income = investment income less investment expenses
    (Japan files them gross; US income is already net). Investment and derivative
    gains are the tagged gain lines (US; Japan has none apart). Other income is
    total revenue less premiums, fees, investment income, gains and reserve
    reversals. "Other items in pre-tax" is the filed pre-tax profit
    less the operating result (Japan: extraordinary items).
  - Forecast P&L: premiums grow at one rate and fees with them. Claims, the
    reserve change, expenses and policyholder dividends are ratios to
    premiums. Net investment income = OPENING investments x yield. Interest
    credited = OPENING policyholder account balances x crediting rate.
    Investment and derivative gains = premiums x a ratio that is 0 by default
    (volatile, not projected). Other income = premiums x its 3-year ratio
    (projected, because its matching costs stay inside expenses). Other items
    are 0. Nothing is circular.
  - Reserves: ins_reserve_rollforward = 1 rolls them forward with the reserve
    change (opening + change), for filings that show the change as its own
    line (Japan); 0 holds reserves / premiums at a typed ratio (US, where the
    change sits inside claims). Policyholder account balances keep their
    share of the reserves.
  - Cash, debt, other assets, other liabilities and non-controlling interests
    stay flat. Equity to owners rolls forward with net income to owners less
    dividends and buybacks. Investments are the balancing line (total
    liabilities + equity less cash and other assets), so the balance sheet
    balances by construction; Investment_Flows rebuilds them from the year's
    flows as an independent check, and a flag says if they turn negative.
  - Tax = rate x pre-tax profit when positive. The NCI share of net income is a
    fixed proportion, treated as paid out, so NCI equity stays flat.
  - Capital: equity to owners / total assets against a typed-in target, as a
    flag. Solvency ratios (ESR, SMR, RBC) are typed in elsewhere, not filed.
  - Valuation: residual income (fin_valuation.py), not a free-cash-flow DCF.
  - The first actual year's opening balances are filed inputs of their own
    (hist_open_<field>, the year end before it, from the history; IFRS 17:
    the report's opening balance sheet). They feed only that year's ratios.
    Where the history holds no earlier balance sheet they are empty and those
    ratios are left blank.
  - IFRS 17 (schema meta basis: ifrs17, verticals/listed_insurer_ifrs.yaml): the
    same formulas with the IFRS 17 labels (insurance revenue, insurance service
    expenses, net financial result, insurance contract liabilities incl. the
    CSM), plus one cost row, the net expense from reinsurance contracts held
    (insurance revenue x ins_reinsurance_pct in the forecast). The investment
    expense row holds the net insurance finance expenses, so net investment
    income is the filed net financial result.
"""

from __future__ import annotations

from build_model import PCT1, USD2
from core import Model
from fin_valuation import RI_COLUMNS, build_ri_valuation, ri_row
from vertical_bank import fill_rows
from vertical_listed import build_data_notes, build_timeline

# Reserve components shown in actual years only; the total moves in the forecast.
RESERVE_PARTS = ("claims_reserve", "unearned_premiums", "future_policy_benefits",
                 "underwriting_reserve", "dividend_reserve")


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

    def fill(sheet, rowfn, hist_only=()):
        fill_rows(wb, m, first, last_hist, sheet, rowfn, hist_only)

    def opening(field):
        """The first actual year's opening balance input, or None when not filed."""
        name = f"hist_open_{field}"
        return name if v.get(name) is not None else None

    build_timeline(wb, m, first, last_hist)
    ifrs = schema["meta"].get("basis") == "ifrs17"
    # history_to_answers.py writes this Data_Notes line when the filing gives premiums
    # WRITTEN (J-GAAP non-life); the premium rows are then labelled so.
    prem = ("Insurance Revenue" if ifrs else
            "Premiums Written" if any(str(x).startswith("Premium basis: WRITTEN") for x in data_notes)
            else "Premiums")
    # IFRS 17 labels for the rows whose meaning changes (same formulas).
    L = (lambda base, ifrs_label: ifrs_label if ifrs else base)  # noqa: E731

    pnl_cols = [
        ("premiums", f"{prem} (m)", USD2),
        ("fees", "Policy Fees (m)", USD2),
        ("inv_income", L("Actual: Investment Income as Filed (m)",
                         "Actual: Net Investment Result as Filed (m)"), USD2),
        ("inv_expenses", L("Actual: Investment Expenses (m)",
                           "Actual: Net Insurance Finance Expenses (m)"), USD2),
        ("nii", L("Net Investment Income (m)", "Net Financial Result (m)"), USD2),
        ("inv_gains", "Investment and Derivative Gains (m)", USD2),
        ("other_income", "Other Income (m)", USD2),
        ("revenue", "Revenue for the Model (m)", USD2),
        ("claims", L("Claims and Benefits (m)", "Insurance Service Expenses (m)"), USD2)]
    if ifrs:
        pnl_cols.append(("reinsurance", "Net Expense from Reinsurance Contracts Held (m)", USD2))
    pnl_cols += [
        ("reserve_change", "Change in Policy Reserves (m)", USD2),
        ("interest_credited", "Interest Credited to Policyholders (m)", USD2),
        ("ph_dividends", "Policyholder Dividends (m)", USD2),
        ("opex", L("Operating Expenses incl. Acquisition (m)",
                   "Other Expenses incl. Non-attributable and Finance Costs (m)"), USD2),
        ("operating", "Operating Result (m)", USD2),
        ("other_items", "Other Items in Pre-tax (m)", USD2),
        ("pretax", "Pre-tax (m)", USD2), ("tax", "Tax (m)", USD2),
        ("ni_total", "Net Income incl. NCI (m)", USD2),
        ("nci_share", "NCI Share of Net Income (m)", USD2),
        ("ni_owners", "Net Income to Owners (m)", USD2),
        ("loss_ratio", L(f"Loss Ratio (Claims / {prem})",
                         f"Insurance Service Expense Ratio (ISE / {prem})"), PCT1),
        ("reserve_ratio", f"Reserve Change / {prem}", PCT1),
        ("expense_ratio", L(f"Expense Ratio (Opex / {prem})",
                            f"Other Expense Ratio (Other Expenses / {prem})"), PCT1),
        ("combined", f"Combined Ratio (All Underwriting Costs / {prem})", PCT1),
        ("inv_yield", L("Net Investment Income / Opening Investments",
                        "Net Financial Result / Opening Investments"), PCT1),
        ("crediting", "Interest Credited / Opening Account Balances", PCT1),
        ("roe", "ROE (on Opening Equity to Owners)", PCT1)]
    m.declare("PnL", pnl_cols)
    m.declare("Equity", [
        ("open", "Equity to Owners Open (m)", USD2),
        ("ni", "Net Income to Owners (m)", USD2),
        ("dividends", "Dividends (m)", USD2), ("buybacks", "Buybacks (m)", USD2),
        ("other", "Other Movements incl. OCI (m)", USD2),
        ("close", "Equity to Owners Close (m)", USD2),
        ("nci", "NCI and Other Equity (m)", USD2)])
    m.declare("Balance_Sheet", [
        ("cash", "Cash (m)", USD2),
        ("investments", "Investments (m)", USD2),
        ("invested", "Investments + Cash (m)", USD2),
        ("other_assets", "Other Assets (m)", USD2),
        ("total_assets", "Total Assets (m)", USD2),
        ("claims_reserve", "Actual: Claims Reserve, as Tagged (m)", USD2),
        ("unearned_premiums", "Actual: Unearned Premiums, as Tagged (m)", USD2),
        ("future_policy_benefits", "Actual: Future Policy Benefits, as Tagged (m)", USD2),
        ("underwriting_reserve", "Actual: Japan Policy Reserve (m)", USD2),
        ("dividend_reserve", "Actual: Japan Dividend Reserve (m)", USD2),
        ("reserves_other", "Actual: Reserves Not Split Out (m)", USD2),
        ("pab", "Policyholder Account Balances (m)", USD2),
        ("reserves", L("Policy Reserves, Total (m)",
                       "Insurance Contract Liabilities incl. CSM (m)"), USD2),
        ("debt", "Debt (m)", USD2),
        ("other_liab", "Other Liabilities (m)", USD2),
        ("total_liab", "Total Liab (m)", USD2),
        ("equity_owners", "Equity to Owners (m)", USD2),
        ("nci", "NCI and Other Equity (m)", USD2),
        ("total_equity", "Total Equity (m)", USD2),
        ("total_le", "Total L+E (m)", USD2),
        ("balance_check", "BS Check (m)", USD2),
        ("reserves_premiums", L(f"Reserves / {prem} (x)",
                                f"Insurance Contract Liabilities / {prem} (x)"), "0.00"),
        ("eq_assets", "Equity to Owners / Assets", PCT1),
        ("target", "Target Equity / Assets", PCT1),
        ("capital_flag", "Capital vs Target", None),
        ("inv_flag", "Investments Flag", None)])
    m.declare("Investment_Flows", [
        ("open", "Investments Open (m)", USD2),
        ("ni", "Net Income to Owners (m)", USD2),
        ("dividends", "Dividends (m)", USD2), ("buybacks", "Buybacks (m)", USD2),
        ("equity_other", "Other Equity Movements (m)", USD2),
        ("d_nci", "Change in NCI (m)", USD2),
        ("d_reserves", "Change in Policy Reserves (m)", USD2),
        ("d_debt", "Change in Debt (m)", USD2),
        ("d_other_liab", "Change in Other Liabilities (m)", USD2),
        ("d_cash", "Change in Cash (m)", USD2),
        ("d_other_assets", "Change in Other Assets (m)", USD2),
        ("close", "Investments Close, from Flows (m)", USD2),
        ("bs", "Investments on Balance Sheet (m)", USD2),
        ("cf_check", "CF Check (m)", USD2)])
    m.declare("Residual_Income", RI_COLUMNS)

    B = lambda k, r: X("Balance_Sheet", k, r)  # noqa: E731

    # ── P&L ──────────────────────────────────────────────────────────────────
    def pnl(r):
        P = lambda k: X("PnL", k, r)  # noqa: E731
        ri = f"-{P('reinsurance')}" if ifrs else ""
        f = {"revenue": f"{P('premiums')}+{P('fees')}+{P('nii')}+{P('inv_gains')}+{P('other_income')}",
             "operating": (f"{P('revenue')}-{P('claims')}{ri}-{P('reserve_change')}"
                           f"-{P('interest_credited')}-{P('ph_dividends')}-{P('opex')}")}
        f.update({
            "loss_ratio": per(P("claims"), P("premiums")),
            "reserve_ratio": per(P("reserve_change"), P("premiums")),
            "expense_ratio": per(P("opex"), P("premiums")),
            "combined": per(f"({P('claims')}{ri.replace('-', '+')}+{P('reserve_change')}"
                            f"+{P('ph_dividends')}+{P('opex')})", P("premiums"))})
        if r > first:
            f.update({
                "inv_yield": per(P("nii"), B("investments", r - 1)),
                "crediting": per(P("interest_credited"), B("pab", r - 1)),
                "roe": per(P("ni_owners"), X("Equity", "close", r - 1))})
        else:
            # First actual year: the opening balances are the hist_open_ inputs. A ratio
            # whose opening balance is not filed is left blank (Data_Notes says why).
            for key, num, base in [("inv_yield", "nii", "investments"),
                                   ("crediting", "interest_credited",
                                    "policyholder_account_balances"),
                                   ("roe", "ni_owners", "equity_parent")]:
                if opening(base):
                    f[key] = per(P(num), opening(base))
        if is_hist(r):
            f.update({
                "premiums": H(r, "premiums"), "fees": H(r, "fee_income"),
                "inv_income": H(r, "investment_income"),
                "inv_expenses": H(r, "investment_expenses"),
                "nii": f"{P('inv_income')}-{P('inv_expenses')}",
                "inv_gains": H(r, "investment_gains"), "other_income": H(r, "other_income"),
                "claims": H(r, "claims"),
                **({"reinsurance": H(r, "reinsurance_expense")} if ifrs else {}),
                "reserve_change": H(r, "reserve_change"),
                "interest_credited": H(r, "interest_credited"),
                "ph_dividends": H(r, "policyholder_dividends"), "opex": H(r, "opex"),
                "other_items": f"{P('pretax')}-{P('operating')}",
                "pretax": H(r, "pretax"), "tax": H(r, "tax"),
                "ni_total": H(r, "net_income_total"), "ni_owners": H(r, "net_income"),
                "nci_share": f"{P('ni_total')}-{P('ni_owners')}"})
            return f
        f.update({
            "premiums": f"{prev('PnL', 'premiums', r)}*(1+ins_premium_growth)",
            "fees": f"{prev('PnL', 'fees', r)}*(1+ins_premium_growth)",
            "nii": f"{B('investments', r - 1)}*ins_investment_yield",
            "inv_gains": f"{P('premiums')}*ins_inv_gains_pct",
            "other_income": f"{P('premiums')}*ins_other_income_pct",
            "claims": f"{P('premiums')}*ins_loss_ratio",
            **({"reinsurance": f"{P('premiums')}*ins_reinsurance_pct"} if ifrs else {}),
            "reserve_change": f"{P('premiums')}*ins_reserve_change_pct",
            "interest_credited": f"{B('pab', r - 1)}*ins_crediting_rate",
            "ph_dividends": f"{P('premiums')}*ins_ph_dividend_pct",
            "opex": f"{P('premiums')}*ins_expense_ratio",
            "other_items": "0",
            "pretax": f"{P('operating')}+{P('other_items')}",
            "tax": f"MAX(0, {P('pretax')})*g_tax_rate",
            "ni_total": f"{P('pretax')}-{P('tax')}",
            "nci_share": f"{P('ni_total')}*ins_nci_share",
            "ni_owners": f"{P('ni_total')}-{P('nci_share')}"})
        return f
    fill("PnL", pnl, hist_only=("inv_income", "inv_expenses"))

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
             "invested": f"{b('investments')}+{b('cash')}",
             "reserves_premiums": per(b("reserves"), X("PnL", "premiums", r)),
             "eq_assets": per(b("equity_owners"), b("total_assets")),
             "target": "ins_target_equity_assets"}
        if is_hist(r):
            f.update({k: H(r, k) for k in RESERVE_PARTS})
            f.update({
                "cash": H(r, "cash"), "investments": H(r, "investments"),
                "total_assets": H(r, "total_assets"),
                "other_assets": f"{b('total_assets')}-{b('cash')}-{b('investments')}",
                "pab": H(r, "policyholder_account_balances"),
                "reserves": H(r, "policy_reserves"),
                "reserves_other": (f"{b('reserves')}-{b('pab')}-"
                                   + "-".join(b(k) for k in RESERVE_PARTS)),
                "debt": H(r, "debt"), "total_liab": H(r, "total_liabilities"),
                "other_liab": f"{b('total_liab')}-{b('reserves')}-{b('debt')}"})
            return f
        f.update({
            "cash": prev("Balance_Sheet", "cash", r),
            "other_assets": prev("Balance_Sheet", "other_assets", r),
            "investments": f"{b('total_le')}-{b('cash')}-{b('other_assets')}",
            "total_assets": f"{b('cash')}+{b('investments')}+{b('other_assets')}",
            "reserves": (f"IF(ins_reserve_rollforward=1, "
                         f"{prev('Balance_Sheet', 'reserves', r)}+{X('PnL', 'reserve_change', r)}, "
                         f"{X('PnL', 'premiums', r)}*ins_reserves_pct_premiums)"),
            "pab": (f"IF({prev('Balance_Sheet', 'reserves', r)}>0, {prev('Balance_Sheet', 'pab', r)}"
                    f"*{b('reserves')}/{prev('Balance_Sheet', 'reserves', r)}, 0)"),
            "debt": prev("Balance_Sheet", "debt", r),
            "other_liab": prev("Balance_Sheet", "other_liab", r),
            "total_liab": f"{b('reserves')}+{b('debt')}+{b('other_liab')}",
            "balance_check": f"{b('total_assets')}-{b('total_le')}",
            "capital_flag": (f'IF({b("eq_assets")}<{b("target")}, "below target", '
                             f'"at or above target")'),
            "inv_flag": (f'IF({b("investments")}<0, "NEGATIVE: reserves and equity do not fund '
                         f'cash and other assets; change the inputs", "ok")')})
        return f
    fill("Balance_Sheet", balance_sheet, hist_only=RESERVE_PARTS + ("reserves_other",))

    # ── Investment_Flows: investments rebuilt from the year's flows (the check) ─
    def flows(r):
        if is_hist(r):
            return {}
        L = lambda k: X("Investment_Flows", k, r)  # noqa: E731
        return {
            "open": prev("Balance_Sheet", "investments", r),
            "ni": X("Equity", "ni", r), "dividends": X("Equity", "dividends", r),
            "buybacks": X("Equity", "buybacks", r), "equity_other": X("Equity", "other", r),
            "d_nci": delta("Balance_Sheet", "nci", r),
            "d_reserves": delta("Balance_Sheet", "reserves", r),
            "d_debt": delta("Balance_Sheet", "debt", r),
            "d_other_liab": delta("Balance_Sheet", "other_liab", r),
            "d_cash": delta("Balance_Sheet", "cash", r),
            "d_other_assets": delta("Balance_Sheet", "other_assets", r),
            "close": (f"{L('open')}+{L('ni')}-{L('dividends')}-{L('buybacks')}+{L('equity_other')}"
                      f"+{L('d_nci')}+{L('d_reserves')}+{L('d_debt')}+{L('d_other_liab')}"
                      f"-{L('d_cash')}-{L('d_other_assets')}"),
            "bs": B("investments", r),
            "cf_check": f"{L('close')}-{L('bs')}"}
    fill("Investment_Flows", flows)

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
        book_note=("Equity attributable to owners of the parent as filed (IFRS: "
                   "親会社の所有者に帰属する持分). Non-controlling interests are not in it. The IFRS 17 "
                   "contractual service margin (CSM) sits inside insurance contract liabilities and "
                   "is not modelled separately; embedded value is not modelled." if ifrs else
                   "Shareholders' equity of the parent as filed (US: incl. any preferred stock; "
                   "Japan: 株主資本 + その他の包括利益累計額). Non-controlling interests and share "
                   "options are not in it. Embedded value and the IFRS 17 contractual service "
                   "margin are not modelled."))
    build_data_notes(wb, data_notes)

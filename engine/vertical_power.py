"""
Capacity vertical (power project). Reads a schema built on verticals/power_project.yaml.

Sheets: Timeline, Scenario, Ops, Capex, Generation, OpEx, Assets, Debt,
Working_Capital, PnL, Funding, Balance_Sheet, Cash_Flow, Returns, KPI.

Timeline: quarterly through construction, then annual from COD (commercial
operation date, when the plant starts selling power).

Modelling choices (each a simplification worth knowing about):
  - Capex is drawn evenly over the construction quarters, funded at the gearing
    ratio by debt; equity tops cash up to the minimum. Interest during
    construction (IDC) is either rolled into the loan and the asset, or expensed.
  - At COD, construction work in progress (CWIP) becomes plant (PP&E) and is
    depreciated straight-line over its life.
  - The loan balance at COD is repaid over the tenor in proportion to each year's
    EBITDA. The target DSCR is a test on the result; it does not size the debt.
  - A debt service reserve account (DSRA) holds the next period's debt service
    for fin_dsra_months.
  - Dividends are paid from cash above the minimum, capped by retained earnings,
    and stopped in any year whose DSCR is below the lock-up level. Cash left at
    the end counts as a final distribution in the equity IRR.
  - Operating costs start at COD; nothing is spent on O&M during construction.
  - Output = capacity factor x availability: the capacity factor input is the
    figure before downtime (set availability to 100% for a net figure).
  - Tax is on book profit; book depreciation is the only deduction (no tax
    depreciation, allowances or credits). Losses carry forward without limit.
    The 25% default is a placeholder.
"""

from __future__ import annotations

from build_model import PCT1, USD2
from core import Model

N0 = "#,##0"
X2 = "0.00"


def build(wb, schema, v):
    m = Model(wb, schema, v)
    m.build_timeline("g_fc_start_date",
                     [(int(v["g_construction_qtrs"]), 3, "quarterly"),
                      (int(v["g_operating_years"]), 12, "annual")])
    m.build_scenario()
    first, last = m.first, m.last
    X, T, scn = m.ref, m.t, m.scn

    def prev(sheet, key, r, first_value="0"):
        return first_value if r == first else X(sheet, key, r - 1)

    def delta(sheet, key, r):
        return X(sheet, key, r) if r == first else f"({X(sheet, key, r)}-{X(sheet, key, r - 1)})"

    # ── declare every sheet's columns ─────────────────────────────────────────
    m.declare("Ops", [("op", "Operating (1/0)", "0"), ("op_year", "Operating Year", "0"),
                      ("first_op", "First Op Period", "0"), ("in_tenor", "In Debt Tenor", "0"),
                      ("in_life", "In Depn Life", "0"), ("holiday", "Tax Holiday", "0")])
    m.declare("Generation", [
        ("cf_eff", "Capacity Factor", PCT1), ("degr_idx", "Degradation Index", "0.0000"),
        ("mwh", "Output (MWh)", N0), ("tariff", "PPA Tariff ($/MWh)", X2),
        ("merchant", "Merchant Price ($/MWh)", X2), ("ppa_rev", "PPA Rev ($M)", None),
        ("merchant_rev", "Merchant Rev ($M)", None), ("cap_rev", "Capacity Rev ($M)", None),
        ("revenue", "Revenue ($M)", None)])
    m.declare("OpEx", [
        ("esc", "Escalation Index", X2), ("fixed_om", "Fixed O&M ($M)", None),
        ("var_om", "Variable O&M ($M)", None), ("insurance", "Insurance ($M)", None),
        ("land", "Land Lease ($M)", None), ("asset_mgmt", "Asset Mgmt ($M)", None),
        ("opex", "OpEx ($M)", None)])
    m.declare("Assets", [
        ("capex", "Capex ($M)", None), ("cwip_open", "CWIP Open ($M)", None),
        ("idc_cap", "IDC Capitalised ($M)", None), ("cwip_close", "CWIP Close ($M)", None),
        ("transfer", "CWIP to PP&E ($M)", None), ("ppe_gross", "PP&E Gross ($M)", None),
        ("dep", "Depreciation ($M)", None), ("acc_dep", "Acc. Depn ($M)", None),
        ("ppe_net", "PP&E Net ($M)", None)])
    m.declare("Debt", [
        ("cons_open", "Construction Loan Open ($M)", None), ("draw", "Draws ($M)", None),
        ("idc", "IDC ($M)", None), ("idc_cap", "IDC Capitalised ($M)", None),
        ("cons_close", "Construction Loan Close ($M)", None), ("open", "Debt Open ($M)", None),
        ("interest", "Interest Expense ($M)", None), ("ebitda_tenor", "EBITDA in Tenor ($M)", None),
        ("repay", "Repayment ($M)", None), ("close", "Debt Close ($M)", None),
        ("ds", "Debt Service ($M)", None), ("cfads", "CFADS ($M)", None),
        ("wc_initial", "Initial WC Build-up ($M)", None),
        ("cfads_cov", "CFADS for DSCR ($M)", None),
        ("dscr", "DSCR", X2), ("lockup", "Lock-up (1/0)", "0"),
        ("dsra_close", "DSRA Balance ($M)", None)])
    m.declare("Working_Capital", [("ar", "Accts Recv ($M)", None), ("ap", "Accts Pay ($M)", None)])
    m.declare("PnL", [
        ("revenue", "Revenue ($M)", USD2), ("opex", "OpEx ($M)", USD2),
        ("ebitda", "EBITDA ($M)", USD2), ("dep", "Depreciation ($M)", USD2),
        ("ebit", "EBIT ($M)", USD2), ("interest", "Interest ($M)", USD2),
        ("pre_tax", "Pre-tax ($M)", USD2), ("nol_open", "Tax Losses Open ($M)", USD2),
        ("nol_used", "Losses Used ($M)", USD2), ("nol_close", "Tax Losses Close ($M)", USD2),
        ("tax", "Tax ($M)", USD2), ("net_income", "Net Income ($M)", USD2)])
    m.declare("Funding", [
        ("cash_open", "Cash Open ($M)", USD2), ("cfo", "Oper CF ($M)", USD2),
        ("capex", "Capex ($M)", USD2), ("d_dsra", "DSRA Funding ($M)", USD2),
        ("draw", "Debt Draws ($M)", USD2), ("repay", "Debt Repaid ($M)", USD2),
        ("cash_before", "Pre-Equity ($M)", USD2), ("equity", "Equity In ($M)", USD2),
        ("div_cap", "Retained Earnings Cap ($M)", USD2), ("dividend", "Dividend ($M)", USD2),
        ("cash_close", "Cash Close ($M)", USD2)])
    m.declare("Balance_Sheet", [
        ("cash", "Cash ($M)", USD2), ("dsra", "DSRA ($M)", USD2), ("ar", "Accts Recv ($M)", USD2),
        ("cwip", "CWIP ($M)", USD2), ("ppe", "PP&E Net ($M)", USD2),
        ("total_assets", "Total Assets ($M)", USD2), ("ap", "Accts Pay ($M)", USD2),
        ("debt", "Debt ($M)", USD2), ("total_liab", "Total Liab ($M)", USD2),
        ("paid_in", "Paid-in Cap ($M)", USD2), ("retained", "Retained Earn ($M)", USD2),
        ("total_equity", "Total Equity ($M)", USD2), ("total_le", "Total L+E ($M)", USD2),
        ("balance_check", "BS Check ($M)", USD2)])
    m.declare("Cash_Flow", [
        ("ni", "Net Income ($M)", USD2), ("dep", "Depreciation ($M)", USD2),
        ("d_ar", "ΔAR ($M)", USD2), ("d_ap", "ΔAP ($M)", USD2), ("cfo", "CF from Ops ($M)", USD2),
        ("capex", "Capex ($M)", USD2), ("d_dsra", "ΔDSRA ($M)", USD2),
        ("cfi", "CF from Inv ($M)", USD2), ("debt_net", "Debt Net ($M)", USD2),
        ("equity", "Equity In ($M)", USD2), ("dividend", "Dividends ($M)", USD2),
        ("cff", "CF from Fin ($M)", USD2), ("net_change", "Net ΔCash ($M)", USD2),
        ("opening", "Open Cash ($M)", USD2), ("closing", "Close Cash ($M)", USD2),
        ("cf_check", "CF Check ($M)", USD2)])
    m.declare("Returns", [
        ("end_date", "Period End", "mmm yyyy"), ("df", "Discount Factor", "0.0000"),
        ("pv_cost", "PV Cost ($M)", None), ("pv_mwh", "PV Output (MWh)", N0),
        ("project_cf", "Project CF ($M)", USD2), ("equity_cf", "Equity CF ($M)", USD2),
        ("cum_eq", "Cum. Equity CF ($M)", USD2), ("payback", "Payback (yrs from COD)", "0.0")])

    constr_months = "g_construction_qtrs*3"

    # ── Capex (single values) ────────────────────────────────────────────────
    keys = ["epc", "dev", "grid", "contingency", "total", "debt_cod"]
    cx = {k: f"Capex!$B${i}" for i, k in enumerate(keys, 2)}
    cx_rows = [
        ("epc", "EPC ($M)", "capex_epc_per_mw*plant_capacity_mw/1000"),
        ("dev", "Development ($M)", f"{cx['epc']}*capex_dev_pct"),
        ("grid", "Grid connection ($M)", "capex_grid"),
        ("contingency", "Contingency ($M)",
         f"({cx['epc']}+{cx['dev']}+{cx['grid']})*capex_contingency"),
        ("total", "Total capex, scenario-adjusted ($M)",
         f"({cx['epc']}+{cx['dev']}+{cx['grid']}+{cx['contingency']})*{scn['capex_mult']}"),
        ("debt_cod", "Debt at COD incl. capitalised IDC ($M)",
         f"SUM({m.rng('Debt', 'draw')})+SUM({m.rng('Debt', 'idc_cap')})"),
    ]
    m.static_sheet("Capex", [(k, lbl, f, USD2) for k, lbl, f in cx_rows])

    # ── Ops flags ────────────────────────────────────────────────────────────
    def ops(r):
        O = lambda k: X("Ops", k, r)  # noqa: E731
        return {
            "op": f"IF({T('m0', r)}>={constr_months}, 1, 0)",
            "op_year": f"IF({O('op')}=1, ({T('m0', r)}-{constr_months})/12+1, 0)",
            "first_op": f"IF(AND({O('op')}=1, {O('op_year')}=1), 1, 0)",
            "in_tenor": f"IF(AND({O('op')}=1, {O('op_year')}<=fin_debt_tenor), 1, 0)",
            "in_life": f"IF(AND({O('op')}=1, {O('op_year')}<=depn_life_years), 1, 0)",
            "holiday": f"IF(AND({O('op')}=1, {O('op_year')}<=g_tax_holiday_years), 1, 0)",
        }
    m.fill("Ops", ops)

    # ── Generation and revenue ───────────────────────────────────────────────
    def generation(r):
        G = lambda k: X("Generation", k, r)  # noqa: E731
        op, oy, mo = X("Ops", "op", r), X("Ops", "op_year", r), T("months", r)
        return {
            "cf_eff": f"plant_cf_year1+{scn['cf_delta_pp']}/100",
            "degr_idx": f"(1-plant_degradation)^MAX(0, {oy}-1)",
            "mwh": (f"{op}*plant_capacity_mw*8760*{mo}/12*{G('cf_eff')}*{G('degr_idx')}"
                    f"*plant_availability"),
            "tariff": (f"rev_ppa_tariff*{m.curve('tariff_escalation', 'mult', oy)}"
                       f"*{scn['tariff_mult']}"),
            "merchant": (f"rev_merchant_price*{m.curve('merchant_price_path', 'price', oy)}"
                         f"/{m.curve('merchant_price_path', 'price', '1')}*{scn['tariff_mult']}"),
            "ppa_rev": f"{G('mwh')}*rev_ppa_share*{G('tariff')}/1000000",
            "merchant_rev": f"{G('mwh')}*(1-rev_ppa_share)*{G('merchant')}/1000000",
            "cap_rev": f"{op}*plant_capacity_mw*rev_capacity_payment*{mo}/12/1000",
            "revenue": f"{G('ppa_rev')}+{G('merchant_rev')}+{G('cap_rev')}",
        }
    m.fill("Generation", generation)

    # ── Operating costs ──────────────────────────────────────────────────────
    def opex(r):
        O = lambda k: X("OpEx", k, r)  # noqa: E731
        op, oy, mo = X("Ops", "op", r), X("Ops", "op_year", r), T("months", r)
        return {
            "esc": f"(1+opex_escalation)^MAX(0, {oy}-1)",
            "fixed_om": f"{op}*plant_capacity_mw*opex_fixed_om*{mo}/12/1000*{O('esc')}",
            "var_om": f"{X('Generation', 'mwh', r)}*opex_var_om/1000000*{O('esc')}",
            "insurance": f"{op}*{cx['total']}*opex_insurance_pct*{mo}/12",
            "land": f"{op}*opex_land_lease*{mo}/12/1000*{O('esc')}",
            "asset_mgmt": f"{op}*opex_asset_mgmt*{mo}/12/1000*{O('esc')}",
            "opex": "+".join(O(k) for k in ("fixed_om", "var_om", "insurance", "land", "asset_mgmt")),
        }
    m.fill("OpEx", opex)

    # ── Fixed assets: CWIP during construction, PP&E from COD ────────────────
    def assets(r):
        A = lambda k: X("Assets", k, r)  # noqa: E731
        op = X("Ops", "op", r)
        return {
            "capex": f"(1-{op})*{cx['total']}/g_construction_qtrs",
            "cwip_open": prev("Assets", "cwip_close", r),
            "idc_cap": X("Debt", "idc_cap", r),
            "cwip_close": f"IF({op}=1, 0, {A('cwip_open')}+{A('capex')}+{A('idc_cap')})",
            "transfer": f"IF({X('Ops', 'first_op', r)}=1, {A('cwip_open')}, 0)",
            "ppe_gross": f"{prev('Assets', 'ppe_gross', r)}+{A('transfer')}",
            "dep": (f"{X('Ops', 'in_life', r)}*{A('ppe_gross')}*(1-depn_salvage_pct)"
                    f"/depn_life_years*{T('months', r)}/12"),
            "acc_dep": f"{prev('Assets', 'acc_dep', r)}+{A('dep')}",
            "ppe_net": f"{A('ppe_gross')}-{A('acc_dep')}",
        }
    m.fill("Assets", assets)

    # ── Debt, DSCR and DSRA ──────────────────────────────────────────────────
    def debt(r):
        D = lambda k: X("Debt", k, r)  # noqa: E731
        op, mo = X("Ops", "op", r), T("months", r)
        period_rate = f"fin_debt_rate*{mo}/12"
        if r < last:
            dsra = f"{X('Debt', 'ds', r + 1)}*fin_dsra_months/{T('months', r + 1)}"
        else:
            dsra = "0"   # released in the final period
        tenor_sum = f"SUM({m.rng('Debt', 'ebitda_tenor')})"
        # The construction loan builds up from draws and capitalised IDC only, so
        # the balance at COD (which sizes repayments) never depends on repayments.
        return {
            "cons_open": prev("Debt", "cons_close", r),
            "draw": f"{X('Assets', 'capex', r)}*fin_gearing",
            "idc": f"(1-{op})*{D('cons_open')}*{period_rate}",
            "idc_cap": f"{D('idc')}*fin_idc_capitalised",
            "cons_close": f"{D('cons_open')}+{D('draw')}+{D('idc_cap')}",
            "open": prev("Debt", "close", r),
            "interest": f"{op}*{D('open')}*{period_rate}+{D('idc')}-{D('idc_cap')}",
            "ebitda_tenor": f"{X('Ops', 'in_tenor', r)}*{X('PnL', 'ebitda', r)}",
            "repay": (f"IF({tenor_sum}=0, 0, {cx['debt_cod']}*{D('ebitda_tenor')}"
                      f"/{tenor_sum})"),
            "close": f"{D('open')}+{D('draw')}+{D('idc_cap')}-{D('repay')}",
            # tenor flag keeps rounding dust left after the last repayment out of DSCR
            "ds": f"{X('Ops', 'in_tenor', r)}*({D('open')}*{period_rate}+{D('repay')})",
            "cfads": (f"{X('PnL', 'ebitda', r)}-{X('PnL', 'tax', r)}"
                      f"-{delta('Working_Capital', 'ar', r)}+{delta('Working_Capital', 'ap', r)}"),
            # Receivables and payables build up from zero in the first operating period.
            # That one-off is still paid in cash (CFADS, cash flow, IRRs) but left out of
            # the DSCR and lock-up tests (check the CFADS definition in the loan terms).
            "wc_initial": (f"{X('Ops', 'first_op', r)}*({delta('Working_Capital', 'ar', r)}"
                           f"-{delta('Working_Capital', 'ap', r)})"),
            "cfads_cov": f"{D('cfads')}+{D('wc_initial')}",
            "dscr": f'IF({D("ds")}>0, {D("cfads_cov")}/{D("ds")}, "")',
            "lockup": (f"IF({D('ds')}>0, IF({D('cfads_cov')}<cash_distribution_lockup*{D('ds')}, 1, 0)"
                       f", 0)"),
            "dsra_close": dsra,
        }
    m.fill("Debt", debt)

    # ── Working capital ──────────────────────────────────────────────────────
    def working_capital(r):
        mo = T("months", r)
        return {"ar": f"{X('PnL', 'revenue', r)}/{mo}*wc_ar_days/30",
                "ap": f"{X('PnL', 'opex', r)}/{mo}*wc_ap_days/30"}
    m.fill("Working_Capital", working_capital)

    # ── P&L ──────────────────────────────────────────────────────────────────
    def pnl(r):
        P = lambda k: X("PnL", k, r)  # noqa: E731
        return {
            "revenue": X("Generation", "revenue", r), "opex": X("OpEx", "opex", r),
            "ebitda": f"{P('revenue')}-{P('opex')}", "dep": X("Assets", "dep", r),
            "ebit": f"{P('ebitda')}-{P('dep')}", "interest": X("Debt", "interest", r),
            "pre_tax": f"{P('ebit')}-{P('interest')}",
            "nol_open": prev("PnL", "nol_close", r),
            "nol_used": f"IF({P('pre_tax')}>0, MIN({P('nol_open')}, {P('pre_tax')}), 0)",
            "nol_close": f"{P('nol_open')}-{P('nol_used')}+MAX(0, -{P('pre_tax')})",
            "tax": (f"MAX(0, {P('pre_tax')}-{P('nol_used')})*g_tax_rate"
                    f"*(1-{X('Ops', 'holiday', r)})"),
            "net_income": f"{P('pre_tax')}-{P('tax')}",
        }
    m.fill("PnL", pnl)

    # ── Funding: equity top-up and dividends ─────────────────────────────────
    def funding(r):
        F = lambda k: X("Funding", k, r)  # noqa: E731
        ni = X("PnL", "net_income", r)
        return {
            "cash_open": prev("Balance_Sheet", "cash", r),
            "cfo": (f"{ni}+{X('Assets', 'dep', r)}-{delta('Working_Capital', 'ar', r)}"
                    f"+{delta('Working_Capital', 'ap', r)}"),
            "capex": f"-{X('Assets', 'capex', r)}",
            "d_dsra": f"-{delta('Debt', 'dsra_close', r)}",
            "draw": X("Debt", "draw", r),
            "repay": f"-{X('Debt', 'repay', r)}",
            "cash_before": "+".join(F(k) for k in ("cash_open", "cfo", "capex", "d_dsra",
                                                   "draw", "repay")),
            "equity": f"MAX(0, cash_min_operating-{F('cash_before')})",
            "div_cap": f"MAX(0, {prev('Balance_Sheet', 'retained', r)}+{ni})",
            "dividend": (f"IF({X('Debt', 'lockup', r)}=1, 0, MIN(MAX(0, {F('cash_before')}"
                         f"+{F('equity')}-cash_min_operating), {F('div_cap')}))"),
            "cash_close": f"{F('cash_before')}+{F('equity')}-{F('dividend')}",
        }
    m.fill("Funding", funding)

    # ── Balance sheet ────────────────────────────────────────────────────────
    def balance_sheet(r):
        B = lambda k: X("Balance_Sheet", k, r)  # noqa: E731
        return {
            "cash": X("Funding", "cash_close", r), "dsra": X("Debt", "dsra_close", r),
            "ar": X("Working_Capital", "ar", r), "cwip": X("Assets", "cwip_close", r),
            "ppe": X("Assets", "ppe_net", r),
            "total_assets": "+".join(B(k) for k in ("cash", "dsra", "ar", "cwip", "ppe")),
            "ap": X("Working_Capital", "ap", r), "debt": X("Debt", "close", r),
            "total_liab": f"{B('ap')}+{B('debt')}",
            "paid_in": f"{prev('Balance_Sheet', 'paid_in', r)}+{X('Funding', 'equity', r)}",
            "retained": (f"{prev('Balance_Sheet', 'retained', r)}+{X('PnL', 'net_income', r)}"
                         f"-{X('Funding', 'dividend', r)}"),
            "total_equity": f"{B('paid_in')}+{B('retained')}",
            "total_le": f"{B('total_liab')}+{B('total_equity')}",
            "balance_check": f"{B('total_assets')}-{B('total_le')}",
        }
    m.fill("Balance_Sheet", balance_sheet)

    # ── Cash flow: rebuilt from balance-sheet movements ──────────────────────
    def cash_flow(r):
        C = lambda k: X("Cash_Flow", k, r)  # noqa: E731
        D = lambda k: delta("Balance_Sheet", k, r)  # noqa: E731
        idc_cap, dep = X("Assets", "idc_cap", r), X("Assets", "dep", r)
        return {
            "ni": X("PnL", "net_income", r), "dep": dep,
            "d_ar": f"-{D('ar')}", "d_ap": D("ap"),
            "cfo": f"{C('ni')}+{C('dep')}+{C('d_ar')}+{C('d_ap')}",
            # spend on assets = rise in CWIP and net PP&E, plus depreciation, less
            # the IDC that was added to CWIP without cash leaving
            "capex": f"-({D('cwip')}+{D('ppe')}+{dep}-{idc_cap})",
            "d_dsra": f"-{D('dsra')}",
            "cfi": f"{C('capex')}+{C('d_dsra')}",
            "debt_net": f"{D('debt')}-{idc_cap}",
            "equity": X("Funding", "equity", r),
            "dividend": f"-{X('Funding', 'dividend', r)}",
            "cff": f"{C('debt_net')}+{C('equity')}+{C('dividend')}",
            "net_change": f"{C('cfo')}+{C('cfi')}+{C('cff')}",
            "opening": prev("Cash_Flow", "closing", r),
            "closing": f"{C('opening')}+{C('net_change')}",
            "cf_check": f"{C('closing')}-{X('Balance_Sheet', 'cash', r)}",
        }
    m.fill("Cash_Flow", cash_flow)

    # ── Returns (per period, feeds KPI) ──────────────────────────────────────
    def returns(r):
        R = lambda k: X("Returns", k, r)  # noqa: E731
        terminal = (f"+{X('Balance_Sheet', 'cash', r)}+{X('Balance_Sheet', 'dsra', r)}"
                    f"-{X('Balance_Sheet', 'debt', r)}") if r == last else ""
        prev_cum = prev("Returns", "cum_eq", r)
        return {
            "end_date": f"EDATE({T('start', r)}, {T('months', r)})",
            "df": f"1/(1+kpi_discount_rate)^({T('m1', r)}/12)",
            "pv_cost": f"({X('Assets', 'capex', r)}+{X('PnL', 'opex', r)})*{R('df')}",
            "pv_mwh": f"{X('Generation', 'mwh', r)}*{R('df')}",
            "project_cf": f"-{X('Assets', 'capex', r)}+{X('Debt', 'cfads', r)}",
            "equity_cf": f"-{X('Funding', 'equity', r)}+{X('Funding', 'dividend', r)}{terminal}",
            "cum_eq": f"{prev_cum}+{R('equity_cf')}",
            "payback": (f'IF(AND({R("cum_eq")}>=0, {prev_cum}<0), '
                        f'({T("m1", r)}-{constr_months})/12, "")'),
        }
    m.fill("Returns", returns)

    # ── KPI (single values, in the template's order) ─────────────────────────
    kpi_defs = {
        "lcoe": ("LCOE ($/MWh)", f"SUM({m.rng('Returns', 'pv_cost')})"
                 f"/SUM({m.rng('Returns', 'pv_mwh')})*1000000", X2),
        "dscr_min": ("Minimum DSCR", f"MIN({m.rng('Debt', 'dscr')})", X2),
        "dscr_avg": ("Average DSCR", f"AVERAGE({m.rng('Debt', 'dscr')})", X2),
        "equity_irr": ("Equity IRR", f"XIRR({m.rng('Returns', 'equity_cf')}, "
                       f"{m.rng('Returns', 'end_date')})", PCT1),
        "project_irr": ("Project IRR (after tax, before financing)",
                        f"XIRR({m.rng('Returns', 'project_cf')}, {m.rng('Returns', 'end_date')})",
                        PCT1),
        "payback_years": ("Equity payback (years from COD)",
                          f'IF(COUNT({m.rng("Returns", "payback")})=0, "not reached", '
                          f'MIN({m.rng("Returns", "payback")}))', "0.0"),
        "ebitda_margin": ("EBITDA margin (life of project)",
                          f"SUM({m.rng('PnL', 'ebitda')})/SUM({m.rng('PnL', 'revenue')})", PCT1),
    }
    rows = [(k, *kpi_defs[k]) for k in schema.get("kpis", [])]
    rows.append(("dscr_test", "Minimum DSCR vs target",
                 f'IF(MIN({m.rng("Debt", "dscr")})<fin_target_dscr, "below target", '
                 f'"meets target")', None))
    m.static_sheet("KPI", [(k, lbl, f, fmt) for k, lbl, f, fmt in rows])

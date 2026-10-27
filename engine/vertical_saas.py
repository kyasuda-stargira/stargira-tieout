"""
Subscription vertical (B2B SaaS). Reads a schema built on verticals/saas.yaml.

Sheets: Timeline, Scenario, Customers, Revenue, Headcount, OpEx, Capitalised,
Working_Capital, PnL, Funding, Balance_Sheet, Cash_Flow, Cap_Table, KPI.

Modelling choices (each a simplification worth knowing about):
  - Revenue is a monthly-recurring-revenue (MRR) stock per tier: new customers add
    MRR, churn removes the departing share, expansion and the annual price
    escalator grow the retained base. Subscription revenue = average MRR x months.
  - Opening customers and opening ARR are all Tier A.
  - Young-customer churn uplift applies to the share of the opening base that the
    current add rate implies joined in the last 12 months (no cohort tracking).
  - Annual-prepay customers leave half a year of service unearned on average, so
    deferred revenue = annual customers' share of MRR x MRR x 12 / 2. Their share
    of MRR is below their share of customers, because they get the prepay discount.
  - The opening balance sheet holds working capital for the opening customers:
    receivables and deferred revenue from the opening MRR, and prepaid, payables
    and accruals at the first period's cost level. Opening equity is cash plus this
    net working capital; the net working capital sits in opening retained earnings.
  - Commissions (inside CAC spend) and capitalised development are amortised at
    1/N of the opening balance per month, N being the amortisation period.
  - Planned rounds come in the period containing their close month. If cash would
    fall below the target (the larger of the minimum and the buffer months of
    costs), an "unplanned funding" line tops it up. That line is the funding gap.
    The cap table issues shares for its total at one price (the Round 2 price x
    cap_unplanned_price_mult), whenever the money arrives.
  - CAC payback and LTV/CAC count all acquisition cost: variable acquisition spend
    (commissions included), the fixed marketing base and sales salaries. Only
    customer-success salaries are left out.
"""

from __future__ import annotations

from build_model import PCT1, USD2
from core import Model, interp

N0 = "#,##0.0"
X2 = "0.00"
ROLES = ["eng", "prod", "sales", "cs", "ga"]


def build(wb, schema, v):
    m = Model(wb, schema, v)
    m.build_timeline("g_model_start_date",
                     [(int(v["g_monthly_periods"]), 1, "monthly"),
                      (int(v["g_quarterly_periods"]), 3, "quarterly")])
    m.build_scenario()
    first = m.first
    X = m.ref
    T = m.t
    scn = m.scn

    def prev(sheet, key, r, first_value):
        return first_value if r == first else X(sheet, key, r - 1)

    # Opening working capital, on its own sheet (written after Working_Capital).
    # Receivables and deferred revenue follow the opening MRR; prepaid, payables
    # and accruals assume costs were already running at the first period's level.
    ob_rows = [
        ("mrr", "Opening MRR ($M)", "g_opening_arr/12", USD2),
        ("ar", "Opening Accts Recv ($M)", None, USD2),
        ("prepaid", "Opening Prepaid ($M)", None, USD2),
        ("ap", "Opening Accts Pay ($M)", None, USD2),
        ("accrued", "Opening Accrued ($M)", None, USD2),
        ("deferred", "Opening Deferred Rev ($M)", None, USD2),
        ("net_wc", "Opening Net Working Capital ($M)", None, USD2),
    ]
    ob = {k: f"Opening_Balances!$B${i}" for i, (k, _, _, _) in enumerate(ob_rows, 2)}
    opening = {("Revenue", "deferred"): ob["deferred"]}
    for k in ("ar", "prepaid", "ap", "accrued", "deferred"):
        opening[("Working_Capital", k)] = ob[k]
        opening[("Balance_Sheet", k)] = ob[k]

    def delta(sheet, key, r):
        o = opening.get((sheet, key))
        if r == first:
            return X(sheet, key, r) if o is None else f"({X(sheet, key, r)}-{o})"
        return f"({X(sheet, key, r)}-{X(sheet, key, r - 1)})"

    # ── declare every sheet's columns ─────────────────────────────────────────
    tiers = [("a", "Tier A"), ("b", "Tier B")]
    cust_cols = [("t_live", "Years (lag-adjusted)", X2), ("season", "Q4 Uplift", X2),
                 ("churn_trend", "Churn Trend (x)", X2)]
    for t, lbl in tiers:
        cust_cols += [(f"open_{t}", f"{lbl} Open", N0), (f"adds_{t}", f"{lbl} Adds", N0),
                      (f"young_{t}", f"{lbl} Share <1yr", PCT1),
                      (f"rate_{t}", f"{lbl} Churn %/mo", "0.00%"),
                      (f"churned_{t}", f"{lbl} Churned", N0), (f"close_{t}", f"{lbl} Close", N0)]
    cust_cols += [("total", "Customers Close", N0)]
    m.declare("Customers", cust_cols)

    rev_cols = [("price_idx", "List Price Index", X2), ("bill_factor", "Billing Mix Factor", X2)]
    for t, lbl in tiers:
        rev_cols += [(f"mrr_open_{t}", f"{lbl} MRR Open ($M)", None),
                     (f"ret_{t}", f"{lbl} Retained ($M)", None),
                     (f"exp_{t}", f"{lbl} Expansion ($M)", None),
                     (f"new_{t}", f"{lbl} New MRR ($M)", None),
                     (f"mrr_close_{t}", f"{lbl} MRR Close ($M)", None)]
    rev_cols += [("mrr_open", "MRR Open ($M)", None), ("mrr_close", "MRR Close ($M)", None),
                 ("arr", "ARR ($M)", USD2), ("sub_rev", "Subscription Rev ($M)", None),
                 ("new_acv", "New ACV ($M)", None), ("svc_rev", "Services Rev ($M)", None),
                 ("total_rev", "Total Rev ($M)", None), ("deferred", "Deferred Rev ($M)", None),
                 ("billings", "Billings ($M)", None)]
    m.declare("Revenue", rev_cols)

    hc_cols = []
    for role in ROLES:
        hc_cols += [(f"n_{role}", f"{role.upper()} Heads", N0),
                    (f"cost_{role}", f"{role.upper()} Cost ($M)", None)]
    hc_cols += [("total_n", "Total Heads", N0), ("total_cost", "Total People Cost ($M)", None)]
    m.declare("Headcount", hc_cols)

    m.declare("OpEx", [
        ("esc", "Non-people Escalation", X2),
        ("hosting", "Hosting ($M)", None), ("support", "Support ($M)", None),
        ("thirdparty", "3rd-party Licences ($M)", None), ("svc_cost", "Services Cost ($M)", None),
        ("cogs", "COGS ($M)", None),
        ("cac_idx", "CAC Efficiency Index", X2), ("cac_spend", "CAC Spend ($M)", None),
        ("comm_cap", "Commissions Capitalised ($M)", None), ("marketing", "Marketing Base ($M)", None),
        ("sm", "S&M Expense ($M)", None),
        ("rd_tools", "R&D Tools ($M)", None), ("rd_gross", "R&D Gross ($M)", None),
        ("dev_cap", "Dev Capitalised ($M)", None), ("rd", "R&D Expense ($M)", None),
        ("ga_nonpeople", "G&A Non-people ($M)", None), ("ga_other", "Other G&A ($M)", None),
        ("bad_debt", "Bad Debt ($M)", None), ("ga", "G&A Expense ($M)", None),
        ("opex", "OpEx ($M)", None), ("nonpeople", "Supplier Spend ($M)", None),
    ])
    m.declare("Capitalised", [
        ("comm_open", "Commissions Open ($M)", None), ("comm_add", "Commissions Added ($M)", None),
        ("comm_amort", "Commissions Amort ($M)", None), ("comm_close", "Commissions Close ($M)", None),
        ("dev_open", "Dev Asset Open ($M)", None), ("dev_add", "Dev Capitalised ($M)", None),
        ("dev_amort", "Dev Amort ($M)", None), ("dev_close", "Dev Asset Close ($M)", None),
    ])
    m.declare("Working_Capital", [
        ("ar", "Accts Recv ($M)", None), ("prepaid", "Prepaid ($M)", None),
        ("ap", "Accts Pay ($M)", None), ("accrued", "Accrued ($M)", None),
        ("deferred", "Deferred Rev ($M)", None),
    ])
    m.declare("PnL", [
        ("revenue", "Revenue ($M)", USD2), ("cogs", "COGS ($M)", USD2),
        ("gross_profit", "Gross Profit ($M)", USD2), ("sm", "S&M ($M)", USD2),
        ("rd", "R&D ($M)", USD2), ("ga", "G&A ($M)", USD2), ("ebitda", "EBITDA ($M)", USD2),
        ("da", "D&A ($M)", USD2), ("ebit", "EBIT ($M)", USD2),
        ("int_income", "Interest Income ($M)", USD2), ("pre_tax", "Pre-tax ($M)", USD2),
        ("nol_open", "Tax Losses Open ($M)", USD2), ("nol_used", "Losses Used ($M)", USD2),
        ("nol_close", "Tax Losses Close ($M)", USD2), ("tax", "Tax ($M)", USD2),
        ("net_income", "Net Income ($M)", USD2),
    ])
    m.declare("Funding", [
        ("cash_open", "Cash Open ($M)", USD2), ("cfo", "Oper CF ($M)", USD2),
        ("cfi", "Invest CF ($M)", USD2), ("raise1", "Round 1 Net ($M)", USD2),
        ("raise2", "Round 2 Net ($M)", USD2), ("cash_before", "Pre-Top-up ($M)", USD2),
        ("target", "Target Cash ($M)", USD2), ("plug", "Unplanned Funding ($M)", USD2),
        ("cash_close", "Cash Close ($M)", USD2),
    ])
    m.declare("Balance_Sheet", [
        ("cash", "Cash ($M)", USD2), ("ar", "Accts Recv ($M)", USD2),
        ("prepaid", "Prepaid ($M)", USD2), ("comm", "Cap. Commissions ($M)", USD2),
        ("dev", "Cap. Development ($M)", USD2), ("total_assets", "Total Assets ($M)", USD2),
        ("ap", "Accts Pay ($M)", USD2), ("accrued", "Accrued ($M)", USD2),
        ("deferred", "Deferred Rev ($M)", USD2), ("total_liab", "Total Liab ($M)", USD2),
        ("paid_in", "Paid-in Cap ($M)", USD2), ("retained", "Retained Earn ($M)", USD2),
        ("total_equity", "Total Equity ($M)", USD2), ("total_le", "Total L+E ($M)", USD2),
        ("balance_check", "BS Check ($M)", USD2),
    ])
    m.declare("Cash_Flow", [
        ("ni", "Net Income ($M)", USD2), ("amort", "Amortisation ($M)", USD2),
        ("comm_paid", "Commissions Paid ($M)", USD2), ("d_ar", "ΔAR ($M)", USD2),
        ("d_prepaid", "ΔPrepaid ($M)", USD2), ("d_ap", "ΔAP ($M)", USD2),
        ("d_accrued", "ΔAccrued ($M)", USD2), ("d_deferred", "ΔDeferred ($M)", USD2),
        ("cfo", "CF from Ops ($M)", USD2), ("cfi", "CF from Inv ($M)", USD2),
        ("cff", "CF from Fin ($M)", USD2), ("net_change", "Net ΔCash ($M)", USD2),
        ("opening", "Open Cash ($M)", USD2), ("closing", "Close Cash ($M)", USD2),
        ("cf_check", "CF Check ($M)", USD2),
    ])
    kpi_cols = [(k, KPI_DEFS[k][0], KPI_DEFS[k][1]) for k in schema.get("kpis", [])]
    kpi_cols += [("net_burn", "Net Burn ($M)", USD2),
                 ("net_new_arr", "Net New ARR ($M)", USD2),
                 ("flag_runway", "Runway Flag", None), ("flag_nrr", "NRR Flag", None),
                 ("flag_gm", "Gross Margin Flag", None), ("flag_payback", "CAC Payback Flag", None),
                 ("flag_series_a", "Series A ARR Gate", None)]
    m.declare("KPI", kpi_cols)

    # ── Customers ────────────────────────────────────────────────────────────
    def customers(r):
        f = {
            "t_live": f"({T('m1', r)}-adds_pipeline_lag_m)/12",
            "season": f"IF(ROUNDUP(MONTH({T('start', r)})/3, 0)=4, adds_seasonality_q4, 1)",
            "churn_trend": (f"IF(churn_ramp_end_year<=1, churn_terminal_mult, "
                            f"1+(churn_terminal_mult-1)*MIN(1, MAX(0, {T('m0', r)}/12-1)"
                            f"/(churn_ramp_end_year-1)))"),
            "total": f"{X('Customers', 'close_a', r)}+{X('Customers', 'close_b', r)}",
        }
        for t, opening in (("a", "g_opening_customers"), ("b", "0")):
            C = lambda k: X("Customers", f"{k}_{t}", r)  # noqa: E731
            pts = [(f"adds_{t}_m{i}_year", f"adds_{t}_m{i}_value") for i in (1, 2, 3)]
            rate = interp(X("Customers", "t_live", r), pts)
            f[f"open_{t}"] = prev("Customers", f"close_{t}", r, opening)
            f[f"adds_{t}"] = (f"IF({T('m1', r)}<=adds_pipeline_lag_m, 0, {rate}"
                              f"*{T('months', r)}*{X('Customers', 'season', r)}*{scn['adds_mult']})")
            f[f"young_{t}"] = (f"IF({C('open')}=0, 0, MIN(1, {C('adds')}/{T('months', r)}*12"
                               f"/{C('open')}))")
            f[f"rate_{t}"] = (f"MIN(1, MAX(0, churn_logo_{t}_monthly+{scn['churn_delta_pp']}/100)"
                              f"*{X('Customers', 'churn_trend', r)}"
                              f"*(1+(churn_y1_uplift-1)*{C('young')}))")
            f[f"churned_{t}"] = f"{C('open')}*(1-(1-{C('rate')})^{T('months', r)})"
            f[f"close_{t}"] = f"{C('open')}+{C('adds')}-{C('churned')}"
        return f
    m.fill("Customers", customers)

    # ── Revenue ──────────────────────────────────────────────────────────────
    def revenue(r):
        R = lambda k: X("Revenue", k, r)  # noqa: E731
        mo = T("months", r)
        f = {
            "price_idx": f"(1+arpu_escalator)^({T('m0', r)}/12)",
            "bill_factor": "bill_monthly_share+bill_annual_share*(1-bill_annual_disc)",
            "mrr_open": f"{R('mrr_open_a')}+{R('mrr_open_b')}",
            "mrr_close": f"{R('mrr_close_a')}+{R('mrr_close_b')}",
            "arr": f"{R('mrr_close')}*12",
            "sub_rev": f"({R('mrr_open')}+{R('mrr_close')})/2*{mo}",
            "new_acv": f"({R('new_a')}+{R('new_b')})*12",
            "svc_rev": f"rev_services_pct*{R('new_acv')}",
            "total_rev": f"{R('sub_rev')}+{R('svc_rev')}",
            "deferred": (f"bill_annual_share*(1-bill_annual_disc)/{R('bill_factor')}"
                         f"*{R('mrr_close')}*12/2"),
            "billings": f"{R('total_rev')}+{delta('Revenue', 'deferred', r)}",
        }
        for t, opening in (("a", "g_opening_arr/12"), ("b", "0")):
            cust = lambda k: X("Customers", f"{k}_{t}", r)  # noqa: E731
            f[f"mrr_open_{t}"] = prev("Revenue", f"mrr_close_{t}", r, opening)
            f[f"ret_{t}"] = (f"IF({cust('open')}=0, 0, {R(f'mrr_open_{t}')}"
                             f"*(1-{cust('churned')}/{cust('open')}))")
            f[f"exp_{t}"] = (f"{R(f'ret_{t}')}*((1+exp_net_expansion_mo)^{mo}"
                             f"*(1+arpu_escalator)^({mo}/12)-1)")
            f[f"new_{t}"] = (f"{cust('adds')}*arpu_{t}_monthly*{R('price_idx')}"
                             f"*(1-arpu_discount_new)*{scn['arpu_mult']}*{R('bill_factor')}/1000000")
            f[f"mrr_close_{t}"] = f"{R(f'ret_{t}')}+{R(f'exp_{t}')}+{R(f'new_{t}')}"
        return f
    m.fill("Revenue", revenue)

    # ── Headcount ────────────────────────────────────────────────────────────
    def headcount(r):
        H = lambda k: X("Headcount", k, r)  # noqa: E731
        f = {"total_n": "+".join(H(f"n_{x}") for x in ROLES),
             "total_cost": "+".join(H(f"cost_{x}") for x in ROLES)}
        for x in ROLES:
            ramp = interp(f"{T('m1', r)}/12", [("hc_ramp_start_year", f"hc_{x}_count_m1"),
                                               ("hc_ramp_end_year", f"hc_{x}_count_m2")])
            f[f"n_{x}"] = f"IF({T('m1', r)}<hc_{x}_start_month, 0, {ramp})"
            f[f"cost_{x}"] = (f"{H(f'n_{x}')}*hc_{x}_salary*(1+hc_oncost_pct)"
                              f"*(1+hc_salary_inflation)^({T('m0', r)}/12)*{T('months', r)}/12/1000")
        return f
    m.fill("Headcount", headcount)

    # ── OpEx ─────────────────────────────────────────────────────────────────
    def opex(r):
        O = lambda k: X("OpEx", k, r)  # noqa: E731
        R = lambda k: X("Revenue", k, r)  # noqa: E731
        H = lambda k: X("Headcount", k, r)  # noqa: E731
        mo = T("months", r)
        return {
            "esc": f"(1+ga_escalation)^({T('m0', r)}/12)",
            "hosting": f"{R('sub_rev')}*cogs_hosting_pct",
            "support": f"{R('sub_rev')}*cogs_support_pct",
            "thirdparty": f"{R('sub_rev')}*cogs_thirdparty_pct",
            "svc_cost": f"{R('svc_rev')}*(1-rev_services_margin)",
            "cogs": f"{O('hosting')}+{O('support')}+{O('thirdparty')}+{O('svc_cost')}",
            "cac_idx": f"(1-sm_cac_improve_yr)^({T('m0', r)}/12)",
            "cac_spend": (f"({R('new_a')}*sm_cac_ratio_a+{R('new_b')}*sm_cac_ratio_b)*12"
                          f"*{O('cac_idx')}"),
            "comm_cap": f"MIN({O('cac_spend')}, sm_commission_pct*{R('new_acv')})",
            "marketing": f"sm_marketing_base*{mo}/1000*{O('esc')}",
            "sm": (f"{O('cac_spend')}-{O('comm_cap')}+{X('Capitalised', 'comm_amort', r)}"
                   f"+{O('marketing')}+{H('cost_sales')}+{H('cost_cs')}"),
            "rd_tools": f"{H('n_eng')}*rd_tools_per_eng*{mo}/12/1000*{O('esc')}",
            "rd_gross": f"{H('cost_eng')}+{H('cost_prod')}+{O('rd_tools')}",
            "dev_cap": f"{O('rd_gross')}*rd_capitalise_pct",
            "rd": f"{O('rd_gross')}-{O('dev_cap')}",
            "ga_nonpeople": (f"({H('total_n')}*(ga_office_per_head+ga_software_per_head)"
                             f"+ga_prof_fees+ga_insurance)*{mo}/12/1000*{O('esc')}"),
            "ga_other": f"{R('total_rev')}*ga_other_pct_rev",
            "bad_debt": f"{R('billings')}*bill_bad_debt_pct",
            "ga": f"{H('cost_ga')}+{O('ga_nonpeople')}+{O('ga_other')}+{O('bad_debt')}",
            "opex": f"{O('sm')}+{O('rd')}+{O('ga')}",
            "nonpeople": (f"{O('cogs')}+{O('cac_spend')}-{O('comm_cap')}+{O('marketing')}"
                          f"+{O('rd_tools')}+{O('ga_nonpeople')}+{O('ga_other')}"),
        }
    m.fill("OpEx", opex)

    # ── Capitalised costs ────────────────────────────────────────────────────
    def capitalised(r):
        K = lambda k: X("Capitalised", k, r)  # noqa: E731
        mo = T("months", r)
        return {
            "comm_open": prev("Capitalised", "comm_close", r, "0"),
            "comm_add": X("OpEx", "comm_cap", r),
            "comm_amort": f"{K('comm_open')}*MIN(1, {mo}/sm_commission_amort)",
            "comm_close": f"{K('comm_open')}+{K('comm_add')}-{K('comm_amort')}",
            "dev_open": prev("Capitalised", "dev_close", r, "0"),
            "dev_add": X("OpEx", "dev_cap", r),
            "dev_amort": f"{K('dev_open')}*MIN(1, {mo}/(rd_amort_years*12))",
            "dev_close": f"{K('dev_open')}+{K('dev_add')}-{K('dev_amort')}",
        }
    m.fill("Capitalised", capitalised)

    # ── Working capital ──────────────────────────────────────────────────────
    def working_capital(r):
        mo = T("months", r)
        O = lambda k: X("OpEx", k, r)  # noqa: E731
        return {
            "ar": f"{X('Revenue', 'billings', r)}/{mo}*wc_ar_days/30",
            "prepaid": f"{O('nonpeople')}/{mo}*wc_prepaid_months",
            "ap": f"{O('nonpeople')}/{mo}*wc_ap_days/30",
            "accrued": f"({O('cogs')}+{O('opex')})/{mo}*wc_accrued_pct",
            "deferred": X("Revenue", "deferred", r),
        }
    m.fill("Working_Capital", working_capital)

    W1 = lambda k: X("Working_Capital", k, first)  # noqa: E731
    ob_formulas = {
        "ar": f"{ob['mrr']}*wc_ar_days/30",
        "prepaid": W1("prepaid"), "ap": W1("ap"), "accrued": W1("accrued"),
        "deferred": (f"bill_annual_share*(1-bill_annual_disc)/{X('Revenue', 'bill_factor', first)}"
                     f"*{ob['mrr']}*12/2"),
        "net_wc": f"{ob['ar']}+{ob['prepaid']}-{ob['ap']}-{ob['accrued']}-{ob['deferred']}",
    }
    m.static_sheet("Opening_Balances", [(k, lbl, f or ob_formulas[k], fmt)
                                        for k, lbl, f, fmt in ob_rows])

    # ── P&L ──────────────────────────────────────────────────────────────────
    def pnl(r):
        P = lambda k: X("PnL", k, r)  # noqa: E731
        return {
            "revenue": X("Revenue", "total_rev", r),
            "cogs": X("OpEx", "cogs", r),
            "gross_profit": f"{P('revenue')}-{P('cogs')}",
            "sm": X("OpEx", "sm", r), "rd": X("OpEx", "rd", r), "ga": X("OpEx", "ga", r),
            "ebitda": f"{P('gross_profit')}-{P('sm')}-{P('rd')}-{P('ga')}",
            "da": X("Capitalised", "dev_amort", r),
            "ebit": f"{P('ebitda')}-{P('da')}",
            "int_income": (f"cash_interest_rate*MAX(0, {X('Funding', 'cash_open', r)})"
                           f"*{T('months', r)}/12"),
            "pre_tax": f"{P('ebit')}+{P('int_income')}",
            "nol_open": prev("PnL", "nol_close", r, "g_nol_opening"),
            "nol_used": f"IF({P('pre_tax')}>0, MIN({P('nol_open')}, {P('pre_tax')}), 0)",
            "nol_close": f"{P('nol_open')}-{P('nol_used')}+MAX(0, -{P('pre_tax')})",
            "tax": f"MAX(0, {P('pre_tax')}-{P('nol_used')})*g_tax_rate",
            "net_income": f"{P('pre_tax')}-{P('tax')}",
        }
    m.fill("PnL", pnl)

    # ── Funding: cash before and after the unplanned top-up ──────────────────
    def raise_in(n, r):
        return (f"IF(AND(raise{n}_month>{T('m0', r)}, raise{n}_month<={T('m1', r)}), "
                f"raise{n}_amount*(1-raise{n}_fees_pct), 0)")

    def funding(r):
        F = lambda k: X("Funding", k, r)  # noqa: E731
        K = lambda k: X("Capitalised", k, r)  # noqa: E731
        W = lambda k: delta("Working_Capital", k, r)  # noqa: E731
        return {
            "cash_open": prev("Balance_Sheet", "cash", r, "cash_opening"),
            "cfo": (f"{X('PnL', 'net_income', r)}+{K('comm_amort')}+{K('dev_amort')}"
                    f"-{K('comm_add')}-{W('ar')}-{W('prepaid')}+{W('ap')}+{W('accrued')}"
                    f"+{W('deferred')}"),
            "cfi": f"-{K('dev_add')}",
            "raise1": raise_in(1, r),
            "raise2": raise_in(2, r),
            "cash_before": f"{F('cash_open')}+{F('cfo')}+{F('cfi')}+{F('raise1')}+{F('raise2')}",
            "target": (f"MAX(cash_min_operating, cash_buffer_months*({X('OpEx', 'cogs', r)}"
                       f"+{X('OpEx', 'opex', r)})/{T('months', r)})"),
            "plug": f"MAX(0, {F('target')}-{F('cash_before')})",
            "cash_close": f"{F('cash_before')}+{F('plug')}",
        }
    m.fill("Funding", funding)

    # ── Balance sheet ────────────────────────────────────────────────────────
    def balance_sheet(r):
        B = lambda k: X("Balance_Sheet", k, r)  # noqa: E731
        F = lambda k: X("Funding", k, r)  # noqa: E731
        W = lambda k: X("Working_Capital", k, r)  # noqa: E731
        equity_in = f"{F('raise1')}+{F('raise2')}+{F('plug')}"
        return {
            "cash": F("cash_close"), "ar": W("ar"), "prepaid": W("prepaid"),
            "comm": X("Capitalised", "comm_close", r), "dev": X("Capitalised", "dev_close", r),
            "total_assets": f"{B('cash')}+{B('ar')}+{B('prepaid')}+{B('comm')}+{B('dev')}",
            "ap": W("ap"), "accrued": W("accrued"), "deferred": W("deferred"),
            "total_liab": f"{B('ap')}+{B('accrued')}+{B('deferred')}",
            "paid_in": f"{prev('Balance_Sheet', 'paid_in', r, 'cash_opening')}+{equity_in}",
            "retained": (f"{prev('Balance_Sheet', 'retained', r, ob['net_wc'])}"
                         f"+{X('PnL', 'net_income', r)}"),
            "total_equity": f"{B('paid_in')}+{B('retained')}",
            "total_le": f"{B('total_liab')}+{B('total_equity')}",
            "balance_check": f"{B('total_assets')}-{B('total_le')}",
        }
    m.fill("Balance_Sheet", balance_sheet)

    # ── Cash flow: rebuilt from balance-sheet movements ──────────────────────
    def cash_flow(r):
        C = lambda k: X("Cash_Flow", k, r)  # noqa: E731
        D = lambda k: delta("Balance_Sheet", k, r)  # noqa: E731
        F = lambda k: X("Funding", k, r)  # noqa: E731
        K = lambda k: X("Capitalised", k, r)  # noqa: E731
        return {
            "ni": X("PnL", "net_income", r),
            "amort": f"{K('comm_amort')}+{K('dev_amort')}",
            "comm_paid": f"-{K('comm_add')}",
            "d_ar": f"-{D('ar')}", "d_prepaid": f"-{D('prepaid')}",
            "d_ap": D("ap"), "d_accrued": D("accrued"), "d_deferred": D("deferred"),
            "cfo": "+".join(C(k) for k in ("ni", "amort", "comm_paid", "d_ar", "d_prepaid",
                                           "d_ap", "d_accrued", "d_deferred")),
            "cfi": f"-{K('dev_add')}",
            "cff": f"{F('raise1')}+{F('raise2')}+{F('plug')}",
            "net_change": f"{C('cfo')}+{C('cfi')}+{C('cff')}",
            "opening": prev("Cash_Flow", "closing", r, "cash_opening"),
            "closing": f"{C('opening')}+{C('net_change')}",
            "cf_check": f"{C('closing')}-{X('Balance_Sheet', 'cash', r)}",
        }
    m.fill("Cash_Flow", cash_flow)

    # ── Cap table (single values; each row may refer to earlier rows) ────────
    labels = [
        ("founders", "Founder shares (000s)", N0),
        ("esop", "ESOP pool (000s)", N0),
        ("fd_pre1", "Fully diluted before Round 1 (000s)", N0),
        ("price1", "Round 1 price ($/share)", X2),
        ("shares1", "Round 1 new shares (000s)", N0),
        ("fd_post1", "Fully diluted after Round 1 (000s)", N0),
        ("post1", "Round 1 post-money ($M)", USD2),
        ("price2", "Round 2 price ($/share)", X2),
        ("shares2", "Round 2 new shares (000s)", N0),
        ("fd_post2", "Fully diluted after Round 2 (000s)", N0),
        ("post2", "Round 2 post-money ($M)", USD2),
        ("unplanned", "Unplanned funding, all periods ($M)", USD2),
        ("price3", "Unplanned funding price ($/share)", X2),
        ("shares3", "Unplanned funding new shares (000s)", N0),
        ("fd_final", "Fully diluted at end of model (000s)", N0),
        ("pct_founders", "Founders at end of model", PCT1),
        ("pct_esop", "ESOP at end of model", PCT1),
        ("pct_r1", "Round 1 investors at end of model", PCT1),
        ("pct_r2", "Round 2 investors at end of model", PCT1),
        ("pct_r3", "Unplanned funding investors at end of model", PCT1),
    ]
    ct = {k: f"Cap_Table!$B${i}" for i, (k, _, _) in enumerate(labels, 2)}
    c = ct.get
    formulas = {
        "founders": "cap_founders_shares",
        "esop": f"{c('founders')}*cap_esop_pct/(1-cap_esop_pct)",
        "fd_pre1": f"{c('founders')}+{c('esop')}",
        "price1": f"raise1_premoney/{c('fd_pre1')}*1000",
        "shares1": f"raise1_amount/raise1_premoney*{c('fd_pre1')}",
        "fd_post1": f"{c('fd_pre1')}+{c('shares1')}",
        "post1": "raise1_premoney+raise1_amount",
        "price2": f"raise2_premoney/{c('fd_post1')}*1000",
        "shares2": f"raise2_amount/raise2_premoney*{c('fd_post1')}",
        "fd_post2": f"{c('fd_post1')}+{c('shares2')}",
        "post2": "raise2_premoney+raise2_amount",
        "unplanned": f"SUM({m.rng('Funding', 'plug')})",
        "price3": f"{c('price2')}*cap_unplanned_price_mult",
        "shares3": f"{c('unplanned')}/{c('price3')}*1000",
        "fd_final": f"{c('fd_post2')}+{c('shares3')}",
        "pct_founders": f"{c('founders')}/{c('fd_final')}",
        "pct_esop": f"{c('esop')}/{c('fd_final')}",
        "pct_r1": f"{c('shares1')}/{c('fd_final')}",
        "pct_r2": f"{c('shares2')}/{c('fd_final')}",
        "pct_r3": f"{c('shares3')}/{c('fd_final')}",
    }
    m.static_sheet("Cap_Table", [(k, lbl, formulas[k], fmt) for k, lbl, fmt in labels])

    # ── KPI ──────────────────────────────────────────────────────────────────
    def kpi(r):
        ctx = _KpiCtx(m, r, first)
        f = {k: KPI_DEFS[k][2](ctx) for k, _, _ in kpi_cols if k in KPI_DEFS}
        prev_arr = prev("Revenue", "arr", r, "g_opening_arr")
        f["net_burn"] = f"-({X('Funding', 'cfo', r)}+{X('Funding', 'cfi', r)})"
        f["net_new_arr"] = f"{X('Revenue', 'arr', r)}-{prev_arr}"
        f["flag_runway"] = (f'IF({ctx.runway()}="", "", IF({ctx.runway()}<cash_runway_alert_m, '
                            f'"below alert", ""))')
        f["flag_nrr"] = (f'IF({ctx.nrr()}="", "", IF({ctx.nrr()}<nrr_target_note, '
                         f'"below target", ""))')
        f["flag_gm"] = (f'IF({ctx.gm()}="", "", IF({ctx.gm()}<cogs_gm_floor_note, '
                        f'"below floor", ""))')
        f["flag_payback"] = (f'IF({ctx.payback()}="", "", IF({ctx.payback()}>sm_payback_note, '
                             f'"above target", ""))')
        f["flag_series_a"] = (f'IF({X("Funding", "raise2", r)}>0, IF({X("Revenue", "arr", r)}'
                              f'<raise2_arr_gate, "ARR below gate", "ARR meets gate"), "")')
        return f
    m.fill("KPI", kpi)


# ── KPI definitions: name -> (header, number format, formula builder) ─────────
class _KpiCtx:
    def __init__(self, m, r, first):
        self.m, self.r, self.first = m, r, first

    def x(self, sheet, key):
        return self.m.ref(sheet, key, self.r)

    def mo(self):
        return self.m.t("months", self.r)

    def prev_arr(self):
        return "g_opening_arr" if self.r == self.first else self.m.ref("Revenue", "arr", self.r - 1)

    def nrr(self):
        R = lambda k: self.x("Revenue", k)  # noqa: E731
        return (f"IF({R('mrr_open')}=0, \"\", (({R('ret_a')}+{R('exp_a')}+{R('ret_b')}"
                f"+{R('exp_b')})/{R('mrr_open')})^(12/{self.mo()}))")

    def gm(self):
        return (f"IF({self.x('PnL', 'revenue')}=0, \"\", "
                f"{self.x('PnL', 'gross_profit')}/{self.x('PnL', 'revenue')})")

    def acq_cost(self):
        """All acquisition cost: variable spend, fixed marketing, sales salaries."""
        return (f"({self.x('OpEx', 'cac_spend')}+{self.x('OpEx', 'marketing')}"
                f"+{self.x('Headcount', 'cost_sales')})")

    def payback(self):
        new = f"({self.x('Revenue', 'new_a')}+{self.x('Revenue', 'new_b')})"
        gp, rev = self.x("PnL", "gross_profit"), self.x("PnL", "revenue")
        return (f"IF(OR({rev}=0, {new}=0, {gp}<=0), \"\", "
                f"{self.acq_cost()}/({new}*{gp}/{rev}))")

    def runway(self):
        burn = f"-({self.x('Funding', 'cfo')}+{self.x('Funding', 'cfi')})"
        return f"IF({burn}<=0, \"\", {self.x('Balance_Sheet', 'cash')}/(({burn})/{self.mo()}))"


def _grr(c):
    R = lambda k: c.x("Revenue", k)  # noqa: E731
    return (f"IF({R('mrr_open')}=0, \"\", (({R('ret_a')}+{R('ret_b')})/{R('mrr_open')})"
            f"^(12/{c.mo()}))")


def _logo_churn(c):
    C = lambda k: c.x("Customers", k)  # noqa: E731
    opn, gone = f"({C('open_a')}+{C('open_b')})", f"({C('churned_a')}+{C('churned_b')})"
    return f"IF({opn}=0, \"\", 1-(1-{gone}/{opn})^(12/{c.mo()}))"


def _ltv_cac(c):
    C = lambda k: c.x("Customers", k)  # noqa: E731
    opn, gone = f"({C('open_a')}+{C('open_b')})", f"({C('churned_a')}+{C('churned_b')})"
    adds = f"({C('adds_a')}+{C('adds_b')})"
    rev, gp = c.x("PnL", "revenue"), c.x("PnL", "gross_profit")
    churn_mo = f"(1-(1-{gone}/{opn})^(1/{c.mo()}))"
    ltv = f"({c.x('Revenue', 'mrr_close')}/{C('total')}*{gp}/{rev}/{churn_mo})"
    cac = f"({c.acq_cost()}/{adds})"
    return (f"IF(OR({adds}=0, {opn}=0, {gone}=0, {C('total')}=0, {rev}=0), \"\", "
            f"{ltv}/{cac})")


def _burn_multiple(c):
    burn = f"-({c.x('Funding', 'cfo')}+{c.x('Funding', 'cfi')})"
    nna = f"({c.x('Revenue', 'arr')}-{c.prev_arr()})"
    return f"IF(OR({nna}<=0, {burn}<=0), \"\", ({burn})/{nna})"


def _rule_of_40(c):
    arr, rev = c.x("Revenue", "arr"), c.x("PnL", "revenue")
    return (f"IF(OR({c.prev_arr()}=0, {rev}=0), \"\", "
            f"({arr}/{c.prev_arr()})^(12/{c.mo()})-1+{c.x('PnL', 'ebitda')}/{rev})")


def _magic(c):
    sm = c.x("PnL", "sm")
    return f"IF({sm}=0, \"\", ({c.x('Revenue', 'arr')}-{c.prev_arr()})/{sm})"


KPI_DEFS = {
    "arr": ("ARR ($M)", USD2, lambda c: c.x("Revenue", "arr")),
    "mrr": ("MRR ($M)", USD2, lambda c: c.x("Revenue", "mrr_close")),
    "nrr": ("NRR (annualised)", PCT1, lambda c: c.nrr()),
    "grr": ("GRR (annualised)", PCT1, _grr),
    "logo_churn_annual": ("Logo Churn (annualised)", PCT1, _logo_churn),
    "cac_payback_months": ("CAC Payback (months)", "0.0", lambda c: c.payback()),
    "ltv_cac": ("LTV / CAC", "0.0", _ltv_cac),
    "burn_multiple": ("Burn Multiple", "0.00", _burn_multiple),
    "rule_of_40": ("Rule of 40", PCT1, _rule_of_40),
    "gross_margin": ("Gross Margin", PCT1, lambda c: c.gm()),
    "magic_number": ("Magic Number", "0.00", _magic),
    "runway_months": ("Runway (months)", "0.0", lambda c: c.runway()),
}

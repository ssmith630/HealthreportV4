"""Build the self-contained HTML dashboard (Carbon g10 look, no scripts, opens offline)."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from pathlib import Path

import health_score as hs
import plan_status
import quarter_compare
import renewals as renewals_mod
from quarters import Window, current_next_windows, quarter_label
from report_builder import health_class, ticket_summary

CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
:root{--bg:#f4f4f4;--panel:#fff;--text:#161616;--muted:#525252;--line:#e0e0e0;--blue:#0f62fe;--red:#da1e28;--green:#24a148;--yellow:#f1c21b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'IBM Plex Sans',-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.5;padding:2rem}
header{border-bottom:1px solid var(--line);padding-bottom:1.25rem;margin-bottom:1.5rem}
h1{font-size:2rem;font-weight:400}
.sub{color:var(--muted);font-weight:300}
html{scroll-behavior:smooth}
section[id]{scroll-margin-top:1rem}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1rem;margin-bottom:1.5rem}
.kpi{display:block;background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--accent,#393939);padding:1.25rem;min-height:104px;color:inherit;text-decoration:none}
a.kpi:hover{background:var(--tint,#e8e8e8);text-decoration:none}
a.kpi:focus-visible,.toc a:focus-visible{outline:2px solid var(--blue);outline-offset:-2px}
.kpi .l{font-size:.75rem;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.5px}
.kpi .v{font-size:1.75rem;font-weight:300;line-height:1.2;margin-top:.4rem;overflow-wrap:anywhere}
.kpi .v.red{color:var(--red)}.kpi .v.green{color:var(--green)}
.panel{background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--accent,#0f62fe);padding:1.5rem;margin-bottom:1.5rem;overflow-x:auto}
.panel h2{font-size:1.25rem;font-weight:500;color:#161616;background:var(--tint,#edf5ff);border-left:.5rem solid var(--accent,#0f62fe);margin:-1.5rem -1.5rem 1rem;padding:.8rem 1.5rem .8rem 1rem}
/* One IBM palette colour per section: accent (60-grade) for borders, tint (10-grade) for bands. Text stays #161616 for AA contrast. */
#mq{--accent:#8a3ffc;--tint:#f6f2ff}.s-cq,.k-cq{--accent:#198038;--tint:#defbe6}.s-opp,.k-opp{--accent:#0f62fe;--tint:#edf5ff}
.s-plan,.k-plan{--accent:#007d79;--tint:#d9fbfb}.s-tix,.k-tix{--accent:#d02670;--tint:#fff0f7}.s-dep,.k-dep{--accent:#ba4e00;--tint:#fff2e8}
.s-ela,.k-ela{--accent:#6929c4;--tint:#f6f2ff}.s-fc,.k-fc{--accent:#9f1853;--tint:#fff0f7}.s-rar,.k-rar{--accent:#a2191f;--tint:#fff1f1}
.s-note,.k-notes{--accent:#525252;--tint:#f4f4f4}.s-renew,.k-renew{--accent:#8e6a00;--tint:#fcf4d6}
.s-acct{--accent:#161616;--tint:#f4f4f4}.s-health,.k-health{--accent:#da1e28;--tint:#fff1f1}
.sub-block{margin:0 0 1.5rem;overflow-x:auto}
.sub-block h3{font-size:1rem;font-weight:500;background:var(--tint);border-left:.375rem solid var(--accent);padding:.5rem .75rem;margin-bottom:.5rem}
.top{font-size:.75rem;font-weight:400;float:right}
.hint{color:var(--muted);font-size:.8125rem;margin:-.5rem 0 1rem}
table{border-collapse:collapse;width:100%;font-size:.8125rem}
th{background:var(--tint,#e8e8e8);text-align:left;font-weight:600;padding:.55rem .6rem;white-space:nowrap;border-bottom:2px solid var(--accent,#c6c6c6)}
td{padding:.5rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:#f9f9f9}
td.num{text-align:right;white-space:nowrap}
td.dt{white-space:nowrap}
tr.over td{background:#fff1f1}
tr.over:hover td{background:#ffe5e5}
tr.stale td{background:#fcf4d6}
tr.stale:hover td{background:#f8ecb8}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.badge{display:inline-block;padding:.05rem .5rem;font-size:.75rem;font-weight:500;background:#e0e0e0;color:#161616}
.badge.red{background:var(--red);color:#fff}.badge.green{background:#defbe6;color:#0e6027}.badge.yellow{background:#fcf4d6;color:#684e00}
.badge.orange{background:#ffd9be;color:#5e2a00}.badge.grey{background:#e0e0e0;color:#161616}
.alert{background:#fff2e8;border-left:.375rem solid #ba4e00;padding:.6rem .9rem;margin:0 0 1rem;font-size:.8125rem}
.alert strong{font-weight:600}
.empty{color:var(--muted);padding:.5rem 0}
.toc{list-style:none}.toc li{padding:.35rem 0;border-bottom:1px solid var(--line)}
.panel h2 .top{float:right;margin-top:.15rem}
footer{color:var(--muted);font-size:.75rem;margin-top:1rem}
.cq-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1rem;margin-bottom:1.25rem}
.cq-tile{background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--accent,#198038);padding:1.25rem 1rem}
.cq-tile .l{font-size:1rem;font-weight:600;color:var(--text)}
.cq-tile .v{font-size:2.5rem;font-weight:300;line-height:1.2;margin-top:.75rem;white-space:nowrap}
.cq-tile .v .u{font-size:.6em}
.cq-tile .v2{font-size:1.75rem;font-weight:300;line-height:1.2;margin-top:.5rem;white-space:nowrap}
.cq-tile .v2.red{color:var(--red)}
.cq-tile .v2 .u{font-size:.6em}
.cq-tile .v2 .u2{font-size:.8125rem;font-weight:400;color:var(--muted)}
.cq-tile .v.red{color:var(--red)}
.tk-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;margin-bottom:1.25rem}
.tk-kpis .kpi{min-height:88px;padding:1rem}
.tk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}
.tk-card{border:1px solid var(--line);padding:1rem;background:var(--panel)}
.tk-card h3{font-size:1rem;font-weight:500}
.tk-card .hint{margin:.25rem 0 .75rem}
.bar-row{display:grid;grid-template-columns:minmax(90px,1.2fr) 3fr 2.5rem;gap:.5rem;align-items:center;font-size:.8125rem;padding:.2rem 0}
.bar-row .nm{overflow-wrap:anywhere}
.bar-row .bar{background:#f4f4f4;height:.75rem}
.bar-row .bar i{display:block;height:100%;background:var(--accent,#d02670)}
.bar-row .ct{text-align:right;font-variant-numeric:tabular-nums}
""" + quarter_compare.CSS


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _money(v) -> str:
    try:
        return f"${float(v or 0):,.0f}"
    except (TypeError, ValueError):
        return ""


def _acct(row: dict, urls: dict[str, str]) -> str:
    name = row.get("account") or ""
    url = row.get("account_url") or urls.get(name, "")
    name = row.get("account_display") or name
    if url.startswith("https://"):
        return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{_e(name)}</a>'
    return _e(name)


def _table(headers: list[str], rows: list[str], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_e(empty)}</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _cq_cell(v) -> str:
    """Money cell for the CQ table: blank when missing, red and in parentheses when negative."""
    if v is None:
        return '<td class="num"><span class="empty">&mdash;</span></td>'
    if v < 0:
        return f'<td class="num" style="color:var(--red)">({_money(-v)})</td>'
    return f'<td class="num">{_money(v)}</td>'


CQ_FIELDS = (("budget", "CQ Budget"), ("deployed", "CQ Deployed"), ("call", "CQ Call"), ("bw_budget", "CQ B/(W) Budget"),
             ("call_to_go", "CQ Call to go"), ("upside", "CQ Upside"), ("stretch", "CQ Stretch"),
             ("pipeline", "CQ Pipeline"))
CQ_TABLE_KEYS = ("budget", "deployed", "call", "bw_budget", "call_to_go", "upside", "stretch", "pipeline",
                 "fy_budget", "fy_deployed", "fy_call", "fy_call_to_go", "fy_pipeline")
CQ_TABLE_HEADERS = ["Account", "CQ Budget", "CQ Deployed", "CQ Call", "CQ B/(W) Budget", "CQ Call to go", "CQ Upside",
                    "CQ Stretch", "CQ Pipeline", "FY Budget", "FY Deployed", "FY Call", "FY Call to go", "FY Pipeline"]


def _compact(v: float) -> str:
    """Large-readout money: $511.9M, $6.1M, $1.9K, $950. The unit is wrapped so CSS can shrink it."""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= size:
            return f"${v / size:,.1f}<span class=\"u\">{unit}</span>"
    return f"${v:,.0f}"


def _cq_tiles(r: dict) -> str:
    """One large-font tile per CQ figure: blank as a dash, negatives red and in parentheses."""
    tiles = []
    for key, label in CQ_FIELDS:
        v = r.get(key)
        if v is None:
            val, cls = "&mdash;", ""
        elif v < 0:
            val, cls = f"({_compact(-v)})", " red"
        else:
            val, cls = _compact(v), ""
        tiles.append(f"<div class=\"cq-tile\"><div class=\"l\">{_e(label)}</div><div class=\"v{cls}\">{val}</div></div>")
    return "<div class=\"cq-tiles\">" + "".join(tiles) + "</div>"


def _link(url, text) -> str:
    url = url or ""
    if url.startswith("https://"):
        return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{_e(text)}</a>'
    return _e(text)


def build_html(data: dict, windows: list[Window], path: Path) -> Path:
    urls = {a.name: a.isc_url for a in data.get("accounts", [])}
    shown = {a.name: getattr(a, "isc_name", "") or a.name for a in data.get("accounts", [])}
    opps = sorted(data.get("opps", []), key=lambda r: (str(r.get("close_date")), r.get("account") or ""))
    plans = sorted(data.get("plans", []), key=lambda r: (plan_status.sort_key(r), r.get("account") or ""))
    tickets = sorted(data.get("tickets", []), key=lambda r: (r.get("account") or "", str(r.get("last_updated") or "")))
    ela = sorted(data.get("ela", []), key=lambda r: (r.get("account") or "", str(r.get("end_date") or ""),
                                                      str(r.get("section") or ""), str(r.get("platform") or "")))
    forecast = sorted(data.get("renewal_forecast", []), key=lambda r: (r.get("account") or "", str(r.get("next_renewal") or ""),
                                                                       str(r.get("product") or "")))
    deployments = data.get("deployments", [])
    rar = data.get("renewal_risk", [])
    notes = data.get("notes", [])
    overdue = [p for p in plans if p["overdue"]]
    stale = [p for p in plans if p.get("needs_update")]
    stale_days = data.get("stale_days", plan_status.STALE_DEFAULT)
    sections = data.get("sections") or {}
    show_opps = sections.get("opportunities", True)
    show_deploy = sections.get("deployments", True)
    show_gainsight = sections.get("gainsight", True)
    pipeline = sum(float(o.get("amount") or 0) for o in opps)
    deploy_total = sum(float(d.get("amount") or 0) for d in deployments)
    quarters = " + ".join(w.label for w in windows)
    all_dates = bool(data.get("deploy_all_dates"))
    dep_scope = "all dates" if all_dates else "current quarter"

    def kpi(label: str, value, target: str, cls: str = "", tone: str = "") -> str:
        """A top-row tile that scrolls to its section (plain anchor, no script). Its top border takes the section colour."""
        tone = f" k-{tone}" if tone else ""
        return (f'<a class="kpi{tone}" href="#{target}" aria-label="{_e(label)}: {_e(value)}. Go to section">'
                f'<div class="l">{_e(label)}</div><div class="v {cls}">{_e(value)}</div></a>')

    # Each account has its own section; the tiles scroll to the account index.
    scores = {s["account"]: s for s in data.get("health_scores", [])}
    at_risk = [s for s in scores.values() if s["rating"] == hs.AT_RISK]

    def score_badge(s: dict) -> str:
        text = hs.NO_DATA if s["score"] is None or s["rating"] == hs.NO_DATA else f'{s["score"]} {s["rating"]}'
        return f'<span class="badge {hs.RATING_BADGE[s["rating"]] or "grey"}">{_e(text)}</span>'

    kpis = "".join([
        kpi("Accounts", len(data.get("accounts", [])), "sec-accounts"),
        *([kpi("Accounts at risk", f"{len(at_risk)} / {_money(sum(float(s['arr'] or 0) for s in at_risk))} ARR",
               "sec-accounts", "red" if at_risk else "green", "health")] if scores else []),
        *([kpi("Open opportunities", len(opps), "sec-accounts", tone="opp"),
           kpi("Open pipeline", _money(pipeline), "sec-accounts", tone="opp")] if show_opps else []),
        *([kpi(f"Deployments ({dep_scope})", len(deployments), "sec-accounts", tone="dep"),
           kpi("Deployment forecast net license revenue", _money(deploy_total), "sec-accounts", tone="dep"),
           kpi("Renewal at risk (NQ, Red)", _money(sum(r["at_risk"] for r in rar)), "sec-rar", tone="rar")] if show_deploy else []),
        *([kpi("Open success plans", len(plans), "sec-accounts", tone="plan"),
           kpi("Overdue success plans", len(overdue), "sec-accounts", "red" if overdue else "green", "plan"),
           kpi(f"Success plans needing update ({stale_days}+ days)", len(stale), "sec-accounts",
               "red" if stale else "green", "plan"),
           kpi("Open support tickets", len(tickets), "sec-tickets", tone="tix"),
           kpi("ELA contract rows", len(ela), "sec-accounts", tone="ela"),
           kpi("Renewal forecast rows", len(forecast), "sec-accounts", tone="fc")] if show_gainsight else []),
        kpi("Notes", len(notes), "sec-notes", tone="notes"),
    ])

    # Detail rows are kept as (account, row html) so each account section can pick its own.
    opp_rows = []
    for o in opps:
        try:
            q = quarter_label(date.fromisoformat(str(o.get("close_date"))[:10]), windows)
        except ValueError:
            q = ""
        opp_rows.append((o.get("account"),
            f"<tr><td>{_link(o.get('record_url'), o.get('name'))}</td><td>{_e(o.get('owner'))}</td>"
            f"<td>{_e(o.get('parent_account_number'))}</td><td>{_e(o.get('next_step'))}</td>"
            f"<td>{_e(o.get('stage'))}</td><td class=\"dt\">{_e(o.get('created_date'))}</td>"
            f"<td class=\"dt\">{_e(o.get('close_date'))}</td><td class=\"num\">{_money(o.get('amount'))}</td>"
            f"<td class=\"num\">{_money(o.get('expected_revenue'))}</td><td>{_e(o.get('forecast_pipeline'))}</td>"
            f"<td>{_e(o.get('record_type'))}</td><td class=\"dt\">{_e(o.get('last_modified'))}</td>"
            f"<td class=\"num\">{_money(o.get('renewed_acv_total'))}</td>"
            f"<td><span class=\"badge\">{_e(q)}</span></td></tr>"))

    plan_rows = []
    for p in plans:
        attention = plan_status.attention(p)
        status = (f'<span class="badge red">{p["days_overdue"]} days overdue</span>' if attention == "Overdue"
                  else '<span class="badge yellow">Needs update</span>' if attention == "Needs update"
                  else '<span class="badge green">On track</span>')
        pct = p.get("percent_complete")
        pct_text = "" if pct in (None, "") else f"{pct}%"
        health = p.get("renewal_health") or ""
        health_cell = (f'<span class="badge {health_class(health)}">{_e(health)}</span>' if health
                       else '<span class="empty">&mdash;</span>')
        plan_rows.append((p.get("account"),
            f"<tr class=\"{'over' if p['overdue'] else 'stale' if attention == 'Needs update' else ''}\">"
            f"<td>{_link(p.get('plan_url'), p.get('name'))}</td>"
            f"<td>{_e(p.get('status'))}</td><td>{_e(p.get('owner'))}</td>"
            f"<td class=\"dt\">{_e(p.get('due_date'))}</td><td>{status}</td>"
            f"<td class=\"dt\">{_e(p.get('last_updated'))}</td><td class=\"num\">{_e(pct_text)}</td>"
            f"<td>{_e(p.get('progression'))}</td><td>{health_cell}</td></tr>"))

    ticket_rows = [(t.get("account"),
        f"<tr><td>{_e(t.get('status'))}</td><td>{_e(t.get('severity'))}</td><td>{_e(t.get('state'))}</td>"
        f"<td>{_e(t.get('brand'))}</td><td>{_e(t.get('product'))}</td>"
        f"<td>{_link(t.get('ticket_url'), t.get('ticket_id'))}</td>"
        f"<td class=\"dt\">{_e(t.get('created'))}</td><td class=\"dt\">{_e(t.get('last_updated'))}</td>"
        f"<td>{_e(t.get('summary'))}</td></tr>")
        for t in tickets]

    ela_rows = [(e.get("account"),
        f"<tr><td>{_e(e.get('contract'))}</td><td class=\"dt\">{_e(e.get('uci'))}</td>"
        f"<td>{_e(e.get('section'))}</td><td>{_e(e.get('platform'))}</td>"
        f"<td class=\"dt\">{_e(e.get('end_date'))}</td>"
        + "".join(f"<td class=\"num\">{_money(e.get(k))}</td>" for k in
                  ("entitled", "deployed", "undeployed", "declared_vs_expected", "rate_pace_target"))
        + "</tr>")
        for e in ela]

    def _score_cell(v):
        return '<span class="empty">&mdash;</span>' if v is None else f"{float(v):.1f}"

    forecast_rows = [(f.get("account"),
        f"<tr><td>{_e(f.get('company'))}</td><td>{_e(f.get('product'))}</td><td>{_e(f.get('license_type'))}</td>"
        f"<td class=\"dt\">{_e(f.get('next_renewal'))}</td><td class=\"num\">{_money(f.get('acv'))}</td>"
        f"<td class=\"num\">{_score_cell(f.get('health_score'))}</td><td>{_e(f.get('segment'))}</td>"
        f"<td>{_e(f.get('product_stage'))}</td><td>{_e(f.get('adoption_intent'))}</td>"
        f"<td>{'Yes' if f.get('adoption_plan') else 'No'}</td><td>{'Yes' if f.get('get_well_plan') else 'No'}</td></tr>")
        for f in forecast]

    deploy_rows = [(d.get("account"),
        f"<tr><td>{_link(d.get('record_url'), d.get('name'))}</td><td class=\"dt\">{_e(d.get('deployment_id'))}</td>"
        f"<td>{_e(d.get('status'))}</td><td class=\"dt\">{_e(d.get('date'))}</td>"
        f"<td>{_e(d.get('product'))}</td>"
        f"<td class=\"num\">{_money(d.get('amount'))}</td><td>{_e(d.get('stage'))}</td>"
        f"<td>{_e(d.get('state_of_engagement'))}</td>"
        f"<td class=\"dt\">{_e(d.get('target_decision_date'))}</td>"
        f"<td class=\"dt\">{_e(d.get('deployed_date'))}</td>"
        f"<td>{_e(d.get('owner'))}</td><td class=\"dt\">{_e(d.get('last_modified'))}</td>"
        f"<td>{_link(d.get('record_url'), 'Open in Salesforce')}</td></tr>")
        for d in deployments]

    note_rows = [
        f"<tr><td>{_e(n.get('area'))}</td><td>{_e(n.get('account_display') or n.get('account'))}</td><td>{_e(n.get('note'))}</td></tr>"
        for n in notes]

    cq = data.get("cq_window")
    cq_rows = []
    cq_by_acct: dict[str, str] = {}
    cq_total_tiles = ""
    for r in data.get("cq_summary", []):
        total = r["account"] == "TOTAL"
        cells = "".join(_cq_cell(r.get(k)) for k in CQ_TABLE_KEYS)
        name = "<strong>TOTAL</strong>" if total else _acct(r, urls)
        style = ' style="font-weight:600"' if total else ""
        cq_rows.append(f"<tr{style}><td>{name}</td>{cells}</tr>")
        if total:
            cq_total_tiles = _cq_tiles(r)
        else:
            cq_by_acct[r["account"]] = _cq_tiles(r)
    cq_title = f"Deployment forecast ({_e(cq.label)} and fiscal year)" if cq else "Deployment forecast"

    cq_section = f"""<section class="panel s-cq" id="sec-cq"><h2>{cq_title}</h2>
<p class="hint">From the Seller and Manager Deployment Forecast and Renewals dashboard. CQ is the current quarter and FY is the fiscal year. Blank means the dashboard has no value for that account. See Notes. The tiles show the total across the selected accounts; the table shows each account.</p>
{cq_total_tiles}
{_table(CQ_TABLE_HEADERS, cq_rows, "No accounts selected.")}</section>""" if show_deploy else ""
    rar_rows = []
    for r in rar:
        pct = "" if r.get("pct_at_risk") is None else f"{r['pct_at_risk']:.0%}"
        rar_rows.append((r["account"], f"<tr><td>{_e(r.get('names'))}</td><td class=\"num\">{_money(r['renewal'])}</td>"
                                       f"<td class=\"num\">{_money(r['at_risk'])}</td><td class=\"num\">{pct}</td>"
                                       f"<td class=\"num\">{'' if r['lines'] is None else r['lines']}</td></tr>"))
    rar_table_rows = [
        f"<tr><td>{_acct(r, urls)}</td><td>{_e(r.get('names'))}</td><td class=\"num\">{_money(r['renewal'])}</td>"
        f"<td class=\"num\">{_money(r['at_risk'])}</td>"
        f"<td class=\"num\">{'' if r.get('pct_at_risk') is None else format(r['pct_at_risk'], '.0%')}</td>"
        f"<td class=\"num\">{'' if r['lines'] is None else r['lines']}</td></tr>" for r in rar]
    if rar:
        rar_table_rows.append(f"<tr style=\"font-weight:600\"><td>TOTAL</td><td></td>"
                              f"<td class=\"num\">{_money(sum(r['renewal'] for r in rar))}</td>"
                              f"<td class=\"num\">{_money(sum(r['at_risk'] for r in rar))}</td><td></td>"
                              f"<td class=\"num\">{sum(r['lines'] or 0 for r in rar)}</td></tr>")
    rq = data.get("renewal_quarters") or []
    next4 = data.get("renewal_next4") or {}
    rq_cols = ("NQ Renewal", "NQ RED", "NQ+1 Renewal", "NQ+1 RED", "NQ+2 Renewal", "NQ+2 RED", "NQ+3 Renewal", "NQ+3 RED")
    rq_tiles = ""
    if next4:
        rq_tiles += '<h3>Next 4 quarters, by at-risk indicator</h3><div class="cq-tiles">' + "".join(
            f"<div class=\"cq-tile\"><div class=\"l\">{_e(label)}</div><div class=\"v\">"
            f"{'&mdash;' if key not in next4 else _compact(next4[key])}</div></div>"
            for key, label in (("Red", "Red"), ("Yellow", "Yellow"), ("Green", "Green"), ("None", "No indicator"))) + "</div>"
    if rq:
        def rq_sum(col: str) -> float:
            return sum(float(r.get(col) or 0) for r in rq)
        rq_tiles += '<h3>By quarter, selected accounts</h3><div class="cq-tiles">' + "".join(
            f"<div class=\"cq-tile\"><div class=\"l\">{label}</div>"
            f"<div class=\"v2\">{_compact(rq_sum(ren))}<span class=\"u2\"> Renewal</span></div>"
            f"<div class=\"v2 red\">{_compact(rq_sum(red))}<span class=\"u2\"> At risk (Red)</span></div></div>"
            for label, ren, red in (("NQ", rq_cols[0], rq_cols[1]), ("NQ+1", rq_cols[2], rq_cols[3]),
                                    ("NQ+2", rq_cols[4], rq_cols[5]), ("NQ+3", rq_cols[6], rq_cols[7]))) + "</div>"
    rq_table_rows = [f"<tr><td>{_acct(r, urls)}</td>" + "".join(f"<td class=\"num\">{_money(r.get(c))}</td>" for c in rq_cols)
                     + "</tr>" for r in rq]
    if rq:
        rq_table_rows.append("<tr style=\"font-weight:600\"><td>TOTAL</td>"
                             + "".join(f"<td class=\"num\">{_money(sum(float(r.get(c) or 0) for r in rq))}</td>" for c in rq_cols)
                             + "</tr>")
    rq_table = (f"<h3>By account</h3>{_table(['Account', *rq_cols], rq_table_rows, '')}" if rq else "")
    rar_section = f"""<section class="panel s-rar" id="sec-rar"><h2>Renewal at risk</h2>
<p class="hint">From the Renewal At Risk page of the Seller and Manager Deployment Forecast and Renewals dashboard. NQ is the next quarter, NQ+1 to NQ+3 the quarters after it, and At risk is the Red health indicator. The next 4 quarters tiles are the dashboard's own totals for its filters; the quarter tiles and the By account table cover the selected accounts. See Notes.</p>
{rq_tiles}{rq_table}
<h3>Next quarter detail (Red health indicator)</h3>
<p class="hint">Red renewal lines only, added up per account. The dashboard returns at most 10,000 lines sorted by account name, so accounts late in the alphabet can be missing from the detail; the By Account table of the page fills in those accounts when the report could read it (the Red lines column is blank for them).</p>
{_table(["Account", "Dashboard account names", "Renewal amount (NQ)", "At-risk amount (NQ)", "% at risk", "Red lines"], rar_table_rows, "No Red renewal lines were found for the selected accounts. See Notes below.")}</section>""" if show_deploy else ""
    # Cross-account overview: the quarter comparison only. Detail tables live in each account's section below.
    opp_section = quarter_compare.render(data.get("opps_all", data.get("opps", [])),
                                         data.get("quarter_windows") or current_next_windows(), urls) if show_opps else ""
    renewals = data.get("renewals", [])
    renewal_q_rows = [
        f"<tr><td><span class=\"badge\">{_e(q['quarter'])}</span></td><td class=\"num\">{q['accounts']}</td>"
        f"<td class=\"num\">{_money(q['arr'])}</td></tr>"
        for q in data.get("renewals_by_quarter", [])]
    renewal_rows = []
    for r in sorted(renewals, key=lambda r: (r["renewal_date"] or "9999", r["account"] or "")):
        days = r["days_to_renewal"]
        when = ('<span class="empty">&mdash;</span>' if days is None
                else f'{-days} days past' if days < 0 else f'{days} days')
        band = r["band"]
        band_cell = f'<span class="badge {renewals_mod.BAND_BADGE[band]}">{_e(band)}</span>'
        renewal_rows.append((r.get("account"),
            f"<tr class=\"{'over' if days is not None and days < 0 else ''}\">"
            f"<td class=\"dt\">{_e(r['renewal_date'])}</td><td>{_e(r['quarter'])}</td><td>{when}</td><td>{band_cell}</td>"
            f"<td class=\"num\">{_money(r['arr']) if r['arr'] is not None else ''}</td>"
            f"<td>{_e(r['stage'])}</td><td>{_e(r['owner'])}</td></tr>"))
    ts = ticket_summary(tickets)

    def ticket_kpi(label: str, value) -> str:
        return f'<div class="kpi k-tix"><div class="l">{_e(label)}</div><div class="v">{_e(value)}</div></div>'

    def breakdown(title: str, key: str) -> str:
        items = ts[key][:ts["top"]]
        peak = max((n for _, n in items), default=1)
        bars = "".join(
            f'<div class="bar-row"><span class="nm">{_e(name)}</span>'
            f'<span class="bar"><i style="width:{max(2, round(100 * n / peak))}%"></i></span>'
            f'<span class="ct">{n}</span></div>' for name, n in items)
        return (f'<div class="tk-card"><h3>{_e(title)}</h3>'
                f'<p class="hint">{ts["total"]} cases across {ts["clients"]} clients &bull; top {ts["top"]} shown</p>'
                f'{bars or "<p class=empty>No cases.</p>"}</div>')

    ticket_section = f"""<section class="panel s-tix" id="sec-tickets"><h2>Support cases</h2>
<p class="hint">Tickets that are still open (Open, IBM is working or awaiting customer feedback), whatever their age. Closed tickets are not shown. Severity, brand, product and state come from the Gainsight ticket report; blank values show as Unspecified. See Notes if they are missing.</p>
<div class="tk-kpis">{ticket_kpi("Open cases", ts["total"])}{ticket_kpi("Severity 1", ts["sev1"])}{ticket_kpi("Severity 2", ts["sev2"])}{"".join(ticket_kpi(s, n) for s, n in ts["states"].items())}</div>
<div class="tk-grid">{breakdown("Cases by client", "by_client")}{breakdown("Cases by brand", "by_brand")}{breakdown("Cases by product", "by_product")}</div>
</section>""" if show_gainsight else ""

    concentration = data.get("renewal_concentration", [])
    concentration_alert = "".join(
        f'<div class="alert" role="note"><strong>Renewal concentration in {_e(m["month"])}:</strong> '
        f'{m["accounts"]} accounts renew, {_money(m["arr"])} ARR. {_e(", ".join(m["names"]))}.</div>'
        for m in concentration)
    renewal_section = f"""<section class="panel s-renew" id="sec-renew"><h2>Renewals by quarter</h2>
<p class="hint">Renewal date and ARR from each account's earliest open ISC renewal opportunity (close date and amount). "Earlier" holds renewal dates before the first report quarter. Each account's own renewal is in its section below. Risk bands: Past due, 0-90 days, 91-180 days, Over 180 days. An alert shows when {renewals_mod.CONCENTRATION_MIN} or more accounts renew in the same month.</p>
{concentration_alert}
{_table(["Renewal Quarter", "Accounts Renewing", "ARR Renewing"], renewal_q_rows,
        "No open ISC renewal opportunities were found. See Notes below.")}</section>""" if show_opps else ""

    # One section per account. Accounts come from the selection; any account that only appears in the data is added too.
    names = [a.name for a in data.get("accounts", [])]
    for rows in (opp_rows, deploy_rows, plan_rows, ticket_rows, ela_rows, forecast_rows, renewal_rows, rar_rows):
        for name, _ in rows:
            if name and name not in names:
                names.append(name)

    def mine(rows, name):
        return [html for acct, html in rows if acct == name]

    def block(cls: str, title: str, headers: list[str], rows: list[str], empty: str, hint: str = "") -> str:
        return (f'<div class="sub-block {cls}"><h3>{_e(title)} ({len(rows)})</h3>{hint}'
                f'{_table(headers, rows, empty)}</div>')

    account_sections = []
    toc = []
    for n, name in enumerate(names, 1):
        sid = f"acct-{n}"
        o, d, p, t, rn, el = (mine(opp_rows, name), mine(deploy_rows, name), mine(plan_rows, name),
                              mine(ticket_rows, name), mine(renewal_rows, name), mine(ela_rows, name))
        fc = mine(forecast_rows, name)
        rr = mine(rar_rows, name)
        parts = []
        if show_opps:
            parts.append(block("s-opp", "Open opportunities",
                               ["Opportunity Name", "Opportunity Owner", "Parent Account Number", "Next Steps", "Stage",
                                "Created Date", "Close Date", "Amount", "Expected Revenue", "Forecast Pipeline",
                                "Opportunity Record Type", "Last Modified Date", "Renewed ACV Total", "Quarter"],
                               o, "No open opportunities were found. See Notes below."))
            parts.append(block("s-renew", "ISC renewal",
                               ["Renewal Date", "Renewal Quarter", "Time to Renewal", "Risk Band", "ARR", "Stage",
                                "Opportunity Owner"],
                               rn, "No open ISC renewal opportunity was found. See Notes below."))
        if show_deploy:
            if name in cq_by_acct:
                parts.append(f"<div class=\"sub-block s-cq\"><h3>Deployment forecast</h3>{cq_by_acct[name]}</div>")
            parts.append(block("s-rar", "Renewal at risk (next quarter, Red)",
                               ["Dashboard account names", "Renewal amount (NQ)", "At-risk amount (NQ)", "% at risk",
                                "Red lines"], rr, "No Red renewal lines were found. See Notes below."))
            parts.append(block("s-dep", f"Deployments{' (all dates)' if all_dates else ' (' + cq.label + ')' if cq else ''}",
                               ["Deployment Name", "Deployment ID", "Status", "Date",
                                "Product", "Forecast Net License Revenue", "Stage", "State of Engagement",
                                "Target Decision Date", "Date Deployed to Production",
                                "Owner", "Last Modified Date", "Salesforce Link"], d,
                               f"No deployments were found for {'the current quarter' if not all_dates else 'these accounts'}. See Notes below."))
        if show_gainsight:
            s = scores.get(name)
            if s:
                no_data = '<span class="empty">No data</span>'
                weights = "".join(
                    f'<tr><td>{_e(label)}</td><td class="num">{w}</td>'
                    f'<td class="num">{no_data if s["points"][k] is None else s["points"][k]}</td></tr>'
                    for k, (label, w) in hs.INPUTS.items())
                flags = (f'<div class="alert" role="note"><strong>Flags that cap the rating at Watch:</strong> '
                         f'{_e(", ".join(s["flags"]))}.</div>' if s["flags"] else "")
                parts.append(
                    f'<div class="sub-block s-health"><h3>Health score {score_badge(s)}</h3>{flags}'
                    f'<p class="hint">Weighted average of the inputs that have data. Coverage {s["coverage"]}% of the total weight. '
                    f'Below {hs.MIN_COVERAGE}% shows Not enough data.</p>'
                    f'{_table(["Input", "Weight", "Points (0-100)"], [weights], "")}</div>')
            parts.append(block("s-plan", "Gainsight success plans",
                               ["Plan Name", "Plan Status", "Plan Owners", "Due Date", "Timeline", "Last Updated",
                                "Percent Complete", "Progression", "Renewal Health"],
                               p, "No success plans were found. See Notes below."))
            parts.append(block("s-tix", "Support tickets",
                               ["Status", "Severity", "State", "Brand", "Product", "Ticket ID", "Date Created",
                                "Last Date Updated", "Summary"],
                               t, "No support tickets were found. See Notes below."))
            parts.append(block("s-ela", "ELA details",
                               ["Contract Name", "UCI", "Contract Section", "Platform", "Contract End Date",
                                "Entitled (Net Revenue)", "Deployed (Net Revenue)", "Undeployed Value",
                                "Declared vs Expected Progress", "Rate Pace Target"],
                               el, "No ELA details were found. See Notes below."))
            parts.append(block("s-fc", "Renewal forecast",
                               ["Company", "Product", "License Type", "Next Renewal Date", "ACV (USD)", "Health Score",
                                "Segment", "Product Stage", "Adoption Intent", "Active Adoption Plan",
                                "Active Get Well Plan"],
                               fc, "No renewal forecast rows were found. See Notes below."))
        account_notes = [x for x in notes if x.get("account") == name]
        counts = []
        if show_opps:
            counts.append(f"{len(o)} opportunities")
        if show_deploy:
            counts.append(f"{len(d)} deployments")
            counts.append(f"{len(rr)} renewal at risk")
        if show_gainsight:
            counts.append(f"{len(p)} success plans")
            counts.append(f"{len(t)} tickets")
            counts.append(f"{len(el)} ELA rows")
            counts.append(f"{len(fc)} renewal forecast rows")
        if account_notes:
            counts.append(f"{len(account_notes)} notes")
        health = f" {score_badge(scores[name])}" if name in scores else ""
        toc.append(f'<li><a href="#{sid}">{_e(shown.get(name, name))}</a>{health} <span class="sub">{_e(", ".join(counts))}</span></li>')
        account_sections.append(
            f'<section class="panel s-acct" id="{sid}"><h2>{_acct({"account": name, "account_display": shown.get(name, name)}, urls)}'
            f' <a class="top" href="#sec-accounts">Back to accounts</a></h2>{"".join(parts)}</section>')
    toc_section = (f'<section class="panel s-acct" id="sec-accounts"><h2>Accounts</h2>'
                   f'<ul class="toc">{"".join(toc)}</ul></section>') if names else ""
    gainsight_sections = renewal_section + ticket_section
    subtitle = " &middot; ".join(
        ([f"Open opportunities and renewals for {_e(quarters)}"] if show_opps else [])
        + (["Deployment forecast and renewal at risk from the dashboard"] if show_deploy else [])
        + (["Deployments for all dates" if all_dates else f"Deployments for {_e(cq.label)}" if cq else "Deployments"] if show_deploy else [])
        + (["Gainsight success plans, support tickets, ELA details and renewal forecast"] if show_gainsight else [])) or "Account health"
    # Order: cross-account overview (quarter comparison, CQ forecast, renewals by quarter), account index,
    # one section per account, then notes.
    body = f"""
<header><h1>Industry Market Account Health Report</h1>
<p class="sub">{subtitle} &middot; Generated {datetime.now():%Y-%m-%d %H:%M}</p></header>
<section class="kpis">{kpis}</section>
{opp_section}
{cq_section}
{rar_section}
{gainsight_sections}
{toc_section}
{"".join(account_sections)}
<section class="panel s-note" id="sec-notes"><h2>Notes</h2>
{_table(["Area", "Account", "Note"], note_rows, "No issues. All sources returned data.")}</section>
<footer>Account, opportunity and deployment names open the record in IBM Sales Cloud when the saved report supplies its record Id.<br>
Sources: {_e("; ".join(data.get("sources", [])) or "none")}. Report date: {datetime.now():%Y-%m-%d}. Figures are as read from the sources at that time. Blank values mean the source returned none; see Notes.</footer>
"""
    doc = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
           f"<title>Industry Market Account Health Report</title><style>{CSS}</style></head><body>{body}</body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path

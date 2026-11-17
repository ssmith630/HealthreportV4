"""Deployments view: fetch, normalise and render deployment records (current quarter by default, any date on request).

Deployment records come from the saved ISC Deployments report (``isc.Isc.deployments``). The Seller Forecast
dashboard supplies the "Seller View" figures (CQ Budget, Deployed, Call, B/(W) Budget, Call to go, Upside,
Stretch) shown above the table. The dashboard has no per-deployment rows of its own.

Each record gets three derived fields on top of the report columns:
- ``deployment_id``: the Salesforce record Id behind the deployment Name link, else the Name.
- ``status``: "Deployed" once a production date exists, otherwise the Stage (else State of Engagement).
- ``date``: the production date when deployed, otherwise the target decision date.

``write_snapshot`` saves the latest result so the ``/deployments`` page can show it without re-running a report.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from browser import BrowserError
from html_builder import CSS, _e, _link, _money, _table
from isc import IscError

SNAPSHOT = Path(__file__).resolve().parent / "reports" / "deployments_latest.json"

SELLER_VIEW = (("CQ Budget", "budget"), ("CQ Deployed", "deployed"), ("CQ Call", "call"),
               ("CQ B/(W) Budget", "bw_budget"), ("CQ Call to go", "call_to_go"), ("CQ Upside", "upside"),
               ("CQ Stretch", "stretch"))
HEADERS = ["Deployment ID", "Deployment Name", "Account", "Status", "Date",
           "Product", "Forecast Net License Revenue", "Stage", "Owner", "Salesforce Link"]


def deployment_id(row: dict) -> str:
    """Salesforce record Id from the record URL, else the deployment Name."""
    url = (row.get("record_url") or "").rstrip("/")
    tail = url.rsplit("/", 1)[-1] if url else ""
    return tail or row.get("name") or ""


def enrich(rows: list[dict]) -> list[dict]:
    """Add deployment_id, status and date to each row (idempotent). Returns the same list."""
    for r in rows:
        r["deployment_id"] = deployment_id(r)
        r["status"] = "Deployed" if r.get("deployed_date") else (r.get("stage") or r.get("state_of_engagement") or "Planned")
        r["date"] = r.get("deployed_date") or r.get("target_decision_date") or ""
    return rows


def fetch(isc, accounts, cfg: dict, cq, progress=None) -> tuple[list[dict], list[dict]]:
    """Deployments from the saved ISC report; cq=None keeps every date. Failures become notes, never exceptions."""
    if not cfg.get("deploy_report_url"):
        return [], [{"area": "Deployments", "account": "",
                     "note": "No deployments report link is saved. Paste an ISC Deployments report link "
                             "on the Connect tab (Report links) to include deployments."}]
    try:
        rows, notes = isc.deployments(accounts, cfg["deploy_report_url"], progress, cq=cq)
    except (IscError, BrowserError) as e:
        return [], [{"area": "Deployments", "account": "", "note": str(e)}]
    return enrich(rows), notes


def summary(rows: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.get("status") or "Unspecified"] = by_status.get(r.get("status") or "Unspecified", 0) + 1
    return {"count": len(rows), "value": sum(float(r.get("amount") or 0) for r in rows),
            "deployed": by_status.get("Deployed", 0), "by_status": by_status}


def write_snapshot(data: dict, demo: bool, report_html: str = "") -> Path:
    """Save the latest deployments plus the Seller View total for the /deployments page."""
    cq = data.get("cq_window")
    total = next((r for r in data.get("cq_summary", []) if r.get("account") == "TOTAL"), {})
    notes = [n for n in data.get("notes", []) if n.get("area") in ("Deployments", "CQ forecast")]
    snap = {"generated": datetime.now().isoformat(timespec="seconds"), "demo": demo, "report_html": report_html,
            "quarter": cq.label if cq else "", "all_dates": bool(data.get("deploy_all_dates")),
            "seller_view": {k: total.get(k) for _, k in SELLER_VIEW},
            "deployments": data.get("deployments", []), "notes": notes}
    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return SNAPSHOT


def read_snapshot() -> dict | None:
    if not SNAPSHOT.exists():
        return None
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except ValueError:
        return None


def snapshot_from_demo(accounts, windows) -> dict:
    """The same shape as read_snapshot(), built from the built-in sample data (no sign-in needed)."""
    import cq_metrics
    from demo_data import demo_data
    data = demo_data(accounts, windows)
    enrich(data["deployments"])
    total = next((r for r in cq_metrics.summarize(data["cq_figures"], accounts) if r["account"] == "TOTAL"), {})
    return {"generated": datetime.now().isoformat(timespec="seconds"), "demo": True, "report_html": "",
            "quarter": windows[0].label, "seller_view": {k: total.get(k) for _, k in SELLER_VIEW},
            "deployments": data["deployments"], "notes": []}


def _tile(label: str, value) -> str:
    if value is None:
        shown, cls = "&mdash;", ""
    elif value < 0:
        shown, cls = f"({_money(-value)})", " red"
    else:
        shown, cls = _money(value), ""
    return f'<div class="kpi k-dep"><div class="l">{_e(label)}</div><div class="v{cls}">{shown}</div></div>'


def render(snap: dict | None) -> str:
    """Standalone HTML page for the deployments view."""
    if not snap:
        body = ('<header><h1>Deployments</h1></header><section class="panel s-dep"><h2>No deployments yet</h2>'
                '<p class="empty">Generate a report from the main page first, or open '
                '<a href="/deployments?demo=1">the demo view</a>.</p></section>')
        return _page(body)
    rows = enrich(list(snap.get("deployments", [])))
    s = summary(rows)
    sv = snap.get("seller_view") or {}
    quarter = snap.get("quarter") or "current quarter"
    table_rows = [
        f"<tr><td class=\"dt\">{_e(r['deployment_id'])}</td><td>{_link(r.get('record_url'), r.get('name'))}</td>"
        f"<td>{_e(r.get('account'))}</td><td><span class=\"badge{' green' if r['status'] == 'Deployed' else ''}\">{_e(r['status'])}</span></td>"
        f"<td class=\"dt\">{_e(r['date'])}</td>"
        f"<td>{_e(r.get('product'))}</td><td class=\"num\">{_money(r.get('amount'))}</td><td>{_e(r.get('stage'))}</td>"
        f"<td>{_e(r.get('owner'))}</td><td>{_link(r.get('record_url'), 'Open in Salesforce')}</td></tr>"
        for r in sorted(rows, key=lambda r: (r.get("date") or "9999", r.get("account") or "", r.get("name") or ""))]
    notes = [f"<tr><td>{_e(n.get('area'))}</td><td>{_e(n.get('account'))}</td><td>{_e(n.get('note'))}</td></tr>"
             for n in snap.get("notes", [])]
    tag = " &middot; <strong>Sample data</strong>" if snap.get("demo") else ""
    all_dates = bool(snap.get("all_dates"))
    scope = "All deployment dates" if all_dates else quarter
    status_tiles = "".join(f'<div class="kpi k-dep"><div class="l">{_e(k)}</div><div class="v">{n}</div></div>'
                           for k, n in sorted(s["by_status"].items()))
    body = f"""
<header><h1>Deployments</h1>
<p class="sub">{_e(scope)} &middot; Generated {_e(str(snap.get('generated', '')).replace('T', ' '))}{tag}</p></header>
<section class="panel s-cq"><h2>Seller View ({_e(quarter)})</h2>
<p class="hint">Totals across the selected accounts from the Deployment Forecast dashboard. Blank means the dashboard returned no value. Week-to-date figures are not read.</p>
<div class="kpis">{"".join(_tile(label, sv.get(key)) for label, key in SELLER_VIEW)}</div></section>
<section class="panel s-dep" id="sec-deployments"><h2>Deployment records</h2>
<div class="tk-kpis"><div class="kpi k-dep"><div class="l">Deployments</div><div class="v">{s['count']}</div></div>
<div class="kpi k-dep"><div class="l">Deployment forecast net license revenue</div><div class="v">{_money(s['value'])}</div></div>{status_tiles}</div>
{_table(HEADERS, table_rows, "No deployments were found" + ("" if all_dates else " for the current quarter") + ". See Notes below.")}</section>
<section class="panel s-note"><h2>Notes</h2>{_table(["Area", "Account", "Note"], notes, "No issues.")}</section>
<footer><a href="/">Back to the report builder</a> &middot; Deployment names open the record in IBM Sales Cloud when the saved report supplies its record Id.</footer>
"""
    return _page(body)


def _page(body: str) -> str:
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
            f"<title>Deployments</title><style>{CSS}</style></head><body>{body}</body></html>")

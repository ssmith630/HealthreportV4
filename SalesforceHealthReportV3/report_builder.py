"""Build the Excel report with openpyxl."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import health_score as hs
import plan_status
import renewals as renewals_mod
from quarters import Window, quarter_label

HEADER_FILL = PatternFill("solid", fgColor="161616")
HEADER_FONT = Font(bold=True, color="FFFFFF", name="Calibri")
OVERDUE_FILL = PatternFill("solid", fgColor="FFD7D9")
OVERDUE_FONT = Font(bold=True, color="A2191F")
WARN_FILL = PatternFill("solid", fgColor="FDF1C6")
THIN = Side(style="thin", color="E0E0E0")
BORDER = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)

OPP_HEADERS = ["Account", "Opportunity Name", "Opportunity Owner", "Parent Account Number", "Next Steps", "Stage",
               "Created Date", "Close Date", "Amount", "Expected Revenue", "Forecast Pipeline",
               "Opportunity Record Type", "Last Modified Date", "Renewed ACV Total", "Quarter"]
OPP_WIDTHS = [28, 42, 22, 20, 50, 14, 14, 14, 16, 16, 18, 24, 16, 18, 10]
OPP_DATES = {"Created Date", "Close Date", "Last Modified Date"}
OPP_MONEY = {"Amount", "Expected Revenue", "Renewed ACV Total"}

PLAN_HEADERS = ["Account", "Plan Name", "Plan Status", "Plan Owners", "Due Date", "Days Overdue", "Overdue?",
                "Renewal Health", "Last Updated", "Days Since Update", "Needs Update?", "Percent Complete",
                "Open Tasks", "Previous Status", "Progression"]
TICKET_HEADERS = ["Account", "Status", "Severity", "State", "Product", "Ticket ID", "Ticket URL",
                  "Date Created", "Last Date Updated", "Summary"]
ELA_HEADERS = ["Account", "Contract Name", "UCI", "Contract Section", "Platform", "Contract End Date",
               "Entitled (Net Revenue)", "Deployed (Net Revenue)", "Undeployed Value",
               "Declared vs Expected Progress", "Rate Pace Target"]
ELA_KEYS = ["entitled", "deployed", "undeployed", "declared_vs_expected", "rate_pace_target"]
FORECAST_HEADERS = ["Account", "Company", "Product", "License Type", "Next Renewal Date", "ACV (USD)", "Health Score",
                    "Segment", "Product Stage", "Adoption Intent", "Active Adoption Plan", "Active Get Well Plan"]
DEPLOY_HEADERS = ["Account", "Deployment Name", "Deployment ID", "Status", "Date",
                  "Product", "Forecast Net License Revenue", "Stage", "State of Engagement",
                  "Target Decision Date", "Date Deployed to Production",
                  "Owner", "Last Modified Date", "Salesforce Link"]
DEPLOY_DATES = {"Date", "Target Decision Date", "Date Deployed to Production", "Last Modified Date"}


CQ_HEADERS = ["Account", "CQ Budget", "CQ Deployed", "CQ Call", "CQ B/(W) Budget", "CQ Call to go", "CQ Upside",
              "CQ Stretch", "CQ Pipeline", "FY Budget", "FY Deployed", "FY Call", "FY Call to go", "FY Pipeline"]
CQ_KEYS = ["budget", "deployed", "call", "bw_budget", "call_to_go", "upside", "stretch", "pipeline", "fy_budget",
           "fy_deployed", "fy_call", "fy_call_to_go", "fy_pipeline"]
RAR_HEADERS = ["Account", "Dashboard account names", "Renewal amount (NQ)", "At-risk amount (NQ)", "% at risk",
               "Red lines"]
RAR_QUARTER_HEADERS = ["Account", "NQ Renewal", "NQ RED", "NQ+1 Renewal", "NQ+1 RED", "NQ+2 Renewal", "NQ+2 RED",
                       "NQ+3 Renewal", "NQ+3 RED"]


TICKET_STATES = ("Open", "IBM is working", "Awaiting customer feedback")


def ticket_summary(tickets: list[dict], top: int = 10) -> dict:
    """Counts behind the ticket dashboard: KPI tiles plus the client and product breakdowns."""
    def count(key: str, shown: str = "") -> list[tuple[str, int]]:
        c: dict[str, int] = {}
        for t in tickets:
            v = (shown and t.get(shown)) or t.get(key) or "Unspecified"
            c[v] = c.get(v, 0) + 1
        return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))

    by_client, by_product = count("account", "account_display"), count("product")
    return {
        "total": len(tickets),
        "sev1": sum(1 for t in tickets if t.get("severity") == "Severity 1"),
        "sev2": sum(1 for t in tickets if t.get("severity") == "Severity 2"),
        "states": {s: sum(1 for t in tickets if t.get("state") == s) for s in TICKET_STATES},
        "clients": len(by_client),
        "top": top,
        "by_client": by_client, "by_product": by_product,
    }


def health_class(value) -> str:
    """Map an ISC Renewal Health value to 'red', 'yellow', 'green' or '' (unknown wording)."""
    v = str(value or "").lower()
    if any(w in v for w in ("critical", "risk", "red", "poor", "unhealthy", "churn")):
        return "red"
    if any(w in v for w in ("watch", "caution", "yellow", "amber", "fair", "moderate", "neutral")):
        return "yellow"
    if any(w in v for w in ("healthy", "green", "good", "strong", "on track")):
        return "green"
    return ""


HEALTH_FILLS = {"red": (PatternFill("solid", fgColor="FFD7D9"), Font(bold=True, color="A2191F")),
                "yellow": (PatternFill("solid", fgColor="FCF4D6"), Font(bold=True, color="684E00")),
                "green": (PatternFill("solid", fgColor="DEFBE6"), Font(bold=True, color="0E6027"))}


def _d(value):
    """Convert ISO strings to date objects so Excel treats them as dates."""
    if isinstance(value, (date, datetime)):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value or ""


# Section colours (match the HTML report): tab colour and header fill per sheet.
SECTION_COLORS = {"Open Opps": "0F62FE", "Success Plans": "007D79", "Support Tickets": "D02670", "Account Health": "DA1E28",
                  "Ticket Summary": "D02670", "ELA Details": "6929C4", "Renewal Forecast": "9F1853",
                  "Deployments": "BA4E00", "CQ Forecast": "198038", "Renewal At Risk": "A2191F", "Renewals": "8E6A00", "Notes": "525252"}

# Sheet order, matching the HTML report: opportunities, then deployments (with the CQ forecast), then Gainsight.
SHEET_ORDER = ("Summary", "Account Health", "Open Opps", "CQ Forecast", "Deployments", "Renewal At Risk", "Renewals by Quarter", "Renewals",
               "Success Plans", "Ticket Summary", "Support Tickets", "ELA Details", "Renewal Forecast", "Notes")

RENEWAL_HEADERS = ["Account", "Renewal Date", "Renewal Quarter", "Days to Renewal", "Risk Band", "ARR", "Stage",
                   "Opportunity Owner"]
RENEWAL_Q_HEADERS = ["Renewal Quarter", "Accounts Renewing", "ARR Renewing"]


def _sheet(wb: Workbook, title: str, headers: list[str], widths: list[int], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    color = next((c for k, c in SECTION_COLORS.items() if title.startswith(k)), "161616")
    ws.sheet_properties.tabColor = color
    header_fill = PatternFill("solid", fgColor=color)
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in ws[1]:
        c.fill, c.font, c.border = header_fill, HEADER_FONT, BORDER
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"
    return ws


def _record_link(cell, url) -> None:
    """Make the cell open the ISC record when the report supplied a record link."""
    if isinstance(url, str) and url.startswith("https://"):
        cell.hyperlink = url
        cell.font = Font(color="0F62FE", underline="single")


def _finish(ws, n_cols: int) -> None:
    ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{max(ws.max_row, 1)}"


def build_report(data: dict, windows: list[Window], path: Path) -> Path:
    opps = sorted(data.get("opps", []), key=lambda r: (str(r.get("close_date")), r.get("account") or ""))
    plans = sorted(data.get("plans", []), key=plan_status.sort_key)
    stale_days = data.get("stale_days", plan_status.STALE_DEFAULT)
    tickets = sorted(data.get("tickets", []), key=lambda r: (r.get("account") or "", str(r.get("last_updated") or "")))
    ela = sorted(data.get("ela", []), key=lambda r: (r.get("account") or "", r.get("uci") or "",
                                                     r.get("section") or "", r.get("platform") or ""))
    forecast = sorted(data.get("renewal_forecast", []), key=lambda r: (r.get("account") or "", str(r.get("next_renewal") or ""),
                                                                       r.get("product") or ""))
    deployments = data.get("deployments", [])
    notes = data.get("notes", [])
    overdue = [p for p in plans if p["overdue"]]
    stale = [p for p in plans if p.get("needs_update")]
    sections = data.get("sections") or {}
    show_opps = sections.get("opportunities", True)
    show_deploy = sections.get("deployments", True)
    show_gainsight = sections.get("gainsight", True)

    scores = data.get("health_scores", [])
    wb = Workbook()

    # Summary ------------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 22
    ws["A1"] = "Industry Market Account Health Report"
    ws["A1"].font = Font(bold=True, size=16, color="0F62FE")
    ws.sheet_properties.tabColor = "0F62FE"
    ws["A2"] = f"Generated {datetime.now():%Y-%m-%d %H:%M}"
    ws["A2"].font = Font(color="525252")
    rows = [
        ("Accounts reported", len(data.get("accounts", []))),
        ("Quarters", " + ".join(w.label for w in windows)),
        *([("Open opportunities", len(opps)),
           ("Open pipeline (USD)", sum(float(o.get("amount") or 0) for o in opps))] if show_opps else []),
        *([("Deployments (all dates)" if data.get("deploy_all_dates") else "Deployments (current quarter)", len(deployments)),
           ("Deployment forecast net license revenue (USD)", sum(float(d.get("amount") or 0) for d in deployments))] if show_deploy else []),
        *([("Open success plans", len(plans)),
           ("Overdue success plans", len(overdue)),
           (f"Success plans needing update (no change in {stale_days}+ days)", len(stale)),
           ("Open support tickets", len(tickets)),
           ("ELA contract rows", len(ela)),
           ("Renewal forecast rows", len(forecast)),
           ("Accounts at risk (health score)", sum(1 for s in scores if s["rating"] == hs.AT_RISK)),
           ("Health score weights", ", ".join(f"{label} {w}" for label, w in hs.INPUTS.values()))] if show_gainsight else []),
        ("Items needing attention (see Notes)", len(notes)),
    ]
    for i, (k, v) in enumerate(rows, 4):
        ws.cell(i, 1, k).font = Font(bold=True)
        c = ws.cell(i, 2, v)
        c.alignment = Alignment(horizontal="left")
        if k.startswith("Open pipeline") or k.startswith("Deployment forecast net license revenue"):
            c.number_format = '"$"#,##0'
        if (k.startswith("Overdue") or k.startswith("Success plans needing update")) and v:
            c.font = OVERDUE_FONT
            c.fill = OVERDUE_FILL

    # Accounts the report was run against (name links to the ISC account page)
    accts = sorted(data.get("accounts", []), key=lambda a: a.name.lower())
    r0 = 4 + len(rows) + 1
    ws.cell(r0, 1, "Accounts included").font = Font(bold=True)
    for i, a in enumerate(accts, r0 + 1):
        c = ws.cell(i, 1, getattr(a, "isc_name", "") or a.name)
        url = a.isc_url
        if url.startswith("https://"):
            c.hyperlink = url
            c.font = Font(color="0F62FE", underline="single")
    r1 = r0 + len(accts) + 2
    ws.cell(r1, 1, "Data sources").font = Font(bold=True)
    for i, s in enumerate(data.get("sources", []), r1 + 1):
        ws.cell(i, 1, s)
    ws.cell(r1 + len(data.get("sources", [])) + 1, 1, f"Report date: {datetime.now():%Y-%m-%d}").font = Font(color="525252")

    # Open opportunities ---------------------------------------------------
    ws = _sheet(wb, "Open Opps by Quarter", OPP_HEADERS, OPP_WIDTHS)
    for o in opps:
        close = _d(o.get("close_date"))
        q = quarter_label(close, windows) if isinstance(close, date) else ""
        ws.append([
            o.get("account_display") or o.get("account"), o.get("name"), o.get("owner"), o.get("parent_account_number"), o.get("next_step"),
            o.get("stage"), _d(o.get("created_date")), close, float(o.get("amount") or 0),
            float(o.get("expected_revenue") or 0), o.get("forecast_pipeline"), o.get("record_type"),
            _d(o.get("last_modified")), float(o.get("renewed_acv_total") or 0), q])
        _record_link(ws.cell(ws.max_row, 2), o.get("record_url"))
    next_col = OPP_HEADERS.index("Next Steps")
    for row in ws.iter_rows(min_row=2):
        for i, h in enumerate(OPP_HEADERS):
            if h in OPP_DATES:
                row[i].number_format = "yyyy-mm-dd"
            elif h in OPP_MONEY:
                row[i].number_format = '"$"#,##0'
        row[next_col].alignment = Alignment(wrap_text=True, vertical="top")
    _finish(ws, len(OPP_HEADERS))  # filter covers data rows only, not the total row
    if opps:
        n = ws.max_row
        ws.append([])
        tot_row = ["TOTAL", f"{len(opps)} opportunities"] + [None] * (len(OPP_HEADERS) - 2)
        for h in OPP_MONEY:
            col = OPP_HEADERS.index(h)
            tot_row[col] = f"=SUBTOTAL(109,{get_column_letter(col + 1)}2:{get_column_letter(col + 1)}{n})"
        ws.append(tot_row)
        tot = ws.max_row
        for c in ws[tot]:
            c.font = Font(bold=True)
        for h in OPP_MONEY:
            ws.cell(tot, OPP_HEADERS.index(h) + 1).number_format = '"$"#,##0'

    # Renewals (ISC renewal opportunities) -----------------------------------
    ws = _sheet(wb, "Renewals by Quarter", RENEWAL_Q_HEADERS, [20, 20, 18])
    for q in data.get("renewals_by_quarter", []):
        ws.append([q["quarter"], q["accounts"], q["arr"]])
        ws.cell(ws.max_row, 3).number_format = '"$"#,##0'
    if not data.get("renewals"):
        ws.append(["No open ISC renewal opportunities. See Notes."])
    ws = _sheet(wb, "Renewals", RENEWAL_HEADERS, [28, 14, 16, 16, 16, 16, 18, 24])
    for r in sorted(data.get("renewals", []), key=lambda r: (r["renewal_date"] or "9999", r["account"] or "")):
        ws.append([r.get("account_display") or r["account"], _d(r["renewal_date"]), r["quarter"], r["days_to_renewal"], r["band"],
                   r["arr"], r["stage"], r["owner"]])
        row = ws.max_row
        ws.cell(row, 2).number_format = "yyyy-mm-dd"
        ws.cell(row, 6).number_format = '"$"#,##0'
        if r["days_to_renewal"] is not None and r["days_to_renewal"] < 0:
            ws.cell(row, 4).font = OVERDUE_FONT
            ws.cell(row, 4).fill = OVERDUE_FILL
            ws.cell(row, 5).font = OVERDUE_FONT
            ws.cell(row, 5).fill = OVERDUE_FILL
    _finish(ws, len(RENEWAL_HEADERS))
    conc = data.get("renewal_concentration", [])
    if conc:  # months where several accounts renew together
        ws.append([])
        ws.append([f"Renewal concentration: months with {renewals_mod.CONCENTRATION_MIN} or more accounts renewing"])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        ws.append(["Month", "Accounts", "ARR", "Account names"])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
        for m in conc:
            ws.append([m["month"], m["accounts"], m["arr"], ", ".join(m["names"])])
            ws.cell(ws.max_row, 3).number_format = '"$"#,##0'

    # Success plans --------------------------------------------------------
    ws = _sheet(wb, "Success Plans", PLAN_HEADERS, [28, 44, 18, 28, 14, 14, 11, 18, 14, 16, 14, 16, 12, 18, 44])
    for p in plans:
        health = p.get("renewal_health") or ""
        ws.append([p.get("account_display") or p.get("account"), p.get("name"), p.get("status"), p.get("owner"), _d(p.get("due_date")),
                   p["days_overdue"] if p["overdue"] else 0, "OVERDUE" if p["overdue"] else "On track", health,
                   _d(p.get("last_updated")), p.get("days_since_update"),
                   "Yes" if p.get("needs_update") else "No", p.get("percent_complete"), p.get("open_tasks"),
                   p.get("previous_status") or "", p.get("progression") or ""])
        _record_link(ws.cell(ws.max_row, 2), p.get("plan_url"))
        if p["overdue"]:
            for c in ws[ws.max_row]:
                c.fill = OVERDUE_FILL
            ws.cell(ws.max_row, 7).font = OVERDUE_FONT
            ws.cell(ws.max_row, 6).font = OVERDUE_FONT
        elif p.get("needs_update"):
            for c in ws[ws.max_row]:
                c.fill = WARN_FILL
            ws.cell(ws.max_row, 11).font = Font(bold=True, color="8E6A00")
        if health_class(health) in HEALTH_FILLS:
            fill, font = HEALTH_FILLS[health_class(health)]
            ws.cell(ws.max_row, 8).fill, ws.cell(ws.max_row, 8).font = fill, font
    for row in ws.iter_rows(min_row=2):
        row[4].number_format = row[8].number_format = "yyyy-mm-dd"
    _finish(ws, len(PLAN_HEADERS))

    # Support tickets ------------------------------------------------------
    ws = _sheet(wb, "Support Tickets", TICKET_HEADERS, [28, 16, 12, 18, 24, 14, 52, 14, 16, 70])
    for t in tickets:
        ws.append([t.get("account_display") or t.get("account"), t.get("status"), t.get("severity"), t.get("state"),
                   t.get("product"), t.get("ticket_id"), t.get("ticket_url"),
                   _d(t.get("created")), _d(t.get("last_updated")), t.get("summary")])
        url = t.get("ticket_url") or ""
        if url.startswith("https://"):
            cell = ws.cell(ws.max_row, 7)
            cell.hyperlink = url
            cell.font = Font(color="0F62FE", underline="single")
    for row in ws.iter_rows(min_row=2):
        row[7].number_format = row[8].number_format = "yyyy-mm-dd"
        row[9].alignment = Alignment(wrap_text=True, vertical="top")
    _finish(ws, len(TICKET_HEADERS))

    # Ticket summary: KPI counts and the cases by client and product -----------------
    ts = ticket_summary(tickets)
    ws = _sheet(wb, "Ticket Summary", ["Measure", "Cases"], [34, 12])
    for label, n in [("Open cases", ts["total"]), ("Severity 1", ts["sev1"]), ("Severity 2", ts["sev2"]),
                     *ts["states"].items()]:
        ws.append([label, n])
    for title, key in (("Cases by client", "by_client"), ("Cases by product", "by_product")):
        ws.append([])
        ws.append([f"{title} (top {ts['top']} of {len(ts[key])})", "Cases"])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
        for name, n in ts[key][:ts["top"]]:
            ws.append([name, n])

    # ELA details: one row per account, contract section and platform --------------------
    ws = _sheet(wb, "ELA Details", ELA_HEADERS, [28, 46, 18, 32, 12, 16, 20, 20, 18, 22, 18])
    for e in ela:
        ws.append([e.get("account_display") or e.get("account"), e.get("contract"), e.get("uci"), e.get("section"), e.get("platform"),
                   _d(e.get("end_date")), *[e.get(k) or 0 for k in ELA_KEYS]])
        row = ws.max_row
        ws.cell(row, 6).number_format = "yyyy-mm-dd"
        for i in range(7, 7 + len(ELA_KEYS)):
            ws.cell(row, i).number_format = '"$"#,##0;[Red]("$"#,##0)'
    _finish(ws, len(ELA_HEADERS))

    # Renewal forecast: one row per account, product and license type -----------------------
    ws = _sheet(wb, "Renewal Forecast", FORECAST_HEADERS, [28, 28, 34, 14, 18, 14, 14, 14, 16, 16, 18, 18])
    for f in forecast:
        hs_val = f.get("health_score")
        ws.append([f.get("account_display") or f.get("account"), f.get("company"), f.get("product"), f.get("license_type"),
                   _d(f.get("next_renewal")), f.get("acv") or 0, hs_val if hs_val is not None else "",
                   f.get("segment"), f.get("product_stage"), f.get("adoption_intent"),
                   "Yes" if f.get("adoption_plan") else "No", "Yes" if f.get("get_well_plan") else "No"])
        row = ws.max_row
        ws.cell(row, 5).number_format = "yyyy-mm-dd"
        ws.cell(row, 6).number_format = '"$"#,##0;[Red]("$"#,##0)'
        ws.cell(row, 7).number_format = "0.0"
    _finish(ws, len(FORECAST_HEADERS))

    # Deployments ----------------------------------------------------------
    ws = _sheet(wb, "Deployments", DEPLOY_HEADERS,
                [28, 30, 20, 14, 14, 30, 16, 14, 22, 18, 24, 22, 18, 52])
    for d in deployments:
        link = d.get("record_url") or ""
        ws.append([d.get("account_display") or d.get("account"), d.get("name"), d.get("deployment_id"), d.get("status"), _d(d.get("date")),
                   d.get("product"), d.get("amount"), d.get("stage"),
                   d.get("state_of_engagement"), _d(d.get("target_decision_date")),
                   _d(d.get("deployed_date")),
                   d.get("owner"), _d(d.get("last_modified")), link])
        _record_link(ws.cell(ws.max_row, 2), link)
        _record_link(ws.cell(ws.max_row, len(DEPLOY_HEADERS)), link)
    for row in ws.iter_rows(min_row=2):
        for i, h in enumerate(DEPLOY_HEADERS):
            if h in DEPLOY_DATES:
                row[i].number_format = "yyyy-mm-dd"
            elif h == "Forecast Net License Revenue":
                row[i].number_format = '"$"#,##0'
    _finish(ws, len(DEPLOY_HEADERS))

    # CQ Forecast ----------------------------------------------------------
    cq = data.get("cq_window")
    cq_rows = data.get("cq_summary", [])
    ws = _sheet(wb, "CQ Forecast", CQ_HEADERS, [28, 16, 16, 16, 18, 16, 16, 16, 16, 16, 16, 16, 16, 16])
    ws.title = f"CQ Forecast {cq.label}"[:31] if cq else "CQ Forecast"
    for r in cq_rows:
        ws.append([r.get("account_display") or r["account"]] + [r[k] for k in CQ_KEYS])
        row = ws.max_row
        total = r["account"] == "TOTAL"
        if r.get("account_url", "").startswith("https://"):
            ws.cell(row, 1).hyperlink = r["account_url"]
            ws.cell(row, 1).font = Font(color="0F62FE", underline="single")
        for i, k in enumerate(CQ_KEYS, 2):
            c = ws.cell(row, i)
            c.number_format = '"$"#,##0;[Red]("$"#,##0)'
            if total:
                c.font = Font(bold=True)
        if total:
            ws.cell(row, 1).font = Font(bold=True)
    _finish(ws, len(CQ_HEADERS))
    if not cq_rows:
        ws.append(["No accounts selected."])

    # Renewal At Risk -------------------------------------------------------
    ws = _sheet(wb, "Renewal At Risk", RAR_HEADERS, [28, 44, 20, 20, 12, 12])
    rar = data.get("renewal_risk", [])
    for r in rar:
        ws.append([r.get("account_display") or r["account"], r.get("names"), r.get("renewal"), r.get("at_risk"), r.get("pct_at_risk"), r.get("lines")])
        row = ws.max_row
        if str(r.get("account_url", "")).startswith("https://"):
            ws.cell(row, 1).hyperlink = r["account_url"]
            ws.cell(row, 1).font = Font(color="0F62FE", underline="single")
        for col in (3, 4):
            ws.cell(row, col).number_format = '"$"#,##0;[Red]("$"#,##0)'
        ws.cell(row, 5).number_format = "0%"
    if rar:
        ws.append(["TOTAL", "", sum(r["renewal"] for r in rar), sum(r["at_risk"] for r in rar), None, sum(r["lines"] or 0 for r in rar)])
        for col in (1, 3, 4, 6):
            ws.cell(ws.max_row, col).font = Font(bold=True)
        for col in (3, 4):
            ws.cell(ws.max_row, col).number_format = '"$"#,##0;[Red]("$"#,##0)'
    _finish(ws, len(RAR_HEADERS))
    if not rar:
        ws.append(["No Red renewal rows for the selected accounts. See Notes."])

    # Renewal At Risk by Quarter: the dashboard's By Account table (NQ to NQ+3) and the Next 4 Quarters split -----
    ws = _sheet(wb, "Renewal At Risk by Quarter", RAR_QUARTER_HEADERS, [28, *[16] * 8])
    rq = data.get("renewal_quarters", [])
    money = '"$"#,##0;[Red]("$"#,##0)'
    for r in rq:
        ws.append([r.get("account_display") or r["account"], *[r.get(c) or 0 for c in RAR_QUARTER_HEADERS[1:]]])
        row = ws.max_row
        if str(r.get("account_url", "")).startswith("https://"):
            ws.cell(row, 1).hyperlink = r["account_url"]
            ws.cell(row, 1).font = Font(color="0F62FE", underline="single")
        for col in range(2, 10):
            ws.cell(row, col).number_format = money
    if rq:
        ws.append(["TOTAL", *[sum(float(r.get(c) or 0) for r in rq) for c in RAR_QUARTER_HEADERS[1:]]])
        for col in range(1, 10):
            ws.cell(ws.max_row, col).font = Font(bold=True)
        for col in range(2, 10):
            ws.cell(ws.max_row, col).number_format = money
    _finish(ws, len(RAR_QUARTER_HEADERS))
    next4 = data.get("renewal_next4") or {}
    if next4:
        ws.append([])
        ws.append(["Next 4 Quarters (dashboard total)", "Amount"])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
        for key, label in (("Red", "Red"), ("Yellow", "Yellow"), ("Green", "Green"), ("None", "No indicator")):
            if key in next4:
                ws.append([label, next4[key]])
                ws.cell(ws.max_row, 2).number_format = money
    if not rq and not next4:
        ws.append(["The dashboard's quarter figures were not available. See Notes."])

    # Account health score: At risk accounts first, then by ARR. One points column per input ----------------
    ws = _sheet(wb, "Account Health", ["Account", "Score", "Rating", "Coverage %",
                                       *[f"{label} points ({w})" for label, w in hs.INPUTS.values()],
                                       "Flags", "ARR", "Opportunity Owner"], [28, 8, 16, 12, 16, 16, 14, 16, 16, 34, 14, 24])
    for s in scores:
        ws.append([s.get("account_display") or s["account"], s["score"], s["rating"], s["coverage"], *[s["points"][k] for k in hs.INPUTS],
                   "; ".join(s["flags"]), s["arr"], s["owner"]])
        row = ws.max_row
        ws.cell(row, 11).number_format = '"$"#,##0'
        if hs.RATING_BADGE[s["rating"]] in HEALTH_FILLS:
            ws.cell(row, 3).fill, ws.cell(row, 3).font = HEALTH_FILLS[hs.RATING_BADGE[s["rating"]]]
    _finish(ws, 12)

    # Notes ----------------------------------------------------------------
    ws = _sheet(wb, "Notes", ["Area", "Account", "Note"], [18, 32, 100])
    for n in notes:
        ws.append([n.get("area"), n.get("account_display") or n.get("account", ""), n.get("note")])
        for c in ws[ws.max_row]:
            c.fill = WARN_FILL
            c.alignment = Alignment(wrap_text=True, vertical="top")
    if not notes:
        ws.append(["", "", "No issues. All sources returned data."])

    # Switched-off sections are dropped from the workbook.
    drop = []
    if not show_opps:
        drop += ["Open Opps by Quarter", "Renewals by Quarter", "Renewals"]
    if not show_deploy:
        drop += [s for s in wb.sheetnames if s in ("Deployments", "Renewal At Risk", "Renewal At Risk by Quarter")
                 or s.startswith("CQ Forecast")]
    if not show_gainsight:
        drop += ["Account Health", "Success Plans", "Ticket Summary", "Support Tickets", "ELA Details", "Renewal Forecast"]
    for s in drop:
        del wb[s]
    wb._sheets.sort(key=lambda s: next((i for i, k in enumerate(SHEET_ORDER) if s.title.startswith(k)), 99))

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
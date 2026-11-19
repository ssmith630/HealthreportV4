"""One-call report pipeline: ISC opportunities and deployments, Gainsight success plans, support tickets, then Excel + HTML.

Each source is best-effort. A failure becomes a row on the Notes sheet instead of
stopping the report.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import cq_metrics
import deployments
import health_score
import plan_status
import renewals
import settings
from accounts import Account
from browser import BrowserError
from demo_data import demo_data
from gainsight import Gainsight, GainsightError, success_plan_url
from html_builder import build_html
from isc import Isc, IscError, _ids18
from quarters import current_next_windows, merge_windows, report_windows, three_quarter_windows
from report_builder import build_report

REPORTS_DIR = Path(__file__).parent / "reports"
Progress = Callable[[int, str], None]


def _in_windows(opp: dict, windows) -> bool:
    try:
        d = date.fromisoformat(str(opp.get("close_date"))[:10])
    except ValueError:
        return False
    return any(w.contains(d) for w in windows)


def _from_company(row: dict, key: str, company: str | None) -> dict:
    """Name the Gainsight company on a row from a company other than the account's main one."""
    if company:
        row[key] = f"[{company}] {row.get(key) or ''}".strip()
    return row


def _review_notes(accounts: list[Account]) -> list[dict]:
    return [{"area": "Accounts", "account": a.name,
             "note": "Account match is marked 'review'. Confirm the ISC and Gainsight IDs before relying on its rows."}
            for a in accounts if a.needs_review]


def run_report(accounts: list[Account], year: int | None, isc: Isc, gs: Gainsight,
               progress: Progress, demo: bool = False, tech_only: bool = True,
               all_quarters: bool = False, show_opps: bool = True,
               show_deploy: bool = True, show_gainsight: bool = True,
               deploy_all_dates: bool = False, stale_days: int = 30) -> tuple[Path, Path]:
    """Build the Excel and HTML reports. Returns (xlsx_path, html_path).

    show_opps / show_deploy / show_gainsight switch the Opportunities (with the renewals, which are classified from
    ISC opportunities), Deployment and Gainsight (success plans, support tickets) sections on or off. A
    switched-off section is neither read nor rendered, and with show_gainsight off Gainsight is not contacted at all.
    deploy_all_dates lists deployments of any date; by default only those with a target or production date in the
    current quarter. The current-quarter forecast figures always stay on the current quarter.
    stale_days (30, 60 or 90) is how long a success plan can go without a change before it needs an update.
    """
    stale_days = plan_status.stale_cutoff(stale_days)
    plans_read: set[str] = set()
    qwindows = current_next_windows()            # current / next quarter, relative to today
    fetch_windows = qwindows                     # opportunities cover only the current and next quarter
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = REPORTS_DIR / f"Industry_Market_Account_Health_Report_{stamp}{'_DEMO' if demo else ''}"
    n = 1
    while base.with_suffix(".xlsx").exists() or base.with_suffix(".html").exists():  # never overwrite a report
        n += 1
        base = REPORTS_DIR / f"Industry_Market_Account_Health_Report_{stamp}_{n}{'_DEMO' if demo else ''}"

    if demo:
        progress(30, "Building sample data")
        data = demo_data(accounts, fetch_windows, deploy_all_dates=deploy_all_dates)
        if not show_opps:
            data["opps"] = []
            data["renewal_opps"] = []
        if not show_deploy:
            data["deployments"] = []
            data["cq_figures"] = {}
            data["renewal_risk"] = []
            data["renewal_quarters"] = []
            data["renewal_next4"] = {}
        if not show_gainsight:
            data["plans"] = []
            data["tickets"] = []
            data["tickets_read"] = []
            data["ela"] = []
            data["renewal_forecast"] = []
    else:
        data = {"opps": [], "plans": [], "tickets": [], "deployments": [], "notes": [], "renewal_opps": [],
                "tickets_read": [], "ela": [], "renewal_risk": [], "renewal_forecast": [],
                "renewal_quarters": [], "renewal_next4": {}}
        cfg = settings.load()
        isc.forget_reports()

        def say(pct: int):
            return lambda msg: progress(pct, msg)

        if show_opps:
            progress(10, f"Reading open opportunities for {' and '.join(w.label for w in fetch_windows)}")
            try:
                rows, notes = isc.open_opps(accounts, fetch_windows, tech_only, cfg["opp_report_url"], say(10),
                                            all_quarters=all_quarters)
                data["opps"] += rows
                data["notes"] += notes
            except (IscError, BrowserError) as e:
                data["notes"].append({"area": "Opportunities", "account": "", "note": str(e)})
            progress(15, "Classifying renewals from ISC opportunities")
            try:
                data["renewal_opps"], notes = isc.renewal_opps(accounts, cfg["opp_report_url"], say(15))
                data["notes"] += notes
            except (IscError, BrowserError) as e:
                data["notes"].append({"area": "Renewals", "account": "", "note": str(e)})

        if not show_deploy:
            pass
        else:
            progress(20, "Reading deployments")
            rows, notes = deployments.fetch(isc, accounts, cfg, None if deploy_all_dates else qwindows[0], say(20))
            data["deployments"] += rows
            data["notes"] += notes

        if show_deploy:
            progress(25, "Reading the deployment forecast from the dashboard")
            try:
                data["cq_figures"], notes = isc.cq_forecast(accounts, cfg["dashboard_url"], qwindows[0], say(25))
                data["notes"] += notes
            except (IscError, BrowserError) as e:
                data["notes"].append({"area": "CQ forecast", "account": "", "note": str(e)})
            progress(27, "Reading renewal at risk from the dashboard")
            try:
                data["renewal_risk"], notes = isc.renewal_at_risk(accounts, cfg["dashboard_url"], qwindows[0], say(27))
                data["notes"] += notes
                # Same dashboard load (cached), so this does not reopen the dashboard.
                data["renewal_quarters"], data["renewal_next4"], notes = isc.renewal_quarters(
                    accounts, cfg["dashboard_url"], qwindows[0], say(27))
                data["notes"] += notes
            except (IscError, BrowserError) as e:
                data["notes"].append({"area": "Renewal at risk", "account": "", "note": str(e)})

        if show_gainsight:
            progress(30, "Connecting to Gainsight")
            gs_ok = gs.is_connected()
        else:
            gs_ok = False
        if show_gainsight and not gs_ok:
            try:
                gs.connect([a.open_id for a in accounts if a.gsid or a.gs_name])
                gs_ok = True
            except (GainsightError, BrowserError) as e:
                data["notes"].append({"area": "Success plans and tickets", "account": "", "note": str(e)})
        if gs_ok:
            unresolved = [a for a in accounts if not a.gsid and a.gs_name]
            if unresolved:  # look the Gainsight company up by name, then by Salesforce account id
                try:
                    sfdc = sorted({i for a in unresolved for i in _ids18(a)} | {i for a in unresolved for i in (a.live_id, a.isc_id) if i})
                    cos = gs.find_companies(sorted({a.gs_name for a in unresolved}), sfdc)
                    for a in unresolved:
                        mine = _ids18(a) | {a.live_id, a.isc_id}
                        by_name = [c for c in cos if str(c.get("Name") or "").strip().lower() == a.gs_name.lower()]
                        by_id = [c for c in cos if c.get("SfdcAccountId") in mine]
                        both = [c for c in by_name if c in by_id]
                        shown = both if len(both) == 1 else (by_name or by_id)
                        if len(shown) == 1:
                            a.gsid = shown[0]["Gsid"]
                            continue
                        if shown:
                            msg = f"Gainsight has {len(shown)} companies for '{a.gs_name}'; add the right GSID to the account file. Candidates: "
                        else:
                            shown = gs.companies_containing(a.gs_name.split()[0])
                            msg = f"Gainsight has no company named '{a.gs_name}' or with this Salesforce account id. " + (
                                "Names containing the first word: " if shown else "No company name contains the first word.")
                        data["notes"].append({"area": "Success plans", "account": a.name, "note": msg + "; ".join(
                            f"{c.get('Name')} = {c['Gsid']} (Salesforce id {c.get('SfdcAccountId') or 'none'})" for c in shown)})
                except GainsightError as e:
                    data["notes"].append({"area": "Success plans", "account": "", "note": str(e)})
            for n, a in enumerate(accounts, 1):
                progress(35 + int(55 * n / len(accounts)), f"Success plans and tickets: {a.name} ({n}/{len(accounts)})")
                if not a.gsid:
                    data["notes"].append({"area": "Success plans", "account": a.name,
                                          "note": "No Gainsight company ID."})
                    continue
                if a.skipped_companies:
                    k = len(a.skipped_companies)
                    data["notes"].append({"area": "Gainsight companies", "account": a.name, "note": (
                        f"{k} non-US Gainsight {'company was' if k == 1 else 'companies were'} not read "
                        "(success plans, support tickets, ELA details, renewal forecast): "
                        + "; ".join(c["name"] or c["gsid"] for c in a.skipped_companies) + ".")})
                # The primary company plus every other Gainsight company under the account's coverage ID.
                # Extra companies with nothing to show add no note, so a large account stays readable.
                tickets_by_id: dict[str, dict] = {}
                tickets_ok = False
                for gsid in a.all_gsids:
                    primary = gsid == a.gsid
                    label = f"Gainsight company {gsid}" if primary else f"Gainsight company {a.company_name(gsid)} ({gsid})"
                    try:
                        got = gs.success_plans(gsid)
                        plans_read.add(gsid)
                        for p in got:
                            data["plans"].append(_from_company(dict(p, account=a.name, account_url=a.isc_url,
                                                                    plan_url=success_plan_url(gsid),
                                                                    company_gsid=gsid),
                                                                "name", None if primary else a.company_name(gsid)))
                        if not got and primary:
                            n_all, n_closed = gs.last_plans
                            data["notes"].append({"area": "Success plans", "account": a.name, "note": (
                                f"{label} has {n_all} success plans, {n_closed} closed, so none are open."
                                if n_all else f"{label} has no success plans.")})
                    except GainsightError as e:
                        data["notes"].append({"area": "Success plans", "account": a.name, "note": f"{label}: {e}"})
                    try:
                        got = gs.support_tickets(gsid)
                        tickets_ok = True
                        for t in got:
                            if t["ticket_id"] not in tickets_by_id:
                                tickets_by_id[t["ticket_id"]] = _from_company(dict(t, account=a.name, account_url=a.isc_url), "summary", None if primary else a.company_name(gsid))
                        if not got and primary:
                            n_all, _ = gs.last_tickets
                            data["notes"].append({"area": "Support tickets", "account": a.name, "note": (
                                f"{label} lists {n_all} ticket rows, but none is still open (Open, IBM is working or awaiting customer feedback)."
                                if n_all else f"The Gainsight support-ticket report returned no rows for {label.lower()}.")})
                        if got and gs.last_ticket_columns:
                            missing = [k for k in ("severity", "product", "state")
                                       if not any(t.get(k) for t in got)]
                            if missing:
                                data["notes"].append({"area": "Support tickets", "account": a.name, "note": (
                                    f"No value found for {', '.join(missing)} in the ticket report. They show as Unspecified. "
                                    f"Columns in the report: {', '.join(gs.last_ticket_columns)}.")})
                    except GainsightError as e:
                        data["notes"].append({"area": "Support tickets", "account": a.name, "note": f"{label}: {e}"})
                if tickets_ok:
                    data["tickets_read"].append(a.name)
                data["tickets"] += sorted(tickets_by_id.values(), key=lambda t: t["last_updated"], reverse=True)
            progress(91, "Reading ELA details")
            by_gsid = {g: a for a in accounts for g in a.all_gsids}
            try:
                fetched = gs.ela_details(list(by_gsid))
                for r in fetched:
                    a = by_gsid.get(r.get("gsid"))
                    if a:
                        data["ela"].append(dict(r, account=a.name, account_url=a.isc_url))
                if by_gsid and not data["ela"]:
                    asked = "; ".join(f"{a.name}: {', '.join(a.all_gsids)}" for a in accounts if a.all_gsids)
                    if fetched:
                        seen = ", ".join(sorted({r.get("gsid") or "(blank)" for r in fetched})[:5])
                        what = (f"The Gainsight Client ELA Details dashboard returned {len(fetched)} rows, but none matched "
                                f"an account's company GSID (GSIDs in the rows: {seen}). ")
                    else:
                        what = "The Gainsight Client ELA Details dashboard returned no rows for these accounts. "
                    data["notes"].append({"area": "ELA details", "account": "",
                                          "note": what + f"Company GSIDs queried: {asked}."})
            except GainsightError as e:
                data["notes"].append({"area": "ELA details", "account": "", "note": str(e)})
            progress(93, "Reading renewal forecast")
            try:
                for r in gs.renewal_forecast(list(by_gsid)):
                    a = by_gsid.get(r.get("gsid"))
                    if a:
                        data["renewal_forecast"].append(dict(r, account=a.name, account_url=a.isc_url))
                if by_gsid and not data["renewal_forecast"]:
                    data["notes"].append({"area": "Renewal forecast", "account": "",
                                          "note": "The Gainsight Renewal Dashboard returned no rows for these accounts."})
            except GainsightError as e:
                data["notes"].append({"area": "Renewal forecast", "account": "", "note": str(e)})
        if show_gainsight:
            try:  # Renewal Health comes from the same saved ISC report (already captured above)
                health, notes = isc.renewal_health(accounts, cfg["opp_report_url"], say(10))
                data["notes"] += notes
                for p in data["plans"]:
                    p["renewal_health"] = health.get(p.get("account"), "")
            except (IscError, BrowserError) as e:
                data["notes"].append({"area": "Renewal health", "account": "", "note": str(e)})
        data["notes"] += _review_notes(accounts)
        if plans_read:  # successful empty reads must also remove plans that were closed
            snap_path = REPORTS_DIR / plan_status.SNAPSHOT.name
            snap = plan_status.apply_progression(data["plans"], plan_status.read_snapshot(snap_path),
                                                 successfully_read_companies=plans_read)
            try:
                plan_status.write_snapshot(snap, snap_path)
            except OSError as e:
                data["notes"].append({"area": "Success plans", "account": "",
                                      "note": f"The plan status snapshot could not be saved, so the next run has nothing to compare with: {e}"})
            if data["plans"] and all(p.get("progression") == plan_status.NO_BASELINE for p in data["plans"]):
                data["notes"].append({"area": "Success plans", "account": "", "note": (
                    "Progression: this is the first run with a plan status snapshot, so there is no earlier run to compare. "
                    "Changes show from the next run.")})
    plan_status.flag_stale(data["plans"], stale_days)
    data["stale_days"] = stale_days

    # Renewals by quarter keep their wider outlook: previous, current and next quarter, the Q4/Q1 fiscal pair and
    # the quarter after next, so the outlook is not cut short.
    renewal_windows = merge_windows(report_windows(year), three_quarter_windows(),
                                    three_quarter_windows(date.today() + timedelta(days=92)))
    data["renewals"] = renewals.rows_from(accounts, data.pop("renewal_opps", []), renewal_windows)
    data["renewals_by_quarter"] = renewals.by_quarter(data["renewals"], renewal_windows)
    data["renewal_concentration"] = renewals.concentration(data["renewals"])
    if demo:
        data["sources"] = ["Sample data only. Nothing was read from ISC or Gainsight."]
    else:
        data["sources"] = ([*(["ISC saved opportunity report (open opportunities and renewals)"] if show_opps else []),
                            *(["ISC deployments report and the Deployment Forecast and Renewals dashboard"] if show_deploy else []),
                            *(["Gainsight success plans, support tickets, ELA details and renewal forecast"] if show_gainsight else [])])
    # The Excel sheet, the opportunities table and the quarter view all use the fetched quarters
    # (current and next).
    data["opps_all"] = data["opps"]
    data["opps"] = data["opps_all"] if all_quarters else [o for o in data["opps_all"] if _in_windows(o, fetch_windows)]
    data["quarter_windows"] = qwindows
    data["accounts"] = accounts
    data["sections"] = {"opportunities": show_opps, "deployments": show_deploy, "gainsight": show_gainsight}
    data["cq_window"] = qwindows[0]
    data["deploy_all_dates"] = deploy_all_dates
    data["cq_summary"] = cq_metrics.summarize(data.get("cq_figures", {}), accounts) if show_deploy else []
    deployments.enrich(data["deployments"])
    data["health_scores"] = health_score.score_all(data) if show_gainsight else []
    shown = {a.name: getattr(a, "isc_name", "") or a.name for a in accounts}
    for key in ("opps", "opps_all", "plans", "tickets", "ela", "renewal_forecast", "notes", "health_scores"):
        for r in data.get(key, []):
            if isinstance(r, dict) and not r.get("account_display") and r.get("account") in shown:
                r["account_display"] = shown[r["account"]]

    progress(90, "Creating Excel file")
    xlsx = build_report(data, fetch_windows, base.with_suffix(".xlsx"))
    progress(96, "Creating HTML dashboard")
    html = build_html(data, fetch_windows, base.with_suffix(".html"))
    if show_deploy:
        deployments.write_snapshot(data, demo, html.name)
    progress(100, "Done")
    return xlsx, html

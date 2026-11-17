"""Validate the Deployments data fetch and views.

    .venv\\Scripts\\python.exe validate_deployments.py            (Windows)
    .venv/bin/python validate_deployments.py                      (Mac)
    ... validate_deployments.py --live                            (also test the signed-in ISC)

Demo checks always run and need no sign-in. --live needs the app's browser window signed in to IBM Sales Cloud
(Connect tab); without it the live checks are reported as SKIPPED. Exit code 0 means every check run passed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import deployments
import settings
from accounts import load_accounts
from quarters import current_next_windows

REQUIRED = ("deployment_id", "name", "status", "date", "amount", "record_url", "account")
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name, detail))


def skip(name: str, why: str) -> None:
    results.append(("SKIP", name, why))


def check_rows(label: str, rows: list[dict], cq) -> None:
    check(f"{label}: returns at least one deployment", bool(rows), f"{len(rows)} rows")
    missing = [k for k in REQUIRED if rows and k not in rows[0]]
    check(f"{label}: rows carry every deployment field", not missing, f"missing {missing}" if missing else "")
    in_cq = all(any(r.get(k) and cq.contains(date.fromisoformat(r[k])) for k in ("target_decision_date", "deployed_date"))
                for r in rows)
    check(f"{label}: all rows fall in {cq.label}", in_cq)
    check(f"{label}: every row has a Salesforce https link", all(str(r.get("record_url", "")).startswith("https://") for r in rows))
    check(f"{label}: deployment forecast net license revenue present", any(r.get("amount") for r in rows))


def run_demo(accounts, windows) -> None:
    snap = deployments.snapshot_from_demo(accounts, windows)
    rows = snap["deployments"]
    check_rows("demo", rows, windows[0])
    check("demo: Seller View has all seven figures", all(snap["seller_view"].get(k) is not None for _, k in deployments.SELLER_VIEW))
    page = deployments.render(snap)
    check("demo: HTML view renders every header", all(h in page for h in deployments.HEADERS))
    check("demo: HTML view has one Open in Salesforce link per row", page.count("Open in Salesforce") == len(rows),
          f"{page.count('Open in Salesforce')} links, {len(rows)} rows")
    check("demo: HTML view shows the Seller View", "Seller View" in page and "CQ Budget" in page)
    check("demo: empty view renders", "No deployments yet" in deployments.render(None))

    from app import app  # Flask routes
    client = app.test_client()
    r = client.get("/deployments?demo=1")
    check("flask: GET /deployments?demo=1 returns 200", r.status_code == 200, str(r.status_code))
    check("flask: demo page lists deployments", b"Deployment records" in r.data)
    check("flask: GET /deployments returns 200", client.get("/deployments").status_code == 200)
    check("flask: existing routes still return 200",
          all(client.get(p).status_code == 200 for p in ("/", "/api/accounts", "/api/status", "/api/settings", "/api/reports")))

    import report_builder
    import openpyxl
    from pathlib import Path
    import tempfile
    from demo_data import demo_data
    data = demo_data(accounts, windows)
    deployments.enrich(data["deployments"])
    data.update(accounts=accounts, opps_all=[], quarter_windows=windows, cq_window=windows[0], cq_summary=[],
                renewals=[], renewals_by_quarter=[], sections={"opportunities": True, "deployments": True, "gainsight": True})
    out = Path(tempfile.mkdtemp()) / "deployments_check.xlsx"
    report_builder.build_report(data, windows, out)
    ws = openpyxl.load_workbook(out)["Deployments"]
    header = [c.value for c in ws[1]]
    check("excel: Deployments sheet has the deployment columns",
          all(h in header for h in ("Deployment ID", "Status", "Date", "Forecast Net License Revenue", "Salesforce Link")),
          str(header))
    check("excel: one row per deployment", ws.max_row - 1 == len(data["deployments"]), f"{ws.max_row - 1} rows")


def run_columns(accounts, windows) -> None:
    """Forecast Net License Revenue: value read when present, a clear note when missing or empty."""
    import isc as isc_mod

    cq = windows[0]
    acct = accounts[0]
    when = cq.start.isoformat()

    def cell(v, label=None, rid=None):
        return {"v": v, "l": label if label is not None else str(v), "id": rid}

    def row(**extra):
        base = {"accountname": cell(acct.name, rid=acct.open_id), "name": cell("DEP-1", rid="a0r000000000001AAA"),
                "stage": cell("Commit"), "owner": cell("A Owner"), "targetdecisiondatec": cell(when, when)}
        base.update({k: v for k, v in extra.items() if v is not None})
        return base

    def run(rows):
        stub = object.__new__(isc_mod.Isc)
        stub._report = lambda url, progress=None: {"reportMetadata": {"name": "Stub"}}
        saved = (isc_mod.ir.report_rows, isc_mod.ir.is_truncated)
        isc_mod.ir.report_rows = lambda rv: rows
        isc_mod.ir.is_truncated = lambda rv, n: False
        try:
            return stub.deployments([acct], "stub", cq=cq)
        finally:
            isc_mod.ir.report_rows, isc_mod.ir.is_truncated = saved

    def notes_for(notes, label):
        return [n["note"] for n in notes if n["note"].startswith(f"No {label} values found")]

    full = row(amount=cell(5000, "$5,000"))
    out, notes = run([full])
    check("columns: values are read when the report has them",
          bool(out) and out[0]["amount"] == 5000,
          str(out[0] if out else "no rows"))
    real = row(**{"totalrevenuefordeploymentinfrevc": cell(7500, "$7,500")})
    out2, notes2 = run([real])
    check("columns: 'Forecast Net License Revenue Used' (Total_Revenue_for_Deployment_in_FRev__c) is read",
          bool(out2) and out2[0]["amount"] == 7500 and not notes_for(notes2, "Forecast Net License Revenue"),
          str(out2[0] if out2 else "no rows"))

    check("columns: no missing-column note when the value is present",
          not notes_for(notes, "Forecast Net License Revenue"))

    out, notes = run([row()])
    msgs = notes_for(notes, "Forecast Net License Revenue")
    check("columns: clear note when the report has no Forecast Net License Revenue column",
          len(msgs) == 1 and "has no" in msgs[0] and "column" in msgs[0], msgs[0] if msgs else "no note")
    check("columns: a missing column does not drop the rows", len(out) == 1 and out[0]["amount"] == 0)

    out, notes = run([row(amount=cell(None, ""))])
    msgs = notes_for(notes, "Forecast Net License Revenue")
    check("columns: clear note when Forecast Net License Revenue is empty for every deployment",
          len(msgs) == 1 and "is empty" in msgs[0], msgs[0] if msgs else "no note")

    out, notes = run([full, row()])
    check("columns: one filled row is enough, no note", not notes_for(notes, "Forecast Net License Revenue"))


def run_all_dates(accounts, windows) -> None:
    """The "Include deployments outside the current quarter" option: cq=None keeps every date."""
    import tempfile
    from datetime import timedelta
    from pathlib import Path

    import isc as isc_mod
    import pipeline

    cq = windows[0]
    acct = accounts[0]
    inside, outside = cq.start.isoformat(), (cq.start - timedelta(days=60)).isoformat()

    def cell(v, rid=None):
        return {"v": v, "l": str(v), "id": rid}

    def row(name, when):
        return {"accountname": cell(acct.name, acct.open_id), "name": cell(name, "a0r000000000001AAA"),
                "stage": cell("Commit"), "owner": cell("A Owner"), "targetdecisiondatec": cell(when)}

    def run(cq_arg):
        stub = object.__new__(isc_mod.Isc)
        stub._report = lambda url, progress=None: {"reportMetadata": {"name": "Stub"}}
        saved = (isc_mod.ir.report_rows, isc_mod.ir.is_truncated)
        isc_mod.ir.report_rows = lambda rv: [row("IN", inside), row("OUT", outside)]
        isc_mod.ir.is_truncated = lambda rv, n: False
        try:
            return stub.deployments([acct], "stub", cq=cq_arg)
        finally:
            isc_mod.ir.report_rows, isc_mod.ir.is_truncated = saved

    rows, _ = run(cq)
    check("all dates: default keeps only the current quarter", [r["name"] for r in rows] == ["IN"], str([r["name"] for r in rows]))
    rows, _ = run(None)
    check("all dates: cq=None keeps deployments outside the quarter", sorted(r["name"] for r in rows) == ["IN", "OUT"],
          str([r["name"] for r in rows]))

    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    try:
        for flag in (False, True):
            _, html = pipeline.run_report(accounts[:6], None, None, None, lambda p, m: None, demo=True, deploy_all_dates=flag)
            page = html.read_text(encoding="utf-8")
            check(f"all dates: demo report with the option {'on' if flag else 'off'} labels deployments correctly",
                  ("Deployments (all dates)" in page) == flag and ("Deployments (current quarter)" in page) != flag)
        from demo_data import demo_data
        base = demo_data(accounts[:6], windows)["deployments"]
        more = demo_data(accounts[:6], windows, deploy_all_dates=True)["deployments"]
        check("all dates: demo adds sample deployments outside the quarter only when asked",
              len(more) > len(base) and all(any(r.get(k) and cq.contains(date.fromisoformat(r[k]))
                                                for k in ("target_decision_date", "deployed_date")) for r in base),
              f"{len(base)} -> {len(more)}")
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot = saved


def run_live(accounts, windows) -> None:
    from isc import Isc
    isc = Isc()
    try:
        connected = isc.is_connected()
    except Exception as e:  # browser not running, no profile, etc.
        skip("live: ISC connection", f"could not check the connection ({e})")
        return
    if not connected:
        skip("live: ISC connection", "not signed in. Start the app, use the Connect tab, then run again with --live")
        return
    rows, notes = deployments.fetch(isc, accounts[:5], settings.load(), windows[0])
    check("live: fetch returned without raising", True, f"{len(rows)} rows, {len(notes)} notes")
    if rows:
        check_rows("live", rows, windows[0])
    else:
        skip("live: row checks", "no rows; notes: " + " | ".join(n["note"] for n in notes)[:300])
    for n in notes:
        print("  note:", n["note"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also fetch from the signed-in ISC")
    args = ap.parse_args()
    accounts = load_accounts()[:12]
    windows = current_next_windows()
    run_demo(accounts, windows)
    run_columns(accounts, windows)
    run_all_dates(accounts, windows)
    if args.live:
        run_live(accounts, windows)
    else:
        skip("live checks", "run with --live to test the signed-in ISC")
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status:<5}{name:<{width + 2}}{detail}")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    print(f"\n{sum(1 for s, _, _ in results if s == 'PASS')} passed, {failed} failed, "
          f"{sum(1 for s, _, _ in results if s == 'SKIP')} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

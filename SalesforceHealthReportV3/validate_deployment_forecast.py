"""Validate the deployment forecast: dashboard record mapping, FY columns, TOTAL row, Excel sheet, HTML section, pipeline wiring.

    .venv\\Scripts\\python.exe validate_deployment_forecast.py            (Windows)
    .venv/bin/python validate_deployment_forecast.py                      (Mac)

Every check runs offline and needs no sign-in. Exit code 0 means every check passed.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import openpyxl

import cq_metrics
import dashboard
import isc as isc_mod
import pipeline
import report_builder
from accounts import load_accounts
from demo_data import demo_data
from quarters import current_next_windows

results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name, detail))


class _StubIsc(isc_mod.Isc):
    """An Isc client whose dashboard load returns canned records, so nothing touches the browser."""

    def __init__(self, deployment, renewal_detail=None):
        self.canned = {"deployment": deployment, "renewal_detail": renewal_detail or []}
        self.loads = 0

    def forget_reports(self):
        pass

    def dashboard_data(self, dashboard_url, prefix, progress=None):
        self.loads += 1
        return self.canned

    def renewal_health(self, accounts, url, progress):
        return {}, []


def record(name: str, prefix: str, fy: str, base: float) -> dict:
    rec = {"Acc.Name": name}
    for i, f in enumerate(dashboard.DEPLOY_FIELDS, 1):
        rec[f"{prefix} {f}"] = base * i
        rec[f"{fy} {f}"] = base * i * 10
    return rec


def run_helpers(windows) -> None:
    check("dashboard: quarter prefix for a quarter start", dashboard.quarter_prefix(date(2026, 10, 1)) == "4Q26")
    check("dashboard: quarter prefix for Q1", dashboard.quarter_prefix(date(2027, 1, 1)) == "1Q27")
    check("dashboard: fiscal year prefix", dashboard.year_prefix(date(2026, 10, 1)) == "FY26")
    check("dashboard: forecast fields include Pipeline", dashboard.DEPLOY_FIELDS[-1] == "Pipeline" and "Budget" in dashboard.DEPLOY_FIELDS)


def run_fetch(accounts, windows) -> None:
    cq = windows[0]
    pre, fy = dashboard.quarter_prefix(cq.start), dashboard.year_prefix(cq.start)
    a, b, c = accounts[0], accounts[1], accounts[2]
    recs = [record(f"{a.name}-US", pre, fy, 1.0),
            record(f"{b.name}-US", pre, fy, 2.0), record(f"{b.name}-JP", pre, fy, 3.0),
            record("NOT AN INDUSTRY MARKET ACCOUNT-US", pre, fy, 9.0)]
    stub = _StubIsc(recs)
    out, notes = stub.cq_forecast(accounts[:3], "https://example.invalid/dash", cq)
    check("fetch: country suffix is stripped to match the account", a.name in out, str(sorted(out)))
    check("fetch: quarter and fiscal-year figures are read", out[a.name]["Budget"] == 1.0 and out[a.name]["FY Budget"] == 10.0
          and out[a.name]["Pipeline"] == 7.0 and out[a.name]["FY Pipeline"] == 70.0, str(out[a.name]))
    check("fetch: several dashboard rows for one account are added up", out[b.name]["Budget"] == 5.0 and out[b.name]["FY Budget"] == 50.0,
          str(out[b.name]))
    check("fetch: a dashboard account outside the selection is dropped", all("NOT AN INDUSTRY" not in k for k in out))
    check("fetch: an account with no dashboard row gets a note, not a value",
          c.name not in out and any(n["account"] == c.name and n["area"] == "CQ forecast" for n in notes), str(notes))
    check("fetch: the note names the quarter prefix", any(pre in n["note"] for n in notes))
    check("fetch: a missing figure stays None", cq_metrics.summarize({a.name: {"Budget": 5.0}}, [a])[0]["deployed"] is None)


def run_metrics(accounts) -> None:
    figs = {accounts[0].name: {"Budget": 100.0, "Deployed": 40.0, "Call": 90.0, "Call to Go": 50.0, "Upside": 5.0, "Stretch": 7.0,
                               "Pipeline": 11.0, "FY Budget": 400.0, "FY Deployed": 160.0, "FY Call": 360.0, "FY Call to Go": 200.0,
                               "FY Pipeline": 44.0},
            accounts[1].name: {"Budget": 10.0, "Deployed": 4.0, "Call": 9.0, "Pipeline": 1.0, "FY Budget": 40.0}}
    rows = cq_metrics.summarize(figs, accounts[:2])
    total = rows[-1]
    check("metrics: one row per account plus TOTAL", len(rows) == 3 and total["account"] == "TOTAL", str([r["account"] for r in rows]))
    check("metrics: TOTAL adds the quarter figures", total["budget"] == 110.0 and total["pipeline"] == 12.0, str(total))
    check("metrics: TOTAL adds the fiscal-year figures, skipping blanks", total["fy_budget"] == 440.0 and total["fy_call"] == 360.0, str(total))
    check("metrics: B/(W) Budget is Call minus Budget", rows[0]["bw_budget"] == -10.0 or rows[1]["bw_budget"] == -10.0)
    check("metrics: Call to go is derived when the dashboard gave none",
          any(r["call_to_go"] == 5.0 for r in rows[:2]), str([r["call_to_go"] for r in rows]))


def run_outputs(accounts, windows) -> None:
    cq = windows[0]
    data = demo_data(accounts, windows)
    rows = cq_metrics.summarize(data["cq_figures"], accounts)
    check("demo: sample forecast carries FY and Pipeline figures",
          all(k in next(iter(data["cq_figures"].values())) for k in ("Pipeline", "FY Budget", "FY Pipeline")))
    data.update(accounts=accounts, opps_all=[], quarter_windows=windows, cq_window=cq, cq_summary=rows, renewals=[],
                renewals_by_quarter=[], sections={"opportunities": True, "deployments": True, "gainsight": True})
    out = Path(tempfile.mkdtemp()) / "forecast_check.xlsx"
    report_builder.build_report(data, windows, out)
    wb = openpyxl.load_workbook(out)
    names = [n for n in wb.sheetnames if n.startswith("CQ Forecast")]
    check("excel: a CQ Forecast sheet named for the quarter exists", len(names) == 1 and cq.label in names[0], str(wb.sheetnames))
    ws = wb[names[0]]
    check("excel: forecast headers match", [c.value for c in ws[1]] == report_builder.CQ_HEADERS, str([c.value for c in ws[1]]))
    check("excel: forecast has Pipeline and fiscal-year columns", all(h in report_builder.CQ_HEADERS for h in ("CQ Pipeline", "FY Budget", "FY Pipeline")))
    check("excel: one row per account plus TOTAL", ws.max_row - 1 == len(accounts) + 1 and ws.cell(ws.max_row, 1).value == "TOTAL",
          f"{ws.max_row - 1} rows")
    check("excel: forecast amounts are numbers", all(isinstance(ws.cell(2, i).value, (int, float)) for i in range(2, 5)))
    data["sections"]["deployments"] = False
    out2 = Path(tempfile.mkdtemp()) / "forecast_off.xlsx"
    report_builder.build_report(data, windows, out2)
    check("excel: forecast sheet is dropped when deployments are off",
          not any(n.startswith("CQ Forecast") for n in openpyxl.load_workbook(out2).sheetnames))


def run_cache() -> None:
    """The real Isc.dashboard_data loads the dashboard once per (url, quarter) and forget_reports clears it."""
    calls = []
    saved = dashboard.capture_dashboard
    dashboard.capture_dashboard = lambda tab, url, prefix, renewal=True, progress=None: calls.append(prefix) or {"deployment": [], "renewal_detail": []}
    try:
        client = object.__new__(isc_mod.Isc)
        client._dash = {}
        client._tab = lambda: object()
        client.dashboard_data("u", "4Q26")
        client.dashboard_data("u", "4Q26")
        check("cache: the dashboard is loaded once for the forecast and renewal fetches", len(calls) == 1, f"{len(calls)} loads")
        client.dashboard_data("u", "1Q27")
        check("cache: another quarter loads again", len(calls) == 2, f"{len(calls)} loads")
        client._reports, client._scoped = {}, {}
        client.forget_reports()
        client.dashboard_data("u", "4Q26")
        check("cache: forget_reports clears the dashboard data", len(calls) == 3, f"{len(calls)} loads")
    finally:
        dashboard.capture_dashboard = saved


def run_pipeline(accounts, windows) -> None:
    import settings

    cq = windows[0]
    pre, fy = dashboard.quarter_prefix(cq.start), dashboard.year_prefix(cq.start)
    acct = accounts[0]
    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, pipeline.deployments.fetch, settings.load)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    pipeline.deployments.fetch = lambda *a, **k: ([], [])
    settings.load = lambda: {"opp_report_url": "", "dashboard_url": "https://example.invalid/dash"}
    try:
        def run(stub, deploy=True):
            return pipeline.run_report([acct], None, stub, None, lambda p, m: None, show_opps=False, show_deploy=deploy,
                                       show_gainsight=False)

        stub = _StubIsc([record(f"{acct.name}-US", pre, fy, 1.0), record("OTHER CO-US", pre, fy, 9.0)])
        _, html = run(stub)
        page = html.read_text(encoding="utf-8")
        check("html: deployment forecast section is rendered", 'id="sec-cq"' in page and "Deployment forecast" in page)
        check("html: forecast table lists the fiscal-year columns", all(h in page for h in ("FY Budget", "CQ Pipeline", "FY Pipeline")))
        check("html: forecast shows the account and not an unselected one", acct.name in page and "OTHER CO" not in page)
        check("html: no scripts", "<script" not in page.lower())
        _, html = run(stub, deploy=False)
        off = html.read_text(encoding="utf-8")
        check("html: forecast section is dropped when deployments are off", 'id="sec-cq"' not in off)

        class _Failing(_StubIsc):
            def dashboard_data(self, *a, **k):
                raise isc_mod.IscError("Dashboard test failure")

        _, html = run(_Failing([]))
        page = html.read_text(encoding="utf-8")
        check("pipeline: a dashboard failure becomes a note, not an exception", "Dashboard test failure" in page)
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, pipeline.deployments.fetch, settings.load = saved


def main() -> int:
    accounts = load_accounts()[:12]
    windows = current_next_windows()
    run_helpers(windows)
    run_fetch(accounts, windows)
    run_metrics(accounts)
    run_outputs(accounts, windows)
    run_cache()
    run_pipeline(accounts, windows)
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status:<5}{name:<{width + 2}}{detail}")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    print(f"\n{sum(1 for s, _, _ in results if s == 'PASS')} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

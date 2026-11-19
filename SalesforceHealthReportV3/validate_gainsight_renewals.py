"""Validate the Gainsight renewal forecast: row parsing, the Excel sheet, the HTML section and the pipeline wiring.

    .venv\\Scripts\\python.exe validate_gainsight_renewals.py            (Windows)
    .venv/bin/python validate_gainsight_renewals.py                      (Mac)

Every check runs offline and needs no sign-in. Exit code 0 means every check passed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import openpyxl

import gainsight
import pipeline
import report_builder
from accounts import load_accounts
from demo_data import demo_data
from quarters import current_next_windows

results: list[tuple[str, str, str]] = []

_RN = gainsight._RN
_RR = gainsight._RR


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name, detail))


def sample_cells(score=72.5) -> dict:
    """A grid row in the shape the Gainsight report-data call returns: cells are {k, v, fv}."""
    return {
        "company_CompanyGsid__gr_Name": {"k": "1P02TESTGSID", "v": "Test Co"},
        "relationship_RelationshipGsid__gr_Name": {"v": " watsonx Orchestrate - US "},
        _RR + "Relationship_Segment__gc": {"v": "Strategic"},
        _RR + "Product_Stage__gc": {"v": "In Use"},
        _RR + "Adoption_Intent__gc": {"v": "Product owned"},
        _RN + "NextRenwlDate": {"k": "2027-01-01", "v": "Jan 1, 2027"},
        "sum_of_" + _RN + "SaasAcvUsdAtPlan": {"k": 36346, "v": "36,346"},
        _RN + "NumericalHealthScore": {"k": score, "fv": str(score)} if score is not None else {"fv": ""},
        _RR + "Has_Active_Adoption_Plan__gc": {"k": True, "fv": "true"},
        _RR + "Has_Active_Get_Well_Plan__gc": {"k": False, "fv": "false"},
    }


def run_parse() -> None:
    row = gainsight._renewal_row(sample_cells(), "SaaS")
    check("parse: company GSID comes from the key of the company cell", row["gsid"] == "1P02TESTGSID", str(row["gsid"]))
    check("parse: license type is the grid it came from", row["license_type"] == "SaaS")
    check("parse: text cells are trimmed", row["product"] == "watsonx Orchestrate - US" and row["company"] == "Test Co"
          and row["segment"] == "Strategic" and row["product_stage"] == "In Use" and row["adoption_intent"] == "Product owned")
    check("parse: next renewal is an ISO date", row["next_renewal"] == "2027-01-01", str(row["next_renewal"]))
    check("parse: ACV is a number", row["acv"] == 36346.0, str(row["acv"]))
    check("parse: health score is a number", row["health_score"] == 72.5, str(row["health_score"]))
    check("parse: plan flags are booleans", row["adoption_plan"] is True and row["get_well_plan"] is False)
    unscored = gainsight._renewal_row(sample_cells(None), "Perpetual")
    check("parse: an empty health score is None, not 0", unscored["health_score"] is None, str(unscored["health_score"]))
    tk = gainsight._ticket({
        "generic_support_data__gc_ticket_id__gc": {"v": "TS1"}, "generic_support_data__gc_status__gc": {"v": "Open"},
        "generic_support_data__gc_severity__gc": {"v": "2"},
        "generic_support_data__gc_attribute_1__gc": {"v": "Order Management On Cloud"},
        "generic_support_data__gc_attribute_6__gc": {"fv": ""}})
    check("ticket: attribute_1 is the product, and the ticket has no brand",
          tk["product"] == "Order Management On Cloud" and "brand" not in tk and tk["severity"] == "Severity 2", str(tk))
    ts = report_builder.ticket_summary([
        {"account": "WALT DISNEY", "account_display": "The Walt Disney Company-US"},
        {"account": "OTHER", "account_display": ""}, {"account": "WALT DISNEY", "account_display": "The Walt Disney Company-US"}])
    check("ticket summary: cases by client use the display name, falling back to the account name",
          ts["by_client"] == [("The Walt Disney Company-US", 2), ("OTHER", 1)], str(ts["by_client"]))

    empty = gainsight._renewal_row({})
    check("parse: an empty row does not raise", empty["gsid"] == "" and empty["acv"] == 0.0 and empty["health_score"] is None
          and empty["adoption_plan"] is False)

    stub = object.__new__(gainsight.Gainsight)
    check("fetch: no GSIDs returns no rows without contacting Gainsight",
          stub.renewal_forecast([]) == [] and stub.renewal_forecast(["", ""]) == [])
    check("fetch: the three license-type grids are configured",
          [g[2] for g in gainsight.RENEWAL_GRIDS] == ["SaaS", "Subscription", "Perpetual"])


def run_outputs(accounts, windows) -> None:
    data = demo_data(accounts, windows)
    rows = data["renewal_forecast"]
    check("demo: sample data carries two renewal forecast rows per account", len(rows) == 2 * len(accounts), f"{len(rows)} rows")
    needed = ("account", "company", "product", "license_type", "next_renewal", "acv", "health_score", "segment",
              "product_stage", "adoption_intent", "adoption_plan", "get_well_plan")
    check("demo: rows carry every field", all(k in r for r in rows for k in needed))
    check("demo: some rows have no health score", any(r["health_score"] is None for r in rows)
          and any(r["health_score"] is not None for r in rows))

    data.update(accounts=accounts, opps_all=[], quarter_windows=windows, cq_window=windows[0], cq_summary=[],
                renewals=[], renewals_by_quarter=[], sections={"opportunities": True, "deployments": True, "gainsight": True})
    out = Path(tempfile.mkdtemp()) / "forecast_check.xlsx"
    report_builder.build_report(data, windows, out)
    wb = openpyxl.load_workbook(out)
    check("excel: Renewal Forecast sheet exists", "Renewal Forecast" in wb.sheetnames, str(wb.sheetnames))
    names = wb.sheetnames
    check("excel: sheet sits after ELA Details and before Notes",
          names.index("ELA Details") < names.index("Renewal Forecast") < names.index("Notes"), str(names))
    ws = wb["Renewal Forecast"]
    check("excel: headers match", [c.value for c in ws[1]] == report_builder.FORECAST_HEADERS, str([c.value for c in ws[1]]))
    check("excel: one row per forecast row", ws.max_row - 1 == len(rows), f"{ws.max_row - 1} rows")
    check("excel: ACV is a number", all(isinstance(ws.cell(r, 6).value, (int, float)) for r in range(2, ws.max_row + 1)))
    scored = [ws.cell(r, 7).value for r in range(2, ws.max_row + 1)]
    check("excel: health score is a number when present and blank when not",
          any(isinstance(v, (int, float)) for v in scored) and any(v in (None, "") for v in scored))
    flags = {ws.cell(r, c).value for r in range(2, ws.max_row + 1) for c in (11, 12)}
    check("excel: plan flags are written as Yes or No", flags <= {"Yes", "No"} and flags == {"Yes", "No"}, str(flags))
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows() if r and r[0].value}
    check("excel: Summary counts the forecast rows", summary.get("Renewal forecast rows") == len(rows),
          str(summary.get("Renewal forecast rows")))

    data["sections"]["gainsight"] = False
    out2 = Path(tempfile.mkdtemp()) / "forecast_off.xlsx"
    report_builder.build_report(data, windows, out2)
    wb2 = openpyxl.load_workbook(out2)
    check("excel: sheet and Summary line are dropped when Gainsight is off", "Renewal Forecast" not in wb2.sheetnames
          and "Renewal forecast rows" not in {r[0].value for r in wb2["Summary"].iter_rows() if r})


def run_pipeline(accounts) -> None:
    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    try:
        _, html = pipeline.run_report(accounts[:4], None, None, None, lambda p, m: None, demo=True)
        page = html.read_text(encoding="utf-8")
        check("html: forecast block is rendered for each account", page.count("Renewal forecast (2)") == 4,
              str(page.count("Renewal forecast (2)")))
        check("html: forecast tile and index counts are shown", "Renewal forecast rows" in page and "2 renewal forecast rows" in page)
        check("html: forecast columns are listed", all(h in page for h in ("ACV (USD)", "Health Score", "Active Get Well Plan")))
        check("html: unscored health shows a dash", 'class="num"><span class="empty">&mdash;</span>' in page)
        check("html: forecast source is named in the subtitle", "renewal forecast" in page.split("</header>")[0])
        check("html: no scripts", "<script" not in page.lower())
        _, html_off = pipeline.run_report(accounts[:4], None, None, None, lambda p, m: None, demo=True, show_gainsight=False)
        off = html_off.read_text(encoding="utf-8")
        check("html: forecast block is dropped when Gainsight is off",
              "Renewal forecast (" not in off and "Renewal forecast rows" not in off)
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot = saved


class _StubGs(gainsight.Gainsight):
    """A Gainsight client that returns canned renewal rows (and nothing else) so run_report can be driven offline."""

    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail
        self.last_plans = (0, 0)
        self.last_tickets = (0, 0)
        self.last_ticket_columns = []
        self.asked: list[str] = []

    def is_connected(self):
        return True

    def success_plans(self, gsid):
        return []

    def support_tickets(self, gsid):
        return []

    def ela_details(self, gsids, max_pages=40):
        return []

    def renewal_forecast(self, gsids, max_pages=40):
        self.asked = list(gsids)
        if self.fail:
            raise gainsight.GainsightError("Renewal test failure")
        return [r for r in self.rows if r["gsid"] in gsids]


class _StubIsc:
    def forget_reports(self):
        pass

    def renewal_health(self, accounts, url, progress):
        return {}, []


def run_live_path(accounts) -> None:
    """The non-demo path with stubbed sources: rows are mapped to accounts, unknown GSIDs dropped, failures become notes."""
    import settings
    from accounts import Account

    acct = Account(name=accounts[0].name, isc_id=accounts[0].isc_id, gsid="1P02MAIN", confidence="high",
                   gs_name="Main Co", gs_companies=[{"gsid": "1P02SECOND", "name": "Second Co"},
                                                    {"gsid": "1P02MAIN", "name": "Main Co"}])
    mine = dict(gainsight._renewal_row(sample_cells(), "SaaS"), gsid="1P02MAIN", product="Main product row")
    second = dict(mine, gsid="1P02SECOND", product="Second company product row")
    other = dict(mine, gsid="1P02NOTSELECTED", product="Unselected product row")

    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, settings.load)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    settings.load = lambda: {"opp_report_url": "", "dashboard_url": ""}
    try:
        def run(gs):
            return pipeline.run_report([acct], None, _StubIsc(), gs, lambda p, m: None, show_opps=False, show_deploy=False)

        gs = _StubGs([mine, second, other])
        _, html = run(gs)
        page = html.read_text(encoding="utf-8")
        check("pipeline: every company of the account is asked for", sorted(gs.asked) == ["1P02MAIN", "1P02SECOND"], str(gs.asked))
        check("pipeline: rows from both companies show under the account",
              "Renewal forecast (2)" in page and "Main product row" in page and "Second company product row" in page)
        check("pipeline: a row for an unselected GSID is dropped", "Unselected product row" not in page)

        _, html = run(_StubGs([]))
        page = html.read_text(encoding="utf-8")
        check("pipeline: no rows becomes a note", "Renewal forecast" in page and "returned no rows for these accounts" in page)

        _, html = run(_StubGs(fail=True))
        page = html.read_text(encoding="utf-8")
        check("pipeline: a failure becomes a note, not an exception", "Renewal test failure" in page)
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, settings.load = saved


class _FakeTab:
    def __init__(self, cookies):
        self._cookies = cookies
        self.navigated = 0

    def navigate(self, url, wait=0):
        self.navigated += 1

    def evaluate(self, *a, **k):
        return ""

    def cookies(self):
        return self._cookies


class _FakeBrowser:
    def __init__(self, cookies):
        self.t = _FakeTab(cookies)

    def tab(self, _name):
        return self.t

    def any_tab(self):
        return self.t


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


def run_connect() -> None:
    """connect() must not wait for apt.sid when sid_sf and apt.uid already work, and must still fail cleanly otherwise."""
    gd = "ibmcs.gainsightcloud.com"
    ck = [{"name": "apt.uid", "value": "U", "domain": gd}, {"name": "sid", "value": "S", "domain": "x.force.com"}]
    ok_resp = _Resp(200, {"data": {"records": [{"Gsid": "1P02X"}]}})
    saved = (gainsight.requests.post, gainsight.time.sleep)
    sleeps: list[float] = []
    gainsight.time.sleep = lambda s: sleeps.append(s)
    try:
        gainsight.requests.post = lambda *a, **k: ok_resp
        g = gainsight.Gainsight(browser=_FakeBrowser(ck))
        g.connect(["0010500000FAKE"], timeout=30)
        check("connect: works without apt.sid when sid_sf and apt.uid are accepted",
              bool(g._cookie_header) and "apt.sid" not in g._cookie_header and "sid_sf=S" in g._cookie_header)
        check("connect: does not wait when the cookies already work", sleeps == [], str(sleeps))

        calls: list[dict] = []
        gainsight.requests.post = lambda *a, **k: calls.append(k) or ok_resp
        g = gainsight.Gainsight(browser=_FakeBrowser(ck + [{"name": "apt.sid", "value": "A", "domain": gd}]))
        g.connect(["0010500000FAKE"], timeout=30)
        check("connect: apt.sid still wins and needs no probe call", "apt.sid=A" in (g._cookie_header or "") and not calls)

        gainsight.requests.post = lambda *a, **k: _Resp(401, {})
        g = gainsight.Gainsight(browser=_FakeBrowser(ck))
        end = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
        real_time = gainsight.time.time
        gainsight.time.time = lambda: next(end, 100.0)
        try:
            err = ""
            try:
                g.connect(["0010500000FAKE"], timeout=30)
            except gainsight.GainsightError as e:
                err = str(e)
        finally:
            gainsight.time.time = real_time
        check("connect: rejected cookies still end in a clear error", "did not load" in err and g._cookie_header is None, err)

        probes: list[int] = []
        gainsight.requests.post = lambda *a, **k: probes.append(1) or ok_resp
        g = gainsight.Gainsight(browser=_FakeBrowser([{"name": "apt.uid", "value": "U", "domain": gd}]))
        end = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
        gainsight.time.time = lambda: next(end, 100.0)
        try:
            try:
                g.connect(["0010500000FAKE"], timeout=30)
            except gainsight.GainsightError:
                pass
        finally:
            gainsight.time.time = real_time
        check("connect: no probe is sent without sid_sf", probes == [] and g._cookie_header is None, str(probes))
    finally:
        gainsight.requests.post, gainsight.time.sleep = saved


def main() -> int:
    accounts = load_accounts()[:12]
    windows = current_next_windows()
    run_parse()
    run_outputs(accounts, windows)
    run_pipeline(accounts)
    run_live_path(accounts)
    run_connect()
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status:<5}{name:<{width + 2}}{detail}")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    print(f"\n{sum(1 for s, _, _ in results if s == 'PASS')} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

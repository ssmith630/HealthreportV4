"""Validate the ELA details: row parsing, the Excel sheet, the HTML section and the pipeline wiring.

    .venv\\Scripts\\python.exe validate_ela.py            (Windows)
    .venv/bin/python validate_ela.py                      (Mac)

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


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name, detail))


def sample_cells() -> dict:
    """A grid row in the shape the Gainsight report-data call returns: cells are {k, v, fv}."""
    return {
        "company_Company_GSID__gr_Name": {"k": "1P02TESTGSID", "v": "Test Co"},
        "ela_details__gc_UCI__gc": {"v": "1234567"},
        "ela_details__gc_CONTRACT_NAME__gc": {"v": " Test Co ELA "},
        "ela_details__gc_Contract_Section_Dropdown__gc": {"v": "Section A"},
        "ela_details__gc_PLATFORM__gc": {"v": "zOS"},
        "ela_details__gc_CONTRACT_END_DATE__gc": {"k": "2027-03-31T00:00:00.000Z", "v": "Mar 31, 2027"},
        "sum_of_ela_details__gc_ENTITLED_AS_TOTAL_NET_REVENUE__gc": {"k": 1000000.5, "v": "1,000,000"},
        "sum_of_ela_details__gc_ENTITLED_DEPLOYED_TOTAL_NET_REVENUE__gc": {"k": 400000, "v": "400,000"},
        "sum_of_FF_Undeployed Value": {"k": 600000.5},
        "sum_of_FF_Declared vs Expected Progress": {"k": -50000},
        "sum_of_ela_details__gc_RATE_PACE_TARGET__gc": {"v": "n/a"},
    }


def run_parse() -> None:
    row = gainsight._ela_row(sample_cells())
    check("parse: company GSID comes from the key of the company cell", row["gsid"] == "1P02TESTGSID", str(row["gsid"]))
    check("parse: text cells are trimmed", row["contract"] == "Test Co ELA" and row["platform"] == "zOS"
          and row["section"] == "Section A" and row["uci"] == "1234567")
    check("parse: end date is an ISO date", row["end_date"] == "2027-03-31", str(row["end_date"]))
    check("parse: amounts are numbers, negatives kept", row["entitled"] == 1000000.5 and row["declared_vs_expected"] == -50000.0,
          str({k: row[k] for k in gainsight.ELA_AMOUNTS}))
    check("parse: an unreadable amount becomes 0", row["rate_pace_target"] == 0.0)
    empty = gainsight._ela_row({})
    check("parse: an empty row does not raise", empty["gsid"] == "" and empty["entitled"] == 0.0)

    stub = object.__new__(gainsight.Gainsight)
    check("fetch: no GSIDs returns no rows without contacting Gainsight", stub.ela_details([]) == [] and stub.ela_details(["", ""]) == [])


def run_outputs(accounts, windows) -> None:
    data = demo_data(accounts, windows)
    ela = data["ela"]
    check("demo: sample data carries ELA rows for the accounts", len(ela) == 2 * len(accounts), f"{len(ela)} rows")
    needed = ("account", "contract", "uci", "section", "platform", "end_date", *gainsight.ELA_AMOUNTS)
    check("demo: ELA rows carry every field", all(k in e for e in ela for k in needed))

    data.update(accounts=accounts, opps_all=[], quarter_windows=windows, cq_window=windows[0], cq_summary=[],
                renewals=[], renewals_by_quarter=[], sections={"opportunities": True, "deployments": True, "gainsight": True})
    out = Path(tempfile.mkdtemp()) / "ela_check.xlsx"
    report_builder.build_report(data, windows, out)
    wb = openpyxl.load_workbook(out)
    check("excel: ELA Details sheet exists", "ELA Details" in wb.sheetnames, str(wb.sheetnames))
    ws = wb["ELA Details"]
    check("excel: ELA Details headers match", [c.value for c in ws[1]] == report_builder.ELA_HEADERS, str([c.value for c in ws[1]]))
    check("excel: one row per ELA row", ws.max_row - 1 == len(ela), f"{ws.max_row - 1} rows")
    check("excel: amounts are written as numbers", all(isinstance(ws.cell(2, i).value, (int, float)) for i in range(7, 12)))
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows() if r and r[0].value}
    check("excel: Summary counts the ELA rows", summary.get("ELA contract rows") == len(ela), str(summary.get("ELA contract rows")))

    data["sections"]["gainsight"] = False
    out2 = Path(tempfile.mkdtemp()) / "ela_off.xlsx"
    report_builder.build_report(data, windows, out2)
    check("excel: ELA Details sheet is dropped when Gainsight is off", "ELA Details" not in openpyxl.load_workbook(out2).sheetnames)


def run_pipeline(accounts) -> None:
    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    try:
        xlsx, html = pipeline.run_report(accounts[:4], None, None, None, lambda p, m: None, demo=True)
        page = html.read_text(encoding="utf-8")
        check("html: ELA details block is rendered for the accounts", page.count("ELA details (2)") == 4, str(page.count("ELA details (2)")))
        check("html: ELA tile and index counts are shown", "ELA contract rows" in page and "2 ELA rows" in page)
        check("html: ELA columns are listed", all(h in page for h in ("Entitled (Net Revenue)", "Undeployed Value", "Rate Pace Target")))
        check("html: ELA source is named in the subtitle", "ELA details" in page.split("</header>")[0])
        check("html: no scripts", "<script" not in page.lower())
        _, html_off = pipeline.run_report(accounts[:4], None, None, None, lambda p, m: None, demo=True, show_gainsight=False)
        off = html_off.read_text(encoding="utf-8")
        check("html: ELA block is dropped when Gainsight is off", "ELA details (" not in off and "ELA contract rows" not in off)
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot = saved


class _StubGs(gainsight.Gainsight):
    """A Gainsight client that returns canned ELA rows (and no plans or tickets) so run_report can be driven offline."""

    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail
        self.last_plans = (0, 0)
        self.last_tickets = (0, 0)
        self.last_ticket_columns = []

    def is_connected(self):
        return True

    def success_plans(self, gsid):
        return []

    def support_tickets(self, gsid):
        return []

    def ela_details(self, gsids, max_pages=40):
        if self.fail:
            raise gainsight.GainsightError("ELA test failure")
        return [r for r in self.rows if r["gsid"] in gsids]

    def renewal_forecast(self, gsids, max_pages=40):
        return []  # no renewal rows; keeps this validator offline


class _UnfilteredGs(_StubGs):
    """Returns its rows whatever GSIDs are asked for, like a dashboard that returns rows for other companies."""

    def ela_details(self, gsids, max_pages=40):
        return list(self.rows)


class _StubIsc:
    def forget_reports(self):
        pass

    def renewal_health(self, accounts, url, progress):
        return {}, []


def run_live_path(accounts) -> None:
    """The non-demo path with stubbed sources: rows are mapped to accounts, unknown GSIDs dropped, failures become notes."""
    import settings
    import types

    acct = accounts[0]
    acct.gsid = acct.gsid or "1P02STUBGSID"
    row = dict(gainsight._ela_row(sample_cells()), gsid=acct.gsid)
    other = dict(row, gsid="1P02NOTSELECTED")

    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, settings.load)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    settings.load = lambda: {"opp_report_url": "", "dashboard_url": ""}
    try:
        def run(gs):
            return pipeline.run_report([acct], None, _StubIsc(), gs, lambda p, m: None, show_opps=False, show_deploy=False)

        _, html = run(_StubGs([row, other]))
        page = html.read_text(encoding="utf-8")
        check("pipeline: ELA row is shown under its account", "ELA details (1)" in page and "Test Co ELA" in page)
        check("pipeline: a row for an unselected GSID is dropped", page.count("Test Co ELA") == 1)

        _, html = run(_StubGs(fail=True))
        page = html.read_text(encoding="utf-8")
        check("pipeline: an ELA failure becomes a note, not an exception", "ELA test failure" in page and "ELA details" in page)

        _, html = run(_UnfilteredGs([other]))
        page = html.read_text(encoding="utf-8")
        check("pipeline: rows that match no account give a note with the GSIDs in the rows and the GSIDs queried",
              "returned 1 rows, but none matched" in page and "1P02NOTSELECTED" in page and f"Company GSIDs queried: {acct.name}: " in page
              and acct.gsid in page)

        _, html = run(_StubGs([]))
        page = html.read_text(encoding="utf-8")
        check("pipeline: no ELA rows at all gives the no-rows note with the GSIDs queried",
              "returned no rows for these accounts" in page and "Company GSIDs queried:" in page and "but none matched" not in page)
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, settings.load = saved


class _MultiGs(_StubGs):
    """Plans, tickets and ELA rows per Gainsight company, to test several companies under one account."""

    def __init__(self, fail_gsid=""):
        super().__init__()
        self.fail_gsid, self.calls = fail_gsid, []
        self.ela_asked: list[str] = []

    def success_plans(self, gsid):
        self.calls.append(gsid)
        if gsid == self.fail_gsid:
            raise gainsight.GainsightError("plan test failure")
        return [{"name": f"Plan {gsid}", "type": "", "status": "Active", "owner": "Owner", "start_date": "",
                 "due_date": "", "overdue": False, "days_overdue": 0}]

    def support_tickets(self, gsid):
        # TKT-SHARED is returned by two companies and must show once. TKT-<gsid> is unique to the company.
        base = {"status": "Open", "severity": "Sev 3", "state": "IBM is working", "product": "P",
                "ticket_url": "", "created": "2026-01-01", "summary": "Ticket"}
        return [dict(base, ticket_id="TKT-SHARED", last_updated="2026-02-01"),
                dict(base, ticket_id=f"TKT-{gsid}", last_updated="2026-03-01")]

    def ela_details(self, gsids, max_pages=40):
        self.ela_asked = list(gsids)
        return [dict(gainsight._ela_row(sample_cells()), gsid="1P02SECOND", contract="Second company ELA")]


def run_companies(accounts) -> None:
    """One account with a second Gainsight company: its plans, tickets and ELA rows are included under the account."""
    import settings
    from accounts import Account

    acct = Account(name=accounts[0].name, isc_id=accounts[0].isc_id, gsid="1P02MAIN", confidence="high",
                   gs_name="Main Co", gs_companies=[{"gsid": "1P02SECOND", "name": "Second Co"},
                                                    {"gsid": "1P02MAIN", "name": "Main Co"}, {"gsid": "", "name": "Blank"}])
    check("companies: primary first, no repeats, blanks dropped", acct.all_gsids == ["1P02MAIN", "1P02SECOND"], str(acct.all_gsids))
    check("companies: name lookup falls back to the GSID", acct.company_name("1P02SECOND") == "Second Co"
          and acct.company_name("1P02OTHER") == "1P02OTHER")
    check("companies: an account with no GSID has none to read", Account("A", "i", "", "high").all_gsids == [])

    saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, settings.load)
    pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
    pipeline.deployments.write_snapshot = lambda *a, **k: None
    settings.load = lambda: {"opp_report_url": "", "dashboard_url": ""}
    try:
        def run(gs):
            return pipeline.run_report([acct], None, _StubIsc(), gs, lambda p, m: None, show_opps=False, show_deploy=False)

        gs = _MultiGs()
        _, html = run(gs)
        page = html.read_text(encoding="utf-8")
        check("companies: every company is read for plans", gs.calls == ["1P02MAIN", "1P02SECOND"], str(gs.calls))
        check("companies: a plan from the main company shows without a company tag", "Plan 1P02MAIN" in page and "[Main Co]" not in page)
        check("companies: a plan from another company is tagged with its name", "[Second Co] Plan 1P02SECOND" in page)
        check("companies: a ticket returned by two companies shows once", page.count("TKT-SHARED") == 1, str(page.count("TKT-SHARED")))
        check("companies: tickets unique to each company both show", "TKT-1P02MAIN" in page and "TKT-1P02SECOND" in page)
        check("companies: ELA is asked for every company", sorted(gs.ela_asked) == ["1P02MAIN", "1P02SECOND"], str(gs.ela_asked))
        check("companies: an ELA row from another company shows under the account", "Second company ELA" in page)

        _, html = run(_MultiGs(fail_gsid="1P02SECOND"))
        page = html.read_text(encoding="utf-8")
        check("companies: one company failing does not stop the others",
              "Plan 1P02MAIN" in page and "TKT-1P02SECOND" in page and "plan test failure" in page)
        check("companies: the failure note names the company", "Second Co (1P02SECOND): plan test failure" in page)

        # A company marked non_us in the account file is not read by any source, and a note names it.
        skipped_acct = Account(name=acct.name, isc_id=acct.isc_id, gsid="1P02MAIN", confidence="high", gs_name="Main Co",
                               gs_companies=[{"gsid": "1P02SECOND", "name": "Second Co"}],
                               skipped_companies=[{"gsid": "1P02ABROAD", "name": "Abroad Co Ltd"}])
        check("non-US: a skipped company is not in the list to read", "1P02ABROAD" not in skipped_acct.all_gsids)
        gs = _MultiGs()
        _, html = pipeline.run_report([skipped_acct], None, _StubIsc(), gs, lambda p, m: None, show_opps=False, show_deploy=False)
        page = html.read_text(encoding="utf-8")
        check("non-US: plans are not read for a skipped company", "1P02ABROAD" not in gs.calls, str(gs.calls))
        check("non-US: ELA is not asked for a skipped company", "1P02ABROAD" not in gs.ela_asked, str(gs.ela_asked))
        check("non-US: a note names the skipped company", "1 non-US Gainsight company was not read" in page and "Abroad Co Ltd" in page)
        check("non-US: an account with nothing skipped gets no such note", "non-US Gainsight" not in
              pipeline.run_report([acct], None, _StubIsc(), _MultiGs(), lambda p, m: None, show_opps=False, show_deploy=False)[1].read_text(encoding="utf-8"))

        # The account file marks the non-US Toyota companies and the loader honours it.
        by_name = {a.name: a for a in load_accounts(industry_market_only=False)}
        toyota = by_name.get("TOYOTA MOTOR")
        if toyota is None:
            check("non-US: Toyota is in the account file", False)
        else:
            skipped = sorted(c["name"] for c in toyota.skipped_companies)
            check("non-US: Toyota skips the four non-US companies",
                  skipped == ["CATALER CORPORATION", "JTEKT", "TOYODA IRON WORKS CO. LTD.", "Toyota De Puerto Rico Corp."], str(skipped))
            check("non-US: no skipped Toyota company is read", not {c["gsid"] for c in toyota.skipped_companies} & set(toyota.all_gsids))
            check("non-US: Toyota still reads its US companies", len(toyota.all_gsids) == 13, str(len(toyota.all_gsids)))
        for name in ("CATERPILLAR", "COMCAST"):
            a = by_name.get(name)
            check(f"non-US: {name} skips nothing", a is not None and not a.skipped_companies)
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, settings.load = saved


def main() -> int:
    accounts = load_accounts()[:12]
    windows = current_next_windows()
    run_parse()
    run_outputs(accounts, windows)
    run_pipeline(accounts)
    run_live_path(accounts)
    run_companies(accounts)
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status:<5}{name:<{width + 2}}{detail}")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    print(f"\n{sum(1 for s, _, _ in results if s == 'PASS')} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

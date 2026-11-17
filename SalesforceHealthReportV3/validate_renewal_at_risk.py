"""Validate renewal at risk: dashboard record aggregation, the 10,000-record cap note, Excel sheet, HTML section, pipeline wiring.

    .venv\\Scripts\\python.exe validate_renewal_at_risk.py            (Windows)
    .venv/bin/python validate_renewal_at_risk.py                      (Mac)

Every check runs offline and needs no sign-in. Exit code 0 means every check passed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import openpyxl

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

    def __init__(self, renewal_detail, deployment=None, by_account=None, more=None, next4=None, page=None):
        self.canned = {"deployment": deployment or [], "renewal_detail": renewal_detail,
                       "renewal_detail_more": more or [], "renewal_by_account": by_account or [],
                       "renewal_next4": next4 or {}, "renewal_by_account_page": page or []}

    def forget_reports(self):
        pass

    def dashboard_data(self, dashboard_url, prefix, progress=None):
        return self.canned

    def renewal_health(self, accounts, url, progress):
        return {}, []


def line(name: str, renewal: float, at_risk: float) -> dict:
    return {"Acc.Name": name, "At Risk Indicator": "01 Red", "Renewal Amt (NQ)": renewal, "AtRisk Amt (NQ)": at_risk}


def run_fetch(accounts, windows) -> None:
    cq = windows[0]
    a, b, c = accounts[0], accounts[1], accounts[2]
    recs = [line(f"{a.name}-US", 100.0, 40.0),
            line(f"{b.name}-US", 200.0, 50.0), line(f"{b.name}-JP", 300.0, 25.0),
            line("NOT AN INDUSTRY MARKET ACCOUNT-US", 999.0, 999.0)]
    stub = _StubIsc(recs)
    rows, notes = stub.renewal_at_risk(accounts[:3], "https://example.invalid/dash", cq)
    by = {r["account"]: r for r in rows}
    check("fetch: only selected accounts are returned", sorted(by) == sorted([a.name, b.name]), str(sorted(by)))
    check("fetch: amounts are added up per account", by[b.name]["renewal"] == 500.0 and by[b.name]["at_risk"] == 75.0, str(by[b.name]))
    check("fetch: line count and dashboard names are kept", by[b.name]["lines"] == 2 and "-JP" in by[b.name]["names"], str(by[b.name]))
    check("fetch: percent at risk is at risk over renewal", abs(by[b.name]["pct_at_risk"] - 0.15) < 1e-9
          and abs(by[a.name]["pct_at_risk"] - 0.4) < 1e-9)
    check("fetch: rows are sorted by account name", [r["account"] for r in rows] == sorted(by, key=str.lower))
    check("fetch: an account with no Red rows gets a note",
          any(n["account"] == c.name and n["area"] == "Renewal at risk" for n in notes), str(notes))
    check("fetch: no cap note under the cap", not any("10,000-record cap" in n["note"] for n in notes))
    c.isc_name = "The Display Name-US"
    _, dnotes = _StubIsc(recs).renewal_at_risk(accounts[:3], "https://example.invalid/dash", cq)
    check("fetch: a note carries the ISC display name", any(n["account"] == c.name and n.get("account_display") == "The Display Name-US" for n in dnotes), str(dnotes))
    c.isc_name = ""
    zero, _ = _StubIsc([line(f"{a.name}-US", 0.0, 0.0)]).renewal_at_risk([a], "u", cq)
    check("fetch: zero renewal gives a blank percent, not a division error", zero[0]["pct_at_risk"] is None)

    try:
        _StubIsc([]).renewal_at_risk([a], "u", cq)
        check("fetch: an empty dashboard result raises a clear error", False, "no error")
    except isc_mod.IscError as e:
        check("fetch: an empty dashboard result raises a clear error", "no records" in str(e), str(e))

    capped = [line("AAA CO-US", 1.0, 1.0)] * (dashboard.RECORD_CAP - 1) + [line("ADOBE SYSTEMS-US", 10.0, 5.0)]
    rows2, notes2 = _StubIsc(capped).renewal_at_risk(accounts[:3], "u", cq)
    cap_note = [n for n in notes2 if "10,000-record cap" in n["note"]]
    check("fetch: hitting the 10,000-record cap adds a note naming the last account",
          len(cap_note) == 1 and "ADOBE SYSTEMS" in cap_note[0]["note"], str(cap_note))
    check("fetch: an account sorting after the cut-off is flagged as possibly cut off",
          any("cut off" in n["note"] for n in notes2 if n["account"]), str([n for n in notes2 if n["account"]]))


def run_fallbacks(accounts, windows) -> None:
    cq = windows[0]
    a, b = accounts[0], accounts[1]
    cap = dashboard.RECORD_CAP
    filler = [line("AAA CO-US", 1.0, 1.0)] * (cap - 1)

    # By Account table fills in an account the capped detail query lacks.
    by_acct = [{"Acc.Name": f"{b.name}-US", "NQ Renewal": 1100000.0, "NQ RED": 1200.0, "NQ+1 Renewal": 5.0}]
    rows, notes = _StubIsc(filler + [line("ABC CO-US", 10.0, 5.0)], by_account=by_acct).renewal_at_risk([a, b], "u", cq)
    got = {r["account"]: r for r in rows}
    check("by account: an account missing from the detail query is read from the By Account table",
          b.name in got and got[b.name]["renewal"] == 1100000.0 and got[b.name]["at_risk"] == 1200.0, str(got.get(b.name)))
    check("by account: the line count is blank for those rows", got[b.name]["lines"] is None)
    check("by account: the note says where the amounts came from",
          any(n["account"] == b.name and "By Account" in n["note"] for n in notes), str(notes))
    check("by account: no 'may have been cut off' note for an account the table covered",
          not any(n["account"] == b.name and "cut off" in n["note"] for n in notes))
    check("by account: the percent at risk is computed", abs(got[b.name]["pct_at_risk"] - 1200.0 / 1100000.0) < 1e-9)

    # Detail rows win over the By Account table.
    both = _StubIsc([line(f"{a.name}-US", 100.0, 40.0)], by_account=[{"Acc.Name": f"{a.name}-US", "NQ Renewal": 9.0, "NQ RED": 9.0}])
    r_both, _ = both.renewal_at_risk([a], "u", cq)
    check("by account: the detail query wins when both have the account", r_both[0]["renewal"] == 100.0 and r_both[0]["lines"] == 1)

    # The By Account table alone is enough.
    only, _ = _StubIsc([], by_account=by_acct).renewal_at_risk([b], "u", cq)
    check("by account: the table alone gives a row when the detail query is empty", len(only) == 1)

    # A reverse-order copy covers the accounts the A to Z copy cut off.
    forward = filler + [line("ADOBE SYSTEMS-US", 10.0, 5.0)]
    backward = [line(f"{b.name}-US", 700.0, 70.0)] * 3 + [line("ZZZ CO-US", 1.0, 1.0)] * (cap - 3 - 1) + [line("AAB CO-US", 1.0, 1.0)]
    rev, rnotes = _StubIsc(forward, more=[backward]).renewal_at_risk([a, b], "u", cq)
    rb = {r["account"]: r for r in rev}
    check("reverse sort: an account only the reverse copy holds is reported",
          b.name in rb and rb[b.name]["renewal"] == 2100.0 and rb[b.name]["lines"] == 3, str(rb.get(b.name)))
    check("reverse sort: the cap note says which direction each copy ran",
          any("A to Z" in n["note"] for n in rnotes) and any("Z to A" in n["note"] for n in rnotes), str(rnotes))

    # An account that has no row anywhere is still flagged, with no By Account table.
    last_acct = max(accounts, key=lambda x: _norm_key(x.name))
    far, fnotes = _StubIsc([line("AAA CO-US", 1.0, 1.0)] * (cap - 1) + [line("ABC CO-US", 1.0, 1.0)]).renewal_at_risk([last_acct], "u", cq)
    check("cut off: an unmatched late account is still flagged without a By Account table",
          any(n["account"] == last_acct.name and "cut off" in n["note"] for n in fnotes), str(fnotes))
    near, _ = _StubIsc([line("AAA CO-US", 1.0, 1.0)] * (cap - 1) + [line("ABC CO-US", 1.0, 1.0)], by_account=by_acct).renewal_at_risk([b], "u", cq)
    check("cut off: with a By Account table the cut-off warning is not used", len(near) == 1)

    # Dashboard capture helpers.
    check("capture: the By Account result is recognised",
          dashboard._is_by_account([{"Acc.Name": "X-US", "NQ Renewal": 1.0, "NQ RED": 2.0}])
          and not dashboard._is_by_account([line("X-US", 1.0, 1.0)]))
    check("capture: the name column is found", dashboard.name_key({"Account Name": "X", "NQ Renewal": 1}) == "Account Name"
          and dashboard.name_key({"Acc.Name": "X"}) == "Acc.Name")

    # The CQ forecast note shows what the dashboard returned.
    dep = [{"Acc.Name": "ZEBRA CO-US", "4Q26 Call": 1.0}, {"Acc.Name": "ALPHA CO-US", "4Q26 Call": 1.0}]
    _, cqn = _StubIsc([], deployment=dep).cq_forecast([a], "u", cq)
    check("cq note: it lists the first and last account names the dashboard returned",
          any("'ALPHA CO-US' to 'ZEBRA CO-US'" in n["note"] and "2 account names" in n["note"] for n in cqn), str(cqn))


def _norm_key(s: str) -> str:
    return isc_mod._norm(s)


def run_quarters(accounts, windows) -> None:
    cq = windows[0]
    a, b = accounts[0], accounts[1]

    # Page-table parsing, with the figures of the Disney capture.
    th, td = (lambda t: ("TH", t)), (lambda t: ("TD", t))
    n4_tables = [[[th("All Qtr_ADJUSTED_RENEWAL_AMOUNT"), th("At Risk Indicator")],
                  [td("$575,056"), th("Red")], [td("$2,211,895"), th("Yellow")],
                  [td("$1,616,495"), th("Green")], [td("$2,107,870"), th("")]]]
    n4 = dashboard.parse_next4(n4_tables)
    check("page tables: Next 4 Quarters is split by indicator, a blank indicator is None",
          n4 == {"Red": 575056.0, "Yellow": 2211895.0, "Green": 1616495.0, "None": 2107870.0}, str(n4))
    check("page tables: a table without an At Risk Indicator header gives nothing", dashboard.parse_next4([[[th("X")], [td("$1")]]]) == {})
    check("page tables: amounts are parsed",
          dashboard._amount("$1,234") == 1234.0 and dashboard._amount("($5)") == -5.0 and dashboard._amount("-") is None
          and dashboard._amount("Red") is None)

    # The By Account widget's own query, taken from the dashboard definition.
    q = ("q_renewal = load \"GS_Renewals_At_Risk_Dataset\";\n"
         "q_renewal = filter q_renewal by 'Acc.Name' is not null;\n"
         "q_renewal = group q_renewal by 'Acc.Name';\n"
         "q_renewal_final = foreach q_renewal_final generate 'Acc.Name', " + ", ".join(f"'{c}'" for c in dashboard.RENEWAL_COLUMNS) + ";\n")
    bundle = {"asset": {"state": {"steps": {"other": {"query": "q = load \"X\";"}, "lens_24_4": {"query": q}}}},
              "datasets": [{"name": "GS_Renewals_At_Risk_Dataset", "id": "0FbDS", "currentVersionId": "0FcVER"}]}
    saql = dashboard.by_account_saql(bundle, "US Industry Market")
    check("by account saql: the dataset is loaded by id and version", saql is not None and 'load "0FbDS/0FcVER"' in saql
          and "GS_Renewals_At_Risk_Dataset" not in saql, str(saql))
    check("by account saql: the market filter comes before the account filter",
          saql is not None and saql.index("'Acc.Market_Name__c' == \"US Industry Market\"") < saql.index("'Acc.Name' is not null"))
    check("by account saql: no market filter when no market is given", "Market_Name" not in (dashboard.by_account_saql(bundle) or ""))
    check("by account saql: a definition without the widget gives None",
          dashboard.by_account_saql({"asset": {"state": {"steps": {}}}, "datasets": []}) is None
          and dashboard.by_account_saql({}) is None)
    check("by account saql: a changed query is not guessed at",
          dashboard.by_account_saql({**bundle, "asset": {"state": {"steps": {"s": {"query": q.replace("is not null", "is null")}}}}}, "M") is None)

    # renewal_quarters with the live record shape (Acc.Name plus the eight columns, zeros present).
    live = {"Acc.Name": "ACME CO-US", "NQ RED": 1221.0, "NQ Renewal": 1075019.0, "NQ+1 RED": 0.0, "NQ+1 Renewal": 1088564.0,
            "NQ+2 RED": 566102.0, "NQ+2 Renewal": 3349218.0, "NQ+3 RED": 7733.0, "NQ+3 Renewal": 998515.0}
    live["Acc.Name"] = f"{a.name}-US"
    lrows, _, _ = _StubIsc([], next4=n4, page=[live]).renewal_quarters([a], "u", cq)
    check("quarters: a live-shaped record fills all eight columns, with NQ+1 RED 0",
          len(lrows) == 1 and [lrows[0][c] for c in dashboard.RENEWAL_COLUMNS]
          == [1075019.0, 1221.0, 1088564.0, 0.0, 3349218.0, 566102.0, 998515.0, 7733.0], str(lrows))

    # renewal_quarters: select the market's accounts only, sum duplicates, note the missing ones.
    recs = [{"Account Name": f"{a.name}-US", "NQ Renewal": 100.0, "NQ RED": 10.0, "NQ+2 Renewal": 5.0},
            {"Account Name": f"{a.name}-JP", "NQ Renewal": 50.0, "NQ RED": 0.0},
            {"Account Name": "NOT AN INDUSTRY MARKET ACCOUNT-US", "NQ Renewal": 999.0}]
    stub = _StubIsc([], next4=n4, page=recs)
    rows, got4, notes = stub.renewal_quarters([a, b], "u", cq)
    check("quarters: only selected accounts, summed, with all eight columns",
          len(rows) == 1 and rows[0]["account"] == a.name and rows[0]["NQ Renewal"] == 150.0 and rows[0]["NQ RED"] == 10.0
          and rows[0]["NQ+1 RED"] == 0.0 and all(c in rows[0] for c in dashboard.RENEWAL_COLUMNS), str(rows))
    check("quarters: the Next 4 Quarters split is passed through", got4 == n4)
    check("quarters: an account missing from the table gets a note",
          any(n["account"] == b.name and "By Account" in n["note"] for n in notes), str(notes))
    rows0, n0, notes0 = _StubIsc([]).renewal_quarters([a], "u", cq)
    check("quarters: no table views gives empty results and one note", rows0 == [] and n0 == {} and len(notes0) == 1, str(notes0))


def run_html_quarters(accounts, windows) -> None:
    import html_builder

    data = demo_data(accounts, windows)
    data.update(accounts=accounts, opps_all=[], quarter_windows=windows, cq_window=windows[0], cq_summary=[], renewals=[],
                renewals_by_quarter=[], sections={"opportunities": True, "deployments": True, "gainsight": True})
    rq = data["renewal_quarters"]
    check("demo: sample data carries renewal quarter rows and the Next 4 Quarters split",
          len(rq) == len(accounts) and set(data["renewal_next4"]) == {"Red", "Yellow", "Green", "None"})
    out = Path(tempfile.mkdtemp()) / "rq.xlsx"
    report_builder.build_report(data, windows, out)
    wb = openpyxl.load_workbook(out)
    check("excel: Renewal At Risk by Quarter sheet exists after Renewal At Risk",
          "Renewal At Risk by Quarter" in wb.sheetnames
          and wb.sheetnames.index("Renewal At Risk by Quarter") == wb.sheetnames.index("Renewal At Risk") + 1, str(wb.sheetnames))
    ws = wb["Renewal At Risk by Quarter"]
    check("excel: quarter sheet headers match", [c.value for c in ws[1]] == report_builder.RAR_QUARTER_HEADERS)
    check("excel: quarter sheet has a TOTAL that adds the NQ Renewal column",
          ws.cell(len(rq) + 2, 1).value == "TOTAL" and abs(ws.cell(len(rq) + 2, 2).value - sum(r["NQ Renewal"] for r in rq)) < 0.01)
    flat = [c.value for row in ws.iter_rows() for c in row]
    check("excel: quarter sheet lists the Next 4 Quarters split", "No indicator" in flat and 575056.0 in flat)

    hpath = html_builder.build_html(data, windows, Path(tempfile.mkdtemp()) / "rq.html")
    page = hpath.read_text(encoding="utf-8")
    check("html: Renewal at risk tiles show the Next 4 Quarters split",
          all(f'{v}<span class="u">{u}</span>' in page for v, u in (("$575.1", "K"), ("$2.2", "M"), ("$1.6", "M"), ("$2.1", "M"))))
    check("html: the By account table has the eight dashboard columns and a TOTAL row",
          all(h in page for h in dashboard.RENEWAL_COLUMNS) and "TOTAL" in page)
    check("html: the old next quarter detail is kept", "Next quarter detail" in page and "Renewal amount (NQ)" in page)


def run_outputs(accounts, windows) -> None:
    data = demo_data(accounts, windows)
    rar = data["renewal_risk"]
    needed = ("account", "account_url", "names", "renewal", "at_risk", "pct_at_risk", "lines")
    check("demo: sample data carries renewal at risk rows", len(rar) > 0, f"{len(rar)} rows")
    check("demo: rows carry every field", all(k in r for r in rar for k in needed))
    data.update(accounts=accounts, opps_all=[], quarter_windows=windows, cq_window=windows[0], cq_summary=[], renewals=[],
                renewals_by_quarter=[], sections={"opportunities": True, "deployments": True, "gainsight": True})
    out = Path(tempfile.mkdtemp()) / "rar_check.xlsx"
    report_builder.build_report(data, windows, out)
    wb = openpyxl.load_workbook(out)
    check("excel: Renewal At Risk sheet exists", "Renewal At Risk" in wb.sheetnames, str(wb.sheetnames))
    ws = wb["Renewal At Risk"]
    check("excel: headers match", [c.value for c in ws[1]] == report_builder.RAR_HEADERS, str([c.value for c in ws[1]]))
    check("excel: one row per account plus TOTAL", ws.max_row - 1 == len(rar) + 1 and ws.cell(ws.max_row, 1).value == "TOTAL",
          f"{ws.max_row - 1} rows")
    check("excel: TOTAL adds the amounts", abs(ws.cell(ws.max_row, 3).value - sum(r["renewal"] for r in rar)) < 0.01
          and abs(ws.cell(ws.max_row, 4).value - sum(r["at_risk"] for r in rar)) < 0.01)
    check("excel: amounts are numbers", all(isinstance(ws.cell(2, i).value, (int, float)) for i in (3, 4)))
    check("excel: sheet follows Deployments", wb.sheetnames.index("Renewal At Risk") == wb.sheetnames.index("Deployments") + 1)

    data["sections"]["deployments"] = False
    out2 = Path(tempfile.mkdtemp()) / "rar_off.xlsx"
    report_builder.build_report(data, windows, out2)
    check("excel: sheet is dropped when deployments are off", "Renewal At Risk" not in openpyxl.load_workbook(out2).sheetnames)

    data["sections"]["deployments"] = True
    data["renewal_risk"] = []
    out3 = Path(tempfile.mkdtemp()) / "rar_empty.xlsx"
    report_builder.build_report(data, windows, out3)
    ws3 = openpyxl.load_workbook(out3)["Renewal At Risk"]
    check("excel: an empty result says so and points to Notes", "See Notes" in str(ws3.cell(2, 1).value), str(ws3.cell(2, 1).value))


def run_pipeline(accounts, windows) -> None:
    import settings

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

        stub = _StubIsc([line(f"{acct.name}-US", 1500.0, 600.0), line("OTHER CO-US", 7000000.0, 7000000.0)])
        _, html = run(stub)
        page = html.read_text(encoding="utf-8")
        check("html: renewal at risk section is rendered", 'id="sec-rar"' in page and "Renewal at risk" in page)
        check("html: the section lists the columns", all(h in page for h in ("Renewal amount (NQ)", "At-risk amount (NQ)", "% at risk", "Red lines")))
        check("html: the section shows the account and its amounts", f"{acct.name}-US" in page and "$1,500" in page and "40%" in page)
        check("html: an unselected account is not shown", "OTHER CO" not in page)
        check("html: the cap limitation is stated", "10,000" in page)
        check("html: no scripts", "<script" not in page.lower())
        _, html = run(stub, deploy=False)
        check("html: the section is dropped when deployments are off", 'id="sec-rar"' not in html.read_text(encoding="utf-8"))
        acct.isc_name = "The Display Name-US"
        try:
            _, html = run(stub)
            page = html.read_text(encoding="utf-8")
            check("html: the account heading and contents show the ISC display name",
                  page.count("The Display Name-US") >= 2 and f">{acct.name}</a>" not in page)
        finally:
            acct.isc_name = ""


        class _Failing(_StubIsc):
            def dashboard_data(self, *a, **k):
                raise isc_mod.IscError("Renewal test failure")

        _, html = run(_Failing([]))
        check("pipeline: a renewal failure becomes a note, not an exception", "Renewal test failure" in html.read_text(encoding="utf-8"))
    finally:
        pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot, pipeline.deployments.fetch, settings.load = saved


def main() -> int:
    accounts = load_accounts()[:12]
    windows = current_next_windows()
    run_fetch(accounts, windows)
    run_fallbacks(accounts, windows)
    run_quarters(accounts, windows)
    run_html_quarters(accounts, windows)
    run_outputs(accounts, windows)
    run_pipeline(accounts, windows)
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status:<5}{name:<{width + 2}}{detail}")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    print(f"\n{sum(1 for s, _, _ in results if s == 'PASS')} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

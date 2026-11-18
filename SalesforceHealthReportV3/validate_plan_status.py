"""Check the success plan rules (needs update, progression) and the three-state support ticket mapping.

    .venv\\Scripts\\python.exe validate_plan_status.py                      (Windows)
    .venv/bin/python validate_plan_status.py                                (Mac)

Uses synthetic inputs and sample data only. Nothing is read from ISC or Gainsight, and the real
reports/plan_status_latest.json is never touched.
Exit code 1 when any check fails, otherwise 0.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import openpyxl

import app as health_app
import gainsight
import pipeline
import plan_status as ps
from accounts import load_accounts

TODAY = date(2026, 3, 1)
results: list[tuple[bool, str]] = []


def check(name: str, got, want=True) -> None:
    results.append((got == want, f"{name}: got {got!r}, want {want!r}"))


def plan(gsid: str, status: str, days_ago: int | None, overdue: int = 0) -> dict:
    return {"gsid": gsid, "status": status, "account": "A", "days_overdue": overdue, "overdue": overdue > 0,
            "last_updated": (TODAY - timedelta(days=days_ago)).isoformat() if days_ago is not None else None}


# ---- cut-off choices ----
for value, want in ((30, 30), (60, 60), (90, 90), ("60", 60), (45, 30), (None, 30), ("x", 30), (0, 30), (-90, 30)):
    check(f"stale_cutoff({value!r})", ps.stale_cutoff(value), want)

# ---- needs update ----
for days, cutoff, want in ((29, 30, False), (30, 30, True), (31, 30, True), (59, 60, False), (60, 60, True),
                           (89, 90, False), (90, 90, True), (45, 90, False), (45, 30, True), (0, 30, False)):
    p = ps.flag_stale([plan("1", "Active", days)], cutoff, TODAY)[0]
    check(f"needs_update at {days} days, cut-off {cutoff}", p["needs_update"], want)
    check(f"days_since_update at {days} days", p["days_since_update"], days)
p = ps.flag_stale([plan("1", "Active", None)], 30, TODAY)[0]
check("missing date is not flagged", (p["needs_update"], p["days_since_update"]), (False, None))
p = ps.flag_stale([{"last_updated": (TODAY + timedelta(days=5)).isoformat()}], 30, TODAY)[0]
check("future date counts as 0 days", p["days_since_update"], 0)
p = ps.flag_stale([{"last_updated": "2026-01-01T10:00:00Z"}], 30, TODAY)[0]
check("ISO timestamp is read", p["days_since_update"], 59)
p = ps.flag_stale([{"last_updated": "not a date"}], 30, TODAY)[0]
check("unreadable date is not flagged", p["needs_update"], False)
p = ps.flag_stale([plan("1", "Active", 40)], 99, TODAY)[0]
check("junk cut-off falls back to 30", p["needs_update"], True)

# ---- attention and sort order ----
overdue = ps.flag_stale([plan("o", "Active", 40, overdue=12)], 30, TODAY)[0]
stale = ps.flag_stale([plan("s", "Active", 40)], 30, TODAY)[0]
fresh = ps.flag_stale([plan("f", "Active", 2)], 30, TODAY)[0]
check("attention overdue wins over needs update", ps.attention(overdue), "Overdue")
check("attention needs update", ps.attention(stale), "Needs update")
check("attention on track", ps.attention(fresh), "On track")
check("attention with no flags", ps.attention({}), "On track")
check("sort: overdue, needs update, then rest", [p["gsid"] for p in sorted([fresh, stale, overdue], key=ps.sort_key)],
      ["o", "s", "f"])
older = ps.flag_stale([plan("old", "Active", 80)], 30, TODAY)[0]
check("sort: longest untouched first", [p["gsid"] for p in sorted([stale, older], key=ps.sort_key)], ["old", "s"])

# ---- progression ----
a, b = plan("p1", "Defining", 5), plan("p2", "Active", 5)
snap = ps.apply_progression([a, b], None, TODAY)
check("first run: no baseline text", (a["progression"], b["progression"]), (ps.NO_BASELINE, ps.NO_BASELINE))
check("first run: snapshot holds both plans", sorted(snap["plans"]), ["p1", "p2"])
check("first run: snapshot stores the status", snap["plans"]["p1"]["status"], "Defining")

later = TODAY + timedelta(days=7)
a2, b2, c2 = plan("p1", "Defining", 5), plan("p2", "Complete", 5), plan("p3", "Active", 5)
snap2 = ps.apply_progression([a2, b2, c2], snap, later)
check("unchanged plan", a2["progression"], f"No change since {TODAY.isoformat()}")
check("changed plan", b2["progression"], f"Active to Complete on {later.isoformat()}")
check("changed plan previous status", b2["previous_status"], "Active")
check("new plan", c2["progression"], f"New since {later.isoformat()}")

latest = later + timedelta(days=7)
b3 = plan("p2", "Complete", 5)
ps.apply_progression([b3], snap2, latest)
check("prior change still shown while status stands", b3["progression"], f"Active to Complete on {later.isoformat()}")
snap3 = ps.apply_progression([b3], snap2, latest)
check("unread plans keep their saved entry", sorted(snap3["plans"]), ["p1", "p2", "p3"])
check("unread plan entry is unchanged", snap3["plans"]["p1"], snap2["plans"]["p1"])

nogs = {"status": "Active", "account": "A"}
ps.apply_progression([nogs], snap, TODAY)
check("plan without an id: no crash, empty progression", nogs["progression"], "")

# Successfully read companies replace their plan inventory, including an empty inventory. Companies that were
# not read keep their prior entries, and a plan that reappears after removal starts a new progression.
company_snapshot = ps.apply_progression([
    dict(plan("read-plan", "Active", 5), company_gsid="read-company"),
    dict(plan("unread-plan", "Defining", 5), company_gsid="unread-company"),
], None, TODAY)
check("snapshot records plan company", company_snapshot["plans"]["read-plan"]["company_gsid"], "read-company")
empty_read = ps.apply_progression([], company_snapshot, TODAY + timedelta(days=1),
                                  successfully_read_companies={"read-company"})
check("successful empty read removes old plan", sorted(empty_read["plans"]), ["unread-plan"])
check("unread company keeps its entry", empty_read["plans"]["unread-plan"],
      company_snapshot["plans"]["unread-plan"])
reopened = dict(plan("read-plan", "Active", 1), company_gsid="read-company")
reopened_snapshot = ps.apply_progression([reopened], empty_read, TODAY + timedelta(days=2),
                                         successfully_read_companies={"read-company"})
check("reopened plan is new", reopened["progression"], f"New since {(TODAY + timedelta(days=2)).isoformat()}")
failed_read = ps.apply_progression([], company_snapshot, TODAY + timedelta(days=1))
check("unsuccessfully read company keeps its entry", sorted(failed_read["plans"]),
      ["read-plan", "unread-plan"])

# ---- fiscal-year API validation ----
client = health_app.app.test_client()
for value in (None, "", "   ", "2000", "2100", 2000, 2100):
    response = client.post("/api/generate", json={"year": value})
    check(f"accepted fiscal year {value!r} reaches account validation",
          (response.status_code, response.get_json()["error"]), (400, "Select at least one account."))
for value in ("abc", "2025.5", "2e3", 1999, 2101, True, 2025.0):
    response = client.post("/api/generate", json={"year": value})
    check(f"invalid fiscal year {value!r} returns JSON 400",
          (response.status_code, response.is_json, "Fiscal year" in response.get_json()["error"]),
          (400, True, True))

# ---- snapshot file ----
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "sub" / "snap.json"
    check("missing snapshot reads as None", ps.read_snapshot(path), None)
    ps.write_snapshot(snap, path)
    check("snapshot round trip", ps.read_snapshot(path)["plans"], snap["plans"])
    path.write_text("{not json", encoding="utf-8")
    check("corrupt snapshot reads as None", ps.read_snapshot(path), None)
    path.write_text('{"plans": []}', encoding="utf-8")
    check("wrong shape reads as None", ps.read_snapshot(path), None)
    path.write_text("[1, 2]", encoding="utf-8")
    check("non-object reads as None", ps.read_snapshot(path), None)
check("default snapshot lives in reports", ps.SNAPSHOT.parent.name, "reports")

# ---- support tickets: three states, big pages ----
check("ticket states", gainsight.TICKET_STATES, ("Open", "IBM is working", "Awaiting customer feedback"))
check("ticket page size", gainsight.TICKET_PAGE_SIZE, 1000)
for status in ("Closed", "Closed - Archived", "Resolved", "Cancelled", "Solution provided"):
    check(f"closed ticket {status!r} is dropped", bool(gainsight._WANTED_STATUS.fullmatch(status)), False)
for status in ("Open", "Waiting Resolution", "Waiting for IBM", "SME Needed", "SME is Working", "In Progress",
               "IBM is working", "Awaiting your feedback", "Waiting on Customer Feedback"):
    check(f"open ticket {status!r} maps to a known state", gainsight.state_label(status) in gainsight.TICKET_STATES, True)

# ---- Success Plan tab link ----
# Opened live for Kroger and AT&T: the same tab id showed each company's own Success Plan tab.
CID = "1P02WPZ71GR8TFJ55WRQMKCYQ6FKF5C49005"
LIVE_URL = ("https://ibmsc--gainsight.vf.force.com/apex/GainsightNXT#customersuccess360?cid="
            "1P02WPZ71GR8TFJ55WRQMKCYQ6FKF5C49005#/9f34d482-b5a2-4a9b-a573-2c82d262f6c5")
check("Success Plan link matches the route opened live", gainsight.success_plan_url(CID), LIVE_URL)
check("Success Plan link for a missing company is empty",
      (gainsight.success_plan_url(""), gainsight.success_plan_url(None)), ("", ""))
check("Success Plan link is https", gainsight.success_plan_url("X1").startswith("https://"), True)

# ---- sample-data report ----
accounts = load_accounts()[:4]
saved = (pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot)
pipeline.REPORTS_DIR = Path(tempfile.mkdtemp())
pipeline.deployments.write_snapshot = lambda *a, **k: None
try:
    counts = {}
    for cutoff in (30, 60, 90):
        xlsx, html = pipeline.run_report(accounts, None, None, None, lambda p, m: None, demo=True, stale_days=cutoff)
        wb = openpyxl.load_workbook(xlsx)
        ws = wb["Success Plans"]
        headers = [c.value for c in ws[1]]
        check(f"workbook headers at {cutoff}", headers[8:], ["Last Updated", "Days Since Update", "Needs Update?",
                                                             "Percent Complete", "Open Tasks", "Previous Status",
                                                             "Progression"])
        rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
        linked = [c for c in ws["B"][1:] if c.value and c.hyperlink and c.hyperlink.target.startswith("https://")]
        check(f"every plan name links to its Success Plan tab at {cutoff}", len(linked), len(rows))
        counts[cutoff] = sum(1 for r in rows if r[10] == "Yes")
        badge_rows = sum(1 for r in rows if r[10] == "Yes" and not r[5])
        summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r and r[0]}
        key = f"Success plans needing update (no change in {cutoff}+ days)"
        check(f"summary row at {cutoff}", summary.get(key), counts[cutoff])
        page = html.read_text(encoding="utf-8")
        check(f"html tile at {cutoff}", f"Success plans needing update ({cutoff}+ days)" in page, True)
        check(f"html badge at {cutoff}", (">Needs update<" in page) == (badge_rows > 0), True)
        check(f"html plan columns at {cutoff}", all(h in page for h in ("Last Updated", "Percent Complete", "Progression")), True)
        check(f"html plan links at {cutoff}", page.count("GainsightNXT#customersuccess360?cid=") >= len(rows), True)
        tickets_ws = wb["Support Tickets"]
        theaders = [c.value for c in tickets_ws[1]]
        states = {r[theaders.index("State")] for r in tickets_ws.iter_rows(min_row=2, values_only=True) if r[0]}
        check(f"ticket states in workbook at {cutoff}", states <= set(gainsight.TICKET_STATES), True)
        check(f"no scripts at {cutoff}", "<script" not in page.lower(), True)
    check("longer cut-off flags fewer or equal plans", counts[30] >= counts[60] >= counts[90], True)
    check("30 flags more than 90 on sample data", counts[30] > counts[90], True)
    check("sample data writes no snapshot", (pipeline.REPORTS_DIR / ps.SNAPSHOT.name).exists(), False)
    _, html = pipeline.run_report(accounts, None, None, None, lambda p, m: None, demo=True, stale_days=45)
    check("invalid cut-off falls back to 30", "(30+ days)" in html.read_text(encoding="utf-8"), True)
finally:
    pipeline.REPORTS_DIR, pipeline.deployments.write_snapshot = saved

failed = [msg for ok, msg in results if not ok]
for ok, msg in results:
    if not ok:
        print("FAIL", msg)
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
print("FAIL" if failed else "PASS")
sys.exit(1 if failed else 0)

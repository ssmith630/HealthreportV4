"""Check the account health score rules in health_score.py against the approved design (HEALTH_SCORE_PROPOSAL.md).

    .venv\\Scripts\\python.exe validate_health_score.py                      (Windows)
    .venv/bin/python validate_health_score.py                                (Mac)

Uses small synthetic inputs only. Nothing is read from ISC or Gainsight.
Exit code 1 when any check fails, otherwise 0.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from types import SimpleNamespace

import gainsight
import health_score as hs
import renewals
from quarters import Window

TODAY = date(2026, 1, 15)
results: list[tuple[bool, str]] = []


def check(name: str, got, want) -> None:
    results.append((got == want, f"{name}: got {got!r}, want {want!r}"))


def plan(days_overdue: int, health: str = "") -> dict:
    return {"days_overdue": days_overdue, "renewal_health": health}


def ticket(sev: str) -> dict:
    return {"severity": sev}


def renewal(band: str, arr: float = 100000) -> dict:
    return {"band": band, "arr": arr, "owner": "A"}


def dep(target: date | None, deployed: date | None = None) -> dict:
    return {"target_decision_date": target.isoformat() if target else "",
            "deployed_date": deployed.isoformat() if deployed else ""}


# Plans: skip when none, then the overdue boundaries.
check("plans none", hs.plan_points([]), None)
for days, want in ((0, 100), (1, 60), (30, 60), (31, 30), (90, 30), (91, 0)):
    check(f"plans {days} days overdue", hs.plan_points([plan(days)]), want)
check("plans worst of several", hs.plan_points([plan(0), plan(45), plan(10)]), 30)

# Renewal Health.
for text, want in (("Green", 100), ("Healthy", 100), ("Yellow", 50), ("Watch", 50), ("Red", 0), ("At Risk", 0),
                   ("", None), ("Unknown wording", None), (None, None)):
    check(f"renewal health {text!r}", hs.renewal_health_points(text), want)

# Support.
check("support none", hs.support_points([]), 100)
check("support sev 3 and 4", hs.support_points([ticket("Severity 3"), ticket("Severity 4")]), 80)
check("support blank severity", hs.support_points([ticket("")]), 80)
check("support sev 2", hs.support_points([ticket("Severity 3"), ticket("Severity 2")]), 50)
check("support sev 1", hs.support_points([ticket("Severity 2"), ticket("Severity 1")]), 0)

# Renewal timing, using the same bands the Renewals table shows.
for band, want in ((renewals.BAND_LATER, 100), (renewals.BAND_180, 75), (renewals.BAND_90, 40),
                   (renewals.BAND_PAST, 0), (renewals.BAND_NONE, None)):
    check(f"timing {band}", hs.timing_points(band), want)
for days, want in ((181, renewals.BAND_LATER), (180, renewals.BAND_180), (91, renewals.BAND_180),
                   (90, renewals.BAND_90), (0, renewals.BAND_90), (-1, renewals.BAND_PAST)):
    check(f"band at {days} days", renewals.band(days), want)

# Deployments.
check("deployments none", hs.deployment_points([], TODAY), None)
check("deployments no dates", hs.deployment_points([dep(None)], TODAY), None)
check("deployments on time", hs.deployment_points([dep(TODAY + timedelta(days=1))], TODAY), 100)
check("deployments target today", hs.deployment_points([dep(TODAY)], TODAY), 100)
check("deployments past target", hs.deployment_points([dep(TODAY - timedelta(days=1))], TODAY), 40)
check("deployments past target but deployed",
      hs.deployment_points([dep(TODAY - timedelta(days=5), TODAY - timedelta(days=2))], TODAY), 100)

# Rating boundaries.
for score, want in ((100, hs.HEALTHY), (75, hs.HEALTHY), (74, hs.WATCH), (50, hs.WATCH), (49, hs.AT_RISK), (0, hs.AT_RISK)):
    check(f"rating at {score}", hs.rating_for(score), want)

# Worked example from the design: overdue 45 days, yellow, one Sev 2, renewal in 70 days, no deployments.
r = hs.score_account("Example", [plan(45, "Yellow")], [ticket("Severity 2")], renewal(renewals.BAND_90), [], TODAY,
                     tickets_read=True)
check("worked example score", r["score"], 42)
check("worked example rating", r["rating"], hs.AT_RISK)
check("worked example coverage", r["coverage"], 85)
check("worked example deployment skipped", r["points"]["deployment"], None)

# Coverage: a lone input is below 60 percent, so no rating.
r = hs.score_account("Thin", [plan(0)], [], None, [], TODAY)
check("thin coverage", r["coverage"], 25)
check("thin rating", r["rating"], hs.NO_DATA)
check("thin score still computed", r["score"], 100)
r = hs.score_account("Empty", [], [], None, [], TODAY)
check("empty score", r["score"], None)
check("empty rating", r["rating"], hs.NO_DATA)

# Coverage of 85 percent (no deployments) rates. Exactly 60 (plans, support, timing) rates. 40 does not.
r = hs.score_account("Enough", [plan(0, "Green")], [], renewal(renewals.BAND_LATER), [], TODAY, tickets_read=True)
check("coverage at 85 percent rates", (r["coverage"], r["rating"]), (85, hs.HEALTHY))
r = hs.score_account("Sixty", [plan(0)], [], renewal(renewals.BAND_LATER), [], TODAY, tickets_read=True)
check("coverage exactly 60 rates", (r["coverage"], r["rating"]), (60, hs.HEALTHY))
r = hs.score_account("Forty", [plan(0)], [], None, [dep(TODAY + timedelta(days=9))], TODAY)
check("coverage 40 is Not enough data", (r["coverage"], r["rating"]), (40, hs.NO_DATA))

# Support is skipped unless Gainsight answered the ticket read (tickets_read); a renewal row is not needed.
r = hs.score_account("NoRead", [plan(0, "Green")], [], None, [], TODAY)
check("support skipped when tickets were not read", r["points"]["support"], None)
r = hs.score_account("Read", [plan(0, "Green")], [], None, [], TODAY, tickets_read=True)
check("support scored when tickets were read, no renewal row", r["points"]["support"], 100)

# Renewal classification from ISC opportunities.
for rtype, name, want in (("Renewal Opportunity", "X", True), ("BP Renewal Opportunity", "X", True),
                          ("Software", "SaaS Renewal-1", False), ("", "SaaS Renewal-X", True),
                          ("", "Expansion deal", False), (None, None, False)):
    check(f"is_renewal {rtype!r} {name!r}", renewals.is_renewal(rtype, name), want)


def ropp(account: str, close: str, amount: float = 1000, rtype: str = "Renewal Opportunity") -> dict:
    return {"account": account, "name": "R", "stage": "Propose", "record_type": rtype, "record_url": "",
            "close_date": close, "amount": amount, "owner": "Pat"}


racc = [SimpleNamespace(name="A", isc_url=""), SimpleNamespace(name="B", isc_url=""), SimpleNamespace(name="C", isc_url="")]
rw = [Window("Q1 2026", date(2026, 1, 1), date(2026, 3, 31))]
rows = renewals.rows_from(racc, [ropp("A", "2026-06-30", 5), ropp("A", "2026-02-01", 7), ropp("B", "2026-01-01")], rw, TODAY)
check("rows_from one row per account with a renewal", [r["account"] for r in rows], ["A", "B"])
check("rows_from earliest close date wins", (rows[0]["renewal_date"], rows[0]["arr"]), ("2026-02-01", 7))
check("rows_from owner and stage", (rows[0]["owner"], rows[0]["stage"], "csm" in rows[0]), ("Pat", "Propose", False))
check("rows_from past due band", rows[1]["band"], renewals.BAND_PAST)
check("rows_from future band", rows[0]["band"], renewals.BAND_90)

# Floor rule: Sev 1 or red Renewal Health caps Healthy at Watch.
good = renewal(renewals.BAND_LATER)
r = hs.score_account("Sev1", [plan(0, "Green")], [ticket("Severity 1")], good, [dep(TODAY + timedelta(days=9))], TODAY,
                     tickets_read=True)
check("sev 1 score would be healthy", r["score"] >= hs.HEALTHY_MIN, True)
check("sev 1 capped at Watch", r["rating"], hs.WATCH)
check("sev 1 flag", r["flags"], [hs.FLAG_SEV1])
r = hs.score_account("Red", [plan(0, "Red")], [], good, [dep(TODAY + timedelta(days=9))], TODAY, tickets_read=True)
check("red health flag", r["flags"], [hs.FLAG_RED])
check("red health never Healthy", r["rating"] != hs.HEALTHY, True)
r = hs.score_account("Clean", [plan(0, "Green")], [], good, [dep(TODAY + timedelta(days=9))], TODAY, tickets_read=True)
check("clean account Healthy", (r["score"], r["rating"], r["flags"]), (100, hs.HEALTHY, []))

# score_all: ordering and the deployments switch.
accts = [SimpleNamespace(name=n) for n in ("Small risk", "Big risk", "Fine")]
data = {
    "accounts": accts, "tickets_read": ["Small risk", "Big risk", "Fine"],
    "plans": [dict(plan(120, "Red"), account="Small risk"), dict(plan(120, "Red"), account="Big risk"),
              dict(plan(0, "Green"), account="Fine")],
    "tickets": [], "deployments": [dict(dep(TODAY - timedelta(days=3)), account="Fine")],
    "renewals": [dict(renewal(renewals.BAND_PAST, 50), account="Small risk"),
                 dict(renewal(renewals.BAND_PAST, 900), account="Big risk"),
                 dict(renewal(renewals.BAND_LATER, 10), account="Fine")],
    "sections": {"deployments": True},
}
check("score_all order", [s["account"] for s in hs.score_all(data, TODAY)], ["Big risk", "Small risk", "Fine"])
check("deployments on counts", hs.score_all(data, TODAY)[2]["points"]["deployment"], 40)
data["sections"] = {"deployments": False}
check("deployments off ignored", hs.score_all(data, TODAY)[2]["points"]["deployment"], None)
# Support ticket statuses that are kept (Gainsight tickets).
for status, want in (("Open", True), ("Waiting Resolution", True), ("waiting_resolution", True),
                     ("Waiting on Customer Feedback", True), ("Waiting for Customer Feedback", True),
                     ("WAITING ON CUSTOMER FEEDBACK ", True), ("Waiting for customer response", True),
                     ("Awaiting Customer Feedback", True), ("Waiting on Customer", True),
                     ("Waiting for IBM", True), ("Awaiting your feedback", True), ("IBM is working", True),
                     ("In Progress", True), ("SME Needed", True), ("SME is Working", True),
                     ("Closed", False), ("Closed - Archived", False), ("Resolved", False),
                     ("Resolution Provided", False), ("Solution provided", False), ("Cancelled", False),
                     ("", False)):
    check(f"ticket status {status!r} kept", bool(gainsight._WANTED_STATUS.fullmatch(status)), want)
for status, state in (("Waiting on Customer Feedback", "Awaiting customer feedback"), ("Waiting for IBM", "Open"),
                     ("SME Needed", "Open"), ("SME is Working", "IBM is working"), ("In Progress", "IBM is working"),
                     ("IBM is working", "IBM is working"), ("Open", "Open"), ("Waiting Resolution", "Open"),
                     ("Awaiting your feedback", "Awaiting customer feedback"), ("", "")):
    check(f"state for {status!r}", gainsight.state_label(status), state)

failed = [msg for ok, msg in results if not ok]
for ok, msg in results:
    if not ok:
        print("FAIL", msg)
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
print("FAIL" if failed else "PASS")
sys.exit(1 if failed else 0)

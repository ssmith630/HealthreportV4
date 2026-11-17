"""Composite account health score. See HEALTH_SCORE_PROPOSAL.md for the design.

Five inputs give 0-100 points each. The score is the weight-averaged points of the inputs that have data; an input
with no data is skipped, never scored as 0. All weights and thresholds are constants here, so every report is
comparable.
"""
from __future__ import annotations

from datetime import date

import renewals
import report_builder     # module import: report_builder imports this module too

# Input key -> (label, weight). The weights sum to 100.
INPUTS = {
    "plans": ("Success plans", 25),
    "renewal_health": ("Renewal Health", 25),
    "support": ("Support", 20),
    "renewal_timing": ("Renewal timing", 15),
    "deployment": ("Deployments", 15),
}
HEALTHY_MIN = 75          # score at or above: Healthy
WATCH_MIN = 50            # score at or above: Watch; below: At risk
MIN_COVERAGE = 60         # percent of total weight that must have data, else "Not enough data"

HEALTHY, WATCH, AT_RISK, NO_DATA = "Healthy", "Watch", "At risk", "Not enough data"
RATING_BADGE = {HEALTHY: "green", WATCH: "yellow", AT_RISK: "red", NO_DATA: ""}
_RANK = {AT_RISK: 0, WATCH: 1, HEALTHY: 2, NO_DATA: 3}
FLAG_SEV1 = "Sev 1 ticket open"
FLAG_RED = "Renewal Health red"


def _date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def plan_points(plans: list[dict]) -> int | None:
    """No open plans: no data. Otherwise by the longest overdue plan."""
    if not plans:
        return None
    worst = max(int(p.get("days_overdue") or 0) for p in plans)
    if worst <= 0:
        return 100
    return 60 if worst <= 30 else 30 if worst <= 90 else 0


def renewal_health_points(value) -> int | None:
    """Blank or unrecognised wording: no data."""
    return {"green": 100, "yellow": 50, "red": 0}.get(report_builder.health_class(value))


def support_points(tickets: list[dict]) -> int:
    """The tickets read are all open (Open, Waiting Resolution or Waiting on Customer Feedback). A blank severity counts as Severity 3/4."""
    if not tickets:
        return 100
    sev = {t.get("severity") for t in tickets}
    return 0 if "Severity 1" in sev else 50 if "Severity 2" in sev else 80


def timing_points(band: str) -> int | None:
    return {renewals.BAND_LATER: 100, renewals.BAND_180: 75, renewals.BAND_90: 40, renewals.BAND_PAST: 0}.get(band)


def deployment_points(deployments: list[dict], today: date) -> int | None:
    """Rows with neither a deployed nor a target decision date are ignored. Any past its target and not deployed: 40."""
    known = [d for d in deployments if d.get("deployed_date") or _date(d.get("target_decision_date"))]
    if not known:
        return None
    late = any(not d.get("deployed_date") and _date(d.get("target_decision_date")) < today for d in known)
    return 40 if late else 100


def rating_for(score: float) -> str:
    return HEALTHY if score >= HEALTHY_MIN else WATCH if score >= WATCH_MIN else AT_RISK


def score_account(name: str, plans: list[dict], tickets: list[dict], renewal: dict | None,
                  deployments: list[dict], today: date | None = None, tickets_read: bool = False) -> dict:
    """Score one account. `renewal` is its renewals.rows_from row (None when ISC has no open renewal opportunity).

    Support is scored only when `tickets_read` is true, meaning Gainsight answered the ticket read for the account;
    otherwise "no tickets" could just mean the read failed or Gainsight was not used.
    """
    today = today or date.today()
    health = next((p["renewal_health"] for p in plans if p.get("renewal_health")), "")
    points = {
        "plans": plan_points(plans),
        "renewal_health": renewal_health_points(health),
        "support": support_points(tickets) if tickets_read else None,
        "renewal_timing": timing_points(renewal["band"]) if renewal else None,
        "deployment": deployment_points(deployments, today),
    }
    present = {k: v for k, v in points.items() if v is not None}
    weight = sum(INPUTS[k][1] for k in present)
    coverage = round(weight * 100 / sum(w for _, w in INPUTS.values()))
    score = round(sum(INPUTS[k][1] * v for k, v in present.items()) / weight) if weight else None

    flags = []
    if any(t.get("severity") == "Severity 1" for t in tickets):
        flags.append(FLAG_SEV1)
    if report_builder.health_class(health) == "red":
        flags.append(FLAG_RED)
    if score is None or coverage < MIN_COVERAGE:
        rating = NO_DATA
    else:
        rating = rating_for(score)
        if flags and rating == HEALTHY:       # floor rule: a Sev 1 ticket or red Renewal Health caps at Watch
            rating = WATCH
    return {"account": name, "score": score, "rating": rating, "coverage": coverage, "points": points,
            "flags": flags, "arr": (renewal or {}).get("arr"), "owner": (renewal or {}).get("owner") or ""}


def score_all(data: dict, today: date | None = None) -> list[dict]:
    """Score every account in `data`, At risk first, then by ARR (largest first)."""
    def group(rows):
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(r.get("account"), []).append(r)
        return out

    plans, tickets, deps = group(data.get("plans", [])), group(data.get("tickets", [])), group(data.get("deployments", []))
    renewal = {r["account"]: r for r in data.get("renewals", [])}
    read = set(data.get("tickets_read", []))
    show_deploy = (data.get("sections") or {}).get("deployments", True)
    rows = [score_account(a.name, plans.get(a.name, []), tickets.get(a.name, []), renewal.get(a.name),
                          deps.get(a.name, []) if show_deploy else [], today, a.name in read)
            for a in data.get("accounts", [])]
    return sorted(rows, key=lambda r: (_RANK[r["rating"]], -float(r["arr"] or 0), r["account"]))

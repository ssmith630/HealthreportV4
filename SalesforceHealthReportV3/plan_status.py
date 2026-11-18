"""Success plan freshness and progression.

``flag_stale`` marks a plan that has not been modified for ``stale_days`` or more days. The default is 30. The UI
offers 60 and 90 as longer cut-offs.

Gainsight keeps no log of status changes, so progression comes from a snapshot saved on every run
(``reports/plan_status_latest.json``, plan id -> status). The next run compares against it and reports "from X to Y".
The first run has nothing to compare with and says so. The snapshot holds only plan ids and status names, and the
``reports`` folder is not committed.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

STALE_DEFAULT = 30
STALE_CHOICES = (30, 60, 90)
SNAPSHOT = Path(__file__).resolve().parent / "reports" / "plan_status_latest.json"
NO_BASELINE = "No earlier run to compare"


def stale_cutoff(value) -> int:
    """30, 60 or 90 days. Anything else falls back to 30."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return STALE_DEFAULT
    return n if n in STALE_CHOICES else STALE_DEFAULT


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def flag_stale(plans: list[dict], stale_days: int = STALE_DEFAULT, today: date | None = None) -> list[dict]:
    """Set ``days_since_update`` and ``needs_update`` on each plan. A plan with no last-modified date is not flagged."""
    today = today or date.today()
    stale_days = stale_cutoff(stale_days)
    for p in plans:
        d = _as_date(p.get("last_updated"))
        p["days_since_update"] = max((today - d).days, 0) if d else None
        p["needs_update"] = p["days_since_update"] is not None and p["days_since_update"] >= stale_days
    return plans


def sort_key(p: dict) -> tuple:
    """Overdue plans first (longest overdue first), then plans needing an update (longest untouched first), then the rest."""
    return (-int(p.get("days_overdue") or 0), not p.get("needs_update"),
            -int(p.get("days_since_update") or 0), p.get("account") or "")


def attention(p: dict) -> str:
    """'Overdue', 'Needs update' or 'On track'. Overdue wins when both apply."""
    if p.get("overdue"):
        return "Overdue"
    return "Needs update" if p.get("needs_update") else "On track"


def read_snapshot(path: Path | None = None) -> dict | None:
    """The previous run's snapshot, or None when missing or unreadable."""
    path = path or SNAPSHOT
    if not path.exists():
        return None
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) and isinstance(snap.get("plans"), dict) else None


def apply_progression(plans: list[dict], previous: dict | None, today: date | None = None,
                      successfully_read_companies: set[str] | None = None) -> dict:
    """Set ``previous_status``, ``status_since`` and ``progression`` on each plan. Returns the snapshot to save.

    Plans from accounts that were not read this time keep their saved entry, so a run on a few accounts does not
    erase the history of the others. Entries for plans that disappeared from a successfully read company are removed.
    """
    today = today or date.today()
    old = (previous or {}).get("plans") or {}
    new = {k: v for k, v in old.items() if isinstance(v, dict)}
    successfully_read_companies = successfully_read_companies or set()
    current_company_plans = {
        (p.get("company_gsid"), p.get("gsid")) for p in plans if p.get("company_gsid") and p.get("gsid")
    }
    new = {
        k: v for k, v in new.items()
        if v.get("company_gsid") not in successfully_read_companies
        or (v.get("company_gsid"), k) in current_company_plans
    }
    for p in plans:
        gsid = p.get("gsid")
        status = p.get("status") or ""
        if not gsid:
            p.update(previous_status="", status_since="", progression=NO_BASELINE if previous is None else "")
            continue
        was = old.get(gsid) if isinstance(old.get(gsid), dict) else None
        if was is None:
            entry = {"status": status, "since": today.isoformat(), "from": ""}
            p["progression"] = NO_BASELINE if previous is None else f"New since {today.isoformat()}"
        elif was.get("status") == status:
            entry = {"status": status, "since": was.get("since") or today.isoformat(), "from": was.get("from") or ""}
            p["progression"] = (f"{entry['from']} to {status} on {entry['since']}" if entry["from"]
                                else f"No change since {entry['since']}")
        else:
            entry = {"status": status, "since": today.isoformat(), "from": was.get("status") or ""}
            p["progression"] = f"{entry['from'] or 'Unknown'} to {status} on {entry['since']}"
        company_gsid = p.get("company_gsid") or (was or {}).get("company_gsid")
        if company_gsid:
            entry["company_gsid"] = company_gsid
        p["previous_status"], p["status_since"] = entry["from"], entry["since"]
        new[gsid] = entry
    return {"generated": datetime.now().isoformat(timespec="seconds"), "plans": new}


def write_snapshot(snap: dict, path: Path | None = None) -> Path:
    path = path or SNAPSHOT
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return path

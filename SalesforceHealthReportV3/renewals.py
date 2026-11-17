"""Renewal rows and the renewals-by-quarter summary, classified from ISC opportunities (no Gainsight)."""
from __future__ import annotations

from datetime import date

from quarters import Window, quarter_label

EARLIER = "Earlier"

# Renewal risk bands, by days to the ISC renewal opportunity's close date. BAND_BADGE maps each band to a badge
# class of the HTML report; the band label itself is shown in both the HTML and the Excel output.
BAND_PAST = "Past due"
BAND_90 = "0-90 days"
BAND_180 = "91-180 days"
BAND_LATER = "Over 180 days"
BAND_NONE = "No date"
BAND_BADGE = {BAND_PAST: "red", BAND_90: "orange", BAND_180: "yellow", BAND_LATER: "green", BAND_NONE: ""}
CONCENTRATION_MIN = 3        # accounts renewing in the same calendar month that raise the alert


def is_renewal(record_type: str, name: str = "") -> bool:
    """True when an ISC opportunity is a renewal.

    The Opportunity Record Type decides ("Renewal Opportunity", "BP Renewal Opportunity"). Only when the report
    gives no record type is the opportunity name used ("RENEW ...", "SaaS Renewal-...").
    """
    if (record_type or "").strip():
        return "renewal" in record_type.lower()
    return "renew" in (name or "").lower()


def band(days_to_renewal: int | None) -> str:
    """Renewal risk band for a number of days to renewal (negative means the renewal date has passed)."""
    if days_to_renewal is None:
        return BAND_NONE
    if days_to_renewal < 0:
        return BAND_PAST
    if days_to_renewal <= 90:
        return BAND_90
    if days_to_renewal <= 180:
        return BAND_180
    return BAND_LATER


def concentration(rows: list[dict], minimum: int = CONCENTRATION_MIN) -> list[dict]:
    """Calendar months in which `minimum` or more accounts renew (renewal date today or later).

    Returns oldest month first: {"month": "2026-12", "accounts": 3, "arr": 1200000.0, "names": [...]}.
    """
    months: dict[str, dict] = {}
    for r in rows:
        if r["days_to_renewal"] is None or r["days_to_renewal"] < 0:
            continue
        m = months.setdefault(r["renewal_date"][:7], {"month": r["renewal_date"][:7], "accounts": 0, "arr": 0.0,
                                                      "names": []})
        m["accounts"] += 1
        m["arr"] += float(r["arr"] or 0)
        m["names"].append(r.get("account_display") or r["account"])
    return [m for _, m in sorted(months.items()) if m["accounts"] >= minimum]


def _sort_key(label: str) -> tuple[int, int]:
    q, year = label.split()
    return int(year), int(q[1:])


def rows_from(accounts, opps: list[dict], windows: list[Window], today: date | None = None) -> list[dict]:
    """One row per account that has an open ISC renewal opportunity, with its renewal quarter.

    `opps` are the renewal opportunities from Isc.renewal_opps. When an account has several, the one with the
    earliest close date is used (an open opportunity whose close date has passed is therefore Past due).
    The row's stage, ARR and owner are read from that opportunity record.
    """
    today = today or date.today()
    first: dict[str, tuple[date, dict]] = {}
    for o in opps:
        try:
            d = date.fromisoformat(str(o.get("close_date"))[:10])
        except ValueError:
            continue
        mine = first.get(o.get("account"))
        if mine is None or d < mine[0]:
            first[o.get("account")] = (d, o)
    rows = []
    for a in accounts:
        if a.name not in first:
            continue
        d, o = first[a.name]
        rows.append({
            "account": a.name, "account_display": getattr(a, "isc_name", "") or a.name, "account_url": a.isc_url,
            "renewal_date": d.isoformat(), "quarter": quarter_label(d, windows),
            "days_to_renewal": (d - today).days, "band": band((d - today).days),
            "arr": o.get("amount"), "stage": o.get("stage") or "", "owner": o.get("owner") or "",
            "opportunity": o.get("name") or "", "record_url": o.get("record_url") or "",
        })
    return rows


def by_quarter(rows: list[dict], windows: list[Window]) -> list[dict]:
    """Accounts and ARR renewing in each quarter, oldest first.

    Quarters before the first report window are grouped as "Earlier" (overdue or stale renewal dates).
    Every report window is listed, even when empty.
    """
    first = min(w.start for w in windows)
    buckets: dict[str, dict] = {w.label: {"quarter": w.label, "accounts": 0, "arr": 0.0} for w in windows}
    for r in rows:
        if not r["renewal_date"]:
            continue
        label = EARLIER if date.fromisoformat(r["renewal_date"]) < first else r["quarter"]
        b = buckets.setdefault(label, {"quarter": label, "accounts": 0, "arr": 0.0})
        b["accounts"] += 1
        b["arr"] += float(r["arr"] or 0)
    ordered = sorted((b for k, b in buckets.items() if k != EARLIER), key=lambda b: _sort_key(b["quarter"]))
    return ([buckets[EARLIER]] if EARLIER in buckets else []) + ordered

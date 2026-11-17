"""Current-quarter (CQ) forecast figures per account, from the Seller Forecast v3.0 dashboard.

Each figure is the dashboard's own per-account value for the current quarter:
- CQ Budget: ``<Q> Budget``.        - CQ Deployed: ``<Q> Deployed``.
- CQ Call / Upside / Stretch: ``<Q> Call`` / ``<Q> Upside`` / ``<Q> Stretch``.
- CQ B/(W) Budget: CQ Call minus CQ Budget.
- CQ Call to go: the dashboard's ``<Q> Call to Go`` (Call minus Deployed).
- CQ Pipeline: ``<Q> Pipeline``.
- FY Budget / Deployed / Call / Call to go / Pipeline: the same figures for the fiscal year (``FY<yy> ...``).

A figure is None (shown blank) when the dashboard has no value for the account.
"""
from __future__ import annotations


FY_KEYS = (("fy_budget", "FY Budget"), ("fy_deployed", "FY Deployed"), ("fy_call", "FY Call"),
           ("fy_call_to_go", "FY Call to Go"), ("fy_pipeline", "FY Pipeline"))


def summarize(figures: dict[str, dict], accounts: list) -> list[dict]:
    """One row per selected account, plus a final TOTAL row.

    ``figures`` maps account name to ``{"Budget", "Deployed", "Call", "Call to Go", "Upside", "Stretch",
    "Pipeline"}`` and the same names prefixed ``FY `` for the year.
    """
    rows = []
    for a in sorted(accounts, key=lambda x: x.name.lower()):
        f = figures.get(a.name, {})
        call_to_go = f.get("Call to Go")
        if call_to_go is None and f.get("Call") is not None and f.get("Deployed") is not None:
            call_to_go = f["Call"] - f["Deployed"]
        row = _row(a.name, getattr(a, "isc_url", ""), f.get("Budget"), f.get("Deployed"),
                   f.get("Call"), f.get("Upside"), f.get("Stretch"), call_to_go)
        row["account_display"] = getattr(a, "isc_name", "") or a.name
        row["pipeline"] = f.get("Pipeline")
        for key, col in FY_KEYS:
            row[key] = f.get(col)
        rows.append(row)
    if rows:
        totals = {k: _sum([r[k] for r in rows]) for k in ("budget", "deployed", "call", "upside", "stretch", "call_to_go")}
        total = _row("TOTAL", "", totals["budget"], totals["deployed"], totals["call"], totals["upside"],
                     totals["stretch"], totals["call_to_go"])
        for key in ("pipeline", *(k for k, _ in FY_KEYS)):
            total[key] = _sum([r[key] for r in rows])
        rows.append(total)
    return rows


def _sum(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def _row(account, url, budget, deployed, call, upside, stretch, call_to_go) -> dict:
    return {
        "account": account, "account_url": url, "budget": budget, "deployed": deployed,
        "call": call, "upside": upside, "stretch": stretch,
        "bw_budget": None if call is None or budget is None else call - budget,
        "call_to_go": call_to_go,
    }

"""Sample data so the report and UI can be tried without signing in to anything."""
from __future__ import annotations

from datetime import date, timedelta

from gainsight import success_plan_url
from quarters import Window


def demo_data(accounts, windows: list[Window], deploy_all_dates: bool = False) -> dict:
    today = date.today()
    stages = ["Qualify", "Engage", "Design", "Propose", "Negotiate", "Closing"]
    owners = ["Alex Rivera", "Jordan Lee", "Sam Patel", "Taylor Kim"]
    opps, plans, tickets, deployments, ela = [], [], [], [], []
    renewal_forecast: list[dict] = []
    cq_figures: dict[str, dict] = {}
    renewal_risk: list[dict] = []
    renewal_quarters: list[dict] = []
    renewal_opps: list[dict] = []
    notes = [
        {"area": "Demo", "account": "", "note": "This report uses SAMPLE data. Nothing was read from ISC or Gainsight."}
    ]
    for i, a in enumerate(accounts):
        w = windows[i % len(windows)]
        for j in range(2):
            close = w.start + timedelta(days=15 + 20 * j + i)
            amount = 75000 * (j + 1) + 12500 * (i % 7)
            opps.append({
                "account": a.name, "name": f"{a.name} - watsonx expansion {j + 1}",
                "owner": owners[(i + j) % len(owners)], "parent_account_number": f"{1000000 + i * 37}",
                "next_step": "Executive briefing scheduled" if j == 0 else "Awaiting procurement",
                "stage": stages[(i + j) % len(stages)],
                "created_date": (today - timedelta(days=200 - i)).isoformat(), "close_date": close.isoformat(),
                "amount": amount, "expected_revenue": amount * 0.6,
                "forecast_pipeline": "Commit" if j == 0 else "Upside",
                "record_type": "Software" if j == 0 else "Renewal",
                "last_modified": (today - timedelta(days=3 + i % 20)).isoformat(),
                "renewed_acv_total": amount * 0.4 if j else 0,
                "record_url": f"https://ibmsc.lightning.force.com/lightning/r/Opportunity/006{i:05d}{j:07d}AAA/view",
            })
        due = today + timedelta(days=(-45 if i % 3 == 0 else 30 + i))
        # Sample freshness: some plans untouched for 10 to 95 days, so the 30, 60 and 90 day cut-offs each show a difference.
        updated = today - timedelta(days=[10, 35, 65, 95][i % 4])
        moved = i % 4 == 1  # sample progression: every fourth plan moved since the "earlier run"
        plans.append({
            "account": a.name, "name": f"{a.name} adoption plan", "type": "Adoption",
            "plan_url": success_plan_url(f"DEMOCO{i:04d}"),
            "status": "Active" if moved else "In Progress", "gsid": f"DEMO{i:04d}",
            "renewal_health": ["Healthy", "Watch", "At Risk", "Critical", ""][i % 5],
            "owner": owners[i % len(owners)], "start_date": today - timedelta(days=120), "due_date": due,
            "overdue": due < today, "days_overdue": max((today - due).days, 0),
            "last_updated": updated, "percent_complete": 20 * (i % 5), "open_tasks": 4 - i % 4, "closed_tasks": i % 5,
            "previous_status": "Defining" if moved else "",
            "status_since": (today - timedelta(days=7)).isoformat() if moved else (today - timedelta(days=60)).isoformat(),
            "progression": (f"Defining to Active on {(today - timedelta(days=7)).isoformat()}" if moved
                            else f"No change since {(today - timedelta(days=60)).isoformat()}"),
        })
        for k in range(2):
            tid = f"TS{7000000 + i * 10 + k}"
            tickets.append({
                "account": a.name, "status": ("Open", "In Progress", "Waiting on Customer Feedback")[(i + k) % 3], "ticket_id": tid,
                "ticket_url": f"https://example.com/tickets/{tid}",
                "created": (today - timedelta(days=30 + k * 10 + i % 5)).isoformat(),
                "last_updated": (today - timedelta(days=1 + k * 4 + i % 3)).isoformat(),
                "summary": "Sample ticket: upgrade question" if k == 0 else "Sample ticket: license entitlement",
                "severity": "Severity 1" if (i + k) % 7 == 0 else "Severity 2",
                "brand": ["Data & AI", "Automation", "Hybrid Cloud", "Security"][(i + k) % 4],
                "product": ["watsonx.ai", "Cloud Pak for Data", "Maximo", "Instana", "Guardium", "Db2"][(i + 2 * k) % 6],
                "state": ["Open", "IBM is working", "Awaiting customer feedback"][(i + k) % 3],
            })
        for k, platform in enumerate(("zOS", "Dist")):
            entitled = 1200000 + 150000 * (i % 6) + 100000 * k
            deployed_amt = entitled * (0.45 + 0.1 * ((i + k) % 5))
            ela.append({
                "account": a.name, "account_url": "", "gsid": "", "company": a.name,
                "uci": f"{9000000 + i * 11}", "contract": f"{a.name} ELA agreement",
                "section": f"Section {k + 1}", "platform": platform,
                "end_date": (today + timedelta(days=200 + 30 * k + 10 * (i % 12))).isoformat(),
                "entitled": entitled, "deployed": deployed_amt, "undeployed": entitled - deployed_amt,
                "declared_vs_expected": deployed_amt - entitled * 0.5, "rate_pace_target": entitled * 0.5,
            })
        for k, (license_type, product) in enumerate((("SaaS", "watsonx Orchestrate - US"), ("Perpetual", "Maximo - US"))):
            renewal_forecast.append({
                "account": a.name, "account_url": "", "gsid": "", "company": a.name, "product": product,
                "license_type": license_type,
                "next_renewal": (today + timedelta(days=60 + 45 * k + 20 * (i % 9))).isoformat(),
                "acv": float(80000 + 20000 * ((i + k) % 7)),
                "health_score": None if k else float(45 + 5 * (i % 10)),
                "segment": ["Strategic", "Enterprise", "Select Territory"][i % 3],
                "product_stage": "In Use" if (i + k) % 2 == 0 else "Not In Use",
                "adoption_intent": "Product owned", "adoption_plan": (i + k) % 3 == 0, "get_well_plan": (i + k) % 7 == 0,
            })
        renewal_opps.append({
            "account": a.name, "name": f"{a.name} - SaaS Renewal",
            "close_date": (today + timedelta(days=(-20 if i % 9 == 0 else 25 * (i % 12) + 10))).isoformat(),
            "amount": 150000 + 35000 * (i % 8), "stage": ["Qualify", "Propose", "Negotiate", "Closing"][i % 4],
            "record_type": "Renewal Opportunity", "owner": owners[(i + 1) % len(owners)],
            "record_url": f"https://ibmsc.lightning.force.com/lightning/r/Opportunity/006R{i:05d}{0:06d}AAA/view",
        })
        budget = 120000 + 10000 * (i % 6)
        call = 40000 + 15000 * (i % 5)
        deployed_q = 25000 + 5000 * (i % 4) if i % 5 == 0 else 0
        cq_figures[a.name] = {"Budget": budget, "Deployed": deployed_q, "Call": call, "Call to Go": call - deployed_q,
                              "Upside": 20000 + 5000 * (i % 3), "Stretch": 10000 * (i % 4),
                              "Pipeline": 60000 + 8000 * (i % 7), "FY Budget": budget * 4, "FY Deployed": deployed_q * 2,
                              "FY Call": call * 4, "FY Call to Go": call * 4 - deployed_q * 2,
                              "FY Upside": 60000, "FY Stretch": 30000, "FY Pipeline": 240000 + 8000 * (i % 7)}
        base = 80000 + 9000 * (i % 7)
        renewal_quarters.append({
            "account": a.name, "account_url": "",
            "NQ Renewal": float(base), "NQ RED": float(base * 0.2 if i % 3 != 2 else 0),
            "NQ+1 Renewal": float(base * 1.1), "NQ+1 RED": 0.0,
            "NQ+2 Renewal": float(base * 2), "NQ+2 RED": float(base * 0.3 if i % 2 == 0 else 0),
            "NQ+3 Renewal": float(base * 0.8), "NQ+3 RED": float(base * 0.05)})
        if i % 3 != 2:  # some sample accounts have no Red renewal lines
            renewal_amt = 90000 + 12000 * (i % 9)
            at_risk = renewal_amt * (0.3 + 0.1 * (i % 5))
            renewal_risk.append({"account": a.name, "account_url": "", "names": f"{a.name.upper()}-US",
                                 "renewal": float(renewal_amt), "at_risk": float(at_risk),
                                 "pct_at_risk": at_risk / renewal_amt, "lines": 1 + i % 3})
        deploy_stages = ["Commit", "Propose", "Adopt"]
        for k in range(1 + i % 2):
            cq = windows[0]
            target = cq.start + timedelta(days=min(10 + 25 * k + i % 50, (cq.end - cq.start).days))
            deployed = (cq.start + timedelta(days=5 + i % 20)).isoformat() if i % 5 == 0 and k == 0 else ""
            deployments.append({
                "amount": 40000 + 15000 * ((i + k) % 6),
                "account": a.name, "account_url": "", "name": f"DEP-{50000 + i * 10 + k}",
                "product": ["Maximo", "watsonx Orchestrate", "Instana"][(i + k) % 3],
                "stage": deploy_stages[(i + k) % len(deploy_stages)],
                "state_of_engagement": ["Planning", "In progress", "Complete"][(i + k) % 3],
                "target_decision_date": target.isoformat(), "deployed_date": deployed,
                "owner": owners[(i + k) % len(owners)],
                "last_modified": (today - timedelta(days=2 + i % 15)).isoformat(),
                "record_url": f"https://ibmsc.lightning.force.com/a0r{i:05d}{k:07d}AAA",
            })
        if deploy_all_dates and i % 3 == 0:  # sample deployments outside the current quarter
            prior = i % 2 == 0
            target = windows[0].start - timedelta(days=40) if prior else windows[-1].start + timedelta(days=20)
            deployments.append({
                "amount": 30000 + 10000 * (i % 4), "account": a.name, "account_url": "",
                "name": f"DEP-{60000 + i}", "product": "Maximo", "stage": "Commit", "state_of_engagement": "In progress",
                "target_decision_date": target.isoformat(),
                "deployed_date": (target + timedelta(days=10)).isoformat() if prior and i % 4 == 0 else "",
                "owner": owners[i % len(owners)], "last_modified": (today - timedelta(days=5)).isoformat(),
                "record_url": f"https://ibmsc.lightning.force.com/a0r9{i:05d}0000000AAA",
            })
        if a.needs_review:
            notes.append({"area": "Accounts", "account": a.name,
                          "note": "Account match is marked 'review'. Confirm the ISC and Gainsight IDs."})
    return {"opps": opps, "plans": plans, "tickets": tickets, "deployments": deployments, "notes": notes,
            "cq_figures": cq_figures, "renewal_opps": renewal_opps, "tickets_read": [a.name for a in accounts],
            "ela": ela, "renewal_risk": renewal_risk, "renewal_forecast": renewal_forecast,
            "renewal_quarters": renewal_quarters,
            "renewal_next4": {"Red": 575056.0, "Yellow": 2211895.0, "Green": 1616495.0, "None": 2107870.0}}

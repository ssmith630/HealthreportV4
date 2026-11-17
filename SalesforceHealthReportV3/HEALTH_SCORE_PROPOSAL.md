# Composite account health score: proposal for approval

Status: **implemented and approved.** The code is in `health_score.py` and is checked by `validate_health_score.py` against synthetic and demo data only. Nothing has been run against live ISC or Gainsight. Every weight and threshold below is a judgement, not derived from outcome data.

## 1. Why and what

V3 shows account health as separate signals: success plans, the ISC Renewal Health text, renewal dates, tickets and deployments. Nothing combines them, so a reader has to scan five sections per account to decide which accounts need attention first.

The Bob Factor offers no health-score model to reuse. Its `installed-base.md` defines renewal risk bands only, and its account-brief skill ranks by amount with tie-breakers and avoids complex scoring. This is a new design. It borrows one principle from the Bob Factor: keep the logic simple enough to explain in one sentence per input.

## 2. Design principles

1. **Additive and transparent.** Each input gives 0 to 100 points. The score is a weighted average. Every account shows its per-input points, so nobody has to trust a black box.
2. **Missing data is not bad data.** If an input is not available for an account, skip it and re-weight the others. Never score it as 0.
3. **Show coverage.** Display "based on 3 of 5 inputs". Below 60% of total weight, show "Not enough data" instead of a colour.
4. **Health is not size.** ARR is not an input. It is used to order accounts and to show ARR at risk, so a small account cannot hide a large one.
5. **Pipeline is not health.** Open opportunities and the CQ call are growth signals. They stay out of the score and keep their own sections.

## 3. Inputs, scoring and weights

All inputs use fields V3 already pulls. Field names come from my earlier read of `pipeline.py`, `html_builder.py` and `report_builder.py`. I have not yet read `gainsight.py` or `isc.py` to confirm each field's exact content, so item 3 under Open questions applies.

| # | Input | Source field | Points rule | Weight (assumed) |
|---|-------|--------------|-------------|------------------|
| 1 | Success plan execution | Gainsight plans: `overdue`, `days_overdue` | No plans: skip. No overdue plans: 100. Any overdue: 60 if the worst is ≤30 days, 30 if 31–90, 0 if over 90. | 25 |
| 2 | ISC Renewal Health | `renewal_health` mapped by `health_class()` | green 100, yellow 50, red 0, blank or unrecognised: skip | 25 |
| 3 | Support load | Tickets: `severity`, `state` (open only). Scored only when Gainsight answered the ticket read for the account (`tickets_read`); otherwise skipped. | No tickets: 100. Open Sev 3/4 only: 80. Any open Sev 2: 50. Any open Sev 1: 0. | 20 |
| 4 | Renewal timing | `renewals.band` from `days_to_renewal`, taken from the close date of the account's earliest open ISC renewal opportunity | Over 180 days: 100. 91–180: 75. 0–90: 40. Past due: 0. No date: skip. | 15 |
| 5 | Deployment status | Deployments: `stage`, `target_decision_date`, `deployed_date` | No deployments: skip. All deployed or on track: 100. Any past target and not deployed: 40. | 15 |

Weights sum to 100.

Rationale for the weights (all assumptions):
- Plans and Renewal Health are the two signals a CSM maintains directly, so they carry the most.
- Support is a leading indicator but noisy, so it sits just below.
- Renewal timing and deployments are context rather than distress, so they carry less.

## 4. Formula

```
score = sum(weight_i * points_i for inputs present) / sum(weight_i for inputs present)
coverage = sum(weight_i for inputs present) / 100
```

Mapping:

| Score | Rating | Badge |
|-------|--------|-------|
| 75–100 | Healthy | green |
| 50–74 | Watch | yellow |
| 0–49 | At risk | red |
| coverage below 60% | Not enough data | grey |

Optional floor rule (see Open questions): an account with ISC Renewal Health red, or a Sev 1 ticket open, cannot rate better than Watch, however good its other inputs are.

## 5. Worked example

Account with overdue plan 45 days (30), Renewal Health yellow (50), one open Sev 2 ticket (50), renewal in 70 days (40), no deployments (skipped):

- Weights present: 25 + 25 + 20 + 15 = 85. Coverage 85%.
- Weighted points: 25×30 + 25×50 + 20×50 + 15×40 = 750 + 1250 + 1000 + 600 = 3600.
- Score: 3600 / 85 = **42, At risk.**

## 6. How it would appear

**HTML dashboard** (no scripts, same constraint as today)
- A "Health score" column in the account index and a badge in each account section, with a title or small text line showing the points per input and the coverage.
- A new KPI tile counting At risk accounts and the ARR they carry, linking to the account index like the existing tiles.

**Excel**
- A new "Account Health" sheet: Account, Score, Rating, Coverage, one points column per input, ARR, Opportunity Owner. Sorted At risk first, then by ARR descending.
- Score and Rating on the Summary sheet, with the weights printed on the sheet so the number can be audited.

## 7. Implementation sketch (for after approval)

- New module `health_score.py`, pure functions, no I/O: `score_account(name, plans, tickets, renewal_row, deployments, today=None, tickets_read=False) -> dict`.
- Called from `pipeline.py` after the data is gathered, writing `data["health_scores"]`.
- Weights and thresholds held as constants at the top of the module (not overridable in settings; see section 8).
- Checks added alongside the existing `validate_*.py` scripts, covering: missing inputs, coverage rule, each threshold boundary, and the worked example above.

## 8. Decisions

1. **Inputs and weights:** approved as written in section 3.
2. **Floor rule:** approved. A Sev 1 ticket or red Renewal Health caps the rating at Watch.
3. **Source fields:** resolved by reading the connector code. Tickets are already limited to status Open or Waiting Resolution, of any age. Severity is normalised to "Severity 1" to "Severity 4". Deployments cover the current quarter only, unless "Include deployments outside the current quarter" is ticked; a deployment counts as deployed when it has a production date. Ticket column names and real ISC stage values are not verified against the live tenants.
4. **CQ call versus budget:** left out of the score. It is a growth signal.
5. **Weights:** fixed as constants in `health_score.py` so every report is comparable. They are not editable in settings.

## 9. Caveats

- The weights are my judgement, not derived from outcome data. Once reports accumulate, the best check is whether At risk accounts match the accounts your CSMs would flag themselves.
- V3's live-system caveats still apply. The Gainsight field names and the ISC Renewal Health column have not been tested against the live tenants.
- A single number can hide the one thing that matters. The per-input points stay visible for that reason.

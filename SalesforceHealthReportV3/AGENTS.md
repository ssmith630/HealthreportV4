# AGENTS.md

Guidance for AI coding agents working in this folder. Read `README.md` for user-facing behavior and `SETUP.md` for installation.

## What this is

Industry Market Account Health Report (V3). A local Flask app that reads IBM Sales Cloud (ISC) and Gainsight through the user's own signed-in Chrome or Edge, then writes one Excel workbook and one HTML dashboard. It runs on the user's computer at `http://127.0.0.1:8765`.

## Design constraints (do not break)

- No Playwright, Selenium or chromedriver. The browser is driven over the Chrome DevTools Protocol (CDP) in `browser.py`, using `websocket-client`.
- No stored tokens, passwords or cookies. Sign-in happens in the browser window the app opens. Do not add credentials to files, `.env` or logs.
- The HTML dashboard has no scripts. It is self-contained and opens offline.
- ISC rejects REST and SOQL calls made with a browser session. Data comes from saved ISC reports read through the Lightning UI, and from the Seller Forecast dashboard's own query responses.
- The sources are best-effort. A failure becomes a row on the Notes sheet, not an exception.
- Keep changes minimal and report only what you verified. Say "not verified" for anything you did not run.

## Layout

| File | Role |
|---|---|
| `app.py` | Flask routes and the web page (`static/index.html`). Port from `APP_PORT`, default 8765. |
| `pipeline.py` | One call that runs every source, then builds Excel and HTML (`run_report`). |
| `browser.py` | Starts or attaches to Chrome or Edge over CDP. |
| `isc.py`, `isc_report.py` | Read saved ISC reports (opportunities, deployments). `_report` returns the report view, `report_rows` turns it into row dicts. |
| `dashboard.py`, `cq_metrics.py` | Current-quarter forecast figures from the Seller Forecast v3.0 dashboard. |
| `gainsight.py` | Success plans and support tickets. |
| `plan_status.py` | Stale flag for success plans (30, 60 or 90 days) and status progression against `reports/plan_status_latest.json`. |
| `accounts.py` | Loads `industry_accounts_gainsight_resolved.json` into `Account` objects. Companies with `"non_us": true` go to `skipped_companies`, not `gs_companies`. |
| `deployments.py`, `renewals.py`, `quarter_compare.py` | Normalise and render one section each. |
| `health_score.py` | Composite 0-100 account score. Design in `HEALTH_SCORE_PROPOSAL.md`. |
| `quarters.py` | Quarter windows. IBM fiscal year equals the calendar year. |
| `report_builder.py`, `html_builder.py` | Excel (openpyxl) and HTML output. |
| `demo_data.py` | Sample data so the app runs without signing in. |
| `settings.py` | Report links saved to `settings.json` (git-ignored). |
| `launcher/` | Windows one-click start: `Health report launcher.html` opens `healthreport://start`, which `register_link.cmd` maps to `start_from_link.cmd` (CRLF, ASCII, ignores the link's content). It starts the app, which opens the browser itself (`NO_BROWSER` is cleared), or opens the browser when the app is already running. Undo with `unregister_link.cmd`. |
| `deployment_account_aliases.json` | Maps ISC deployment report account names to account numbers. MARRIOTT VACATION CLUB has its own alias, so its deployments are not counted under MARRIOTT. |

## Run

Use the private environment. From this folder:

- Windows: `.\.venv\Scripts\python.exe app.py`, or `py -3 launch.py`
- macOS: `.venv/bin/python app.py`, or `python3 launch.py`

`launch.py` creates `.venv` from `requirements.txt` on first run. Python 3.10 or newer is required.

## Checks

There is no test framework. The `validate_*.py` scripts are the tests. Run them after any change, from this folder:

```
.\.venv\Scripts\python.exe validate_health_score.py
.\.venv\Scripts\python.exe validate_deployments.py          # add --live for the signed-in ISC
.\.venv\Scripts\python.exe validate_accounts.py             # --master <file> to point at another master list
.\.venv\Scripts\python.exe validate_plan_status.py            # also: validate_ela, validate_gainsight_renewals, validate_deployment_forecast, validate_renewal_at_risk
.\.venv\Scripts\python.exe -m py_compile <file>.py
```

- `validate_deployments.py --live` needs Chrome signed in to ISC. Without it the live checks are skipped.
- A full live report has not been verified end to end, and the app has not been run on a Mac from this code.

## Data facts worth knowing

- The deployments report is "Copy of deployment for ISC accounts" (`settings.DEFAULT_DEPLOY_REPORT`). It is cut off at 2,000 rows, so `isc.py` re-runs it per Stage and per account batch.
- The deployment date used for the quarter filter and the health score is Target Decision Date (the ISC report labels it "Target Commit Date"), plus the production date. The report has no Environment, Change / Ticket, Target Deployment Date or CSM Managed column.
- Some account names differ between the accounts file and ISC. Add them to `deployment_account_aliases.json` rather than changing code.
- Renewals are classified from ISC opportunities only, not from Gainsight.

## Files and folders

- Do not edit or delete `.venv` or `.browser_profile`. The profile holds the ISC sign-in session.
- `reports/` holds generated output and is git-ignored. The app recreates it. Do not edit files in it.
- Never open or print `.env` or any `*.env` file.
- Source files use LF line endings, except `dashboard.py` and `isc_report.py`, which use CRLF. Keep each file's existing endings.

## Style

- Python with `from __future__ import annotations`, standard library plus the packages in `requirements.txt`.
- Follow IBM style for user-visible text: sentence-style capitalization, no emojis, plain wording.
- Update `README.md`, `static/index.html` and the matching `validate_*.py` check when a user-visible column or rule changes.

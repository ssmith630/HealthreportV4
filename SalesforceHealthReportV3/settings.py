"""User settings saved by the app (report links). Stored in settings.json, which is git-ignored.

Report links are typed into the Connect tab, so nobody edits config files.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / "settings.json"
LIGHTNING = "https://ibmsc.lightning.force.com"

# The saved ISC report that lists open opportunities (the report V2 used). Users can replace it
# on the Connect tab. It must be visible to the person running the app.
DEFAULT_OPP_REPORT = f"{LIGHTNING}/lightning/r/Report/00OgR000000KWLVUA4/view?queryScope=userFolders"
# The saved ISC report of the Deployment object. Users can replace it on the Connect tab.
DEFAULT_DEPLOY_REPORT = f"{LIGHTNING}/lightning/r/Report/00OgR000000cvenUAA/view?queryScope=userFolders"

# The Seller and Manager Deployment Forecast & Renewals CRM Analytics dashboard: deployment forecast and renewal at risk.
DEFAULT_DASHBOARD = (f"{LIGHTNING}/lightning/page/analytics?wave__assetType=dashboard"
                     "&wave__assetId=0FKgR0000001FRdWAM&wave__folderId=00l3h000000tolWAAQ")

_REPORT_ID = re.compile(r"/Report/([A-Za-z0-9]{15,18})")
_DASH_ID = re.compile(r"wave__assetId=(0FK[A-Za-z0-9]{12,15})")


def normalize_dashboard_url(value: str) -> str:
    """Accept a pasted dashboard link (or bare 0FK dashboard id) and return a clean Lightning URL."""
    value = (value or "").strip()
    if not value:
        return ""
    m = _DASH_ID.search(value)
    if m:
        did = m.group(1)
    elif re.fullmatch(r"0FK[A-Za-z0-9]{12,15}", value):
        did = value
    else:
        raise ValueError("That does not look like an ISC dashboard link. Open the Deployment Forecast dashboard in ISC and copy its address.")
    return f"{LIGHTNING}/lightning/page/analytics?wave__assetType=dashboard&wave__assetId={did}"


def normalize_report_url(value: str) -> str:
    """Accept a pasted report link (or bare report id) and return a clean Lightning URL."""
    value = (value or "").strip()
    if not value:
        return ""
    m = _REPORT_ID.search(value)
    if m:
        rid = m.group(1)
    elif re.fullmatch(r"00O[A-Za-z0-9]{12,15}", value):
        rid = value
    else:
        raise ValueError("That does not look like an ISC report link. Open the report in ISC and copy its address.")
    return f"{LIGHTNING}/lightning/r/Report/{rid}/view?queryScope=userFolders"


def load() -> dict:
    data: dict = {}
    if PATH.exists():
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    return {
        "opp_report_url": data.get("opp_report_url") or os.environ.get("ISC_OPP_REPORT_URL", "").strip() or DEFAULT_OPP_REPORT,
        "deploy_report_url": data.get("deploy_report_url") or os.environ.get("ISC_DEPLOY_REPORT_URL", "").strip() or DEFAULT_DEPLOY_REPORT,
        "dashboard_url": data.get("dashboard_url") or os.environ.get("ISC_FORECAST_DASHBOARD_URL", "").strip() or DEFAULT_DASHBOARD,
    }


def save(opp_report_url: str, deploy_report_url: str = "", dashboard_url: str = "") -> dict:
    values = {
        "opp_report_url": normalize_report_url(opp_report_url) or DEFAULT_OPP_REPORT,
        "deploy_report_url": normalize_report_url(deploy_report_url) or DEFAULT_DEPLOY_REPORT,
        "dashboard_url": normalize_dashboard_url(dashboard_url) or DEFAULT_DASHBOARD,
    }
    PATH.write_text(json.dumps(values, indent=1), encoding="utf-8")
    return values

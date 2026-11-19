"""Gainsight success plans, using cookies harvested from the same signed-in browser.

We open one ISC account page so the browser holds the Gainsight cookies, harvest them, and
call the Gainsight REST API with ``requests``. The ``apt.sid`` cookie can take minutes to
appear (or never appear), so we do not wait for it: as soon as ``sid_sf`` and ``apt.uid``
are present we try one small company query, and keep the cookies if Gainsight accepts them.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

import requests

import plan_status
from browser import Browser, BrowserError, get_browser

BASE = "https://ibmcs.gainsightcloud.com"
DEFAULT_VIEW_ID = "1I0067OM8R6W0YBFLF81M6MWB8BXMOIZMP56"
LIGHTNING = "https://ibmsc.lightning.force.com"
COOKIE_NAMES = ("sid_sf", "apt.sid", "apt.uid", "AWSALB", "AWSALBCORS")


# The Success Plan tab of the Customer 360 page. The id is the same for every company: the route
# was opened for Kroger and again for AT&T and showed that company's own plans each time.
SUCCESS_PLAN_TAB_ID = "9f34d482-b5a2-4a9b-a573-2c82d262f6c5"


def success_plan_url(company_gsid: str | None) -> str:
    """Link to the Success Plan tab of a company's Customer 360 page.

    Gainsight documents no link to a single success plan, so each plan links to its company's tab.
    The link goes straight to Gainsight (the ISC Lightning wrapper landed on Gainsight Home), so
    the user must be signed in to ISC. Returns "" when the company id is missing.
    """
    if not company_gsid:
        return ""
    return ("https://ibmsc--gainsight.vf.force.com/apex/GainsightNXT#customersuccess360?cid="
            + str(company_gsid) + "#/" + SUCCESS_PLAN_TAB_ID)

# The C360 "Support Tickets (updated daily)" report widget (Generic Support Data object).
TICKET_REPORT_ID = "231bc01b-f6bc-4fbf-8aa7-4cd8aa6f0f13"
TICKET_SECTION_ID = "067eeee1-63af-42fd-a135-c3d43c7c78a5"
TICKET_PAGE_SIZE = 1000  # Gainsight returned 200, 500 and 1000 rows per call in probes; 50 per call made the loop slow
TICKET_WIDGET_ID = "g_f18f7620-c34d-40f6-82d8-c7d785fef962"
# Only open tickets are listed, whatever their age: Open, Waiting Resolution, Waiting on Customer
# Feedback, and the live Gainsight wording seen for tickets still being worked ("Awaiting your
# feedback", "IBM is working", "In Progress", "Waiting for IBM", "SME Needed", "SME is Working").
# Closed, Resolved, Resolution Provided, Solution provided and Cancelled are not listed.
_WANTED_STATUS = re.compile(
    r"\s*(open|waiting[\s_-]*resolution|in[\s_-]*progress|ibm[\s_-]+is[\s_-]+working"
    r"|sme[\s_-]+(?:needed|is[\s_-]+working)"
    r"|(?:waiting|awaiting)[\s_-]+(?:(?:on|for)[\s_-]+)?(?:ibm|customer|client|your)(?:[\s_-]+(?:feedback|response))?)\s*",
    re.I)
_HREF = re.compile(r'href="([^"]+)"')

# The "Client ELA Details" grid (widget w9) on the Gainsight ELA dashboard, over the ELA Details object.
ELA_REPORT_ID = "421711f7-608f-4945-a18a-c38ac0ae70a5"
ELA_DASHBOARD_ID = "5f2465cc-fe3d-41ee-88d3-138de2b35127"
ELA_WIDGET_ID = "w9"
ELA_SOURCE = {"objectName": "ela_details__gc", "objectLabel": "ELA Details", "connectionId": "MDA",
              "connectionType": "MDA", "dataStoreType": "REDSHIFT"}
# Report column -> row key, and which of them are money amounts.
_ELA_COLUMNS = {
    "company_Company_GSID__gr_Name": "company",
    "ela_details__gc_UCI__gc": "uci",
    "ela_details__gc_CONTRACT_NAME__gc": "contract",
    "ela_details__gc_Contract_Section_Dropdown__gc": "section",
    "ela_details__gc_PLATFORM__gc": "platform",
    "ela_details__gc_CONTRACT_END_DATE__gc": "end_date",
    "sum_of_ela_details__gc_ENTITLED_AS_TOTAL_NET_REVENUE__gc": "entitled",
    "sum_of_ela_details__gc_ENTITLED_DEPLOYED_TOTAL_NET_REVENUE__gc": "deployed",
    "sum_of_FF_Undeployed Value": "undeployed",
    "sum_of_FF_Declared vs Expected Progress": "declared_vs_expected",
    "sum_of_ela_details__gc_RATE_PACE_TARGET__gc": "rate_pace_target",
}
ELA_AMOUNTS = ("entitled", "deployed", "undeployed", "declared_vs_expected", "rate_pace_target")

# The three renewal grids on the Gainsight Renewal Dashboard, over the Renewal Forecast SaaS and Subscription V1
# object. Each grid is one license type; report ids are the widgets' ``referenceId`` in the dashboard layout.
RENEWAL_DASHBOARD_ID = "e4da055a-a64d-4f40-9fd4-a8a0cdb6899f"
RENEWAL_SOURCE = {"objectName": "renewal_aforecast_asaas_aand_asubscription_av1",
                  "objectLabel": "Renewal Forecast SaaS and Subscription V1", "connectionId": "MDA",
                  "connectionType": "MDA", "dataStoreType": "REDSHIFT"}
RENEWAL_GRIDS = (  # (widget id, report id, license type)
    ("w9", "d66e239c-8b8a-43b7-80dc-175a21f84ac2", "SaaS"),
    ("w10", "e190e0dd-b7bb-4c9f-92b5-494c0178871e", "Subscription"),
    ("w2", "0db1e964-f537-471f-8c99-29a64dd90caf", "Perpetual"),
)
_RN = "renewal_aforecast_asaas_aand_asubscription_av1_"
_RR = "relationship_RelationshipGsid__gr_"
_RENEWAL_TEXT = {  # report column -> row key (display text)
    "company_CompanyGsid__gr_Name": "company",
    "relationship_RelationshipGsid__gr_Name": "product",
    _RR + "Relationship_Segment__gc": "segment",
    _RR + "Product_Stage__gc": "product_stage",
    _RR + "Adoption_Intent__gc": "adoption_intent",
}

_CLICK_TAB_JS = """
(() => {
  const els = document.querySelectorAll('a, button, [role=tab], lightning-tab-bar *');
  for (const e of els) {
    if ((e.textContent || '').trim() === 'Software Products') { e.click(); return true; }
  }
  return false;
})()
"""


class GainsightError(RuntimeError):
    pass


def parse_date(value) -> date | None:
    """DueDate arrives as an ISO string or as epoch milliseconds."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).date()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, OSError):
        return None


def _cell(row: dict, field: str) -> dict:
    return row.get(f"generic_support_data__gc_{field}__gc") or {}


_COL = re.compile(r"^generic_support_data__gc_(.+)__gc$")
# Candidate report-column names for the ticket categories (case and punctuation ignored). Checked against the
# live tenant: ``attribute_1`` holds the product name (for example "Order Management On Cloud"); the other
# ``attribute_*`` columns are an id, a flag and an empty column.
_CATEGORY_FIELDS = {
    "severity": ("severity", "ticket_severity", "case_severity", "priority", "ticket_priority"),
    "product": ("product", "product_name", "ticket_product", "product_family", "offering", "attribute_1"),
    "state": ("state", "ticket_state", "case_state", "sub_status", "substatus", "ticket_sub_status",
              "ticket_substatus", "status_detail", "case_status", "current_status"),
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def column_names(row: dict) -> list[str]:
    """The ticket report's column names present on a row (for diagnostics)."""
    return sorted(m.group(1) for m in (_COL.match(k) for k in row) if m)


def _category(row: dict, kind: str) -> str:
    """First non-empty value among the candidate columns for ``kind``."""
    cols = {_norm(m.group(1)): k for k, m in ((k, _COL.match(k)) for k in row) if m}
    for name in _CATEGORY_FIELDS[kind]:
        key = cols.get(_norm(name))
        if key:
            cell = row.get(key) or {}
            val = str(cell.get("v") if cell.get("v") not in (None, "") else cell.get("k") or "").strip()
            if val:
                return re.sub(r"<[^>]+>", "", val).strip()
    return ""


def severity_label(value: str) -> str:
    """'Severity 1' style label from text such as '1', 'Sev 1', 'Severity 1 - Critical' or 'P1'."""
    m = re.search(r"\b(?:sev(?:erity)?|p)?\s*([1-4])\b", str(value or ""), re.I)
    return f"Severity {m.group(1)}" if m else (str(value).strip() if value else "")


TICKET_STATES = ("Open", "IBM is working", "Awaiting customer feedback")


def state_label(value: str) -> str:
    """One of the three ticket states: Open, IBM is working, Awaiting customer feedback.

    Waiting Resolution, Waiting for IBM and SME Needed count as Open, because nobody at IBM is working them yet.
    An empty value stays empty.
    """
    v = str(value or "").lower()
    if not v.strip():
        return ""
    if re.search(r"(await|wait).*(feedback|customer|client|info|response|reply)", v):
        return "Awaiting customer feedback"
    if re.search(r"(ibm|support|vendor).*(work|progress|investigat)|\bin progress\b|\bwork in progress\b|\bsme[\s_-]+is[\s_-]+working\b", v):
        return "IBM is working"
    return "Open"


def _ticket(row: dict) -> dict:
    """One report-data row -> a ticket. The Ticket URL cell is an HTML link."""
    link = _HREF.search(str(_cell(row, "url_test").get("v") or ""))
    return {
        "status": str(_cell(row, "status").get("v") or ""),
        "ticket_id": str(_cell(row, "ticket_id").get("v") or ""),
        "ticket_url": link.group(1) if link and link.group(1).startswith("https://") else "",
        "created": str(_cell(row, "date_created").get("k") or ""),
        "last_updated": str(_cell(row, "last_date_updated").get("k") or ""),
        "summary": str(_cell(row, "summary").get("v") or ""),
        "severity": severity_label(_category(row, "severity")),
        "product": _category(row, "product"),
        "state": state_label(_category(row, "state") or str(_cell(row, "status").get("v") or "")),
    }


def _ela_row(row: dict) -> dict:
    """One ELA grid row -> a flat dict. Amounts are numbers, the end date is an ISO string."""
    out = {"gsid": str((row.get("company_Company_GSID__gr_Name") or {}).get("k") or "")}
    for col, key in _ELA_COLUMNS.items():
        cell = row.get(col) or {}
        if key in ELA_AMOUNTS:
            try:
                out[key] = float(cell.get("k") if cell.get("k") is not None else cell.get("v"))
            except (TypeError, ValueError):
                out[key] = 0.0
        elif key == "end_date":
            out[key] = str(cell.get("k") or cell.get("v") or "")[:10]
        else:
            out[key] = str(cell.get("v") if cell.get("v") is not None else "").strip()
    return out


def _renewal_row(row: dict, license_type: str = "") -> dict:
    """One Renewal Dashboard grid row -> a flat dict. ACV is a number; an unscored health score is None."""
    out = {"gsid": str((row.get("company_CompanyGsid__gr_Name") or {}).get("k") or ""), "license_type": license_type}
    for col, key in _RENEWAL_TEXT.items():
        cell = row.get(col) or {}
        out[key] = str(cell.get("v") if cell.get("v") is not None else "").strip()
    cell = row.get(_RN + "NextRenwlDate") or {}
    out["next_renewal"] = str(cell.get("k") or "")[:10]
    cell = row.get("sum_of_" + _RN + "SaasAcvUsdAtPlan") or {}
    try:
        out["acv"] = float(cell.get("k") if cell.get("k") is not None else cell.get("v"))
    except (TypeError, ValueError):
        out["acv"] = 0.0
    cell = row.get(_RN + "NumericalHealthScore") or {}
    try:
        out["health_score"] = float(cell.get("k") if cell.get("k") is not None else cell.get("v"))
    except (TypeError, ValueError):
        out["health_score"] = None
    for col, key in ((_RR + "Has_Active_Adoption_Plan__gc", "adoption_plan"), (_RR + "Has_Active_Get_Well_Plan__gc", "get_well_plan")):
        cell = row.get(col) or {}
        out[key] = cell.get("k") is True or str(cell.get("fv") or "").lower() == "true"
    return out


def _gr(rec: dict, field: str, sub: str = "Name"):
    """Read a ``Field__gr.Name`` value whether Gainsight nested or flattened it."""
    nested = rec.get(f"{field}__gr")
    if isinstance(nested, dict):
        return nested.get(sub)
    return rec.get(f"{field}__gr.{sub}")


class Gainsight:
    def __init__(self, browser: Browser | None = None):
        self.browser = browser or get_browser()
        self._cookie_header: str | None = None
        self.last_plans = (0, 0)      # (records returned, closed) for the last success_plans call
        self.last_tickets = (0, 0)    # (rows returned, rows kept) for the last support_tickets call
        self.last_ticket_columns: list[str] = []  # report column names seen in the last call

    # ---- session ------------------------------------------------------
    def _headers(self) -> dict:
        if not self._cookie_header:
            raise GainsightError("Gainsight is not connected. Use Connect > Connect Gainsight.")
        return {"Content-Type": "application/json", "x-gs-host": "SALESFORCE", "Cookie": self._cookie_header}

    def _collect_cookies(self) -> dict[str, str]:
        tab = self.browser.any_tab()
        found: dict[str, str] = {}
        for c in tab.cookies():
            dom = c.get("domain", "")
            if "gainsightcloud.com" in dom:
                found[c["name"]] = c["value"]
            elif c["name"] == "sid" and "sid_sf" not in found and ("force.com" in dom or "salesforce.com" in dom):
                found["sid_sf"] = c["value"]
        return found

    def connect(self, account_ids: list[str], timeout: float = 30.0) -> None:
        """Harvest Gainsight cookies by rendering the canvas on an ISC account.

        Tries up to five account ids, because an account the signed-in user cannot open
        shows Salesforce's "couldn't find the record" page and never loads Gainsight.
        """
        tab = self.browser.tab("force.com") or self.browser.tab("salesforce.com")
        if tab is None:
            raise GainsightError("Sign in to ISC first.")
        if isinstance(account_ids, str):
            account_ids = [account_ids]
        missing = 0
        for acct_id in account_ids[:5]:
            tab.navigate(f"{LIGHTNING}/lightning/r/Account/{acct_id}/view", wait=25)
            end = time.time() + timeout
            clicked = False
            while time.time() < end:
                try:
                    body = tab.evaluate("document.body ? document.body.innerText.slice(0, 3000) : ''",
                                        await_promise=False, timeout=5) or ""
                    if "find the record" in body or "don't have access" in body.lower():
                        missing += 1
                        break
                    if not clicked:
                        clicked = bool(tab.evaluate(_CLICK_TAB_JS, await_promise=False, timeout=5))
                except BrowserError:
                    pass
                cookies = self._collect_cookies()
                if "apt.sid" in cookies:
                    self._cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if k in COOKIE_NAMES)
                    return
                if "sid_sf" in cookies and "apt.uid" in cookies and self._cookies_work(cookies):
                    return
                time.sleep(2)
        if missing:
            raise GainsightError(
                f"ISC could not open {missing} of the accounts tried (record not found or no access), so Gainsight "
                "did not load. Check that you are signed in with your own IBM ID, then try again.")
        raise GainsightError(
            "Gainsight did not load inside ISC. In the browser window, open an account's "
            "'Software Products' tab, then click Connect Gainsight again."
        )

    def _cookies_work(self, cookies: dict[str, str]) -> bool:
        """Keep ``cookies`` only if Gainsight answers one small company query with HTTP 200 and a data payload."""
        header = "; ".join(f"{k}={v}" for k, v in cookies.items() if k in COOKIE_NAMES)
        body = {"select": ["Gsid"], "limit": 1,
                "where": {"expression": "A", "conditions": [
                    {"name": "Name", "alias": "A", "operator": "CONTAINS", "value": ["a"]}]}}
        try:
            r = requests.post(f"{BASE}/v1/data/objects/query/company", json=body, timeout=20,
                              headers={"Content-Type": "application/json", "x-gs-host": "SALESFORCE", "Cookie": header})
            ok = r.status_code == 200 and isinstance((((r.json() or {}).get("data")) or {}).get("records"), list)
        except (requests.RequestException, ValueError):
            return False
        if ok:
            self._cookie_header = header
        return ok

    def is_connected(self) -> bool:
        if not self._cookie_header:
            return False
        try:
            r = requests.post(
                f"{BASE}/v1/successPlan/fetch/view",
                headers=self._headers(),
                json={"searchTerm": "", "viewId": DEFAULT_VIEW_ID, "entityType": "COMPANY",
                      "entityId": "x", "context": "SP", "companyId": "x"},
                timeout=20,
            )
            return r.status_code not in (401, 403)
        except requests.RequestException:
            return False

    # ---- success plans ------------------------------------------------
    def success_plans(self, gsid: str, today: date | None = None, stale_days: int = 30) -> list[dict]:
        """Open success plans for a company, flagged when the due date has passed or no change was made for stale_days."""
        today = today or date.today()
        body = {
            "select": ["Gsid", "Name", "CompanyId", "StartDate", "DueDate", "IsClosed",
                       "StatusId", "StatusId__gr.Name", "SuccessPlanTypeId", "SuccessPlanTypeId__gr.Name",
                       "OwnerId", "OwnerId__gr.Name", "ModifiedDate", "PercentComplete",
                       "OpenTaskCount", "ClosedTaskCount"],
            "where": {"expression": "A", "conditions": [
                {"name": "CompanyId", "alias": "A", "operator": "EQ", "value": [gsid]}]},
            "limit": 200,
        }
        r = requests.post(f"{BASE}/v1/data/objects/query/cta_group", headers=self._headers(), json=body, timeout=40)
        if r.status_code in (401, 403):
            self._cookie_header = None
            raise GainsightError("Gainsight session expired. Click Connect Gainsight again.")
        if r.status_code != 200:
            raise GainsightError(f"Gainsight returned HTTP {r.status_code} for {gsid}.")
        records = ((r.json() or {}).get("data") or {}).get("records") or []
        self.last_plans = (len(records), sum(1 for x in records if x.get("IsClosed")))

        plans = []
        for rec in records:
            if rec.get("IsClosed"):
                continue
            due = parse_date(rec.get("DueDate"))
            overdue_days = (today - due).days if due and due < today else 0
            plans.append({
                "gsid": rec.get("Gsid") or "",
                "name": rec.get("Name"),
                "type": _gr(rec, "SuccessPlanTypeId") or "",
                "status": _gr(rec, "StatusId") or "",
                "owner": _gr(rec, "OwnerId") or "",
                "start_date": (parse_date(rec.get("StartDate")) or ""),
                "due_date": due or "",
                "overdue": overdue_days > 0,
                "days_overdue": overdue_days,
                "last_updated": parse_date(rec.get("ModifiedDate")) or "",
                "percent_complete": rec.get("PercentComplete"),
                "open_tasks": rec.get("OpenTaskCount"),
                "closed_tasks": rec.get("ClosedTaskCount"),
            })
        return plan_status.flag_stale(plans, stale_days, today)

    # ---- support tickets ----------------------------------------------
    def support_tickets(self, gsid: str, max_pages: int = 5) -> list[dict]:
        """Open support tickets for a company: the data behind the C360 "Support Tickets" tab.

        The tab is a report widget over "Generic Support Data" (ServiceNow and CSP tickets). We call
        the same endpoint it does, with the widget filtered to this one company. Tickets belong to the
        company, not to an individual success plan. Tickets whose status is "Open",
        "Waiting Resolution" or "Waiting on Customer Feedback" are kept, however old they are.
        """
        out: dict[str, dict] = {}
        seen = 0
        for page in range(max_pages):
            body = {
                "reportId": TICKET_REPORT_ID, "sourceDetails": {"connectionType": "MDA", "connectionId": "MDA"},
                "properties": {}, "entityType": "company", "entityId": gsid,
                "widgetId": TICKET_WIDGET_ID, "sectionId": TICKET_SECTION_ID,
                "pageInfo": {"offset": page * TICKET_PAGE_SIZE, "pageSize": TICKET_PAGE_SIZE},
                "widgetGlobalFilters": {"expression": "bm1", "conditions": [{
                    "leftOperand": {"type": "BASE_FIELD", "fieldName": "Company_GSID__gc", "dbName": "gsd86751",
                                    "label": "Company GSID", "dataType": "LOOKUP",
                                    "objectName": "generic_support_data__gc",
                                    "properties": {"referenceEntity": "company"}},
                    "locked": True, "filterAlias": "bm1", "comparisonOperator": "IN", "rightOperandType": "VALUE",
                    "filterValue": {"value": [gsid]}}]},
            }
            r = requests.post(
                f"{BASE}/v3/bi/reporting/section/report-data?useCache=false&requestSource=C360&includeId=true"
                f"&piedc=true&accOrCid={gsid}&c360RevampEnabled=true&connectionType=MDA",
                headers=self._headers(), json=body, timeout=60)
            if r.status_code in (401, 403):
                self._cookie_header = None
                raise GainsightError("Gainsight session expired. Click Connect Gainsight again.")
            if r.status_code != 200:
                raise GainsightError(f"Gainsight returned HTTP {r.status_code} for tickets of {gsid}.")
            data = (r.json() or {}).get("data") or {}
            for row in data.get("data") or []:
                if not self.last_ticket_columns:
                    self.last_ticket_columns = column_names(row)
                t = _ticket(row)
                seen += 1
                if t["ticket_id"] and _WANTED_STATUS.fullmatch(t["status"]):
                    out.setdefault(t["ticket_id"], t)  # a ticket repeats once per relationship
            if not (data.get("pageInfo") or {}).get("nextAvailable") or not data.get("data"):
                break
        self.last_tickets = (seen, len(out))
        return sorted(out.values(), key=lambda t: t["last_updated"], reverse=True)

    # ---- ELA details --------------------------------------------------
    def ela_details(self, gsids: list[str], max_pages: int = 40) -> list[dict]:
        """ELA contract rows for the given companies: the "Client ELA Details" grid on the ELA dashboard.

        One call per run, filtered to the companies by Company GSID, and paged 50 rows at a time. A company
        has one row per contract section and platform.
        """
        gsids = sorted({g for g in gsids if g})
        if not gsids:
            return []
        flt = {"entityId": ELA_REPORT_ID, "widgetId": ELA_WIDGET_ID, "applicable": True, "sourceDetails": ELA_SOURCE,
               "filter": {"leftOperand": {"type": "BASE_FIELD", "fieldName": "Company_GSID__gc",
                                          "dbName": "company_gsid__gc", "label": "Company GSID",
                                          "dataType": "LOOKUP", "objectName": "ela_details__gc",
                                          "properties": {"referenceEntity": "company"}},
                          "logicalOperator": "AND", "comparisonOperator": "IN", "filterValue": {"value": gsids}}}
        out: list[dict] = []
        for page in range(max_pages):
            body = {"reportId": ELA_REPORT_ID, "sourceDetails": ELA_SOURCE, "properties": {},
                    "widgetId": ELA_WIDGET_ID, "dashboardId": ELA_DASHBOARD_ID, "widgetGlobalFilters": [flt],
                    "pageInfo": {"offset": page * 50, "pageSize": 50}}
            r = requests.post(
                f"{BASE}/v3/bi/reporting/report-data?useCache=false&requestSource=DASHBOARD&includeId=true"
                "&piedc=true&connectionType=MDA", headers=self._headers(), json=body, timeout=60)
            if r.status_code in (401, 403):
                self._cookie_header = None
                raise GainsightError("Gainsight session expired. Click Connect Gainsight again.")
            if r.status_code != 200:
                raise GainsightError(f"Gainsight returned HTTP {r.status_code} for the ELA details.")
            data = (r.json() or {}).get("data") or {}
            out += [_ela_row(x) for x in data.get("data") or []]
            if not (data.get("pageInfo") or {}).get("nextAvailable") or not data.get("data"):
                break
        return out

    # ---- renewal forecast ---------------------------------------------
    def renewal_forecast(self, gsids: list[str], max_pages: int = 40) -> list[dict]:
        """Renewal forecast rows for the given companies: the SaaS, Subscription and Perpetual grids on the
        Gainsight Renewal Dashboard. One row per relationship (product) with ACV, next renewal date and health score.

        One filtered call series per grid, paged 50 rows at a time. The grid's own report filters (license type) stay applied.
        """
        gsids = sorted({g for g in gsids if g})
        if not gsids:
            return []
        out: list[dict] = []
        for widget_id, report_id, license_type in RENEWAL_GRIDS:
            flt = {"entityId": report_id, "widgetId": widget_id, "applicable": True, "sourceDetails": RENEWAL_SOURCE,
                   "filter": {"leftOperand": {"type": "BASE_FIELD", "fieldName": "CompanyGsid", "dbName": "Company_GSID",
                                              "label": "Company GSID", "dataType": "LOOKUP",
                                              "objectName": RENEWAL_SOURCE["objectName"],
                                              "properties": {"referenceEntity": "company"}},
                              "logicalOperator": "AND", "comparisonOperator": "IN", "filterValue": {"value": gsids}}}
            for page in range(max_pages):
                body = {"reportId": report_id, "sourceDetails": RENEWAL_SOURCE, "properties": {}, "widgetId": widget_id,
                        "dashboardId": RENEWAL_DASHBOARD_ID, "widgetGlobalFilters": [flt],
                        "pageInfo": {"offset": page * 50, "pageSize": 50}}
                r = requests.post(
                    f"{BASE}/v3/bi/reporting/report-data?useCache=false&requestSource=DASHBOARD&includeId=true"
                    "&piedc=true&connectionType=MDA", headers=self._headers(), json=body, timeout=60)
                if r.status_code in (401, 403):
                    self._cookie_header = None
                    raise GainsightError("Gainsight session expired. Click Connect Gainsight again.")
                if r.status_code != 200:
                    raise GainsightError(f"Gainsight returned HTTP {r.status_code} for the {license_type} renewal forecast.")
                data = (r.json() or {}).get("data") or {}
                out += [_renewal_row(x, license_type) for x in data.get("data") or []]
                if not (data.get("pageInfo") or {}).get("nextAvailable") or not data.get("data"):
                    break
        return out

    # ---- company lookup -----------------------------------------------
    def _company_query(self, field: str, operator: str, values: list[str], limit: int = 200) -> list[dict]:
        body = {"select": ["Gsid", "Name", "SfdcAccountId"],
                "where": {"expression": "A", "conditions": [
                    {"name": field, "alias": "A", "operator": operator, "value": values}]},
                "limit": limit}
        r = requests.post(f"{BASE}/v1/data/objects/query/company", headers=self._headers(), json=body, timeout=40)
        if r.status_code in (401, 403):
            self._cookie_header = None
            raise GainsightError("Gainsight session expired. Click Connect Gainsight again.")
        if r.status_code != 200:
            raise GainsightError(f"Gainsight returned HTTP {r.status_code} for the company lookup by {field}.")
        return [x for x in ((r.json() or {}).get("data") or {}).get("records") or [] if x.get("Gsid")]

    def find_companies(self, names: list[str], sfdc_ids: list[str]) -> list[dict]:
        """Gainsight companies (Gsid, Name, SfdcAccountId) whose name is in ``names`` or whose Salesforce
        account id is in ``sfdc_ids``. Field names come from the V1/V2 code; not yet run against the live tenant."""
        found: dict[str, dict] = {}
        for field, values in (("Name", names), ("SfdcAccountId", sfdc_ids)):
            if values:
                for rec in self._company_query(field, "IN", values):
                    found[rec["Gsid"]] = rec
        return list(found.values())

    def companies_containing(self, word: str, limit: int = 10) -> list[dict]:
        """Companies whose name contains ``word`` (for the Notes sheet when nothing matches)."""
        return self._company_query("Name", "CONTAINS", [word], limit) if word else []

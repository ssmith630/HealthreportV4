"""IBM Sales Cloud (ISC) access through the user's own signed-in browser.

IBM blocks REST/SOQL calls made with a browser session (HTTP 401/403), so this module
reads saved ISC *Reports* instead: the report is opened in the signed-in browser and the
data Salesforce loads for it is captured over CDP (see isc_report.py). No tokens or
cookies leave the browser.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import date

import dashboard
import isc_report as ir
import renewals
from accounts import Account
from browser import Browser, BrowserError, Tab, get_browser
from isc_report import ReportError
from quarters import Window

LIGHTNING = "https://ibmsc.lightning.force.com"
HOME_URL = f"{LIGHTNING}/lightning/page/home"

_CLOSED = re.compile(r"closed|\bwon\b|\blost\b", re.I)
_TECH_TYPES = {"ibm technology opportunity", "ibm opportunity"}

# Report column names (API name or label) tried for each field, in order. The saved ISC
# report decides what exists, so several likely spellings are accepted.
_F_RECORD_TYPE = ("RECORDTYPE", "RECORD_TYPE", "Record Type", "Opportunity Record Type")
_F_PARENT_ACCT = ("Parent_Account_Number__c", "Parent Account Number", "Parent Account #",
                  "PARENT_ACCOUNT_NUMBER", "Account.Parent_Account_Number__c", "Parent Account Number (Account)")
_F_EXPECTED_REV = ("EXPECTED_AMOUNT", "EXPECTED_REVENUE", "Expected Amount", "Expected Revenue")
_F_FORECAST_PIPE = ("Forecast_Pipeline__c", "Forecast Pipeline", "Forecast_Pipeline", "Pipeline Forecast")
_F_LAST_MODIFIED = ("LAST_UPDATE", "LAST_MODIFIED_DATE", "Last Modified Date", "Last Updated", "LAST_UPDATE_DATE")
_F_RENEWED_ACV = ("Renewed_ACV_Total__c", "Renewed ACV Total", "Renewed ACV (Total)", "Renewed_ACV_Total")

# Deployment object columns (field API names seen in V2's sf_deployments_raw.json, plus label spellings).
_D_NAME = ("Name", "Deployment Name", "DEPLOYMENT_NAME")
_D_PRODUCT = ("Product: Product Name", "Product2.Name", "Product Name")
_D_STAGE = ("Stage__c", "Stage", "STAGE")
_D_STATE = ("State_of_Engagement__c", "State of Engagement")
_D_DECISION = ("Target_Decision_Date__c", "Target Decision Date", "Target Commit Date")
_D_DEPLOYED = ("Date_Deployed_to_Production__c", "Date Deployed to Production")
_D_OWNER = ("Owner_Full_Name__c", "MK_Owner_Full_Name__c", "Owner Full Name", "FULL_NAME", "OWNER", "Deployment Owner", "Owner")
_D_AMOUNT = ("Total_Revenue_for_Deployment_in_FRev__c", "Forecast Net License Revenue Used",
             "Forecast Net License Revenue", "Amount__c", "Deployment_Amount__c", "Deployment Amount", "Deployment Value", "Forecast Amount",
             "Amount", "AMOUNT", "Revenue", "Value")


class IscError(RuntimeError):
    pass


def _prefix(i: str) -> str:
    return (i or "")[:15]


def _record_url(cell: dict | None) -> str:
    """Lightning link to the record behind a report cell, or "" when the report gave no record Id.

    Salesforce report results carry a recordId on hyperlinked cells (the Opportunity Name, the deployment Name).
    Opportunity Ids (prefix 006) get the object-specific URL; any other Id uses the generic /<id> form.
    """
    rid = (cell or {}).get("id") or ""
    if not isinstance(rid, str) or not re.fullmatch(r"[a-zA-Z0-9]{15}|[a-zA-Z0-9]{18}", rid):
        return ""
    if rid.startswith("006"):
        return f"{LIGHTNING}/lightning/r/Opportunity/{rid}/view"
    return f"{LIGHTNING}/{rid}"


_GENERIC_WORDS = {"THE", "COMPANY", "GROUP", "CORP", "CORPORATION", "INC", "LTD", "HOLDINGS", "INTERNATIONAL", "CO"}


def _diagnostic_words(a) -> list[str]:
    """Words to look for in dashboard account names when an account has no row.

    Taken from the display name, the coverage name and the aliases, split on anything that is not a
    letter or digit, so 'CENCORA/AMERISOURCEBERGEN' gives two words, not one. Words with digits
    (account numbers) and generic words are left out.
    """
    words: list[str] = []
    for src in (getattr(a, "isc_name", ""), a.name, *getattr(a, "aliases", [])):
        for w in re.split(r"[^A-Za-z0-9]+", str(src or "")):
            if len(w) >= 4 and not any(c.isdigit() for c in w) and w.upper() not in _GENERIC_WORDS \
                    and w.upper() not in {x.upper() for x in words}:
                words.append(w)
    return words or re.split(r"[^A-Za-z0-9]+", a.name)[:1]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _index(accounts: list[Account]) -> tuple[dict[str, Account], dict[str, Account]]:
    by_id: dict[str, Account] = {}
    by_name: dict[str, Account] = {}
    for a in accounts:
        for i in (a.live_id, a.isc_id):
            if i:
                by_id.setdefault(_prefix(i), a)
        by_name.setdefault(_norm(a.name), a)
        for alias in a.aliases:  # report-style names and account numbers, e.g. "THE BOEING CO-US", "DB0006HE-US"
            by_name.setdefault(_norm(alias), a)
    return by_id, by_name


def _match(row: dict, by_id: dict[str, Account], by_name: dict[str, Account]) -> Account | None:
    cell = ir.pick(row, "ACCOUNT_NAME", "Account Name", "Account", "Account_Name__c")
    if cell:
        rid = _prefix(cell.get("id") or "")
        if rid in by_id:
            return by_id[rid]
        n = _norm(ir.text(cell))
        if n in by_name:
            return by_name[n]
    num = ir.pick(row, "Account Number", "ACCOUNT_NUMBER", "Account_Number__c", "Client Number", "Customer Number")
    if num and _norm(ir.text(num)) in by_name:  # account numbers are indexed through Account.aliases
        return by_name[_norm(ir.text(num))]
    return None


def _hint(rows: list[dict], accounts: list[Account]) -> str:
    """Diagnostic for a report with rows but no matches: its column labels, and where the accounts' names appear."""
    if not rows:
        return ""
    labels = sorted({k[4:] for k in rows[0] if k.startswith("lbl:")})
    needles = {n for a in accounts for n in (_norm(a.name), *(_norm(x) for x in a.aliases)) if len(n) >= 5}
    found: list[str] = []
    for row in rows:
        for key, cell in row.items():
            if key.startswith("lbl:") and any(n in _norm(ir.text(cell)) for n in needles):
                item = f"{key[4:]}='{ir.text(cell)}'"
                if item not in found:
                    found.append(item)
        if len(found) >= 6:
            break
    return (f" Columns: {', '.join(labels) or 'none'}. Cells resembling the selected accounts: "
            f"{'; '.join(found[:6]) or 'none found in any column'}. Searched for: {', '.join(sorted(needles)[:8])}.")


def _renewal_health(row: dict) -> str:
    """Value of any report column whose name or label contains 'Renewal Health' (e.g. 'Account: Renewal Health')."""
    for key, cell in row.items():
        if "renewalhealth" in key:
            t = ir.text(cell).strip()
            if t and t != "-":
                return t
    return ""


def _products(row: dict) -> str:
    """Join the values of any report column whose label mentions 'product'."""
    seen, out = set(), []
    for key, cell in row.items():
        if key.startswith("lbl:") and "product" in key and id(cell) not in seen:
            seen.add(id(cell))
            t = ir.text(cell).strip()
            if t and t != "-" and t not in out:
                out.append(t)
    return "; ".join(out)


_BATCH = 10  # accounts per scoped opportunities run; a batch that is still capped at 2,000 rows is split in half
_WIDE_START, _WIDE_END = "2000-01-01", "2040-12-31"


def _id18(i: str) -> str:
    """15-character Salesforce id -> 18-character id (18-character ids are returned unchanged)."""
    i = (i or "").strip()
    if len(i) != 15 or not i.isalnum():
        return i if len(i) == 18 else ""
    suffix = ""
    for chunk in (i[0:5], i[5:10], i[10:15]):
        bits = sum(1 << n for n, c in enumerate(chunk) if c.isupper())
        suffix += "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"[bits]
    return i + suffix


def _ids18(a: Account) -> set[str]:
    return {x for x in (_id18(a.live_id), _id18(a.isc_id)) if x}


def _scope(meta: dict, column: str, values: list[str]) -> None:
    """Edit report metadata in place: widen the close-date range and filter to the given accounts."""
    meta["reportFilters"] = [f for f in meta.get("reportFilters", []) if f.get("column") != "Opportunity.CloseDate"] + [
        {"column": column, "filterType": "fieldValue", "isRunPageEditable": True, "operator": "equals",
         "value": ",".join(values)}]
    meta["standardDateFilter"] = {"column": "Opportunity.CloseDate", "durationValue": "CUSTOM",
                                  "startDate": _WIDE_START, "endDate": _WIDE_END}


class Isc:
    def __init__(self, browser: Browser | None = None):
        self.browser = browser or get_browser()
        self._reports: dict[str, dict] = {}
        self._scoped: dict[tuple, tuple[list[dict], bool, str, str]] = {}
        self._dash: dict[tuple, dict] = {}

    # ---- connection ---------------------------------------------------
    def _tab(self) -> Tab | None:
        return self.browser.tab("force.com") or self.browser.tab("salesforce.com")

    def open_sign_in(self) -> None:
        """Open the ISC home page; the user completes IBM SSO in that window."""
        self.browser.ensure_running(HOME_URL)
        if self._tab() is None:
            self.browser.tab(open_url=HOME_URL)

    def status(self) -> dict:
        """Cheap check (no ISC API call): is the browser signed in to Salesforce?"""
        if not self.browser.is_running():
            return {"connected": False, "detail": "The browser window is not open yet."}
        tab = self._tab()
        if tab is None:
            return {"connected": False, "detail": "No ISC page is open. Click Sign in to ISC."}
        try:
            url = tab.current_url()
            cookies = tab.cookies()
        except BrowserError:
            return {"connected": False, "detail": "Could not read the browser window."}
        host = urllib.parse.urlparse(url).netloc
        if "lightning.force.com" not in host:
            return {"connected": False, "detail": "The ISC window is still on the sign-in page. Finish signing in."}
        if not any(c.get("name") == "sid" and ("force.com" in c.get("domain", "") or "salesforce.com" in c.get("domain", ""))
                   for c in cookies):
            return {"connected": False, "detail": "ISC is open but no session was found. Finish signing in."}
        return {"connected": True, "detail": "Signed in to ISC."}

    def is_connected(self) -> bool:
        return self.status()["connected"]

    # ---- reports ------------------------------------------------------
    def forget_reports(self) -> None:
        self._reports.clear()
        self._scoped.clear()
        self._dash.clear()

    def _report(self, url: str, progress=None) -> dict:
        if url in self._reports:
            return self._reports[url]
        tab = self._tab()
        if tab is None:
            raise IscError("ISC is not open. Use Connect > Sign in to ISC.")
        try:
            rv = ir.capture_report(tab, url, progress)
        except ReportError as e:
            raise IscError(str(e)) from e
        self._reports[url] = rv
        return rv

    # ---- deployments --------------------------------------------------
    def _split_by_account(self, rv: dict, idx: int, stage: str, accounts: list[Account], progress=None) -> tuple[list[dict], bool]:
        """Rows of one Stage of the deployments report, read in batches of accounts (for a stage over the 2,000-row cap)."""
        tab = self._tab()
        if tab is None:
            return [], False
        ids = [a for a in accounts if _ids18(a)]
        names = [a for a in accounts if not _ids18(a)]
        todo = [("Account.Id", ids[i:i + _BATCH]) for i in range(0, len(ids), _BATCH)]
        todo += [("Account.Name", names[i:i + _BATCH]) for i in range(0, len(names), _BATCH)]
        merged: list[dict] = []
        complete = True

        def edit(meta: dict, kind: str, values: list[str]) -> None:
            fl = [dict(f) for f in meta.get("reportFilters", [])]
            fl[idx]["value"] = stage
            meta["reportFilters"] = fl + [{"column": kind, "filterType": "fieldValue", "isRunPageEditable": True,
                                           "operator": "equals", "value": ",".join(values)}]

        while todo:
            kind, group = todo.pop(0)
            values = sorted({i for a in group for i in _ids18(a)}) if kind.endswith(".Id") \
                else sorted({n for a in group for n in (a.name, *a.aliases)})
            if progress:
                progress(f"Reading {stage} deployments for {len(group)} account(s)...")
            try:
                part_rv = ir.rerun_report(tab, rv, lambda meta, k=kind, v=values: edit(meta, k, v))
            except ReportError:
                return merged, False
            part = ir.report_rows(part_rv)
            if ir.is_truncated(part_rv, len(part)) and len(group) > 1:
                mid = len(group) // 2
                todo[:0] = [(kind, group[:mid]), (kind, group[mid:])]
                continue
            complete = complete and not ir.is_truncated(part_rv, len(part))
            merged.extend(part)
        return merged, complete

    def _rows_by_stage(self, rv: dict, report_url: str, accounts: list[Account], progress=None) -> tuple[list[dict], bool]:
        """Re-run a truncated report once per Stage using the run-page filter override (``fvN=<stage>``).

        Returns (rows, complete). Falls back to the truncated rows when the report has no editable Stage filter.
        """
        rows = ir.report_rows(rv)
        filters = rv.get("reportMetadata", {}).get("reportFilters", [])
        idx = next((i for i, f in enumerate(filters) if str(f.get("column", "")).endswith("Stage__c")), None)
        if idx is None:
            return rows, False
        stages = [s.strip() for s in str(filters[idx].get("value", "")).split(",") if s.strip()]
        if not stages:
            return rows, False
        merged: list[dict] = []
        complete = True
        sep = "&" if "?" in report_url else "?"
        for n, stage in enumerate(stages, 1):
            if progress:
                progress(f"Reading deployments in stage {stage} ({n} of {len(stages)})...")
            part = self._report(f"{report_url}{sep}fv{idx}={urllib.parse.quote(stage)}")
            part_rows = ir.report_rows(part)
            if ir.is_truncated(part, len(part_rows)):
                part_rows, ok = self._split_by_account(rv, idx, stage, accounts, progress)
                complete = complete and ok
            merged.extend(part_rows)
        return merged, complete

    def deployments(self, accounts: list[Account], report_url: str, progress=None,
                    cq: Window | None = None) -> tuple[list[dict], list[dict]]:
        """Deployment records from a saved ISC report of the Deployment object, for the chosen accounts.

        When cq (the current quarter) is given, only deployments whose target decision date or production
        date falls inside it are returned. With cq=None every deployment date is kept.
        """
        notes: list[dict] = []
        rv = self._report(report_url, progress)
        rows = ir.report_rows(rv)
        truncated = ir.is_truncated(rv, len(rows))
        if truncated:
            rows, complete = self._rows_by_stage(rv, report_url, accounts, progress)
            truncated = not complete
        report_name = rv.get("reportMetadata", {}).get("name", "the report")
        if rows and ir.pick(rows[0], "OPPORTUNITY_NAME", "Opportunity Name") and ir.pick(rows[0], "CLOSE_DATE", "Close Date"):
            raise IscError(f"'{report_name}' is an opportunities report, not a deployments report. Paste a link to a "
                           "report built on the Deployment object (Name, Account, Stage, Target Decision Date).")
        by_id, by_name = _index(accounts)

        out: list[dict] = []
        matched_accounts: set[str] = set()
        for row in rows:
            acct = _match(row, by_id, by_name)
            if acct is None:
                continue
            matched_accounts.add(acct.name)
            out.append({
                "amount": ir.number(ir.pick(row, *_D_AMOUNT)),
                "account": acct.name,
                "account_display": getattr(acct, "isc_name", "") or acct.name,
                "account_url": acct.isc_url,
                "name": ir.text(ir.pick(row, *_D_NAME)),
                "record_url": _record_url(ir.pick(row, *_D_NAME)),
                "product": ir.text(ir.pick(row, *_D_PRODUCT)),
                "stage": ir.text(ir.pick(row, *_D_STAGE)),
                "state_of_engagement": ir.text(ir.pick(row, *_D_STATE)),
                "target_decision_date": ir.iso_date(ir.pick(row, *_D_DECISION)),
                "deployed_date": ir.iso_date(ir.pick(row, *_D_DEPLOYED)),
                "owner": ir.text(ir.pick(row, *_D_OWNER)),
                "last_modified": ir.iso_date(ir.pick(row, *_F_LAST_MODIFIED)),
            })

        outside = 0
        if cq is not None:
            def in_cq(r: dict) -> bool:
                for key in ("target_decision_date", "deployed_date"):
                    try:
                        if r[key] and cq.contains(date.fromisoformat(r[key])):
                            return True
                    except ValueError:
                        pass
                return False
            before = len(out)
            out = [r for r in out if in_cq(r)]
            outside = before - len(out)

        if not rows:
            notes.append({"area": "Deployments", "account": "",
                          "note": f"'{report_name}' returned no rows. Open it in ISC to confirm you can see data."})
        elif not out and not outside:
            notes.append({"area": "Deployments", "account": "",
                          "note": f"'{report_name}' returned {len(rows)} rows but none belong to the selected accounts. "
                                  "Check that the report includes the Account Name column." + _hint(rows, accounts)})
        elif not out:
            notes.append({"area": "Deployments", "account": "",
                          "note": f"{outside} deployments for the selected accounts were left out because none has a target "
                                  f"decision date or production date in {cq.label}."})
        else:
            if outside:
                notes.append({"area": "Deployments", "account": "",
                              "note": f"{outside} deployments were left out because their target decision date and "
                                      f"production date are outside {cq.label}."})
            if truncated:
                notes.append({"area": "Deployments", "account": "",
                              "note": f"'{report_name}' looks truncated at {len(rows)} rows (Salesforce caps a report run at "
                                      "2,000). Narrow the report filters."})
            for a in sorted(accounts, key=lambda x: x.name.lower()):
                if a.name not in matched_accounts:
                    notes.append({"area": "Deployments", "account": a.name, "account_display": getattr(a, "isc_name", "") or a.name,
                                  "note": f"No deployments for this account in '{report_name}'."})
            for key, msg, names in (("stage", "Stage", _D_STAGE), ("owner", "Owner", _D_OWNER),
                                    ("amount", "Forecast Net License Revenue", _D_AMOUNT)):
                if any(o[key] for o in out):
                    continue
                if rows and ir.pick(rows[0], *names) is None:
                    why = f"The report has no {msg} column. Add the '{msg}' column to the ISC deployments report."
                else:
                    why = f"The report has the {msg} column, but it is empty for every deployment."
                notes.append({"area": "Deployments", "account": "", "note": f"No {msg} values found. {why}"})
            if not all(o["record_url"] for o in out):
                notes.append({"area": "Deployments", "account": "",
                              "note": "Some deployments have no Salesforce link because the report gave no record Id for "
                                      "the Name column. Include the deployment Name as a hyperlinked column."})
        out.sort(key=lambda r: (r["account"].lower(), r["target_decision_date"] or "9999", r["name"]))
        return out, notes

    # ---- deployment forecast and renewal at risk (CRM Analytics dashboard) -----------
    def dashboard_data(self, dashboard_url: str, prefix: str, progress=None) -> dict:
        """Open the dashboard once and keep what it returned, so the forecast and renewal fetches share one load."""
        key = (dashboard_url, prefix)
        if key in self._dash:
            return self._dash[key]
        tab = self._tab()
        if tab is None:
            raise IscError("ISC is not open. Use Connect > Sign in to ISC.")
        try:
            data = dashboard.capture_dashboard(tab, dashboard_url, prefix, renewal=True, progress=progress)
        except dashboard.DashboardError as e:
            raise IscError(str(e)) from e
        self._dash[key] = data
        return data

    @staticmethod
    def _dash_account(raw: str, by_name: dict[str, Account]) -> Account | None:
        """Account for a dashboard name. Dashboard names carry a country suffix ("BETANXT-US"), so retry without it."""
        raw = str(raw or "")
        return by_name.get(_norm(raw)) or by_name.get(_norm(re.sub(r"-[A-Za-z]{2,3}$", "", raw.strip())))

    def cq_forecast(self, accounts: list[Account], dashboard_url: str, cq: Window, progress=None) -> tuple[dict[str, dict], list[dict]]:
        """Deployment forecast per account name for the current quarter and the fiscal year.

        Keys: Budget, Deployed, Call, Call to Go, Upside, Stretch, Pipeline for the quarter, and the same names
        prefixed ``FY `` for the year. Read from the Seller and Manager Deployment Forecast dashboard.
        """
        notes: list[dict] = []
        prefix = dashboard.quarter_prefix(cq.start)
        fy = dashboard.year_prefix(cq.start)
        records = self.dashboard_data(dashboard_url, prefix, progress)["deployment"]
        _, by_name = _index(accounts)
        out: dict[str, dict] = {}
        for rec in records:
            acct = self._dash_account(rec.get("Acc.Name"), by_name)
            if acct is None:
                continue
            mine = out.setdefault(acct.name, {**{f: None for f in dashboard.DEPLOY_FIELDS},
                                              **{f"FY {f}": None for f in dashboard.DEPLOY_FIELDS}})
            for f in dashboard.DEPLOY_FIELDS:  # several dashboard rows can map to one account: add them up
                for col, pre in ((f, prefix), (f"FY {f}", fy)):
                    v = rec.get(f"{pre} {f}")
                    if isinstance(v, (int, float)):
                        mine[col] = (mine[col] or 0.0) + float(v)
        returned = sorted({str(r.get("Acc.Name") or "") for r in records}, key=str.lower)
        sample = (f" The dashboard returned {len(returned)} account names, from '{returned[0]}' to '{returned[-1]}'."
                  if returned else " The dashboard returned no account names.")
        for a in sorted(accounts, key=lambda x: x.name.lower()):
            if a.name not in out:
                words = _diagnostic_words(a)
                near = [n for n in returned if any(_norm(w) in _norm(n) for w in words)]
                notes.append({"area": "CQ forecast", "account": a.name, "account_display": getattr(a, "isc_name", "") or a.name,
                              "note": "No row for this account in the Deployment Forecast dashboard "
                                      f"({prefix}), so the dashboard holds no figures for it and they are "
                                      f"blank. Of {len(records)} account rows, names containing "
                                      f"{' or '.join(repr(w) for w in words)}: {'; '.join(near[:8]) or 'none'}.{sample}"})
        return out, notes

    @staticmethod
    def _strip_country(raw: str) -> str:
        return _norm(re.sub(r"-[A-Za-z]{2,3}$", "", str(raw or "").strip()))

    def _detail_rows(self, records: list[dict], by_name: dict[str, Account]) -> dict[str, dict]:
        """Add up one copy of the Seller Renewal detail query per account name."""
        agg: dict[str, dict] = {}
        for rec in records:
            acct = self._dash_account(rec.get("Acc.Name"), by_name)
            if acct is None:
                continue
            row = agg.setdefault(acct.name, {"account": acct.name, "account_display": getattr(acct, "isc_name", "") or acct.name,
                                             "account_url": acct.isc_url, "renewal": 0.0,
                                             "at_risk": 0.0, "lines": 0, "names": set()})
            row["renewal"] += float(rec.get("Renewal Amt (NQ)") or 0)
            row["at_risk"] += float(rec.get("AtRisk Amt (NQ)") or 0)
            row["lines"] += 1
            row["names"].add(str(rec.get("Acc.Name")))
        return agg

    def _by_account_rows(self, records: list[dict], by_name: dict[str, Account]) -> dict[str, dict]:
        """Rows from the Seller Renewal By Account table (``NQ Renewal`` and ``NQ RED``), one per account name."""
        agg: dict[str, dict] = {}
        for rec in records:
            key = dashboard.name_key(rec)
            acct = self._dash_account(rec.get(key) if key else "", by_name)
            if acct is None:
                continue
            row = agg.setdefault(acct.name, {"account": acct.name, "account_display": getattr(acct, "isc_name", "") or acct.name,
                                             "account_url": acct.isc_url, "renewal": 0.0,
                                             "at_risk": 0.0, "lines": None, "names": set()})
            row["renewal"] += float(rec.get("NQ Renewal") or 0)
            row["at_risk"] += float(rec.get("NQ RED") or 0)
            row["names"].add(str(rec.get(key)))
        return agg

    def renewal_at_risk(self, accounts: list[Account], dashboard_url: str, cq: Window, progress=None) -> tuple[list[dict], list[dict]]:
        """Renewal at risk per account, from the dashboard's Seller Renewal page (next quarter, NQ).

        One row per matched account with the renewal and at-risk amounts and the dashboard account names that
        were added up. The detail query is capped at 10,000 records and sorted by account name, so accounts that
        sort after the last returned name may be missing. Three things reduce that gap: a second copy of the
        query in the reverse order (when the capture got one), the page's By Account table for accounts the
        detail query lacks (``lines`` is None for those rows), and a note for whatever is still missing.
        """
        notes: list[dict] = []
        data = self.dashboard_data(dashboard_url, dashboard.quarter_prefix(cq.start), progress)
        primary = data["renewal_detail"]
        by_account = data.get("renewal_by_account") or []
        if not primary and not by_account:
            raise IscError("The Renewal At Risk page of the dashboard returned no records. Open the dashboard in the "
                           "browser window, check that it has a Renewal At Risk page, then retry.")
        _, by_name = _index(accounts)
        sets = [s for s in [primary, *(data.get("renewal_detail_more") or [])] if s]
        agg: dict[str, dict] = {}
        partial: set[str] = set()      # accounts whose rows touch the cut-off of every copy that has them
        ends = []                      # per copy: (last name, ascending, capped)
        for recs in sets:
            capped = len(recs) >= dashboard.RECORD_CAP
            last = self._strip_country(recs[-1].get("Acc.Name"))
            asc = self._strip_country(recs[0].get("Acc.Name")) <= last
            ends.append((last, asc, capped))
            if capped:
                notes.append({"area": "Renewal at risk", "account": "",
                              "note": f"The dashboard returned {len(recs)} records, which is its 10,000-record cap. The "
                                      f"list is sorted by account name ({'A to Z' if asc else 'Z to A'}) and ends at "
                                      f"'{recs[-1].get('Acc.Name')}', so accounts that sort after it may be missing."})
            for name, row in self._detail_rows(recs, by_name).items():
                complete = not capped or all(
                    (self._strip_country(n) < last) if asc else (self._strip_country(n) > last) for n in row["names"])
                if complete or name not in agg:
                    agg[name] = row
                    partial.discard(name) if complete else partial.add(name)
        for name in sorted(partial):
            notes.append({"area": "Renewal at risk", "account": name, "account_display": agg[name].get("account_display", name),
                          "note": "The dashboard's list ended at this account's rows, so its amounts may be incomplete."})
        fallback = {n: r for n, r in self._by_account_rows(by_account, by_name).items() if n not in agg}
        for name, row in fallback.items():
            agg[name] = row
            notes.append({"area": "Renewal at risk", "account": name, "account_display": row.get("account_display", name),
                          "note": "No Red renewal lines in the detail query, so the amounts come from the dashboard's By Account "
                                  "table: renewal is the account's NQ Renewal and at risk is its NQ RED. The line count is blank."})
        rows = []
        for r in sorted(agg.values(), key=lambda x: x["account"].lower()):
            r["names"] = "; ".join(sorted(r["names"]))
            r["pct_at_risk"] = (r["at_risk"] / r["renewal"]) if r["renewal"] else None
            rows.append(r)
        for a in sorted(accounts, key=lambda x: x.name.lower()):
            if a.name in agg:
                continue
            key = _norm(a.name)
            beyond = not by_account and all(c and ((key > e) if asc else (key < e)) for e, asc, c in ends)
            notes.append({"area": "Renewal at risk", "account": a.name, "account_display": getattr(a, "isc_name", "") or a.name,
                          "note": "No Red renewal rows for this account in the dashboard"
                                  + (", and its name sorts after the last returned name, so it may have been cut off."
                                     if beyond else ".")})
        return rows, notes

    def renewal_quarters(self, accounts: list[Account], dashboard_url: str, cq: Window, progress=None) -> tuple[list[dict], dict, list[dict]]:
        """Renewal and Red at-risk amounts for the next four quarters, as the dashboard's Renewal At Risk page shows them.

        Returns ``(rows, next4, notes)``. ``rows`` has one row per matched account with a key for each of
        ``dashboard.RENEWAL_COLUMNS`` (``NQ Renewal``, ``NQ RED``, ``NQ+1 Renewal`` and so on; a column the page
        did not show is 0). ``next4`` is the "Next 4 Quarters" split by At Risk Indicator
        (``Red``, ``Yellow``, ``Green``, ``None``). ``next4`` is the market-wide figure of the dashboard, which is
        filtered to the Industry Market, not only the accounts of this report. Both are empty, with a note, when
        neither could be read from the dashboard.
        """
        data = self.dashboard_data(dashboard_url, dashboard.quarter_prefix(cq.start), progress)
        next4 = data.get("renewal_next4") or {}
        recs = data.get("renewal_by_account_page") or []
        notes: list[dict] = []
        if not recs and not next4:
            notes.append({"area": "Renewal at risk", "account": "",
                          "note": "The dashboard's Renewal At Risk page did not return the By Account figures or the Next 4 "
                                  "Quarters split, so the next four quarters (NQ+1 to NQ+3 and Yellow, Green) are blank."})
            return [], {}, notes
        _, by_name = _index(accounts)
        agg: dict[str, dict] = {}
        for rec in recs:
            key = dashboard.name_key(rec)
            acct = self._dash_account(rec.get(key) if key else "", by_name)
            if acct is None:
                continue
            row = agg.setdefault(acct.name, {"account": acct.name, "account_display": getattr(acct, "isc_name", "") or acct.name,
                                             "account_url": acct.isc_url, **{c: 0.0 for c in dashboard.RENEWAL_COLUMNS}})
            for c in dashboard.RENEWAL_COLUMNS:
                row[c] += float(rec.get(c) or 0)
        for a in sorted(accounts, key=lambda x: x.name.lower()):
            if recs and a.name not in agg:
                notes.append({"area": "Renewal at risk", "account": a.name, "account_display": getattr(a, "isc_name", "") or a.name,
                              "note": "No row for this account in the dashboard's By Account table."})
        return sorted(agg.values(), key=lambda x: x["account"].lower()), next4, notes

    # ---- renewal health -----------------------------------------------
    def renewal_health(self, accounts: list[Account], report_url: str, progress=None) -> tuple[dict[str, str], list[dict]]:
        """Renewal Health per account name, read from the saved ISC report's 'Renewal Health' column.

        Uses every row of the report that belongs to a chosen account (any stage or close date) and,
        when an account has several values, takes the one on its most recently modified row.
        """
        rows = self._scoped_rows(accounts, report_url, progress)[0]
        by_id, by_name = _index(accounts)
        best: dict[str, tuple[str, str]] = {}
        matched = 0
        for row in rows:
            acct = _match(row, by_id, by_name)
            if acct is None:
                continue
            matched += 1
            value = _renewal_health(row)
            if not value:
                continue
            stamp = ir.iso_date(ir.pick(row, *_F_LAST_MODIFIED))
            if acct.name not in best or stamp > best[acct.name][0]:
                best[acct.name] = (stamp, value)
        return {name: v for name, (_, v) in best.items()}, []

    def renewal_opps(self, accounts: list[Account], report_url: str, progress=None) -> tuple[list[dict], list[dict]]:
        """Open renewal opportunities of the chosen accounts, read from the saved ISC opportunity report.

        Uses every row of the report that belongs to a chosen account, whatever its close date, and keeps the
        ones renewals.is_renewal accepts (Opportunity Record Type, else name) that are not in a closed, won or
        lost stage. Unlike open_opps, the IBM Technology record-type filter is not applied, because renewals
        use their own record types. Returns (opportunities, notes).
        """
        rows = self._scoped_rows(accounts, report_url, progress)[0]
        by_id, by_name = _index(accounts)
        out: list[dict] = []
        for row in rows:
            acct = _match(row, by_id, by_name)
            if acct is None:
                continue
            stage = ir.text(ir.pick(row, "STAGE_NAME", "Stage"))
            name = ir.text(ir.pick(row, "OPPORTUNITY_NAME", "Opportunity Name"))
            rtype = ir.text(ir.pick(row, *_F_RECORD_TYPE)).strip()
            if _CLOSED.search(stage) or not renewals.is_renewal(rtype, name):
                continue
            amount = ir.pick(row, "Opportunity_Amount_USD__c", "Opportunity Amount USD") \
                or ir.pick(row, "AMOUNT_CONVERTED", "Amount (converted)", "AMOUNT", "Amount")
            out.append({
                "account": acct.name, "name": name, "stage": stage, "record_type": rtype,
                "record_url": _record_url(ir.pick(row, "OPPORTUNITY_NAME", "Opportunity Name")),
                "close_date": ir.iso_date(ir.pick(row, "CLOSE_DATE", "Close Date")),
                "amount": ir.number(amount),
                "owner": ir.text(ir.pick(row, "FULL_NAME", "OWNER", "Opportunity Owner", "Opportunity Owner Name", "Owner")),
            })
        notes = []
        have = {o["account"] for o in out}
        for a in sorted(accounts, key=lambda x: x.name.lower()):
            if a.name not in have:
                notes.append({"area": "Renewals", "account": a.name, "account_display": getattr(a, "isc_name", "") or a.name, "note": (
                    "No open renewal opportunity was found for this account in the saved ISC opportunity report. "
                    "Renewals are the opportunities whose Opportunity Record Type contains 'Renewal' (or, when the report "
                    "has no record type column, whose name contains 'renew') and that are not closed, won or lost.")})
        return out, notes

    # ---- open opportunities -------------------------------------------
    def _scoped_rows(self, accounts: list[Account], report_url: str, progress=None) -> tuple[list[dict], bool, str, str]:
        """Rows of the opportunities report restricted to ``accounts`` over a wide close-date range.

        The saved report covers every customer and Salesforce returns at most 2,000 rows per run, so the
        unscoped run is cut off before this app sees the data (and mostly with other customers' rows).
        Here the report's own request is re-posted with the close-date filter widened and an Account Id
        filter added, in batches that are split further whenever a batch is still capped.

        Returns (rows, complete, report_name, problem). ``problem`` is non-empty when the scoped run was not
        possible and the plain (possibly truncated) report was used instead.
        """
        key = (report_url, tuple(sorted(a.name for a in accounts)))
        if key in self._scoped:
            return self._scoped[key]
        base = self._report(report_url, progress)
        name = base.get("reportMetadata", {}).get("name", "the report")
        rows = ir.report_rows(base)
        tab = self._tab()
        by_ids = [a for a in accounts if _ids18(a)]
        by_names = [a for a in accounts if not _ids18(a)]
        try:
            if tab is None or base.get("reportMetadata", {}).get("reportBooleanFilter"):
                raise ReportError("this report uses custom filter logic")
            complete = True
            merged: list[dict] = []
            tried: list[str] = []
            todo: list[tuple[str, list[Account]]] = []
            for kind, pool in (("Opportunity.Account.Id", by_ids), ("Opportunity.Account.Name", by_names)):
                todo += [(kind, pool[i:i + _BATCH]) for i in range(0, len(pool), _BATCH)]
            done = 0
            while todo:
                kind, group = todo.pop(0)
                values = sorted({i for a in group for i in _ids18(a)}) if kind.endswith(".Id") \
                    else sorted({n for a in group for n in (a.name, *a.aliases)})
                if progress:
                    progress(f"Reading opportunities for {len(group)} account(s), {done} of {len(accounts)} done...")
                tried.append(f"{kind} = {', '.join(values)}")
                rv = ir.rerun_report(tab, base, lambda meta, k=kind, v=values: _scope(meta, k, v))
                part = ir.report_rows(rv)
                if ir.is_truncated(rv, len(part)) and len(group) > 1:
                    mid = len(group) // 2
                    todo[:0] = [(kind, group[:mid]), (kind, group[mid:])]
                    continue
                complete = complete and not ir.is_truncated(rv, len(part))
                merged.extend(part)
                done += len(group)
            result = (merged, complete, name, "" if merged else
                      f"The scoped run returned 0 rows (filters tried: {' | '.join(tried)}); the saved report on its own "
                      f"returned {len(rows)} rows.")
        except ReportError as e:
            result = (rows, not ir.is_truncated(base, len(rows)), name,
                      f"Could not filter '{name}' to the selected accounts ({e}), so the saved report's own "
                      "filters were used.")
        self._scoped[key] = result
        return result

    def open_opps(self, accounts: list[Account], windows: list[Window], tech_only: bool,
                  report_url: str, progress=None, all_quarters: bool = False) -> tuple[list[dict], list[dict]]:
        """Open opportunities from the saved report for the chosen accounts.

        Kept to ``windows`` unless ``all_quarters`` is set, in which case every open opportunity is
        returned whatever its close date. Rows that are dropped are counted on the Notes sheet.
        """
        notes: list[dict] = []
        rows, complete, report_name, problem = self._scoped_rows(accounts, report_url, progress)
        if problem:
            notes.append({"area": "Opportunities", "account": "", "note": problem})
        if rows and not (ir.pick(rows[0], "OPPORTUNITY_NAME", "Opportunity Name")
                         and ir.pick(rows[0], "CLOSE_DATE", "Close Date")):
            raise IscError(f"'{report_name}' is not an opportunities report. It needs Opportunity Name, Stage "
                           "and Close Date columns. Paste a different report link on the Connect tab.")
        by_id, by_name = _index(accounts)

        out: list[dict] = []
        seen_in_report = matched = outside = 0
        dropped = {"closed": 0, "omitted": 0, "rtype": 0, "no_date": 0}
        matched_accounts: set[str] = set()
        for row in rows:
            seen_in_report += 1
            acct = _match(row, by_id, by_name)
            if acct is None:
                continue
            matched += 1
            matched_accounts.add(acct.name)
            stage = ir.text(ir.pick(row, "STAGE_NAME", "Stage"))
            if _CLOSED.search(stage):
                dropped["closed"] += 1
                continue
            if ir.text(ir.pick(row, "FORECAST_CATEGORY_NAME", "FORECAST_CATEGORY", "Forecast Category")).strip().lower() == "omitted":
                dropped["omitted"] += 1
                continue
            rtype = ir.text(ir.pick(row, *_F_RECORD_TYPE)).strip()
            if tech_only and rtype and rtype.lower() not in _TECH_TYPES:
                dropped["rtype"] += 1
                continue
            close = ir.iso_date(ir.pick(row, "CLOSE_DATE", "Close Date"))
            try:
                d = date.fromisoformat(close)
            except ValueError:
                dropped["no_date"] += 1
                continue
            if not all_quarters and not any(w.contains(d) for w in windows):
                outside += 1
                continue
            amount = ir.pick(row, "Opportunity_Amount_USD__c", "Opportunity Amount USD") \
                or ir.pick(row, "AMOUNT_CONVERTED", "Amount (converted)", "AMOUNT", "Amount")
            out.append({
                "account": acct.name,
                "account_url": acct.isc_url,
                "name": ir.text(ir.pick(row, "OPPORTUNITY_NAME", "Opportunity Name")),
                "record_url": _record_url(ir.pick(row, "OPPORTUNITY_NAME", "Opportunity Name")),
                "owner": ir.text(ir.pick(row, "FULL_NAME", "OWNER", "Opportunity Owner", "Opportunity Owner Name", "Owner")),
                "parent_account_number": ir.text(ir.pick(row, *_F_PARENT_ACCT)),
                "next_step": ir.text(ir.pick(row, "NEXT_STEP", "Next Step")),
                "stage": stage,
                "created_date": ir.iso_date(ir.pick(row, "CREATED_DATE", "Created Date")),
                "close_date": close,
                "amount": ir.number(amount),
                "expected_revenue": ir.number(ir.pick(row, *_F_EXPECTED_REV)),
                "forecast_pipeline": ir.text(ir.pick(row, *_F_FORECAST_PIPE)),
                "record_type": rtype,
                "last_modified": ir.iso_date(ir.pick(row, *_F_LAST_MODIFIED)),
                "renewed_acv_total": ir.number(ir.pick(row, *_F_RENEWED_ACV)),
            })

        if not seen_in_report:
            notes.append({"area": "Opportunities", "account": "",
                          "note": f"'{report_name}' returned no rows. Open it in ISC to confirm you can see data."})
        elif not matched:
            notes.append({"area": "Opportunities", "account": "",
                          "note": f"'{report_name}' returned {seen_in_report} opportunities but none belong to the "
                                  "selected accounts. Check the report link, or that the report includes the Account Name column."
                                  + _hint(rows, accounts)})
        elif not out:
            where = "" if all_quarters else f" with a close date in {' or '.join(w.label for w in windows)}"
            notes.append({"area": "Opportunities", "account": "",
                          "note": f"{matched} opportunities matched your accounts, but none are open{where}."})
        if not complete:
            notes.append({"area": "Opportunities", "account": "",
                          "note": f"'{report_name}' looks truncated: Salesforce returns at most 2,000 rows per report run "
                                  f"({len(rows)} received), so some opportunities are missing. Narrow the report filters "
                                  "(for example to the selected customers) or split it into several reports."})
        if matched:
            for a in sorted(accounts, key=lambda x: x.name.lower()):
                if a.name not in matched_accounts:
                    notes.append({"area": "Opportunities", "account": a.name,
                                  "note": f"No rows for this account in '{report_name}'. The report may be filtered "
                                          "(owner, stage, date or customer) or the account name/ID differs."})
            reasons = [(dropped["closed"], "in a closed, won or lost stage"),
                       (dropped["omitted"], "with Omitted forecast category"),
                       (dropped["rtype"], "with a record type other than IBM Technology "
                                          "(clear 'IBM Technology opportunities only' to include them)"),
                       (dropped["no_date"], "with no usable close date"),
                       (outside, "closing outside the selected quarters")]
            parts = [f"{n} {text}" for n, text in reasons if n]
            if parts:
                notes.append({"area": "Opportunities", "account": "",
                              "note": f"Of {matched} rows matching your accounts, {len(out)} were kept. Left out: "
                                      + "; ".join(parts) + "."})
        if out:
            # Tell the user which ISC report columns to add when a field came back empty everywhere.
            checks = [("amount", "No dollar values found. Add the 'Opportunity Amount USD' column to the ISC report."),
                      ("expected_revenue", "No Expected Revenue values found. Add the 'Expected Amount' (Expected Revenue) column to the ISC report."),
                      ("parent_account_number", "No Parent Account Number values found. Add the 'Parent Account Number' column to the ISC report."),
                      ("next_step", "No Next Step values found. Add the 'Next Step' column to the ISC report."),
                      ("created_date", "No Created Date values found. Add the 'Created Date' column to the ISC report."),
                      ("last_modified", "No Last Modified Date values found. Add the 'Last Modified Date' column to the ISC report."),
                      ("record_type", "No Opportunity Record Type values found. Add the 'Opportunity Record Type' column to the ISC report.")]
            for key, msg in checks:
                if not any(o[key] for o in out):
                    notes.append({"area": "Opportunities", "account": "", "note": msg})
        out.sort(key=lambda r: (r["close_date"], r["account"]))
        return out, notes

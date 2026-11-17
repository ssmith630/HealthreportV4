"""Read per-account figures from the "Seller and Manager Deployment Forecast & Renewals" CRM Analytics dashboard.

The forecast fields and the renewal at risk figures are not columns of any ISC report, so the dashboard is the
only source. IBM blocks REST/SOQL calls made with a browser session, but the dashboard works in the signed-in
browser. While it renders, it POSTs Wave ``query`` requests. This module opens the dashboard and reads those
responses through CDP network events (the same approach ``isc_report`` uses for Aura reports). Nothing leaves
the browser, and no query is sent that the dashboard did not send itself.

Two pages are read:
- Seller Deployment (the page the dashboard opens on). Its per-account query has one row per account with
  columns named ``<Q> Budget``, ``<Q> Deployed``, ``<Q> Call``, ``<Q> Call to Go``, ``<Q> Upside``, ``<Q> Stretch``
  and ``<Q> Pipeline``, plus the same set prefixed ``FY<yy>``, where ``<Q>`` is like ``4Q26``.
- Seller Renewal (after "Renewal At Risk" is clicked in the left navigation). Its detail query has one row per
  account and product with ``Renewal Amt (NQ)``, ``AtRisk Amt (NQ)`` and ``At Risk Indicator``. The figures of
  the "Next 4 Quarters" chart are read from the page itself: it has a "Table equivalent for screen reader
  users" button that renders the exact values as an HTML table (see ``read_next4``). The By Account
  widget (``NQ Renewal``, ``NQ RED``, and the ``NQ+1`` to ``NQ+3`` equivalents) is not sent on the wire and its
  table drops empty columns, so its own SAQL is read from the dashboard definition (the ``bundle`` response)
  and run again with the page's own request headers (see ``replay_by_account``). Both are best effort, and
  the report works without them.

The dashboard caps a Wave query at 10,000 records. The renewal detail query is sorted by account name, so when
a result has exactly that many records the accounts that sort last may be missing. The report covers only the
accounts of one market, so the capture first selects that market in the dashboard's ``Market Name`` filter
(``US Industry Market`` by default). The filter is global, so every query then returns only that market's
accounts, which is far below the cap. Results that arrived before the filter was applied are discarded. The
by-account query has one row per account and is also used when the detail result is capped or empty.
"""
from __future__ import annotations

import json
import re
import time

from browser import BrowserError, Tab

FIELDS = ("Budget", "Deployed", "Call", "Call to Go", "Upside", "Stretch")
DEPLOY_FIELDS = FIELDS + ("Pipeline",)
RECORD_CAP = 10000
DEFAULT_MARKET = "US Industry Market"
_QCOL = re.compile(r"^(\dQ\d{2}) Call$")

_DETAIL_KEYS = ("Acc.Name", "UTT.Name", "At Risk Indicator", "Renewal Amt (NQ)", "AtRisk Amt (NQ)")
_BY_ACCOUNT_KEYS = ("Acc.Name", "NQ Renewal")
RENEWAL_COLUMNS = ("NQ Renewal", "NQ RED", "NQ+1 Renewal", "NQ+1 RED", "NQ+2 Renewal", "NQ+2 RED", "NQ+3 Renewal", "NQ+3 RED")


class DashboardError(RuntimeError):
    pass


def quarter_prefix(start) -> str:
    """Dashboard column prefix for the quarter that starts on ``start`` (a date), e.g. ``4Q26``."""
    return f"{(start.month - 1) // 3 + 1}Q{str(start.year)[2:]}"


def year_prefix(start) -> str:
    """Dashboard column prefix for the fiscal year of the quarter that starts on ``start``, e.g. ``FY26``."""
    return f"FY{str(start.year)[2:]}"


def _is_wave_query(url: str) -> bool:
    u = url.lower()
    return "wave/query" in u or "wave%2fquery" in u


def _records(body: str) -> list[dict]:
    try:
        data = json.loads(body)
    except ValueError:
        return []
    recs = (data.get("results") or {}).get("records") if isinstance(data, dict) else None
    if not isinstance(recs, list) or not recs or not isinstance(recs[0], dict):
        return []
    if any("\u00a0" in k for k in recs[0]):  # the renewal measures are named with a non-breaking space
        recs = [{k.replace("\u00a0", " "): v for k, v in r.items()} for r in recs]
    return recs


def _account_records(body: str) -> list[dict]:
    """Records of a Wave query result that are one row per account with ``<Q> Call`` columns."""
    recs = _records(body)
    return recs if recs and "Acc.Name" in recs[0] and any(_QCOL.match(k) for k in recs[0]) else []


def name_key(rec: dict) -> str | None:
    """Key that holds the account name in a by-account record: ``Acc.Name``, else the first key ending in ``Name``."""
    if "Acc.Name" in rec:
        return "Acc.Name"
    return next((k for k in rec if k.endswith("Name")), None)


def _is_by_account(recs: list[dict]) -> bool:
    """True for the Seller Renewal by-account result: one name column and ``NQ Renewal`` measures."""
    seen: set[str] = set()
    for r in recs[:200]:
        seen.update(r)
    return bool(recs) and "NQ Renewal" in seen and any(k.endswith("Name") for k in seen)


def _has_keys(recs: list[dict], keys: tuple[str, ...]) -> bool:
    """True when the records carry every key. A record omits a measure it has no value for, so look at several."""
    seen: set[str] = set()
    for r in recs[:200]:
        seen.update(r)
    return bool(recs) and all(k in seen for k in keys)


# ---- frame access ---------------------------------------------------------
# The dashboard renders in a child frame whose URL contains /analytics/dashboard/. Its buttons sit in shadow
# roots, so a script is run in that frame (an isolated world) and walks the shadow DOM.
# Text is compared after dropping zero-width spaces and turning non-breaking spaces into plain ones ("NQ\xa0Renewal").
_CLICK = """((name) => { let c = 0;
  function visit(root) { for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) visit(el.shadowRoot);
    if (el.children.length === 0 && (el.textContent || '').replace(/\\u200b/g, '').replace(/\\s+/g, ' ').trim() === name) { el.click(); c++; }
  } }
  visit(document); return c; })(%s)"""
_COUNT = _CLICK.replace("el.click(); c++;", "c++;")


def _frame_id(node: dict) -> str | None:
    if "/analytics/dashboard/" in node["frame"].get("url", ""):
        return node["frame"]["id"]
    for child in node.get("childFrames", []):
        found = _frame_id(child)
        if found:
            return found
    return None


def click_text(tab: Tab, label: str, click: bool = True) -> int:
    """Click every leaf element of the dashboard frame whose text is ``label``. Returns how many were clicked.

    With ``click`` false, nothing is clicked and the result is how many elements have that text.
    """
    try:
        fid = _frame_id(tab.send("Page.getFrameTree")["frameTree"])
        if not fid:
            return 0
        ctx = tab.send("Page.createIsolatedWorld", {"frameId": fid, "worldName": "healthreport"})["executionContextId"]
        res = tab.send("Runtime.evaluate", {"expression": (_CLICK if click else _COUNT) % json.dumps(label),
                                            "returnByValue": True, "contextId": ctx})
    except (BrowserError, KeyError):
        return 0
    value = (res.get("result") or {}).get("value")
    return value if isinstance(value, int) else 0


# ---- accessible tables on the Seller Renewal page --------------------------
_TABLE_BUTTON = """((act) => { const btns = [];
  function visit(root) { for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) visit(el.shadowRoot);
    if (el.tagName === 'BUTTON' && /table equivalent for screen reader users/i.test(el.textContent || '')) btns.push(el);
  } }
  visit(document);
  let c = 0;
  for (const b of btns) {
    if (act === 'all' || (act === 'off' && /^enable/i.test((b.textContent || '').trim()))) { b.click(); c++; }
  }
  return [btns.length, c]; })(%s)"""
_TABLES = """(() => { const out = [];
  function visit(root) { for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) visit(el.shadowRoot);
    if (el.tagName === 'TABLE') out.push([...el.querySelectorAll('tr')].map(tr => [...tr.children].map(
      c => [c.tagName, (c.textContent || '').replace(/\\u200b/g, '').replace(/\\s+/g, ' ').trim()])));
  } }
  visit(document); return out; })()"""
_AMOUNT = re.compile(r"^\(?-?\$?\s*-?[\d,]+(?:\.\d+)?\)?$")


def _amount(text: str) -> float | None:
    """``$1,234`` -> 1234.0, ``($5)`` or ``-$5`` -> -5.0, anything that is not an amount -> None."""
    t = text.strip()
    if not t or not _AMOUNT.match(t):
        return None
    neg = t.startswith("(") or "-" in t
    value = float(re.sub(r"[^\d.]", "", t) or "0")
    return -value if neg else value


def parse_next4(tables: list) -> dict:
    """Read the "Next 4 Quarters" chart table: rows of an amount cell and an optional indicator header cell.

    Returns ``{"Red": amount, "Yellow": amount, "Green": amount, "None": amount}`` for the indicators present,
    where ``"None"`` is the blank indicator. Returns ``{}`` when no table has an ``At Risk Indicator`` header.
    """
    for rows in tables:
        if not rows or not any("at risk indicator" in text.lower() for tag, text in rows[0] if tag == "TH"):
            continue
        out: dict = {}
        for row in rows[1:]:
            amounts = [_amount(text) for tag, text in row if tag == "TD"]
            amounts = [a for a in amounts if a is not None]
            if not amounts:
                continue
            label = next((text for tag, text in row if tag == "TH" and text), "None")
            out[label] = out.get(label, 0.0) + amounts[0]
        return out
    return {}


def _eval(tab: Tab, expression: str):
    try:
        fid = _frame_id(tab.send("Page.getFrameTree")["frameTree"])
        if not fid:
            return None
        ctx = tab.send("Page.createIsolatedWorld", {"frameId": fid, "worldName": "healthreport"})["executionContextId"]
        res = tab.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "contextId": ctx})
    except (BrowserError, KeyError):
        return None
    return (res.get("result") or {}).get("value")


def read_next4(tab: Tab, wait: float = 20.0) -> dict:
    """Read the "Next 4 Quarters" figures of the Seller Renewal page from its "table equivalent" view.

    Returns the dict of ``parse_next4``; it is empty when the page did not render the table. The toggle buttons
    are stateful, so only a button that is off is clicked. A table can take several seconds to render after its
    button is clicked, so the read polls for up to ``wait`` seconds. When the table still is not there, every
    button is clicked twice (off, then on).
    """
    def read() -> dict:
        return parse_next4(_eval(tab, _TABLES) or [])

    def poll() -> dict:
        end = time.time() + wait
        found = read()
        while not found and time.time() < end:
            tab.pump(2.0)
            found = read()
        return found

    next4 = read()
    if not next4:
        res = _eval(tab, _TABLE_BUTTON % json.dumps("off"))
        if isinstance(res, list) and res[1]:
            next4 = poll()
    if not next4:
        res = _eval(tab, _TABLE_BUTTON % json.dumps("all"))
        if isinstance(res, list) and res[0]:
            tab.pump(1.0)
            _eval(tab, _TABLE_BUTTON % json.dumps("all"))
            next4 = poll()
    return next4


# ---- By Account widget, from the dashboard definition -----------------------
_RENEWALS_DATASET = "GS_Renewals_At_Risk_Dataset"
_MARKET_FIELD = "Acc.Market_Name__c"
_REPLAY_HEADERS = ("authorization", "_confirmationtoken", "content-type", "uidashboardid", "uidashboarddatasetids",
                   "uidashboardstepid", "allowlimitedassetaccess")


def by_account_saql(bundle: dict, market: str = "") -> str | None:
    """The By Account widget's SAQL from the dashboard definition, ready to run, or None when it is not there.

    The widget is the saql step that groups the renewals dataset by ``Acc.Name`` and returns the eight
    ``RENEWAL_COLUMNS``. The dashboard loads the dataset by name; a direct query needs ``<id>/<versionId>``. The
    dashboard's Market Name filter is applied by the page, not by the query, so ``market`` is added as a filter.
    """
    try:
        steps = bundle["asset"]["state"]["steps"]
        saql = next(q for s in steps.values() for q in [s.get("query")]
                    if isinstance(q, str) and f'load "{_RENEWALS_DATASET}"' in q
                    and "group q_renewal by 'Acc.Name'" in q and all(f"'{c}'" in q for c in RENEWAL_COLUMNS))
        ds = next(x for x in bundle["datasets"] if x.get("name") == _RENEWALS_DATASET)
        saql = saql.replace(f'load "{_RENEWALS_DATASET}"', f'load "{ds["id"]}/{ds["currentVersionId"]}"')
    except (KeyError, TypeError, StopIteration, AttributeError):
        return None
    if market:
        marker = "q_renewal = filter q_renewal by 'Acc.Name' is not null;"
        if marker not in saql:
            return None  # the query changed; do not guess where the market filter goes
        saql = saql.replace(marker, f"q_renewal = filter q_renewal by '{_MARKET_FIELD}' == {json.dumps(market)};\n{marker}", 1)
    return saql


def replay_by_account(tab: Tab, request: dict, bundle: dict, market: str = "") -> list[dict]:
    """Run the By Account widget's query in the page and return its records (empty when it cannot be run).

    ``request`` is a Wave query request the page itself sent (``url``, ``headers``, ``postData``). Its URL, session
    headers and metadata are reused, so the call is made as the signed-in user. Nothing is read from or written to
    disk. Records have ``Acc.Name`` and the eight ``RENEWAL_COLUMNS``; a measure with no value is 0.
    """
    saql = by_account_saql(bundle, market)
    if not saql or not request:
        return []
    try:
        metadata = json.loads(request.get("postData") or "{}").get("metadata", {})
    except ValueError:
        metadata = {}
    headers = {k: v for k, v in (request.get("headers") or {}).items() if k.lower() in _REPLAY_HEADERS}
    body = json.dumps({"query": saql, "metadata": metadata})
    js = ("fetch(%s,{method:'POST',credentials:'include',headers:%s,body:%s}).then(r=>r.text())"
          % (json.dumps(request["url"]), json.dumps(headers), json.dumps(body)))
    try:
        text = tab.evaluate(js, timeout=60)
    except BrowserError:
        return []
    recs = _records(text) if isinstance(text, str) else []
    return recs if _has_keys(recs, ("Acc.Name",) + RENEWAL_COLUMNS) else []


# ---- capture --------------------------------------------------------------
def capture_dashboard(tab: Tab, url: str, prefix: str, renewal: bool = True, progress=None,
                      timeout: float = 150.0, market: str = DEFAULT_MARKET) -> dict:
    """Open the dashboard and return the query results this report needs.

    Returns ``{"deployment": [...], "renewal_detail": [...], "renewal_detail_more": [[...]],
    "renewal_by_account": [...], "renewal_next4": {...}, "renewal_by_account_page": [...]}``. ``renewal_next4``
    is read from the page's table view (see ``read_next4``) and ``renewal_by_account_page`` is the By Account
    widget's query run again (see ``replay_by_account``); they are empty when they could not be read.
    ``deployment`` holds the per-account records that carry ``<prefix> Call``
    columns. ``renewal_detail`` is the largest detail result; it is empty when ``renewal`` is false or the Seller
    Renewal page did not answer in time. ``renewal_detail_more`` holds any other copies of the detail query
    (for example one in the reverse sort order). ``renewal_by_account`` is the page's by-account result, which
    is empty when the page did not send it. ``market`` is selected in the dashboard's Market Name filter before
    the figures are read (pass an empty string to read the unfiltered dashboard). Raises DashboardError when no
    deployment records arrive.
    """
    tab.send("Page.enable")
    tab.send("Network.enable")
    try:  # a background or minimized window reports the page as hidden, and the dashboard then skips its filter bar
        tab.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except BrowserError:
        pass
    try:  # the dashboard may render in a child frame that is its own target
        tab.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
    except BrowserError:
        pass
    tab.events.clear()
    tab.send("Page.navigate", {"url": url})
    start = time.time()
    pending: dict[tuple, str] = {}
    out: dict = {"deployment": [], "renewal_detail": [], "renewal_detail_more": [], "renewal_by_account": []}
    detail_sets: list[list[dict]] = []
    bundle_reqs: dict[tuple, str] = {}  # dashboard definition responses (large, so read only when needed)
    bundle: dict = {}
    template: dict = {}                 # a Wave query request the page sent itself
    found_prefixes: set[str] = set()
    stage, stage_at = "load", start    # load -> market -> widgets -> renewal
    step, step_at = 0, start           # inside "market": 0 open filter, 1 pick market, 2 wait
    unfiltered: dict = {}

    def drain() -> None:
        for ev in list(tab.events):
            method, sid = ev.get("method"), ev.get("sessionId")
            if method == "Target.attachedToTarget":
                try:
                    tab.send("Network.enable", session_id=ev["params"].get("sessionId"))
                except BrowserError:
                    pass
            elif method == "Network.requestWillBeSent":
                req = ev["params"]["request"]
                if not template and _is_wave_query(req["url"]) and req.get("postData"):
                    template.update(req)
            elif method == "Network.responseReceived":
                p = ev["params"]
                if _is_wave_query(p["response"]["url"]):
                    pending[(sid, p["requestId"])] = p["response"]["url"]
                elif re.search(r"wave(/|%2f)dashboards(/|%2f)\w+(/|%2f)bundle", p["response"]["url"], re.I):
                    bundle_reqs[(sid, p["requestId"])] = p["response"]["url"]
        tab.events.clear()
        for key in list(bundle_reqs):
            if bundle:
                bundle_reqs.clear()
                break
            try:
                text = tab.send("Network.getResponseBody", {"requestId": key[1]}, session_id=key[0]).get("body", "")
            except BrowserError:
                continue
            del bundle_reqs[key]
            try:
                found = json.loads(text)
            except ValueError:
                continue
            if isinstance(found, dict) and by_account_saql(found):
                bundle.update(found)
        for key in list(pending):
            sid, rid = key
            try:
                body = tab.send("Network.getResponseBody", {"requestId": rid}, session_id=sid).get("body", "")
            except BrowserError:
                continue  # body not ready yet; try again on the next pass
            del pending[key]
            recs = _records(body)
            if not recs:
                continue
            keys = {k for r in recs[:200] for k in r}
            if "Acc.Name" in keys and any(_QCOL.match(k) for k in keys):
                found_prefixes.update(m.group(1) for k in keys for m in [_QCOL.match(k)] if m)
                if f"{prefix} Call" in keys and len(recs) > len(out["deployment"]):
                    out["deployment"] = recs
            elif _has_keys(recs, _DETAIL_KEYS):
                detail_sets.append(recs)
                detail_sets.sort(key=len, reverse=True)
                out["renewal_detail"], out["renewal_detail_more"] = detail_sets[0], detail_sets[1:]
            elif _is_by_account(recs) and len(recs) > len(out["renewal_by_account"]):
                out["renewal_by_account"] = recs

    while time.time() - start < timeout:
        tab.pump(2.0)
        drain()
        now = time.time()
        if stage == "load" and (out["deployment"] or now - start > 45):
            if not renewal and out["deployment"] and not market:
                break
            if market:
                # Select the market first, so every query that follows returns only that market's accounts.
                stage, stage_at, step, step_at = "market", now, 0, now
                if progress:
                    progress(f"Filtering the dashboard to {market}")
            else:
                stage, stage_at = "widgets", now
                if progress:
                    progress("Opening the Renewal At Risk page of the dashboard")
                click_text(tab, "Load more widgets")
        elif stage == "market":
            if step == 0:
                if click_text(tab, market, click=False):
                    step = 3  # the market is already selected
                elif click_text(tab, "Market Name", click=False):  # the filter bar renders after the first query
                    click_text(tab, "Market Name")
                    step, step_at = 1, now
                elif now - stage_at > 40:
                    step = 3
            elif step == 1 and now - step_at > 2:
                if click_text(tab, market):
                    drain()  # keep nothing that was sent before the filter changed
                    unfiltered = {k: out[k] for k in ("deployment", "renewal_detail", "renewal_by_account")}
                    for k in out:
                        out[k] = []
                    detail_sets.clear()
                    pending.clear()
                    step, step_at = 2, now
                elif now - step_at > 12:
                    step = 3
            elif step == 2 and (out["deployment"] or now - step_at > 25):
                step = 3
            if step == 3:
                for k, v in unfiltered.items():  # a query the filter did not repeat keeps its earlier result
                    if not out[k]:
                        out[k] = v
                if not renewal and out["deployment"]:
                    break
                stage, stage_at, step = "widgets", now, 0
                if progress:
                    progress("Opening the Renewal At Risk page of the dashboard")
                click_text(tab, "Load more widgets")
        elif stage == "widgets" and now - stage_at > 10:
            if click_text(tab, "Renewal At Risk") or now - stage_at > 25:
                stage, stage_at = "renewal", now
        elif stage == "renewal":
            if now - stage_at > 60:
                break
            if (out["renewal_detail"] and now - stage_at > 10) or now - stage_at > 20:
                # The by-account query is not sent on the wire. Read the "Next 4 Quarters" table equivalent from the
                # page and run the By Account widget's own query. Best effort: the report works without them.
                if progress:
                    progress("Reading the Renewal At Risk figures")
                drain()
                out["renewal_next4"] = read_next4(tab)
                drain()
                out["renewal_by_account_page"] = replay_by_account(tab, template, bundle, market)
                break
    out.setdefault("renewal_next4", {})
    out.setdefault("renewal_by_account_page", [])
    if out["deployment"]:
        return out
    if found_prefixes:
        raise DashboardError(
            f"The deployment dashboard returned figures for {', '.join(sorted(found_prefixes))} but not for the "
            f"current quarter ({prefix}). Set the dashboard's quarter to the current quarter and retry.")
    raise DashboardError(
        "The deployment dashboard did not return per-account figures. Make sure you are signed in to IBM Sales "
        "Cloud (Connect tab), open the dashboard in the browser window, then retry.")

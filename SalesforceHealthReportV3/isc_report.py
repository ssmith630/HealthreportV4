"""Read a saved ISC (Salesforce) *Report* through the user's signed-in browser.

IBM blocks REST/SOQL calls made with a browser session, but the Lightning UI works.
When a report page renders, Salesforce POSTs an Aura ``runReport`` action. We open
the report and read that response through CDP network events. This is the same
approach V2 used for the "Marriott Opps" report.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime

from browser import BrowserError, Tab


class ReportError(RuntimeError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _results_in(body: str) -> list[dict]:
    try:
        data = json.loads(body)
    except ValueError:
        return []
    out = []
    for action in data.get("actions", []) if isinstance(data, dict) else []:
        rv = action.get("returnValue")
        if isinstance(rv, dict) and "factMap" in rv and "reportMetadata" in rv:
            out.append(rv)
    return out


REQUEST_KEY = "_aura_request"  # the page's own runReport request, kept so the report can be re-run with new filters


def capture_report(tab: Tab, url: str, progress=None, timeout: float = 90.0) -> dict:
    """Open ``url`` in ``tab`` and return the largest ``runReport`` result seen.

    The result also carries the page's own Aura request under ``REQUEST_KEY`` (``url`` and ``post``)
    so ``rerun_report`` can run the same report again with different filters.
    """
    tab.send("Network.enable")
    tab.events.clear()
    tab.send("Page.navigate", {"url": url})
    start = time.time()
    seen: dict[str, str] = {}
    requests: dict[str, dict] = {}
    best: dict | None = None
    signed_in_hint = False
    while time.time() - start < timeout:
        tab.pump(2.0)
        for ev in list(tab.events):
            method = ev.get("method")
            if method == "Network.responseReceived":
                p = ev["params"]
                u = p["response"]["url"]
                if "/aura" in u and "runReport" in u:
                    seen[p["requestId"]] = u
            elif method == "Network.requestWillBeSent":
                p = ev["params"]
                req = p.get("request", {})
                if "/aura" in req.get("url", "") and "runReport" in req.get("url", ""):
                    requests[p["requestId"]] = {"url": req["url"], "post": req.get("postData")}
        tab.events.clear()
        for rid in list(seen):
            try:
                body = tab.send("Network.getResponseBody", {"requestId": rid}).get("body", "")
            except BrowserError:
                continue  # body not ready yet; try again on the next pass
            del seen[rid]
            for rv in _results_in(body):
                if best is None or len(json.dumps(rv["factMap"])) >= len(json.dumps(best["factMap"])):
                    best = rv
                    req = requests.get(rid)
                    if req and not req["post"]:
                        try:
                            req["post"] = tab.send("Network.getRequestPostData", {"requestId": rid}).get("postData")
                        except BrowserError:
                            pass
                    if req and req["post"]:
                        best[REQUEST_KEY] = req
        if best is not None:
            return best
        cur = tab.current_url()
        if "/lightning/r/Report" not in cur and not signed_in_hint and time.time() - start > 10:
            signed_in_hint = True
            if progress:
                progress("Waiting for you to finish the IBM sign-in in the browser window...")
    raise ReportError(
        "The report page did not return data. Make sure you are signed in to IBM Sales Cloud "
        "(Connect tab) and can open the report in the browser window."
    )


def rerun_report(tab: Tab, rv: dict, edit, timeout: float = 200.0) -> dict:
    """Run the report behind ``rv`` again with its metadata changed by ``edit(report_metadata_dict)``.

    Re-posts the page's own captured Aura ``runReport`` request from inside the signed-in page, so the
    session and anti-forgery token are the browser's. This lets the caller replace the saved filters
    (date range, account list) that a ``?fvN=`` URL override cannot express.
    """
    req = rv.get(REQUEST_KEY)
    if not req or not req.get("post"):
        raise ReportError("The report request was not captured, so the report cannot be re-run with new filters.")
    form = urllib.parse.parse_qs(req["post"])
    try:
        msg = json.loads(form["message"][0])
        action = msg["actions"][0]
        outer = json.loads(action["params"]["reportMetadata"])
    except (KeyError, IndexError, ValueError) as e:
        raise ReportError(f"The captured report request could not be read: {e}") from e
    meta = outer.get("reportMetadata", outer)
    edit(meta)
    action["params"]["reportMetadata"] = json.dumps(outer)
    body = urllib.parse.urlencode({k: form[k][0] for k in ("aura.context", "aura.pageURI", "aura.token") if k in form}
                                  | {"message": json.dumps(msg)})
    js = ("fetch(%s,{method:'POST',credentials:'include',headers:{'Content-Type':"
          "'application/x-www-form-urlencoded;charset=UTF-8'},body:%s}).then(r=>r.text())"
          % (json.dumps(req["url"]), json.dumps(body)))
    try:
        text = tab.evaluate(js, timeout=timeout)
    except BrowserError as e:
        raise ReportError(f"Re-running the report failed: {e}") from e
    results = _results_in(text if isinstance(text, str) else "")
    if not results:
        raise ReportError("Re-running the report returned no data: " + str(text)[:200])
    result = max(results, key=lambda r: len(json.dumps(r["factMap"])))
    result[REQUEST_KEY] = req
    return result


# ---- flatten ------------------------------------------------------------

MAX_REPORT_ROWS = 2000  # Salesforce returns at most this many detail rows per report run


def is_truncated(rv: dict, row_count: int) -> bool:
    """True when Salesforce says the result is partial (allData false) or the row cap was reached."""
    return rv.get("allData") is False or row_count >= MAX_REPORT_ROWS


def report_rows(rv: dict) -> list[dict]:
    """Flatten a report result into rows keyed by normalised column API name and label."""
    meta = rv.get("reportMetadata", {})
    cols: list[str] = meta.get("detailColumns", [])
    info = rv.get("reportExtendedMetadata", {}).get("detailColumnInfo", {})
    fact = rv.get("factMap", {})
    keys = [k for k, v in fact.items() if v.get("rows")]
    if "T!T" in keys:
        keys = ["T!T"]

    group_cols = [g.get("name") for g in meta.get("groupingsDown", []) if isinstance(g, dict)]
    group_vals: dict[str, list] = {}

    def walk(groups: list, level: int, path: list) -> None:
        for g in groups:
            here = path + [(group_cols[level] if level < len(group_cols) else None, g)]
            group_vals[g.get("key")] = here
            walk(g.get("groupings") or [], level + 1, here)

    walk((rv.get("groupingsDown") or {}).get("groupings") or [], 0, [])

    rows = []
    for k in keys:
        for r in fact[k]["rows"]:
            row: dict[str, dict] = {}
            for gcol, g in group_vals.get(k.split("!")[0], []):
                if gcol:
                    entry = {"v": g.get("value"), "l": g.get("label"), "id": g.get("value")}
                    parts = gcol.split(".")
                    row.setdefault(_norm(gcol), entry)
                    row.setdefault(_norm("".join(parts[1:])), entry)
                    row.setdefault(_norm(parts[-1]), entry)
            for col, cell in zip(cols, r.get("dataCells", [])):
                rid, val = cell.get("recordId"), cell.get("value")
                if isinstance(val, str) and val != cell.get("label") and re.fullmatch(r"[a-zA-Z0-9]{15}|[a-zA-Z0-9]{18}", val):
                    rid = val
                entry = {"v": val, "l": cell.get("label"), "id": rid}
                parts = col.split(".")
                row.setdefault(_norm(col), entry)
                row.setdefault(_norm("".join(parts[1:])), entry)
                row.setdefault(_norm(parts[-1]), entry)
                label = (info.get(col) or {}).get("label") or ""
                for lab in {label, label.split(": ")[-1]}:
                    if lab:
                        row.setdefault("lbl:" + _norm(lab), entry)
            rows.append(row)
    return rows


def pick(row: dict, *names: str) -> dict | None:
    for n in names:
        n = _norm(n)
        if n in row:
            return row[n]
        if "lbl:" + n in row:
            return row["lbl:" + n]
    return None


def text(cell: dict | None) -> str:
    if not cell:
        return ""
    lab, val = cell.get("l"), cell.get("v")
    if isinstance(val, dict):
        val = val.get("label") or val.get("amount")
    if lab not in (None, "", "-"):
        return str(lab)
    return "" if val is None else str(val)


def number(cell: dict | None) -> float:
    if not cell:
        return 0.0
    val = cell.get("v")
    if isinstance(val, dict):
        val = val.get("amount")
    if isinstance(val, (int, float)):
        return float(val)
    m = re.search(r"-?[\d,]*\.?\d+", str(val if val is not None else cell.get("l") or "").replace(" ", ""))
    try:
        return float(m.group(0).replace(",", "")) if m else 0.0
    except ValueError:
        return 0.0


def iso_date(cell: dict | None) -> str:
    if not cell:
        return ""
    for raw in (cell.get("v"), cell.get("l")):
        if not isinstance(raw, str) or not raw:
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
            return raw[:10]
        for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%m/%d/%Y, %I:%M %p"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
    return ""

"""Check an industry master account JSON file against the app's account list.

    .venv\\Scripts\\python.exe validate_accounts.py                         (Windows)
    .venv/bin/python validate_accounts.py                                   (Mac)
    ... validate_accounts.py --master "path\\to\\industry_accounts_master.json"

The reference list is the file the app loads (accounts.load_accounts: industry_accounts_gainsight_resolved.json,
or ACCOUNTS_FILE). Names are compared ignoring case and surrounding spaces.

It also checks that every Gainsight company listed under an account ("gainsight_companies") is loaded, with the
primary company first (ERROR if not).

It also checks the Industry Market filter (accounts.load_accounts keeps only accounts whose coverage ID is listed
in "Industry Market 2026 Account Coverage 022826.xlsx"):
  - every coverage ID in the workbook must exist in the account file (ERROR if not)
  - the app list after filtering must hold exactly the accounts whose coverage ID is in the workbook (ERROR if not)
  - an account in the master file that the filter drops is reported as a WARNING, not an error
  - if the workbook cannot be read, the filter is skipped and that is reported as a WARNING

ERROR   the file cannot be trusted for this account (missing, duplicated, or blank name)
WARNING worth a look, but the report still runs (ids differ, review flag, account only in one list)
Exit code 1 when there is any ERROR, otherwise 0.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import defaultdict
from pathlib import Path

import accounts as acc

DEFAULT_MASTER = "industry_accounts_master (1).json"

# Coverage IDs that two accounts share on purpose. The Industry Market workbook has one Marriott row, but the
# app lists Marriott Vacation Club-US as its own account.
SHARED_COVERAGE = {"A0001940": {"MARRIOTT", "MARRIOTT VACATION CLUB"}}


def _key(name: str) -> str:
    return " ".join((name or "").split()).upper()


def _load_master(path: Path) -> tuple[list[dict], dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        return list(data.get("accounts", [])), data.get("metadata", {})
    return list(data), {}


def _reference_rows() -> list[dict]:
    """Raw rows of the app's account file, so accounts without an ISC id (skipped by the app) are visible."""
    data = json.loads(acc.accounts_path().read_text(encoding="utf-8-sig"))
    return list(data.get("accounts", data) if isinstance(data, dict) else data)


def _dupes(values: list[tuple[str, str]]) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = defaultdict(list)
    for key, label in values:
        if key:
            seen[key].append(label)
    return {k: v for k, v in seen.items() if len(v) > 1}


def validate(master_path: Path) -> dict:
    """Return {"errors": [...], "warnings": [...], "matches": [...], "summary": {...}}."""
    errors: list[dict] = []
    warnings: list[dict] = []
    matches: list[str] = []

    def err(account: str, issue: str, detail: str = "") -> None:
        errors.append({"account": account, "issue": issue, "detail": detail})

    def warn(account: str, issue: str, detail: str = "") -> None:
        warnings.append({"account": account, "issue": issue, "detail": detail})

    master, meta = _load_master(master_path)
    ref_rows = _reference_rows()
    app_accounts = acc.load_accounts(industry_market_only=False)  # every account with an ISC id
    filtered = acc.load_accounts()                                # after the Industry Market filter
    status = acc.filter_status()
    filtered_ids = {a.isc_id for a in filtered}
    ref = {_key(r.get("account_name") or ""): r for r in ref_rows if r.get("account_name")}
    app_names = {_key(a.name): a for a in app_accounts}

    # Industry Market filter checks.
    if not status["applied"]:
        warn("(filter)", "Industry Market filter not applied", status["reason"])
    else:
        try:
            wb_ids = acc.load_industry_market_ids()
        except Exception as exc:  # already reported above; keep going
            wb_ids = set()
            warn("(filter)", "Could not re-read the Industry Market workbook", str(exc))
        by_cov = {a.coverage_id: a for a in app_accounts if a.coverage_id}
        for cid in sorted(wb_ids - set(by_cov)):
            err(cid, "Coverage ID in the Industry Market workbook is not in the account file")
        expected = {a.isc_id for a in app_accounts if a.coverage_id in wb_ids}
        if expected != filtered_ids:
            err("(filter)", "Filtered app list does not match the workbook",
                f"expected {len(expected)}, app list has {len(filtered_ids)}")
        extra = sum(len(v) - 1 for v in SHARED_COVERAGE.values())  # accounts that share a workbook row on purpose
        if len(filtered) - extra != len(wb_ids):
            warn("(filter)", "Filtered account count differs from workbook coverage IDs",
                 f"{len(filtered)} accounts / {len(wb_ids)} coverage IDs")
        for a in app_accounts:
            if not a.coverage_id:
                warn(a.name, "No coverage ID, dropped by the Industry Market filter")
            elif a.coverage_id not in wb_ids:
                warn(a.name, "Coverage ID not in the Industry Market workbook, dropped by the filter", a.coverage_id)
        for cid, labels in _dupes([(a.coverage_id, a.name) for a in app_accounts]).items():
            if set(labels) == SHARED_COVERAGE.get(cid, set()):
                continue  # known and intended, see SHARED_COVERAGE
            warn(", ".join(labels), "Shared coverage ID in the account file", cid)

    declared = meta.get("total_accounts")
    if declared is not None and declared != len(master):
        warn("(file)", "Declared account count differs", f"metadata says {declared}, file has {len(master)}")

    # Duplicates inside the master file: by name and by the identifiers that should be unique.
    for key, labels in _dupes([(_key(m.get("account_name", "")), m.get("account_name", "")) for m in master]).items():
        err(key, "Duplicate account name in master file", f"{len(labels)} entries")
    for field, getter in (("coverage_id", lambda m: m.get("coverage_id", "")),
                          ("ISC live id", lambda m: (m.get("isc") or {}).get("live_id", "")),
                          ("Gainsight GSID", lambda m: (m.get("gainsight") or {}).get("company_gsid", ""))):
        for value, labels in _dupes([(getter(m), m.get("account_name", "")) for m in master]).items():
            warn(", ".join(labels), f"Shared {field}", value)

    # Duplicates in the reference list.
    for key, labels in _dupes([(_key(r.get("account_name", "")), r.get("account_name", "")) for r in ref_rows]).items():
        err(key, "Duplicate account name in the app account list", f"{len(labels)} entries")

    for m in master:
        name = (m.get("account_name") or "").strip()
        if not name:
            err("(blank)", "Master entry has no account_name", json.dumps(m)[:80])
            continue
        if name != m.get("account_name"):
            warn(name, "Name has leading or trailing spaces")
        k = _key(name)
        r = ref.get(k)
        if r is None:
            close = difflib.get_close_matches(k, list(ref), n=1, cutoff=0.8)
            err(name, "Not in the app account list", f"closest: {ref[close[0]]['account_name']}" if close else "no similar name")
            continue
        if name != r.get("account_name"):
            warn(name, "Name matches only after ignoring case or spaces", f"app list has '{r.get('account_name')}'")
        if k not in app_names:
            err(name, "In the app list but skipped by the app", "the row has no isc_account_id")
            continue
        matches.append(name)
        if status["applied"] and r.get("isc_account_id") not in filtered_ids:
            warn(name, "Excluded by the Industry Market filter", f"coverage id '{r.get('coverage_id') or ''}'")

        isc = m.get("isc") or {}
        gs = m.get("gainsight") or {}
        if not isc.get("live_id") and not r.get("isc_live_id"):
            warn(name, "No ISC live id in either file")
        elif isc.get("live_id") != r.get("isc_live_id"):
            warn(name, "ISC live id differs", f"master {isc.get('live_id') or '-'} / app list {r.get('isc_live_id') or '-'}")
        app_isc = r.get("isc_account_id")
        if app_isc not in (isc.get("db2_enterprise_id"), isc.get("db2_id")):
            warn(name, "ISC account id differs", f"master {isc.get('db2_enterprise_id') or '-'} / app list {app_isc or '-'}")
        if gs.get("company_gsid") != r.get("gainsight_company_gsid"):
            warn(name, "Gainsight GSID differs",
                 f"master {gs.get('company_name') or '-'} / app list {r.get('gainsight_company_name') or '-'}")
        if (m.get("coverage_id") or "") != (r.get("coverage_id") or ""):
            warn(name, "Coverage id differs", f"master {m.get('coverage_id')} / app list {r.get('coverage_id')}")
        if not r.get("gainsight_company_gsid"):
            warn(name, "App list has no Gainsight GSID", "the app will look it up by name")
        for flag in m.get("review_flags") or []:
            warn(name, "Master review flag", flag)
        if (r.get("confidence") or "high").lower() != "high":
            warn(name, "App list confidence is not high", r.get("confidence"))

    master_keys = {_key(m.get("account_name", "")) for m in master}
    for k, r in ref.items():
        if k not in master_keys:
            warn(r["account_name"], "In the app account list but not in the master file")

    # Other Gainsight companies under each coverage ID: loaded from "gainsight_companies", primary first, no repeats.
    for a in app_accounts:
        if not a.gsid:
            continue
        listed = {(c.get("gsid") or "").strip() for c in (ref.get(_key(a.name), {}).get("gainsight_companies") or [])
                  if isinstance(c, dict) and not c.get("non_us")} - {""}
        if a.all_gsids[0] != a.gsid or len(a.all_gsids) != len(set(a.all_gsids)):
            err(a.name, "Gainsight company list is not primary first without repeats", str(a.all_gsids))
        if set(a.all_gsids) != listed | {a.gsid}:
            err(a.name, "Gainsight companies were not all loaded",
                f"file lists {len(listed | {a.gsid})}, app reads {len(a.all_gsids)}")

    # Deployment-report aliases should point at real app accounts.
    alias_path = acc.APP_DIR / acc.ALIASES_FILE
    if alias_path.exists():
        for a in json.loads(alias_path.read_text(encoding="utf-8-sig")).get("aliases", []):
            if _key(a.get("account_name", "")) not in app_names:
                warn(a.get("account_name", "(blank)"), "Deployment alias names an account that is not in the app list")

    # Marriott Vacation Club-US is its own account. The workbook has no row for it, so it reuses Marriott's
    # coverage ID (SHARED_COVERAGE), and its deployment rows must not be counted under Marriott as well.
    mvc, mar = app_names.get(_key("MARRIOTT VACATION CLUB")), app_names.get(_key("MARRIOTT"))
    if mvc is None or mar is None:
        err("MARRIOTT VACATION CLUB", "Marriott or Marriott Vacation Club is missing from the app list")
    else:
        if mvc.coverage_id != mar.coverage_id:
            err(mvc.name, "Coverage ID differs from Marriott", f"{mvc.coverage_id} / {mar.coverage_id}")
        for what, x, y in (("ISC account id", mvc.isc_id, mar.isc_id), ("ISC live id", mvc.live_id, mar.live_id),
                           ("Gainsight company", mvc.gsid, mar.gsid)):
            if not x or x == y:
                err(mvc.name, f"{what} is blank or the same as Marriott", f"{x} / {y}")
        if any("VACATION CLUB" in (x or "").upper() for x in mar.aliases):
            err(mar.name, "Marriott still carries the Vacation Club deployment name", str(mar.aliases))
        if not any("VACATION CLUB" in (x or "").upper() for x in mvc.aliases):
            err(mvc.name, "Vacation Club has no deployment name", str(mvc.aliases))
        if status["applied"] and mvc.isc_id not in filtered_ids:
            err(mvc.name, "Dropped by the Industry Market filter", mvc.coverage_id)

    return {
        "errors": errors, "warnings": warnings, "matches": matches,
        "summary": {"master_accounts": len(master), "app_list_rows": len(ref_rows), "app_accounts_loaded": len(filtered),
                    "app_accounts_before_filter": len(app_accounts), "filter_applied": status["applied"],
                    "matched": len(matches), "errors": len(errors), "warnings": len(warnings)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", default=DEFAULT_MASTER, help="path to the industry master account JSON file")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args()
    path = Path(args.master)
    path = path if path.is_absolute() else acc.APP_DIR / path
    if not path.exists():
        print(f"ERROR master file not found: {path}")
        return 1
    try:
        result = validate(path)
    except (json.JSONDecodeError, OSError, FileNotFoundError) as exc:
        print(f"ERROR could not read the account files: {exc}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if result["errors"] else 0

    s = result["summary"]
    print(f"Master file: {path.name}")
    print(f"Master accounts {s['master_accounts']}   app list rows {s['app_list_rows']}   "
          f"before filter {s['app_accounts_before_filter']}   loaded by the app {s['app_accounts_loaded']}   "
          f"Industry Market filter {'applied' if s['filter_applied'] else 'NOT applied'}")
    print(f"Matched {s['matched']}   Errors {s['errors']}   Warnings {s['warnings']}\n")
    for title, rows in (("ERRORS", result["errors"]), ("WARNINGS", result["warnings"])):
        print(f"{title} ({len(rows)})")
        for r in sorted(rows, key=lambda x: (x["issue"], x["account"])):
            print(f"  {r['account']}: {r['issue']}" + (f" - {r['detail']}" if r["detail"] else ""))
        print()
    print("FAIL" if result["errors"] else "PASS")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

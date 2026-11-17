"""Account list loader.

Reads industry_accounts_gainsight_resolved.json, which already maps each account to
its Salesforce (ISC) account ID and Gainsight company GSID, so the app needs no
name search. Set ACCOUNTS_FILE in .env to use a different file.

Non-US companies: a company in "gainsight_companies" with "non_us": true is not read. It is kept in
Account.skipped_companies so the report can say which ones were skipped. The primary company is always read.

Industry Market filter: only accounts whose coverage ID appears in
"Industry Market 2026 Account Coverage 022826.xlsx" (column "US Coverage ID") are kept.
Set INDUSTRY_MARKET_FILE in .env to use a different workbook. If the workbook is missing
or unreadable, the filter is skipped and the full list is returned (see filter_status()).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_FILE = "industry_accounts_gainsight_resolved.json"


@dataclass
class Account:
    name: str
    isc_id: str
    gsid: str
    confidence: str          # "high" or "review"
    live_id: str = ""        # the account id the user can actually open in ISC (dashboard "isc_live_id")
    account_number: str = ""  # number shown in the ISC Deployments report (see deployment_account_aliases.json)
    aliases: list[str] = field(default_factory=list)  # other names the reports use for this account
    gs_name: str = ""        # Gainsight company name, used to look up the GSID when gsid is empty
    coverage_id: str = ""    # US Coverage ID, the key for the Industry Market filter
    gs_companies: list[dict] = field(default_factory=list)  # other Gainsight companies under the coverage ID: {"gsid", "name"}
    skipped_companies: list[dict] = field(default_factory=list)  # companies marked "non_us" in the account file; never read: {"gsid", "name"}
    isc_name: str = ""       # optional name shown in the ISC deployment and renewal views only (alias file "isc_name")

    @property
    def all_gsids(self) -> list[str]:
        """The primary Gainsight company first, then the others under the same coverage ID, without repeats."""
        out: list[str] = []
        for g in [self.gsid, *(c.get("gsid", "") for c in self.gs_companies)]:
            if g and g not in out:
                out.append(g)
        return out

    def company_name(self, gsid: str) -> str:
        """Gainsight company name for a GSID, or the GSID itself when the name is not known."""
        if gsid == self.gsid and self.gs_name:
            return self.gs_name
        return next((c.get("name") for c in self.gs_companies if c.get("gsid") == gsid and c.get("name")), gsid)

    @property
    def needs_review(self) -> bool:
        return self.confidence.lower() != "high"

    @property
    def open_id(self) -> str:
        """Best id for opening the account in ISC."""
        return self.live_id or self.isc_id

    @property
    def isc_url(self) -> str:
        return f"https://ibmsc.lightning.force.com/lightning/r/Account/{self.open_id}/view"


ALIASES_FILE = "deployment_account_aliases.json"
INDUSTRY_MARKET_FILE = "Industry Market 2026 Account Coverage 022826.xlsx"
COVERAGE_HEADER = "us coverage id"

# Result of the last filter attempt: {"applied", "source", "kept", "dropped", "reason"}.
_filter_status: dict = {"applied": False, "source": "", "kept": 0, "dropped": 0, "reason": "not run"}


def industry_market_path() -> Path:
    raw = os.environ.get("INDUSTRY_MARKET_FILE", "").strip() or INDUSTRY_MARKET_FILE
    p = Path(raw)
    return p if p.is_absolute() else APP_DIR / p


def load_industry_market_ids() -> set[str]:
    """Coverage IDs listed in the Industry Market workbook (upper case, trimmed).

    Finds the header row by its "US Coverage ID" cell, so a shifted header row still works.
    Raises FileNotFoundError or ValueError when the workbook cannot be used.
    """
    path = industry_market_path()
    if not path.exists():
        raise FileNotFoundError(f"Industry Market workbook not found: {path}")
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            col = None
            ids: set[str] = set()
            for row in ws.iter_rows(values_only=True):
                if col is None:
                    for i, v in enumerate(row):
                        if " ".join(str(v or "").split()).lower() == COVERAGE_HEADER:
                            col = i
                            break
                    continue
                v = row[col] if col < len(row) else None
                v = str(v or "").strip().upper()
                if v:
                    ids.add(v)
            if col is not None:
                if not ids:
                    raise ValueError(f"No coverage IDs found under 'US Coverage ID' in {path.name}")
                return ids
    finally:
        wb.close()
    raise ValueError(f"No 'US Coverage ID' column found in {path.name}")


def filter_status() -> dict:
    """What the last load_accounts() did with the Industry Market filter."""
    return dict(_filter_status)


def _load_aliases() -> dict[str, dict]:
    """account_name (upper case) -> {"report_name", "account_number"} from deployment_account_aliases.json."""
    path = APP_DIR / ALIASES_FILE
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(r.get("account_name") or "").strip().upper(): r for r in data.get("aliases", []) if r.get("account_name")}


def accounts_path() -> Path:
    raw = os.environ.get("ACCOUNTS_FILE", "").strip() or DEFAULT_FILE
    p = Path(raw)
    return p if p.is_absolute() else APP_DIR / p


def load_accounts(industry_market_only: bool = True) -> list[Account]:
    path = accounts_path()
    if not path.exists():
        raise FileNotFoundError(f"Account list not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("accounts", data) if isinstance(data, dict) else data
    aliases = _load_aliases()
    out: list[Account] = []
    for r in rows:
        isc_id = (r.get("isc_account_id") or "").strip()
        if not isc_id:
            continue
        name = (r.get("account_name") or r.get("isc_account_name") or isc_id).strip()
        alias = aliases.get(name.upper(), {})
        number = (alias.get("account_number") or "").strip()
        out.append(Account(
            name=name,
            isc_id=isc_id,
            gsid=(r.get("gainsight_company_gsid") or "").strip(),
            confidence=(r.get("confidence") or "high").strip(),
            live_id=(r.get("isc_live_id") or "").strip(),
            account_number=number,
            aliases=[x for x in (alias.get("report_name", "").strip(), number, *alias.get("other_numbers", [])) if x],
            isc_name=(alias.get("isc_name") or "").strip(),
            gs_name=(r.get("gainsight_company_name") or "").strip(),
            coverage_id=(r.get("coverage_id") or "").strip().upper(),
            gs_companies=[{"gsid": (c.get("gsid") or "").strip(), "name": (c.get("name") or "").strip()}
                          for c in r.get("gainsight_companies") or []
                          if isinstance(c, dict) and (c.get("gsid") or "").strip() and not c.get("non_us")
                          and (c.get("gsid") or "").strip() != (r.get("gainsight_company_gsid") or "").strip()],
            skipped_companies=[{"gsid": (c.get("gsid") or "").strip(), "name": (c.get("name") or "").strip()}
                               for c in r.get("gainsight_companies") or []
                               if isinstance(c, dict) and (c.get("gsid") or "").strip() and c.get("non_us")
                               and (c.get("gsid") or "").strip() != (r.get("gainsight_company_gsid") or "").strip()],
        ))
    out.sort(key=lambda a: a.name.lower())
    if industry_market_only:
        out = _apply_industry_market(out)
    return out


def _apply_industry_market(accounts: list[Account]) -> list[Account]:
    """Keep only accounts whose coverage_id is in the Industry Market workbook."""
    global _filter_status
    try:
        ids = load_industry_market_ids()
    except Exception as exc:  # best effort: a bad workbook must not hide every account
        _filter_status = {"applied": False, "source": str(industry_market_path()), "kept": len(accounts),
                          "dropped": 0, "reason": f"{type(exc).__name__}: {exc}"}
        return accounts
    kept = [a for a in accounts if a.coverage_id and a.coverage_id in ids]
    _filter_status = {"applied": True, "source": industry_market_path().name, "kept": len(kept),
                      "dropped": len(accounts) - len(kept), "reason": ""}
    return kept


def accounts_as_dicts() -> list[dict]:
    return [dict(asdict(a), needs_review=a.needs_review, isc_url=a.isc_url) for a in load_accounts()]

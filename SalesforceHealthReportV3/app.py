"""Salesforce Health Report V3 - local web app (one page, one report button)."""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, Response, jsonify, request, send_from_directory  # noqa: E402

import deployments  # noqa: E402
import settings  # noqa: E402
from accounts import accounts_as_dicts, load_accounts  # noqa: E402
from browser import BrowserError, get_browser  # noqa: E402
from gainsight import Gainsight, GainsightError  # noqa: E402
from isc import Isc  # noqa: E402
from plan_status import stale_cutoff  # noqa: E402
from pipeline import REPORTS_DIR, run_report  # noqa: E402
from quarters import current_next_windows, default_year, report_windows  # noqa: E402

PORT = int(os.environ.get("APP_PORT", "8765"))
app = Flask(__name__, static_folder="static", static_url_path="/static")

_isc = Isc()
_gs = Gainsight()
_lock = threading.Lock()   # one report at a time (single shared browser)


def parse_fiscal_year(value) -> int | None:
    """Return a blank year as None; otherwise require an integer from 2000 through 2100."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Fiscal year must be a whole number from 2000 through 2100, or blank.")
    if isinstance(value, int):
        year = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if not re.fullmatch(r"[+-]?[0-9]+", raw):
            raise ValueError("Fiscal year must be a whole number from 2000 through 2100, or blank.")
        year = int(raw)
    else:
        raise ValueError("Fiscal year must be a whole number from 2000 through 2100, or blank.")
    if not 2000 <= year <= 2100:
        raise ValueError("Fiscal year must be a whole number from 2000 through 2100, or blank.")
    return year


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/accounts")
def api_accounts():
    try:
        return jsonify(accounts=accounts_as_dicts(), default_year=default_year(),
                       windows=[w.label for w in report_windows(default_year())],
                       quarters=[w.label for w in current_next_windows()])
    except FileNotFoundError as e:
        return jsonify(error=str(e)), 500


@app.get("/api/status")
def api_status():
    running = get_browser().is_running()
    isc = _isc.status()
    return jsonify(
        browser=running,
        isc=isc["connected"],
        isc_detail=isc["detail"],
        gainsight=_gs.is_connected() if running else False,
    )


@app.get("/api/settings")
def api_settings_get():
    return jsonify(settings.load())


@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    try:
        values = settings.save(body.get("opp_report_url", ""), body.get("deploy_report_url", ""),
                               body.get("dashboard_url", ""))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(values)


@app.post("/api/connect/isc")
def api_connect_isc():
    try:
        _isc.open_sign_in()
    except BrowserError as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True, message="Sign in to IBM Sales Cloud in the browser window that opened.")


@app.post("/api/connect/gainsight")
def api_connect_gainsight():
    if not _isc.is_connected():
        return jsonify(error="Sign in to ISC first."), 400
    try:
        _gs.connect([a.open_id for a in load_accounts() if a.gsid])
    except (GainsightError, BrowserError) as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)


@app.post("/api/generate")
def api_generate():
    body = request.get_json(silent=True) or {}
    try:
        year = parse_fiscal_year(body.get("year"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    selected = set(body.get("accounts") or [])
    if not selected:  # nothing is pre-selected; an empty list never means "all"
        return jsonify(error="Select at least one account."), 400
    accounts = [a for a in load_accounts() if a.isc_id in selected]
    demo = bool(body.get("demo"))
    tech_only = body.get("tech_only", True)
    all_quarters = bool(body.get("all_quarters"))
    show_opps = bool(body.get("show_opps", True))
    show_deploy = bool(body.get("show_deploy", True))
    show_gainsight = bool(body.get("show_gainsight", True))
    deploy_all_dates = bool(body.get("deploy_all_dates"))
    stale_days = stale_cutoff(body.get("stale_days"))
    if not accounts:
        return jsonify(error="Select at least one account."), 400
    if not _lock.acquire(blocking=False):
        return jsonify(error="A report is already running."), 409

    q: queue.Queue = queue.Queue()

    def work():
        try:
            xlsx, html = run_report(accounts, year, _isc, _gs, lambda p, m: q.put(("progress", p, m)),
                                    demo=demo, tech_only=tech_only, all_quarters=all_quarters,
                                    show_opps=show_opps, show_deploy=show_deploy,
                                    show_gainsight=show_gainsight, deploy_all_dates=deploy_all_dates,
                                    stale_days=stale_days)
            q.put(("done", 100, json.dumps({"xlsx": xlsx.name, "html": html.name})))
        except Exception as e:  # report any failure to the user
            q.put(("error", 0, str(e)))
        finally:
            _lock.release()

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            kind, pct, msg = q.get()
            yield f"data: {json.dumps({'type': kind, 'percent': pct, 'message': msg})}\n\n"
            if kind in ("done", "error"):
                break

    return Response(stream(), mimetype="text/event-stream")


@app.get("/api/reports")
def api_reports():
    REPORTS_DIR.mkdir(exist_ok=True)
    files = sorted([*REPORTS_DIR.glob("*.xlsx"), *REPORTS_DIR.glob("*.html")],
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify(reports=[{"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
                            for f in files[:100]])


@app.get("/api/download/<path:name>")
def api_download(name: str):
    return send_from_directory(REPORTS_DIR, name, as_attachment=True)


@app.get("/deployments")
def view_deployments():
    """Deployments view: the latest report's deployments, or sample data with ?demo=1."""
    if request.args.get("demo") == "1":
        snap = deployments.snapshot_from_demo(load_accounts()[:12], current_next_windows())
    else:
        snap = deployments.read_snapshot()
    return Response(deployments.render(snap), mimetype="text/html")


@app.get("/reports/<path:name>")
def view_report(name: str):
    """Open an HTML report in the browser (not a download)."""
    return send_from_directory(REPORTS_DIR, name)


if __name__ == "__main__":
    REPORTS_DIR.mkdir(exist_ok=True)
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)


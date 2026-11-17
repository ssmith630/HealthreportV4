"""Drive the user's real Chrome/Edge over the Chrome DevTools Protocol (CDP).

No Playwright, Selenium or chromedriver. The only dependency is the small
``websocket-client`` package. The browser uses a dedicated profile folder so the
user signs in once and stays signed in (~8 h session lifetime).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import websocket  # websocket-client

HERE = Path(__file__).parent
PROFILE_DIR = Path(os.environ.get("BROWSER_PROFILE_DIR", HERE / ".browser_profile"))
DEBUG_PORT = int(os.environ.get("BROWSER_DEBUG_PORT", "9223"))


class BrowserError(RuntimeError):
    pass


def find_browser() -> str:
    """Return the path of an installed Chrome or Edge."""
    override = os.environ.get("BROWSER_PATH")
    if override and Path(override).exists():
        return override
    candidates: list[str] = []
    if sys.platform.startswith("win"):
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for r in roots:
            candidates += [
                rf"{r}\Google\Chrome\Application\chrome.exe",
                rf"{r}\Microsoft\Edge\Application\msedge.exe",
            ]
    elif sys.platform == "darwin":
        # Apps may sit in /Applications (all users) or ~/Applications (drag-installed by one user).
        for base in ("/Applications", str(Path.home() / "Applications")):
            candidates += [
                f"{base}/Google Chrome.app/Contents/MacOS/Google Chrome",
                f"{base}/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                f"{base}/Chromium.app/Contents/MacOS/Chromium",
                f"{base}/Brave Browser.app/Contents/MacOS/Brave Browser",
            ]
    else:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(n)
            if p:
                candidates.append(p)
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise BrowserError("Google Chrome or Microsoft Edge was not found. Please install Chrome, or set BROWSER_PATH "
                       "in the .env file to the browser's executable.")


def _http_json(path: str, method: str = "GET", timeout: float = 3.0):
    req = urllib.request.Request(f"http://127.0.0.1:{DEBUG_PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body.strip() else None


class Tab:
    """One browser tab, controlled through its CDP websocket."""

    def __init__(self, target: dict):
        self.id = target["id"]
        self.url = target.get("url", "")
        self._ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=30, suppress_origin=True
        )
        self._next_id = 0
        self._lock = threading.Lock()
        self.events: list[dict] = []   # CDP events seen while waiting for command replies

    def pump(self, seconds: float) -> None:
        """Read incoming CDP events for ``seconds`` and keep them in ``self.events``."""
        with self._lock:
            end = time.time() + seconds
            while time.time() < end:
                self._ws.settimeout(max(0.2, end - time.time()))
                try:
                    data = json.loads(self._ws.recv())
                except websocket.WebSocketTimeoutException:
                    return
                if "method" in data:
                    self.events.append(data)
            del self.events[:-3000]

    def send(self, method: str, params: dict | None = None, timeout: float = 30.0,
             session_id: str | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            msg = {"id": msg_id, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id  # address a child target such as the Gainsight iframe
            self._ws.send(json.dumps(msg))
            deadline = time.time() + timeout
            while time.time() < deadline:
                self._ws.settimeout(max(0.5, deadline - time.time()))
                try:
                    raw = self._ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                data = json.loads(raw)
                if "method" in data:
                    self.events.append(data)
                    continue
                if data.get("id") == msg_id:
                    if "error" in data:
                        raise BrowserError(f"{method}: {data['error'].get('message')}")
                    return data.get("result", {})
            raise BrowserError(f"{method} timed out after {timeout:.0f}s")

    def evaluate(self, expression: str, await_promise: bool = True, timeout: float = 60.0):
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "timeout": int(timeout * 1000),
            },
            timeout=timeout + 5,
        )
        if "exceptionDetails" in result:
            text = result["exceptionDetails"].get("exception", {}).get("description") or "script error"
            raise BrowserError(text)
        return result.get("result", {}).get("value")

    def navigate(self, url: str, wait: float = 20.0) -> None:
        self.send("Page.navigate", {"url": url})
        end = time.time() + wait
        time.sleep(0.8)
        while time.time() < end:
            try:
                if self.evaluate("document.readyState", await_promise=False, timeout=5) == "complete":
                    return
            except BrowserError:
                pass
            time.sleep(0.5)

    def current_url(self) -> str:
        try:
            return self.evaluate("location.href", await_promise=False, timeout=5) or ""
        except BrowserError:
            return ""

    def cookies(self) -> list[dict]:
        return self.send("Network.getAllCookies").get("cookies", [])

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


class Browser:
    """Launches (or re-attaches to) the dedicated browser and hands out tabs."""

    def __init__(self):
        self._tabs: dict[str, Tab] = {}
        self._proc: subprocess.Popen | None = None

    # ---- lifecycle ----------------------------------------------------
    def is_running(self) -> bool:
        try:
            _http_json("/json/version", timeout=1.5)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def ensure_running(self, start_url: str = "about:blank") -> None:
        if self.is_running():
            return
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        args = [
            find_browser(),
            f"--remote-debugging-port={DEBUG_PORT}",
            "--remote-allow-origins=*",
            f"--user-data-dir={PROFILE_DIR}",
            "--disable-site-isolation-trials",
            "--no-first-run",
            "--no-default-browser-check",
            start_url,
        ]
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if self.is_running():
                return
            time.sleep(0.5)
        raise BrowserError("The browser did not start. Close other Chrome windows that use the same profile and retry.")

    # ---- tabs ---------------------------------------------------------
    def _targets(self) -> list[dict]:
        try:
            return [t for t in (_http_json("/json") or []) if t.get("type") == "page"]
        except (urllib.error.URLError, OSError):
            return []

    def tab(self, url_contains: str | None = None, open_url: str | None = None) -> Tab | None:
        """Return a tab whose URL contains ``url_contains``; open ``open_url`` if none and given."""
        targets = self._targets()
        match = None
        for t in targets:
            if url_contains is None or url_contains in t.get("url", ""):
                match = t
                break
        if match is None and open_url:
            match = _http_json("/json/new?" + urllib.request.quote(open_url, safe=":/?=&%"), method="PUT")
        if match is None:
            return None
        cached = self._tabs.get(match["id"])
        if cached:
            try:
                cached.send("Runtime.evaluate", {"expression": "1"}, timeout=3)
                cached.url = match.get("url", "")
                return cached
            except Exception:
                cached.close()
                self._tabs.pop(match["id"], None)
        tab = Tab(match)
        self._tabs[match["id"]] = tab
        return tab

    def any_tab(self) -> Tab:
        tab = self.tab() or self.tab(open_url="about:blank")
        if tab is None:
            raise BrowserError("No browser tab is available.")
        return tab


_browser: Browser | None = None


def get_browser() -> Browser:
    global _browser
    if _browser is None:
        _browser = Browser()
    return _browser

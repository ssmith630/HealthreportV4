"""Start the Salesforce Health Report V3 on Windows, macOS or Linux.

Run it with:   py -3 launch.py        (Windows)
               python3 launch.py      (macOS, Linux)

On the first run it creates a private Python environment in .venv and installs requirements.txt.
Later runs start the app straight away. Standard library only, so any Python 3 can run this file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
IS_WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
MIN_VERSION = (3, 10)

# Commands tried, in order, when the Python running this file is older than 3.10.
# macOS ships Python 3.9, which is too old.
CANDIDATES = (
    [["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"], ["py", "-3"], ["python"]]
    if IS_WINDOWS else
    [["python3.13"], ["python3.12"], ["python3.11"], ["python3.10"],
     ["/opt/homebrew/bin/python3"], ["/usr/local/bin/python3"], ["python3"], ["python"]]
)


def pause_and_exit(code: int) -> None:
    """Keep the window open after a failure so the message can be read."""
    if sys.stdin and sys.stdin.isatty():
        try:
            input("Press Enter to close.")
        except EOFError:
            pass
    sys.exit(code)


def is_new_enough(cmd: list[str]) -> bool:
    if not shutil.which(cmd[0]):
        return False
    probe = "import sys; sys.exit(0 if sys.version_info >= %r else 1)" % (MIN_VERSION,)
    try:
        return subprocess.run(cmd + ["-c", probe], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_python() -> list[str]:
    """The command for a Python 3.10 or newer: this one if it qualifies, otherwise the first that does."""
    if sys.version_info >= MIN_VERSION:
        return [sys.executable]
    for cmd in CANDIDATES:
        if is_new_enough(cmd):
            return cmd
    print("Python 3.10 or newer was not found.")
    if IS_WINDOWS:
        print("  1. Install Python from https://www.python.org/downloads/")
        print('  2. On the first installer screen, tick "Add python.exe to PATH".')
    else:
        print("  Option 1: install it with Homebrew:  brew install python@3.12")
        print("  Option 2: download it from https://www.python.org/downloads/macos/")
        print("            then run 'Install Certificates.command' from /Applications/Python 3.x/")
    print("Then run this file again.")
    pause_and_exit(1)
    raise SystemExit(1)


def remove_foreign_venv() -> None:
    """A .venv copied from another operating system cannot run here, so remove it."""
    # Do not test for Lib/: a case-insensitive Mac filesystem would match lib/.
    if IS_WINDOWS and (VENV / "bin").is_dir() and not (VENV / "Scripts").is_dir():
        print("Removing a .venv that was created on a Mac...")
        shutil.rmtree(VENV, ignore_errors=True)
    elif not IS_WINDOWS and (VENV / "Scripts").is_dir():
        print("Removing a .venv that was created on Windows...")
        shutil.rmtree(VENV, ignore_errors=True)


def clear_mac_quarantine() -> None:
    """Downloaded or unzipped files are quarantined by Gatekeeper. Failure here is harmless."""
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(HERE)], capture_output=True)


def create_venv() -> None:
    print("First run: setting up (about a minute)...")
    shutil.rmtree(VENV, ignore_errors=True)
    py = find_python()
    try:
        subprocess.run(py + ["-m", "venv", str(VENV)], check=True)
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "--quiet", "-r", str(HERE / "requirements.txt")],
                       check=True)
    except (OSError, subprocess.CalledProcessError):
        print("Setup failed. Check your internet connection and that Python 3.10+ is installed.")
        if not IS_WINDOWS:
            print("If you used python.org's installer, run 'Install Certificates.command' once.")
        shutil.rmtree(VENV, ignore_errors=True)
        pause_and_exit(1)


def main() -> None:
    os.chdir(HERE)
    clear_mac_quarantine()
    remove_foreign_venv()
    if not VENV_PY.exists():
        create_venv()
    print("Starting... your browser will open. Press Ctrl+C to stop.")
    try:
        code = subprocess.call([str(VENV_PY), "app.py"])
    except KeyboardInterrupt:
        code = 0
    if code:
        print(f"The app stopped with exit code {code}.")
        pause_and_exit(code)


if __name__ == "__main__":
    main()

"""Runtime locations and subprocess environment."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOVIES = Path(
    os.environ.get("CINEMA_MOVIES_DIR", Path.home() / "Movies/Youtube/Movies")
)
DATA = Path(os.environ.get("CINEMA_DATA_DIR", Path.home() / "Movies/Youtube/Cinema"))
DB = DATA / "catalogue.sqlite3"
DOWNLOAD_TIMEOUT = 1800
PORT = 8765
OAUTH_REDIRECT = f"http://127.0.0.1:{PORT}"
DOWNLOAD_ROOT = DATA / "À regarder"


def configure_tools():
    locations = (
        "/Library/Frameworks/Python.framework/Versions/3.13/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    )
    os.environ["PATH"] = os.pathsep.join((*locations, os.environ.get("PATH", "")))

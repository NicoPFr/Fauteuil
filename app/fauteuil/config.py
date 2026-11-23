"""Runtime locations and subprocess environment."""

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = APP_ROOT / "web"
DOWNLOADER_SCRIPT = APP_ROOT / "downloader" / "ytmp4.sh"
if not DOWNLOADER_SCRIPT.is_file():
    DOWNLOADER_SCRIPT = APP_ROOT.parent / "ytmp4.sh"
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
    if runtime := os.environ.get("FAUTEUIL_RUNTIME"):
        os.environ["PATH"] = os.pathsep.join((str(Path(runtime) / "bin"), "/usr/bin:/bin:/usr/sbin:/sbin"))
        return
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

"""Google authorization and YouTube Data API client."""

import json
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse as up
import urllib.request
from functools import lru_cache

from fauteuil.storage.database import connect, db

oauth_lock = threading.Lock()
google_refresh_lock = threading.Lock()
oauth_attempt = {}
OAUTH_TTL = 240
GOOGLE_RECONNECT_MESSAGE = "Ton autorisation Google a expiré ou a été retirée. Reconnecte ton compte pour revoir tes abonnements."


def keychain(action, value=None, name="google-refresh"):
    service = "io.github.nicopfr.fauteuil." + name
    cmd = (
        ["security", "find-generic-password", "-s", service, "-w"]
        if action == "get"
        else (
            ["security", "delete-generic-password", "-s", service]
            if action == "delete"
            else [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                service,
                "-a",
                "cinema",
                "-w",
                value or "",
            ]
        )
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if action == "get":
            return result.stdout.strip() if result.returncode == 0 else None
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=1)
def google_ssl_context():
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        try:
            import certifi
        except ImportError:
            return context
        context.load_verify_locations(cafile=certifi.where())
    return context


def google_access_token():
    with google_refresh_lock:
        with oauth_lock:
            rows = db(
                "select key,value from settings where key in ('google_access','google_expiry','google_client_id','google_reconnect')"
            )
            settings = {row["key"]: row["value"] for row in rows}
            if settings.get("google_reconnect"):
                return None
            if (
                settings.get("google_access")
                and float(settings.get("google_expiry", 0)) > time.time() + 60
            ):
                return settings["google_access"]
            client_id = settings.get("google_client_id")
            if not client_id:
                return None
            refresh = keychain("get")
            secret = keychain("get", name="google-client-secret")
        if not refresh:
            return None
        form = {
            "client_id": client_id,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }
        if secret:
            form["client_secret"] = secret
        revoked = False
        try:
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=up.urlencode(form).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(
                req, timeout=20, context=google_ssl_context()
            ) as response:
                token = json.load(response)
            access = token["access_token"]
            expiry = time.time() + int(token.get("expires_in", 3600))
        except urllib.error.HTTPError as error:
            with error:
                try:
                    revoked = (
                        error.code == 400
                        and json.load(error).get("error") == "invalid_grant"
                    )
                except (AttributeError, OSError, ValueError):
                    pass
            if not revoked:
                return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        with oauth_lock:
            # An authorization or disconnection may have completed during the request.
            if refresh != keychain("get"):
                return None
            with connect() as c:
                if revoked:
                    c.execute(
                        "delete from settings where key in ('google_access','google_expiry')"
                    )
                    c.execute(
                        "insert or replace into settings values('google_reconnect','1')"
                    )
                    keychain("delete")
                    return None
                c.executemany(
                    "insert or replace into settings values(?,?)",
                    [
                        ("google_access", access),
                        ("google_expiry", str(expiry)),
                    ],
                )
            return access


def gapi(path, params, token):
    url = "https://www.googleapis.com/youtube/v3/" + path + "?" + up.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(
        req, timeout=25, context=google_ssl_context()
    ) as response:
        return json.load(response)

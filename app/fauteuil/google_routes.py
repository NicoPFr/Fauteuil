"""Browser authorization flow and Google HTTP endpoints."""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.parse as up
import urllib.request
import webbrowser

from . import config, google
from .catalogue import add
from .database import connect, db
from .google import GOOGLE_RECONNECT_MESSAGE, OAUTH_TTL, oauth_attempt, oauth_lock
from .playlists import subscription_suggestions
from .youtube_links import iso_duration


class GoogleRoutes:
    def google_callback(self, query):
        state = query.get("state", [""])[0]
        with oauth_lock:
            attempt = oauth_attempt.get(state)
            if (
                not attempt
                or attempt["status"] != "pending"
                or time.monotonic() > attempt["expires"]
            ):
                return self.send_error(
                    400, "Cette demande de connexion Google est invalide ou expirée."
                )
            attempt["status"] = "exchanging"

        code = query.get("code", [""])[0]
        token = None
        message = "Google a refusé l’autorisation. Tu peux réessayer depuis Fauteuil."
        if code and not query.get("error"):
            form = {
                "code": code,
                "client_id": attempt["client_id"],
                "client_secret": attempt["secret"],
                "code_verifier": attempt["verifier"],
                "redirect_uri": config.OAUTH_REDIRECT,
                "grant_type": "authorization_code",
            }
            try:
                request = urllib.request.Request(
                    "https://oauth2.googleapis.com/token",
                    data=up.urlencode(form).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with urllib.request.urlopen(
                    request, timeout=25, context=google.google_ssl_context()
                ) as response:
                    token = json.load(response)
                if not token.get("access_token") or not token.get("refresh_token"):
                    token = None
                    message = "Google n’a pas fourni les jetons nécessaires. Relance la connexion."
            except (OSError, ValueError):
                message = "Fauteuil n’a pas pu terminer la connexion avec Google. Relance la connexion."

        with oauth_lock:
            if oauth_attempt.get(state) is not attempt:
                return self.send_error(
                    400, "Cette demande de connexion a été remplacée."
                )
            if token:
                if google.keychain("set", token["refresh_token"]):
                    with connect() as connection:
                        connection.execute(
                            "delete from settings where key='google_reconnect'"
                        )
                        connection.executemany(
                            "insert or replace into settings values(?,?)",
                            [
                                ("google_access", token["access_token"]),
                                (
                                    "google_expiry",
                                    str(time.time() + token.get("expires_in", 3600)),
                                ),
                            ],
                        )
                    attempt["status"] = "connected"
                    message = "Compte Google connecté. Tu peux revenir dans Fauteuil."
                else:
                    message = (
                        "Le trousseau macOS n’a pas pu enregistrer la connexion Google."
                    )
            if attempt["status"] != "connected":
                attempt["status"] = "error"
            attempt["message"] = message
            attempt.pop("secret", None)
            attempt.pop("verifier", None)
        body = (
            '<!doctype html><meta charset="utf-8"><title>Fauteuil</title><p>'
            + message
            + "</p>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def google_trending(self, url):
        token = google.google_access_token()
        if not token:
            return self.send_json({"items": []})
        try:
            data = google.gapi(
                "videos",
                {
                    "part": "snippet,contentDetails",
                    "chart": "mostPopular",
                    "regionCode": "FR",
                    "maxResults": 30,
                },
                token,
            )
            items = []
            for v in data.get("items", []):
                sn = v.get("snippet", {})
                title = sn.get("title", "")
                duration = iso_duration(v.get("contentDetails", {}).get("duration"))
                if (
                    v.get("id")
                    and duration > 180
                    and not re.search(r"#shorts|\bshorts\b", title, re.I)
                ):
                    items.append(
                        {
                            "id": v["id"],
                            "title": title,
                            "thumbnail": sn.get("thumbnails", {})
                            .get("high", sn.get("thumbnails", {}).get("medium", {}))
                            .get("url", ""),
                            "duration": duration,
                            "channel": sn.get("channelTitle", "YouTube"),
                            "category": sn.get("categoryId"),
                        }
                    )
            add(items)
            return self.send_json({"items": items})
        except Exception as e:
            return self.send_json({"items": [], "error": str(e)})

    def google_suggestions(self, url):
        token = google.google_access_token()
        if not token:
            if db("select 1 from settings where key='google_reconnect'"):
                return self.send_json(
                    {
                        "connected": False,
                        "items": [],
                        "notice": GOOGLE_RECONNECT_MESSAGE,
                    }
                )
            if google.keychain("get"):
                return self.send_json(
                    {
                        "connected": True,
                        "items": [],
                        "error": "Les abonnements YouTube sont momentanément indisponibles.",
                    }
                )
            return self.send_json({"connected": False, "items": []})
        try:
            return self.send_json(subscription_suggestions(token))
        except (OSError, ValueError):
            return self.send_json(
                {
                    "connected": True,
                    "items": [],
                    "error": "Les abonnements YouTube sont momentanément indisponibles.",
                }
            )

    def google_status(self, url):
        if urllib.parse.parse_qs(url.query).get("check") == ["1"]:
            google.google_access_token()
        client = db("select value from settings where key='google_client_id'")
        with oauth_lock:
            attempt = next(iter(oauth_attempt.values()), {})
            if (
                attempt.get("status") == "pending"
                and time.monotonic() > attempt["expires"]
            ):
                attempt.update(
                    status="error",
                    message="La demande de connexion Google a expiré. Réessaie.",
                )
                attempt.pop("secret", None)
                attempt.pop("verifier", None)
            status = attempt.get("status", "idle")
            message = attempt.get("message", "")
            connected = (
                bool(google.keychain("get"))
                if status not in ("pending", "exchanging")
                else False
            )
            if status in ("idle", "connected") and db(
                "select 1 from settings where key='google_reconnect'"
            ):
                status, message, connected = (
                    "reauthorize",
                    GOOGLE_RECONNECT_MESSAGE,
                    False,
                )
        configured = bool(
            client
            and client[0]["value"]
            and google.keychain("get", name="google-client-secret")
        )
        return self.send_json(
            {
                "client_id": client[0]["value"] if client else "",
                "configured": configured,
                "connected": connected,
                "status": status,
                "message": message,
            }
        )

    def google_setup(self, d):
        pages = {
            "api": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
            "branding": "https://console.cloud.google.com/auth/branding",
            "audience": "https://console.cloud.google.com/auth/audience",
            "client": "https://console.cloud.google.com/auth/clients",
        }
        url = pages.get(str(d.get("step", "")))
        if url:
            try:
                if webbrowser.open(url):
                    return self.send_json({"ok": True})
            except (OSError, webbrowser.Error):
                pass
        return self.send_json(
            {
                "ok": False,
                "error": "Impossible d’ouvrir Google Cloud dans le navigateur.",
            }
        )

    def google_settings(self, d):
        client_id = str(d.get("client_id") or "").strip()
        secret = str(d.get("client_secret") or "").strip()
        if not re.fullmatch(
            r"[A-Za-z0-9_-]{1,200}\.apps\.googleusercontent\.com", client_id
        ) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", secret):
            return self.send_json(
                {
                    "ok": False,
                    "error": "Ce fichier ne contient pas les identifiants attendus pour une application de bureau.",
                }
            )
        with oauth_lock:
            previous = db("select value from settings where key='google_client_id'")
            changed = not previous or previous[0]["value"] != client_id
            if secret and not google.keychain("set", secret, "google-client-secret"):
                return self.send_json(
                    {
                        "ok": False,
                        "error": "Le trousseau macOS n’a pas pu enregistrer le secret client.",
                    }
                )
            if changed or secret:
                oauth_attempt.clear()
            if changed:
                google.keychain("delete")
                if not secret:
                    google.keychain("delete", name="google-client-secret")
                db(
                    "delete from settings where key in ('google_access','google_expiry','google_reconnect')",
                    write=True,
                )
            db(
                "insert or replace into settings values('google_client_id',?)",
                (client_id,),
                write=True,
            )
        return self.send_json({"ok": True})

    def google_disconnect(self, d):
        with oauth_lock:
            oauth_attempt.clear()
            google.keychain("delete")
            db(
                "delete from settings where key in ('google_access','google_expiry','google_reconnect')",
                write=True,
            )
        return self.send_json({"ok": True})

    def google_start(self, d):
        with oauth_lock:
            client = db("select value from settings where key='google_client_id'")
            secret = google.keychain("get", name="google-client-secret")
            if not client or not client[0]["value"] or not secret:
                return self.send_json(
                    {
                        "error": "La configuration OAuth de Fauteuil est requise une seule fois. Utilise le lien de configuration dans cette fenêtre."
                    }
                )
            client_id = client[0]["value"]
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(48)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            attempt = {
                "status": "pending",
                "expires": time.monotonic() + OAUTH_TTL,
                "client_id": client_id,
                "secret": secret,
                "verifier": verifier,
            }
            oauth_attempt.clear()
            oauth_attempt[state] = attempt
        params = {
            "client_id": client_id,
            "redirect_uri": config.OAUTH_REDIRECT,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.readonly",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + up.urlencode(params)
        browser = "safari" if d.get("browser") == "safari" else None

        def open_google():
            try:
                if webbrowser.get(browser).open(url):
                    return
            except (OSError, webbrowser.Error):
                pass
            with oauth_lock:
                if (
                    oauth_attempt.get(state) is attempt
                    and attempt["status"] == "pending"
                ):
                    attempt.update(
                        status="error",
                        message="Impossible d’ouvrir le navigateur pour la connexion Google.",
                    )
                    attempt.pop("secret", None)
                    attempt.pop("verifier", None)

        threading.Thread(target=open_google, daemon=True).start()
        return self.send_json({"ok": True})

    def legacy_settings(self, d):
        client = d.get("google_client_id", "").strip()
        with connect() as c:
            c.execute(
                "insert or replace into settings values(?,?)",
                ("google_client_id", client),
            )
        return self.send_json({"ok": True})

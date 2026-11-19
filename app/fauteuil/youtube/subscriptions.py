"""Suggestions from a user's YouTube subscriptions."""

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fauteuil.catalogue.metadata import add
from fauteuil.youtube import google
from fauteuil.youtube.links import iso_duration

SUBSCRIPTION_CHANNEL_LIMIT = 100
SUBSCRIPTION_TTL = 20 * 60
subscription_lock = threading.Lock()
subscription_cache = None


def subscription_playlists(token):
    channels = []
    page = None
    while len(channels) < SUBSCRIPTION_CHANNEL_LIMIT:
        params = {
            "part": "snippet",
            "mine": "true",
            "maxResults": 50,
            "order": "relevance",
        }
        if page:
            params["pageToken"] = page
        response = google.gapi("subscriptions", params, token)
        for item in response.get("items", []):
            channel_id = item.get("snippet", {}).get("resourceId", {}).get("channelId")
            if channel_id and channel_id not in channels:
                channels.append(channel_id)
        next_page = response.get("nextPageToken")
        if not next_page or next_page == page:
            break
        page = next_page

    playlists = {}
    for offset in range(0, min(len(channels), SUBSCRIPTION_CHANNEL_LIMIT), 50):
        ids = channels[offset : min(offset + 50, SUBSCRIPTION_CHANNEL_LIMIT)]
        response = google.gapi(
            "channels", {"part": "contentDetails,snippet", "id": ",".join(ids)}, token
        )
        for channel in response.get("items", []):
            playlist = (
                channel.get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )
            if playlist:
                playlists[playlist] = channel.get("snippet", {}).get(
                    "title", "Abonnement"
                )
    return playlists


def subscription_uploads(playlist, channel, token):
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist,
        "maxResults": 10,
    }
    items = []
    for video in google.gapi("playlistItems", params, token).get("items", []):
        snippet = video.get("snippet", {})
        content = video.get("contentDetails", {})
        video_id = content.get("videoId") or snippet.get("resourceId", {}).get(
            "videoId"
        )
        title = snippet.get("title", "")
        if (
            not video_id
            or not title
            or title in ("Private video", "Deleted video")
            or re.search(r"#shorts|\bshorts\b", title, re.I)
        ):
            continue
        thumbnails = snippet.get("thumbnails", {})
        cover = thumbnails.get("high", thumbnails.get("medium", {}))
        items.append(
            {
                "id": video_id,
                "title": title,
                "thumbnail": cover.get("url", ""),
                "channel": channel,
                "published_at": content.get("videoPublishedAt")
                or snippet.get("publishedAt", ""),
            }
        )
    return items


def subscription_suggestions(token):
    global subscription_cache
    fingerprint = hashlib.sha256(token.encode()).digest()
    with subscription_lock:
        if (
            subscription_cache
            and subscription_cache[0] == fingerprint
            and subscription_cache[1] > time.monotonic()
        ):
            return subscription_cache[2]
        playlists = subscription_playlists(token)
        uploads = []
        failures = []
        with ThreadPoolExecutor(max_workers=10) as workers:
            futures = [
                workers.submit(subscription_uploads, playlist, channel, token)
                for playlist, channel in playlists.items()
            ]
            for future in as_completed(futures):
                try:
                    uploads.extend(future.result())
                except (OSError, ValueError) as error:
                    failures.append(error)
        if playlists and len(failures) == len(playlists):
            raise failures[0]
        uploads.sort(
            key=lambda video: (video["published_at"], video["id"]), reverse=True
        )
        unique = {}
        for video in uploads:
            unique.setdefault(video["id"], video)
        ids = list(unique)
        suggestions = []
        for offset in range(0, len(ids), 50):
            batch = ids[offset : offset + 50]
            response = google.gapi(
                "videos", {"part": "contentDetails", "id": ",".join(batch)}, token
            )
            durations = {
                row["id"]: iso_duration(row.get("contentDetails", {}).get("duration"))
                for row in response.get("items", [])
            }
            suggestions.extend(
                dict(unique[video_id], duration=durations[video_id])
                for video_id in batch
                if durations.get(video_id, 0) > 180
            )
            if len(suggestions) >= 48:
                break
        result = {"connected": True, "items": suggestions[:48]}
        if failures:
            result["notice"] = "Certaines chaînes n’ont pas pu être actualisées."
        if suggestions:
            add(suggestions[:48])
        subscription_cache = (
            fingerprint,
            time.monotonic() + (120 if failures else SUBSCRIPTION_TTL),
            result,
        )
        return result

# Contributing

Small, focused contributions are welcome. Fauteuil is intentionally a lightweight
personal media app, not a media server or a replacement for the YouTube website.

1. Fork the repository and create a branch.
2. Install Python 3.11+, FFmpeg, yt-dlp, Node.js and Xcode Command Line Tools.
3. Run `make test`; run `make build` to build the native app.
4. Describe the user-facing change and how you verified it in your pull request.

Use disposable fixtures for playback and deletion tests. Never run tests against
someone's real library. Do not commit OAuth files, tokens, databases or downloaded
videos. UI strings currently live in the HTML/JavaScript files and are French.

## Local architecture

- The Swift app launches the Python gateway in `app/fauteuil/gateway.py`. WebKit
  and the Google OAuth callback reach it at `127.0.0.1:8765`. The gateway serves
  the interface, catalogue, local media, profiles and Google integration.
- The gateway starts a separate download service in
  `app/fauteuil/download_worker.py`. It owns downloads, progressive playback
  state, offline queues and deletion of completed temporary videos. The gateway
  calls it through a Unix socket in a private directory. The worker shuts down
  when the gateway closes the control pipe.
- The two processes share the existing SQLite catalogue. Progress and pending
  cleanup are written in one transaction, so deletion can be retried after an
  interruption. The gateway streams media files directly to WebKit. A worker
  failure leaves the catalogue and local playback accessible, and restarting
  the app starts a fresh worker.
- The web interface separates catalogue, player, playlists and video details
  into scripts under `app/static`. The native window still ships as a single
  macOS bundle with both services.

`make install` replaces the bundle in `~/Applications` after building and
verifying it. `make build` and `make universal` write to `build` for development
and packaging.

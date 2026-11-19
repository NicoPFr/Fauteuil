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

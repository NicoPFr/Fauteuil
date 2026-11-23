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

The [architecture guide](docs/ARCHITECTURE.md) maps the repository, traces
playback through both processes, and explains the storage and cleanup rules.

When changing the worker protocol, update the client, worker handler and
transport test together. When changing playback or removal, cover the
affected combination of personal media, temporary downloads, retained copies
and progressive streams. Document ownership, failure handling or an ordering
constraint in code where it would be hard to infer from the implementation.

`make install` replaces the bundle in `~/Applications` after building and
verifying it. `make build` and `make universal` write to `build` for development
and packaging. Run `make test` and build the bundle when changing its entry
points or resource layout.

The public website is served from the `gh-pages` branch. When adding or moving a
guide, update the home-page links, the canonical and language links on both
translations, and the [sitemap](https://nicopfr.github.io/Fauteuil/sitemap.xml).

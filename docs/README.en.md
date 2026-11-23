# Fauteuil

A personal video library for macOS. Browse local files and YouTube playlists, play videos inside the app, resume where you left off and keep selected titles available offline.

[Download Fauteuil](https://github.com/NicoPFr/Fauteuil/releases/latest) · [French README](../README.md)

![Fauteuil home with a fictional media library](assets/home.jpg)

- Index local video files in place and group episodes into seasons.
- Browse YouTube subscriptions, import ordered playlists and continue to the next episode.
- Start playback while supported videos download. Fauteuil falls back to a temporary local copy when progressive playback fails.
- Keep a selection offline or let Fauteuil clean up completed temporary downloads.

The 1.4 universal build targets macOS 15+, Apple Silicon and Intel. On first launch, choose **Installer dans Applications** to install into `~/Applications`. If Fauteuil is already installed, you can open the existing copy or confirm an in-place update that keeps your videos, settings, and tools. Quit any running copy before starting the update.

If prompted, **Installer les outils** downloads and verifies the private runtime from GitHub. Setup requires an internet connection and around 145 MB of downloads. The app does not require Homebrew or Terminal. GitHub release builds are ad hoc signed and are not notarized. For macOS 13 or 14, use [1.3.1](https://github.com/NicoPFr/Fauteuil/releases/tag/v1.3.1).

Local playback needs no account. YouTube subscriptions and personal playlists use a user-provided OAuth client. See the [Google setup guide in French](CONFIGURER_GOOGLE.md). Download only content you have permission to save.

MIT licensed. Made with Swift, WebKit, Python and SQLite.

# Fauteuil — Local video library and offline player for macOS

Fauteuil brings local movies, TV episodes and YouTube playlists into a personal media library on your Mac. Watch in the built-in player, resume where you left off and keep selected videos for offline playback.

[**Download Fauteuil for macOS**](https://github.com/NicoPFr/Fauteuil/releases/latest) · [Fauteuil website](https://nicopfr.github.io/Fauteuil/en/) · [Documentation en français](../README.md)

![Fauteuil’s macOS video library with fictional movies and series](assets/home.jpg)

## What you can do

- Add local video files in place and group episodes into seasons.
- Import a public YouTube playlist by URL. Connect your own Google OAuth client to browse subscriptions and personal playlists.
- Start watching supported videos during download. Fauteuil falls back to a temporary local copy if progressive playback fails.
- Resume playback, continue to the next episode and choose which titles to keep offline.

[Organize local movies on Mac](https://nicopfr.github.io/Fauteuil/en/guides/organize-local-videos-on-mac/) · [Watch YouTube playlists offline on Mac](https://nicopfr.github.io/Fauteuil/en/guides/youtube-playlists-offline-on-mac/)

## Install on a Mac

[Step-by-step installation and updates](https://nicopfr.github.io/Fauteuil/en/guides/install-on-mac/)

The universal build supports **macOS 15 or later**, on Apple Silicon and Intel. Unzip the download, open **Fauteuil.app**, then choose **Installer dans Applications**. If a copy is already installed, quit it first and confirm the in-place update when prompted. Setup continues in the open window after the copy finishes. For later launches, open Fauteuil from Applications.

If you see **Installer les outils**, Fauteuil downloads and verifies its runtime. Initial setup requires an internet connection and around **145 MB** of downloads. You can start without Homebrew or Terminal. The application interface is in French. For macOS 13 or 14, use [Fauteuil 1.3.1](https://github.com/NicoPFr/Fauteuil/releases/tag/v1.3.1), which requires manual dependency installation.

Releases are ad hoc signed. macOS may ask you to confirm opening the app because the releases have not been notarized by Apple. [Apple support](https://support.apple.com/en-us/102445)

Local playback needs no account. YouTube decides which videos and formats are available. Save only content you have permission to download. [Google configuration guide in French](CONFIGURER_GOOGLE.md)

[Connect a YouTube account](https://nicopfr.github.io/Fauteuil/en/guides/connect-youtube-to-fauteuil/) · [Find downloads and settings on Mac](https://nicopfr.github.io/Fauteuil/en/guides/video-storage-and-privacy/) · [Compare macOS releases](https://nicopfr.github.io/Fauteuil/en/releases/)

[Architecture](ARCHITECTURE.md) · [Contributing](../CONTRIBUTING.md) · [MIT license](../LICENSE)

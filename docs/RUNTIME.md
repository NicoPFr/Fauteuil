# Outils de Fauteuil

Au premier lancement, Fauteuil installe une copie propre à l’architecture du Mac dans le dossier utilisateur `~/Library/Application Support/Fauteuil`. Les outils occupent jusqu’à environ 300 Mo après installation, en plus de la vidéothèque. Chaque archive ou exécutable téléchargé est vérifié avec l’empreinte SHA-256 épinglée dans le script d’installation. Un échec permet de recommencer au lancement suivant.

- [CPython 3.13, distributions Astral](https://github.com/astral-sh/python-build-standalone/releases/tag/20261110)
- [Deno 2.9.8](https://github.com/denoland/deno/releases/tag/v2.9.8)
- [FFmpeg et FFprobe builds vanloctech du 11 juin 2026](https://github.com/vanloctech/ffmpeg-macos/releases/tag/ffmpeg-2026.06.11)
- [yt-dlp 2026.10.26, exécutable universel](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26)

Le ZIP Fauteuil contient le script d’installation. Les exécutables des projets ci-dessus sont téléchargés directement depuis leurs releases GitHub.

#!/bin/bash
# Install pinned upstream tools in Fauteuil's private Application Support directory.
set -euo pipefail
umask 077
destination="${1:?Missing runtime directory}"
parent="$(dirname "$destination")"
mkdir -p "$parent"
stage="$(mktemp -d "$parent/.runtime.XXXXXX")"
cleanup() {
  if [[ -d "$stage/previous" && ! -e "$destination" ]]; then
    /bin/mv "$stage/previous" "$destination" || return
  fi
  /bin/rm -rf "$stage"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
case "${2:-$(/usr/bin/uname -m)}" in
  arm64)
    python_arch=aarch64
    python_hash=7a2384641584ab9984221fcb0fc9265df465922af71be71410529e2a9c3f0540
    deno_hash=9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e
    ffmpeg_arch=arm64
    ffmpeg_hash=26f5133f88d5ab1254cc03499acef42dae2dbc574dd26e0dade8c968420127e9
    ;;
  x86_64)
    python_arch=x86_64
    python_hash=d27d59a722db9f69926eb6d9564071247221f45a04da920ea7f3001c97e32f9b
    deno_hash=4b2f5edd2639b7470f034238b321d7002d8dfb4726d8558f52c17afac370eb2c
    ffmpeg_arch=x64
    ffmpeg_hash=8317f17790749fd3502384201fd4d9d2a998ff9b8452d3b8b54b3b9af6a16e39
    ;;
  *) echo 'Architecture macOS non prise en charge.' >&2; exit 1 ;;
esac
fetch() {
  local label="$1" url="$2" checksum="$3" output="$4" actual
  echo "Téléchargement et vérification de $label…"
  /usr/bin/curl -q --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 240 --retry 2 --max-filesize 80000000 --output "$output" "$url"
  actual="$(/usr/bin/shasum -a 256 "$output")"
  if [[ "${actual%% *}" != "$checksum" ]]; then
    echo "L’empreinte de $label est incorrecte." >&2
    exit 1
  fi
}
mkdir -p "$stage/tools/bin" "$stage/video"
fetch Python "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.13.18%2B20261110-${python_arch}-apple-darwin-install_only_stripped.tar.gz" "$python_hash" "$stage/python.tar.gz"
fetch Deno "https://github.com/denoland/deno/releases/download/v2.9.8/deno-${python_arch}-apple-darwin.zip" "$deno_hash" "$stage/deno.zip"
fetch FFmpeg "https://github.com/vanloctech/ffmpeg-macos/releases/download/ffmpeg-2026.06.11/ffmpeg-macos-${ffmpeg_arch}.tar.gz" "$ffmpeg_hash" "$stage/video.tar.gz"
fetch yt-dlp 'https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp_macos' '0f922c15ccc0b83eefa1c8bc9104e434318c4bf16e408e1ee93b7745a6efe0aa' "$stage/tools/bin/yt-dlp"
echo 'Installation des outils…'
/usr/bin/tar -xzf "$stage/python.tar.gz" -C "$stage/tools"
/usr/bin/tar -xzf "$stage/video.tar.gz" -C "$stage/video"
/usr/bin/unzip -q "$stage/deno.zip" deno -d "$stage/tools/bin"
/bin/ln -s ../python/bin/python3 "$stage/tools/bin/python3"
for tool in ffmpeg ffprobe; do
  found="$(/usr/bin/find "$stage/video" -type f -name "$tool" -print)"
  [[ -n "$found" && -f "$found" ]]
  /bin/mv "$found" "$stage/tools/bin/$tool"
done
/bin/chmod +x "$stage/tools/bin/"{deno,ffmpeg,ffprobe,yt-dlp}
for tool in python3 deno yt-dlp; do
  "$stage/tools/bin/$tool" --version >/dev/null
done
for tool in ffmpeg ffprobe; do
  "$stage/tools/bin/$tool" -version >/dev/null
done
# Progressive playback reads the remote stream over HTTPS.
if ! "$stage/tools/bin/ffmpeg" -hide_banner -protocols | /usr/bin/grep -x '  https' >/dev/null; then
  echo 'FFmpeg ne prend pas en charge HTTPS.' >&2
  exit 1
fi
if [[ -e "$destination" ]]; then
  /bin/mv "$destination" "$stage/previous"
fi
if ! /bin/mv "$stage/tools" "$destination"; then
  if [[ -d "$stage/previous" ]]; then /bin/mv "$stage/previous" "$destination"; fi
  exit 1
fi
echo 'Outils installés.'

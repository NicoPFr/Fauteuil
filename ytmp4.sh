#!/usr/bin/env bash
set -Eeuo pipefail
export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
if [[ "${1:-}" == --cinema-check-space ]]; then
  shift
  exec python3 - "$@" <<'PYTHON'
import json
import os
from pathlib import Path
import shutil
import sys

def size(value):
    try:
        return max(0, int(float(value)))
    except (ValueError, OverflowError):
        return 0

destination, total, video, audio, combined_id, video_id, audio_id = sys.argv[1:]
estimated_size = max(size(total), size(video) + size(audio))
# ffmpeg can need room for the input streams and the merged output at once.
reserve = size(os.environ.get('YT_CINEMA_RESERVE_BYTES', 512 * 1024**2))
required = max(512 * 1024**2, reserve) + 2 * estimated_size
available = shutil.disk_usage(destination).free
if available < required:
    print(f"CINEMA_DISK_REQUIRED {required} {available}", file=sys.stderr)
    sys.exit(1)
formats = [(video_id, size(video)), (audio_id, size(audio))] if video_id != '-' and audio_id != '-' else [(combined_id, size(total))]
Path(destination).parent.joinpath('.cinema-formats.json').write_text(
    json.dumps({'combined': combined_id, 'formats': formats}), encoding='utf-8')
PYTHON
fi
BASE_DIR="${YT_MOVIES_DIR:-${HOME}/Movies/Youtube}"
OUT_DIR="${BASE_DIR}/Movies"
LOG_DIR="${BASE_DIR}/logs"
mkdir -p "$OUT_DIR" "$LOG_DIR"
usage(){ echo "Usage: $(basename "$0") [--fr-subs] [--playlist] [--no-archive] URL [URL ...]"; }
SUBS=0; PLAYLIST=0; NO_ARCHIVE=0; URLS=()
while (($#)); do
  case "$1" in
    --fr-subs) SUBS=1;; --playlist) PLAYLIST=1;; --no-archive) NO_ARCHIVE=1;; -h|--help) usage; exit 0;;
    --) shift; URLS+=("$@"); break;; -* ) echo "Option inconnue: $1" >&2; usage >&2; exit 2;;
    *) URLS+=("$1");;
  esac
  shift
done
((${#URLS[@]})) || { usage >&2; exit 2; }
for tool in yt-dlp ffmpeg python3; do command -v "$tool" >/dev/null || { echo "Dépendance absente: $tool" >&2; exit 127; }; done
stamp="$(date +'%Y%m%d-%H%M%S')-$$"
LOG_FILE="$LOG_DIR/ytmp4-$stamp.log"; TRC_FILE="$LOG_DIR/ytmp4-$stamp.trc"
ln -sf "$LOG_FILE" "$LOG_DIR/latest.log"; ln -sf "$TRC_FILE" "$LOG_DIR/latest.trc"
exec > >(tee -a "$LOG_FILE") 2> >(tee -a "$TRC_FILE" >&2)
echo "Destination : $OUT_DIR"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PAGE_HOOK="${SCRIPT_DIR}/ytmp4-page.py"
if [[ "${YT_CINEMA_STREAM:-0}" == 1 ]]; then
  ((${#URLS[@]} == 1)) || { echo "La lecture progressive attend une seule vidéo." >&2; exit 2; }
  exec python3 "${SCRIPT_DIR}/ytmp4-stream.py" "${URLS[0]}"
fi
OPTS=(--paths "$OUT_DIR" --newline --concurrent-fragments 8 --format-sort lang --format 'bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[vcodec^=avc1]+bestaudio/best[ext=mp4]/best' --merge-output-format mp4 --embed-metadata --embed-thumbnail --write-thumbnail --convert-thumbnails jpg -o "%(title)s/%(title)s.%(ext)s" --exec "after_move:python3 \"$PAGE_HOOK\" %(filepath)q")
if [[ "${YT_CINEMA_PROGRESS:-0}" == 1 ]]; then
  OPTS+=(--progress --progress-delta 0.5
    --match-filters 'live_status !=? is_live & live_status !=? is_upcoming & live_status !=? post_live'
    --progress-template 'download:CINEMA_MEDIA %(info.format_id)j %(progress._percent)j %(progress.status)s')
  printf -v cinema_script '%q' "$0"
  printf -v cinema_output '%q' "$OUT_DIR"
  OPTS+=(--exec "before_dl:bash $cinema_script --cinema-check-space $cinema_output %(filesize,filesize_approx|0)q %(requested_formats.0.filesize,requested_formats.0.filesize_approx|0)q %(requested_formats.1.filesize,requested_formats.1.filesize_approx|0)q %(format_id|-)q %(requested_formats.0.format_id|-)q %(requested_formats.1.format_id|-)q")
fi
((NO_ARCHIVE)) || OPTS+=(--download-archive "$LOG_DIR/archive.txt")
((PLAYLIST)) || OPTS+=(--no-playlist)
((SUBS)) && OPTS+=(--write-subs --sub-langs fr --sub-format vtt --embed-subs)
status=0
for url in "${URLS[@]}"; do yt-dlp "${OPTS[@]}" -- "$url" || status=1; done
exit "$status"

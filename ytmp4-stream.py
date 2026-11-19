#!/usr/bin/env python3
"""Fauteuil: download once into HLS fragments, then remux the same bytes to MP4."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FORMAT = 'bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[ext=mp4][vcodec^=avc1]'


def write_state(stage, state):
    target = stage / '.cinema-stream.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(target)


def remux_stream(info, stage, output):
    streams = info.get('requested_formats') or [info]
    duration = float(info.get('duration') or 0)
    if duration <= 0 or info.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
        raise ValueError('Le replay de cette vidéo n’est pas encore disponible.')
    estimated = sum(float(s.get('filesize') or s.get('filesize_approx') or 0) for s in streams)
    reserve = int(os.environ.get('YT_CINEMA_RESERVE_BYTES', 5 * 1024**3))
    required = reserve + 2 * estimated
    available = shutil.disk_usage(stage).free
    if available < required:
        raise ValueError(f'CINEMA_DISK_REQUIRED {int(required)} {available}')
    folder = stage / 'stream'
    folder.mkdir(exist_ok=True)
    state = {'path': str(output.resolve()), 'duration': duration, 'seconds': 0, 'complete': False}
    write_state(stage, state)
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y']
    for stream in streams:
        headers = stream.get('http_headers') or info.get('http_headers') or {}
        # Only yt-dlp supplied header values; never forward local app credentials.
        headers = ''.join(f'{key}: {value}\r\n' for key, value in headers.items()
                          if '\n' not in key + str(value) and '\r' not in key + str(value))
        if str(stream['url']).startswith(('http://', 'https://')):
            command += ['-rw_timeout', '20000000', '-reconnect', '1', '-reconnect_delay_max', '5']
            if headers:
                command += ['-headers', headers]
        command += ['-i', stream['url']]
    command += ['-map', '0:v:0', '-map', '1:a:0' if len(streams) > 1 else '0:a:0?',
                '-c', 'copy', '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'event',
                '-hls_segment_type', 'fmp4', '-hls_flags', 'temp_file',
                '-hls_fmp4_init_filename', 'init.mp4', '-hls_segment_filename', str(folder / 'segment%06d.m4s'),
                '-progress', 'pipe:1', '-nostats', str(folder / 'index.m3u8')]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as process:
        for line in process.stdout:
            if line.startswith('out_time_us='):
                try:
                    state['seconds'] = max(0, int(line.split('=', 1)[1]) / 1_000_000)
                except ValueError:
                    continue
                write_state(stage, state)
        if process.wait():
            raise ValueError('Le téléchargement a été interrompu. Vérifie ta connexion.')
    state['input_complete'] = True
    write_state(stage, state)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix('.part.mp4')
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(folder / 'index.m3u8'), '-map', '0', '-c', 'copy',
                    '-metadata', 'title=' + str(info.get('title', output.stem)),
                    '-movflags', '+faststart', str(partial)], check=True)
    partial.replace(output)
    state.update(complete=True, seconds=duration)
    write_state(stage, state)


def main():
    stage = Path(os.environ['YT_MOVIES_DIR']).resolve()
    movies = stage / 'Movies'
    # yt-dlp still selects the best AVC/AAC streams and writes the original sidecars.
    result = subprocess.run(['yt-dlp', '--no-warnings', '--no-playlist', '--extractor-args', 'youtube:lang=fr',
                             '--skip-download', '--no-simulate', '--dump-single-json', '--write-thumbnail', '--convert-thumbnails', 'jpg',
                             '--write-subs', '--sub-langs', 'fr', '--sub-format', 'vtt',
                             '--format-sort', 'lang', '--format', FORMAT, '--paths', str(movies), '-o', '%(title)s/%(title)s.%(ext)s',
                             '--', sys.argv[1]], capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    info = json.loads(result.stdout)
    downloads = info.get('requested_downloads') or []
    download = downloads[0] if downloads else info
    filename = download.get('filepath') or download.get('filename') or download.get('_filename')
    if not filename:
        raise ValueError('YouTube n’a pas fourni de destination vidéo exploitable.')
    output = Path(filename).with_suffix('.mp4').resolve()
    if movies not in output.parents:
        raise ValueError('Destination du téléchargement invalide.')
    remux_stream(info, stage, output)
    subprocess.run([sys.executable, str(Path(__file__).with_name('ytmp4-page.py')), str(output)], check=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

"""Run the actual yt-dlp metadata workflow and FFmpeg, entirely on disposable files."""
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch


class QuietServer(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class ProgressiveDownloadTest(unittest.TestCase):
    def test_real_extractor_destination_and_complete_media(self):
        helper = Path(__file__).resolve().parents[2] / 'ytmp4-stream.py'
        with tempfile.TemporaryDirectory(prefix='fauteuil-progressive-') as temporary:
            root = Path(temporary).resolve()
            stage = root / 'job'
            stage.mkdir()
            source = root / 'fixture.mp4'
            subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'color=size=160x90:rate=12',
                '-f', 'lavfi', '-i', 'sine=sample_rate=48000', '-t', '4',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '24',
                '-c:a', 'aac', '-movflags', '+faststart', str(source)
            ], check=True)
            original = source.read_bytes()
            handler = functools.partial(QuietServer, directory=str(root))
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = f'http://127.0.0.1:{server.server_port}/fixture.mp4'
            metadata = root / 'metadata.json'
            metadata.write_text(json.dumps({
                'id': 'fixture', 'title': 'Film : français / essai', 'duration': 4,
                'extractor': 'generic', 'extractor_key': 'Generic', 'webpage_url': url,
                'formats': [{'format_id': 'mp4', 'url': url, 'ext': 'mp4',
                             'vcodec': 'avc1', 'acodec': 'mp4a', 'filesize': source.stat().st_size}]
            }))
            namespace = {'__file__': str(helper), '__name__': 'isolated_test'}
            exec(compile(helper.read_text(), str(helper), 'exec'), namespace)
            actual_run = subprocess.run

            def local_extraction(command, *args, **kwargs):
                # Substitute only YouTube extraction. The real yt-dlp executable still
                # selects formats, sanitizes paths and emits its current JSON schema.
                if command[0] == 'yt-dlp':
                    command = command[:-2] + ['--load-info-json', str(metadata)]
                return actual_run(command, *args, **kwargs)

            with patch('subprocess.run', side_effect=local_extraction), \
                 patch('sys.argv', [str(helper), url]), \
                 patch.dict(os.environ, {'YT_MOVIES_DIR': str(stage), 'YT_CINEMA_RESERVE_BYTES': '0'}):
                self.assertEqual(namespace['main'](), 0)
            state = json.loads((stage / '.cinema-stream.json').read_text())
            output = Path(state['path'])
            self.assertTrue(output.is_relative_to(stage / 'Movies'))
            self.assertTrue(output.is_file())
            self.assertTrue(state['complete'])
            playlist = (stage / 'stream/index.m3u8').read_text()
            self.assertIn('#EXT-X-ENDLIST', playlist)
            probe = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_name',
                '-of', 'json', str(output)
            ]))
            self.assertAlmostEqual(float(probe['format']['duration']), 4, delta=.2)
            self.assertEqual({s['codec_name'] for s in probe['streams']}, {'h264', 'aac'})
            self.assertEqual(source.read_bytes(), original)

            # YouTube's high quality formats normally have separate video/audio URLs.
            video = root / 'video.mp4'
            audio = root / 'audio.m4a'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-i', str(source), '-map', '0:v', '-c', 'copy', str(video)], check=True)
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-i', str(source), '-map', '0:a', '-c', 'copy', str(audio)], check=True)
            info = json.loads(metadata.read_text())
            info['formats'] = [
                {'format_id': '137', 'url': url.replace('fixture.mp4', 'video.mp4'),
                 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none'},
                {'format_id': '140', 'url': url.replace('fixture.mp4', 'audio.m4a'),
                 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a', 'language': 'fr'}
            ]
            metadata.write_text(json.dumps(info))
            separate = root / 'separate'
            separate.mkdir()
            with patch('subprocess.run', side_effect=local_extraction), \
                 patch('sys.argv', [str(helper), url]), \
                 patch.dict(os.environ, {'YT_MOVIES_DIR': str(separate), 'YT_CINEMA_RESERVE_BYTES': '0'}):
                self.assertEqual(namespace['main'](), 0)
            state = json.loads((separate / '.cinema-stream.json').read_text())
            output = Path(state['path'])
            self.assertTrue(output.is_relative_to(separate / 'Movies'))
            probe = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_name',
                '-of', 'json', str(output)
            ]))
            self.assertEqual({s['codec_name'] for s in probe['streams']}, {'h264', 'aac'})
            self.assertAlmostEqual(float(probe['format']['duration']), 4, delta=.2)


if __name__ == '__main__':
    unittest.main()

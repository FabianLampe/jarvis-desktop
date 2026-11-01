"""Local UI preview, with read-only access to the configured soundtrack only."""
from __future__ import annotations

import argparse
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from jarvis_core.config import default_config_path, load, repo_root


def preview_handler(music_file: Path | None):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            self._remaining = None
            super().__init__(*args, directory=str(Path(__file__).parent / 'ui'), **kwargs)

        def do_GET(self):
            if urlsplit(self.path).path == '/preview-music.json':
                available = bool(music_file and music_file.is_file())
                data = json.dumps({'configured': available,
                                   'file': music_file.name if (available and music_file) else ''}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            super().do_GET()

        def send_head(self):
            if urlsplit(self.path).path != '/preview-soundtrack':
                return super().send_head()
            if not music_file or not music_file.is_file():
                self.send_error(404, 'No local soundtrack configured')
                return None
            source = music_file.open('rb')
            size = music_file.stat().st_size
            start, end = 0, size - 1
            byte_range = self.headers.get('Range')
            if byte_range:
                match = re.fullmatch(r'bytes=(\d+)-(\d*)', byte_range)
                if match:
                    start = int(match[1])
                    end = min(int(match[2]), size - 1) if match[2] else size - 1
                if not match or start > end:
                    source.close()
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return None
            self.send_response(206 if byte_range else 200)
            self.send_header('Content-Type', self.guess_type(str(music_file)))
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            if byte_range:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            source.seek(start)
            self._remaining = end - start + 1
            return source

        def copyfile(self, source, outputfile):
            if self._remaining is None:
                return super().copyfile(source, outputfile)
            try:
                while self._remaining:
                    data = source.read(min(65536, self._remaining))
                    if not data:
                        break
                    outputfile.write(data)
                    self._remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Browser cancelled playback or replaced a range request.

    return Handler


def main():
    parser = argparse.ArgumentParser(description='Jarvis-Vorschau mit lokalem Soundtrack')
    parser.add_argument('--config', default=str(default_config_path()))
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    value = load(args.config).experience.music_file
    music = Path(value).expanduser() if value else None
    if music and not music.is_absolute():
        music = repo_root() / music
    with ThreadingHTTPServer(('127.0.0.1', args.port), preview_handler(music)) as server:
        print(f'Jarvis-Vorschau: http://127.0.0.1:{args.port}/preview.html?open=1&nochat=1&experience=wake', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()

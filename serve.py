#!/usr/bin/env python3
"""Static server for the arkivbot2 status page (the `arkivbot-web` job).

It differs from the bare `python3 -m http.server` the job used to run in four
ways:

* request logging is suppressed, so visitor polling cannot fill the job's
  stderr log;
* `out`/`status` are served as UTF-8 text instead of
  `application/octet-stream`, and with `Cache-Control: no-store`;
* every response carries `X-Content-Type-Options: nosniff`;
* a missing `status` file answers `200 unknown` instead of `404`, so the job
  stays healthy before its first run.

`Last-Modified` is preserved (the page uses it to detect a stalled run).

Usage (see jobs.yaml):
    python3 serve.py --port 8080 --bind 0.0.0.0
"""

from __future__ import annotations

import argparse
import contextlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from os import PathLike
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'public_html'
RUNTIME_FILES = {'out': b'', 'status': b'unknown\n'}


class Handler(SimpleHTTPRequestHandler):
    server_version = 'arkivbot2-status'
    sys_version = ''
    root: Path = ROOT  # overridable with --root (used by the tests)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(self.root), **kwargs)

    # --- responses -----------------------------------------------------

    def send_head(self) -> Any:
        name = self.path.split('?', 1)[0].lstrip('/')
        if name in RUNTIME_FILES and not (self.root / name).is_file():
            body = RUNTIME_FILES[name]
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return _BytesReader(body)
        return super().send_head()

    def guess_type(self, path: str | PathLike[str]) -> str:
        if Path(str(path)).name in RUNTIME_FILES:
            return 'text/plain; charset=utf-8'
        return super().guess_type(path)

    def end_headers(self) -> None:
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if Path(self.path.split('?', 1)[0]).name in RUNTIME_FILES:
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    # --- noise ---------------------------------------------------------

    def log_message(self, *args: Any) -> None:
        """Silence per-request logging (visitor polling would flood the log)."""


class _BytesReader:
    """Minimal file-like wrapper so send_head can return a body for /status."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size is None or size < 0 else self._offset + size
        chunk = self._data[self._offset:end]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--root', default=str(ROOT),
                        help='document root (default: ./public_html)')
    args = parser.parse_args()

    Handler.root = Path(args.root).resolve()
    if not (Handler.root / 'index.html').is_file():
        raise SystemExit(f'no index.html in {Handler.root}')

    with ThreadingHTTPServer((args.bind, args.port), Handler) as httpd:
        print(f'arkivbot2 status page on {args.bind}:{args.port} (root {Handler.root})', flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            httpd.serve_forever()


if __name__ == '__main__':
    main()

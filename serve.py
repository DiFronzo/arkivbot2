#!/usr/bin/env python3
"""Static server for the arkivbot2 status page (the `arkivbot-web` job).

It serves an allowlist, not a directory tree: `/` and `/index.html` for the
page, `/out` and `/status` for the two files `run.sh` writes. Everything else
is a 404, so nothing that happens to sit under `public_html/` (or under the
tool home, if a symlink pointed there) is reachable from the internet.

Other deliberate differences from `python3 -m http.server`:

* request logging is suppressed, so visitor polling cannot fill the job's log;
* `out`/`status` are served as UTF-8 text with `Cache-Control: no-store`;
* every response carries `X-Content-Type-Options: nosniff` and
  `Referrer-Policy: no-referrer`;
* a missing `status` file answers `200 unknown` instead of `404`, so the job
  stays healthy before its first run.

`Last-Modified` is preserved for the two runtime files (the page uses it to
detect a stalled run).

Usage (see jobs.yaml):
    python3 serve.py --port 8080 --bind 0.0.0.0
"""

from __future__ import annotations

import argparse
import contextlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from os import PathLike
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / 'public_html'

# URL path -> file served from the document root. No other path is reachable.
ALLOWED = {
    '/': 'index.html',
    '/index.html': 'index.html',
    '/out': 'out',
    '/status': 'status',
}
# Served with a synthetic body when the file does not exist yet.
PLACEHOLDERS = {'out': b'', 'status': b'unknown\n'}


class _BytesReader:
    """Minimal file-like wrapper so send_head can return a synthetic body."""

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


class Handler(SimpleHTTPRequestHandler):
    server_version = 'arkivbot2-status'
    sys_version = ''
    root: Path = ROOT  # overridable with --root (used by the tests)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(self.root), **kwargs)

    # --- routing -------------------------------------------------------

    def _route(self) -> str | None:
        """Return the file name for this request path, or None if not allowed."""
        path = self.path.split('?', 1)[0].split('#', 1)[0]
        name = ALLOWED.get(path)
        if name is None:
            return None
        target = self.root / name
        # Never follow a symlink out of the document root.
        if target.is_symlink():
            return None
        return name

    def send_head(self) -> Any:
        name = self._route()
        if name is None:
            return self._plain(404, b'not found\n', cacheable=False)

        if not (self.root / name).is_file() and name in PLACEHOLDERS:
            return self._plain(200, PLACEHOLDERS[name], cacheable=False)
        return super().send_head()

    def _plain(self, status: int, body: bytes, *, cacheable: bool) -> _BytesReader:
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if not cacheable:
            self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        return _BytesReader(body)

    # --- headers -------------------------------------------------------

    def guess_type(self, path: str | PathLike[str]) -> str:
        if Path(str(path)).name in PLACEHOLDERS:
            return 'text/plain; charset=utf-8'
        return super().guess_type(path)

    def end_headers(self) -> None:
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        name = ALLOWED.get(self.path.split('?', 1)[0].split('#', 1)[0])
        if name in PLACEHOLDERS:
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    # --- noise ---------------------------------------------------------

    def log_message(self, *args: Any) -> None:
        """Silence per-request logging (visitor polling would flood the log)."""


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

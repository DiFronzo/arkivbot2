#!/usr/bin/env python3
"""Preview the status page locally with a synthetic archiving run.

Serves `public_html/` and replaces `out`/`status` with generated content, so the
page can be exercised without touching a wiki, the real log files, or the
deployed site. Nothing is written to disk.

The generated log matches `run.sh` byte-for-byte in shape (same `date` format,
including the space-padded day, and the same marker lines), so parsing bugs
cannot hide behind the preview.

    ./web-preview.py                  # animates a run, then goes idle
    ./web-preview.py --state running  # hold the run open (keeps appending)
    ./web-preview.py --state done     # a completed run in the log
    ./web-preview.py --state idle     # empty state, as before the first run
    ./web-preview.py --loop           # replay the run over and over
    ./web-preview.py --replay captured.log   # serve a real captured log
    ./web-preview.py --port 9000 --speed 0.5

Standard library only; no venv needed (works with `uv run python web-preview.py`
or plain `python3 web-preview.py`).
"""

from __future__ import annotations

import argparse
import http.server
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'public_html'
RULE = '-' * 42

# Real page names, so the preview looks like production output.
PAGES = [
    ('Wikipedia:Torget', 64, 31),
    ('Wikipedia:Administratorenes oppslagstavle', 4, 3),
    ('Brukerdiskusjon:Cocu', 18, 8),
    ('Brukerdiskusjon:Patandor', 9, 2),
    ('Diskusjon:Forside', 5, 4),
    ('Wikipedia:Notiser', 10, 5),
    ('Brukerdiskusjon:1000mm', 8, 4),
    ('Wikipedia:Patruljeringsforum', 5, 2),
    ('Diskusjon:Norge', 3, 0),
    ('Brukerdiskusjon:Orland', 3, 1),
]


def stamp() -> str:
    """Timestamp in the exact shape `date` writes (macOS has no %Z, and the day
    is space-padded for 1-9)."""
    now = datetime.now()
    zone = now.astimezone().tzname() or time.tzname[0]
    return f'{now:%a %b} {now.day:>2} {now:%H:%M:%S} {zone} {now:%Y}'


class Sim:
    """Holds the synthetic log and the status flag."""

    def __init__(self, speed: float, loop: bool, animate: bool) -> None:
        self.speed = speed
        self.loop = loop
        self.animate = animate
        self.lines: list[str] = []
        self.running = False
        self.lock = threading.Lock()

    def out(self) -> str:
        with self.lock:
            return '\n'.join(self.lines) + '\n'

    def status(self) -> str:
        with self.lock:
            return 'true' if self.running else 'false'

    def write(self, *lines: str) -> None:
        with self.lock:
            self.lines.extend(lines)

    def load_replay(self, path: Path) -> None:
        """Serve a previously captured run instead of inventing one."""
        text = path.read_text(encoding='utf-8')
        start = max(text.rfind('Bot started'), text.rfind('SIMULATED run started'))
        finish = max(text.rfind('Bot finished'), text.rfind('SIMULATED run finished'))
        with self.lock:
            self.lines = text.splitlines()
            self.running = start != -1 and start > finish

    # -- synthetic run ---------------------------------------------------

    def run_once(self) -> None:
        with self.lock:
            self.lines = [
                RULE,
                f'{stamp()} : Bot started',
                RULE,
                'Python 3.13.18',
                '------------------------------------------',
            ]
            self.running = True

        for index, (page, found, archived) in enumerate(PAGES):
            self.pause()
            self.write(RULE)
            self.write(f'>>> [[no:{page}]] <<<')
            self.write(f'{found} thread(s) found on [[no:{page}]]')
            if archived:
                self.write(f'Archiving {archived} thread(s).')
            else:
                self.write('No thread is old enough. Skipping')

            if index == len(PAGES) - 2:
                self.write('ERROR: Missing or malformed template in page '
                           f'[[no:{page}]]: Couldn\'t find the template in the header')
                self.write('Traceback (most recent call last):')
                self.write('  File "scripts/archivebot.py", line 1003, in process_page')
                self.write('    archiver.run()')
                self.write('pywikibot.exceptions.TranslationError: '
                           'No translation available')

        self.pause()
        with self.lock:
            self.lines.extend([RULE, f'{stamp()} : Bot finished', RULE])
            self.running = False

    def pause(self) -> None:
        if self.animate:
            time.sleep(max(self.speed, 0.05))

    def thread(self) -> None:
        while True:
            self.run_once()
            if not self.loop:
                return
            time.sleep(max(self.speed * 4, 1))


class Handler(http.server.SimpleHTTPRequestHandler):
    sim: Sim
    server_version = 'arkivbot2-preview'

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)  # type: ignore[arg-type]

    def _runtime(self, path: str, send_body: bool = True) -> None:
        body = (self.sim.out() if path == '/out' else self.sim.status()).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split('?', 1)[0]
        if path in ('/out', '/status'):
            self._runtime(path)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        path = self.path.split('?', 1)[0]
        if path in ('/out', '/status'):
            self._runtime(path, send_body=False)
            return
        super().do_HEAD()

    def log_message(self, format: str, *args: object) -> None:
        """Keep the terminal for the preview instructions."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8080,
                        help='port to listen on (default: 8080, like jobs.yaml)')
    parser.add_argument('--state', choices=('running', 'done', 'idle'),
                        default='running',
                        help='initial state to show (default: running)')
    parser.add_argument('--speed', type=float, default=1.0,
                        help='seconds between simulated pages (default: 1.0)')
    parser.add_argument('--loop', action='store_true',
                        help='replay the run continuously')
    parser.add_argument('--no-animate', dest='animate', action='store_false',
                        help='write the whole run at once')
    parser.add_argument('--replay', metavar='FILE',
                        help='serve a captured log instead of a synthetic run')
    args = parser.parse_args()

    if not (ROOT / 'index.html').is_file():
        raise SystemExit(f'no index.html in {ROOT}')

    sim = Sim(speed=args.speed, loop=args.loop, animate=args.animate)
    if args.replay:
        sim.load_replay(Path(args.replay))
    elif args.state == 'done':
        sim.animate = False
        sim.run_once()
    elif args.state == 'running':
        threading.Thread(target=sim.thread, daemon=True).start()

    Handler.sim = sim
    with http.server.ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as httpd:
        print(f'Status page preview: http://127.0.0.1:{args.port}/')
        print(f'  serving {ROOT} with in-memory out/status (nothing written to disk)')
        state = f'replay={args.replay}' if args.replay else f'state={args.state}'
        print(f'  {state} animation={"on" if args.animate else "off"}'
              f'{" loop" if args.loop else ""}. Ctrl+C to stop')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nstopped')


if __name__ == '__main__':
    main()

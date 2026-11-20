#!/usr/bin/env python3
"""Filter archivebot output before it is published on the status page.

The public page gets the narrative: page headings, thread counts, and the
`ERROR:` summary lines. Traceback frames and absolute filesystem paths stay in
the job log and in pywikibot's own `logs/main.log`, both of which are readable
only by tool maintainers.

Reads stdin, writes stdout.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator

# A traceback frame and the source line beneath it.
FRAME = re.compile(r'^\s+File ".+"')
# Python 3.11+ renders tracebacks with ^^^^ / ~~~~ underlines; no value on a
# public page.
CARETS = re.compile(r'^\s*[~^]{2,}\s*$')
# Absolute paths that reveal the tool home or the runner's layout.
ABS_PATH = re.compile(r'(?:/data/project|/home|/Users|/private|/tmp)/[^\s:,)\]]+')


def sanitize(lines: Iterable[str]) -> Iterator[str]:
    """Yield the lines that are safe to publish, with paths replaced."""
    drop_source_line = False
    for line in lines:
        if drop_source_line and re.match(r'^\s+\S', line):
            drop_source_line = False
            continue
        drop_source_line = False
        if FRAME.match(line):
            drop_source_line = True
            continue
        if CARETS.match(line):
            continue
        yield ABS_PATH.sub('<path>', line)


def main() -> int:
    for line in sanitize(sys.stdin):
        sys.stdout.write(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

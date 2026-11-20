#!/usr/bin/env python3
"""Run archivebot with one safety net: blacklisted URLs are neutralised.

no.wiki's spam blacklist rejects an archive-page save when the threads being
moved contain a blacklisted link (archive.today is one). archivebot catches the
error, skips the page, and those threads never leave the talk page. Rejecting the
save is correct behaviour on the wiki's side; the link really is blacklisted.

This wrapper retries such a save once, with the offending URL wrapped in
<nowiki>. The URL stays visible and copyable in the archive, only the clickable
link is lost, and every rewrite is logged as SANITIZED so the edit is auditable.
If the retry is rejected too, or the offending text is not present in the page,
the original error is re-raised and archivebot skips the page as before.

Run it instead of scripts/archivebot.py; arguments are passed through:

    PYTHONPATH="$PYWIKI:$PYWIKI/scripts" python archivebot_wrapper.py \
        -lang:no -log:main.log Autoarkivering
"""

from __future__ import annotations

import re
import sys
from typing import Any

from pywikibot.exceptions import SpamblacklistError
from pywikibot.page import BasePage

# Characters that can appear inside a URL in wikitext.
URL_BOUNDARY = r'[^\s\[\]{}|<>"\']'
TRAILING = '.,;:!?'

_original_save: Any = None


def _trim(url: str) -> tuple[str, str]:
    """Split trailing punctuation off a URL, keeping balanced parentheses."""
    trailing = ''
    while url:
        last = url[-1]
        if last in TRAILING:
            trailing = last + trailing
            url = url[:-1]
            continue
        if last == ')' and url.count('(') < url.count(')'):
            trailing = last + trailing
            url = url[:-1]
            continue
        break
    return url, trailing


def _host(url: str) -> str:
    """Return the host part of a URL or bare domain."""
    match = re.match(r'(?i)(?:https?:)?//([^/?#]+)', url)
    if match:
        return match.group(1)
    return re.split(r'[/?#]', url, maxsplit=1)[0]


def neutralise(text: str, needle: str) -> tuple[str, int]:
    """Wrap every URL *hosted at* `needle` in <nowiki>.

    Only links whose host matches are touched: a page that mentions the same
    string in prose, or a link on an unrelated host that happens to contain it,
    is left alone. Text already inside <nowiki> is skipped.

    :return: the rewritten text and how many occurrences were wrapped.
    """
    if not needle or not text:
        return text, 0

    pattern = re.compile(
        rf'{URL_BOUNDARY}*{re.escape(needle)}{URL_BOUNDARY}*', re.IGNORECASE
    )
    whole_url = '/' in needle  # the API reported a full URL, not a domain
    wrapped = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal wrapped
        url, trailing = _trim(match.group(0))
        if not url:
            return match.group(0)
        if not whole_url and needle.lower() not in _host(url).lower():
            return match.group(0)
        # A bare host with no scheme and no path is prose, not a link.
        if '://' not in url and not url.startswith('//') and '/' not in url:
            return match.group(0)
        if text[:match.start()].endswith('<nowiki>'):
            return match.group(0)
        wrapped += 1
        return f'<nowiki>{url}</nowiki>{trailing}'

    return pattern.sub(repl, text), wrapped
def save_with_fallback(self: Any, *args: Any, **kwargs: Any) -> Any:
    """Save the page; on a spam block, retry once with the URL neutralised."""
    try:
        return _original_save(self, *args, **kwargs)
    except SpamblacklistError as error:
        text, count = neutralise(self.text, error.url)
        if not count:
            # The blocked text is not in the page (different normalisation?), so
            # there is nothing safe to rewrite: behave exactly as before.
            raise
        print(
            f'SANITIZED: wrapped {count} occurrence(s) of {error.url} in '
            f'<nowiki> on [[{self.title()}]], retrying the save',
            file=sys.stderr,
            flush=True,
        )
        self.text = text
        return _original_save(self, *args, **kwargs)


def install() -> None:
    """Patch pywikibot's save once for this process."""
    global _original_save
    _original_save = BasePage.save
    BasePage.save = save_with_fallback


def main() -> int:
    install()

    import archivebot  # lives in the pinned checkout, on PYTHONPATH

    archivebot.main()
    return 0


if __name__ == '__main__':
    sys.exit(main())

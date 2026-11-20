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

import pywikibot
from pywikibot.exceptions import LockedPageError, SpamblacklistError
from pywikibot.page import BasePage

# Characters that can appear inside a URL in wikitext.
URL_BOUNDARY = r'[^\s\[\]{}|<>"\']'
TRAILING = '.,;:!?'
MAX_ATTEMPTS = 5

_original_save: Any = None
_original_exception: Any = None
_error: Any = None


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
    # The API reports the matched text, but a regex fragment would arrive with
    # backslashes; strip them so the literal text can be found.
    needle = needle.replace('\\', '')

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
        # Only links pointed at the reported site. Anything else on the page is
        # left exactly as it is.
        if not whole_url and needle.lower() not in _host(url).lower():
            return match.group(0)
        if text[:match.start()].endswith('<nowiki>'):
            return match.group(0)
        wrapped += 1
        return f'<nowiki>{url}</nowiki>{trailing}'

    return pattern.sub(repl, text), wrapped
def save_with_fallback(self: Any, *args: Any, **kwargs: Any) -> Any:
    """Save the page, neutralising whatever sites the spam filter rejects.

    A wiki can report a different blacklisted site on each attempt (a page can
    cite several), so this loops until the save goes through, nothing new can be
    neutralised, or the attempt budget runs out.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _original_save(self, *args, **kwargs)
        except SpamblacklistError as error:
            if attempt == MAX_ATTEMPTS:
                raise
            text, count = neutralise(self.text, error.url)
            if not count:
                # The blocked text is not in the page (different normalisation?),
                # so there is nothing safe to rewrite: behave as before.
                raise
            print(
                f'SANITIZED: wrapped {count} occurrence(s) of {error.url} in '
                f'<nowiki> on [[{self.title()}]] '
                f'(attempt {attempt}/{MAX_ATTEMPTS})',
                file=sys.stderr,
                flush=True,
            )
            self.text = text
    raise AssertionError('unreachable: the loop either returns or raises')


def concise_exception(msg: str = '', *args: Any, **kwargs: Any) -> None:
    """Replace archivebot's traceback for conditions the wiki decided.

    archivebot's generic handler prints a full traceback for every failure. For
    a protected page or a still-blocked link the outcome is known, so print one
    actionable line instead and keep the traceback for anything unexpected.
    """
    error = sys.exc_info()[1]
    title = getattr(error, 'title', '') or '(unknown page)'
    if isinstance(error, LockedPageError):
        _error(f'SKIPPED: {title} is protected, an admin has to lower the protection')
        return
    if isinstance(error, SpamblacklistError):
        _error(f'SKIPPED: {title} is still blocked by the spam filter ({error.url})')
        return
    _original_exception(msg, *args, **kwargs)


def install() -> None:
    """Patch pywikibot once for this process."""
    global _original_save, _original_exception, _error
    _original_save = BasePage.save
    BasePage.save = save_with_fallback
    # pywikibot re-exports these at package level without declaring them, so
    # the type checker cannot see them; ruff prefers plain attribute access.
    _original_exception = pywikibot.exception  # type: ignore[attr-defined]
    _error = pywikibot.error  # type: ignore[attr-defined]
    pywikibot.exception = concise_exception  # type: ignore[attr-defined]


def main() -> int:
    install()

    import archivebot  # lives in the pinned checkout, on PYTHONPATH

    archivebot.main()
    return 0


if __name__ == '__main__':
    sys.exit(main())

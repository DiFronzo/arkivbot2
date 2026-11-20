#!/usr/bin/env python3
"""Shape-check the MW_* OAuth credentials without ever printing a secret.

pywikibot reports ``mwoauth-invalid-authorization: Invalid signature`` when the
signature built from (consumer token, consumer secret, access token, access
secret) does not match. Almost always one of the four values is wrong: wrong
consumer, stale/regenerated token, swapped token and secret, an OAuth 2.0
credential used where 1.0a is required, or stray whitespace.

This script inspects *shape only*: presence, length, character class, duplicated
values, whitespace. It never prints a value, a prefix, or a suffix, so its output
is safe to paste into a chat or a bug report.

Usage:
    uv run python check-oauth.py
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

# Wikimedia OAuth 1.0a owner-only consumers use hex tokens/secrets.
# Lengths below are the usual ones; a mismatch is a warning, not proof of a bug.
FIELDS = {
    'MW_CONSUMER_TOKEN': ('consumer token', 32),
    'MW_CONSUMER_SECRET': ('consumer secret', 40),
    'MW_ACCESS_TOKEN': ('access token', 32),
    'MW_ACCESS_SECRET': ('access secret', 40),
}

HEX_ONLY = re.compile(r'^[0-9a-fA-F]+$')
BASE64ISH = re.compile(r'^[A-Za-z0-9+/=_.-]+$')


def load_env() -> str:
    """Populate the environment like pywikibot does; return the source note."""
    env_file = Path(os.environ.get('PYWIKIBOT_DIR', Path.cwd())) / '.env'
    try:
        import dotenv
    except ImportError:
        return 'python-dotenv not installed; using the current environment only'
    if env_file.is_file():
        dotenv.load_dotenv(env_file)
        return f'values loaded from {env_file} (contents not displayed)'
    return 'no .env found; using the current environment only'


def describe(value: str) -> tuple[str, list[str]]:
    """Return a printable shape and a list of concerns. Never includes value."""
    notes: list[str] = []
    shape: list[str] = []

    shape.append(f'{len(value)} chars')

    if value != value.strip():
        notes.append('leading/trailing whitespace or newline')
    if value[:1] in {'"', "'"} or value[-1:] in {'"', "'"}:
        notes.append('surrounding quote leaked into the value')
    if any(ch in value for ch in '\r\n\t '):
        notes.append('embedded whitespace')

    if HEX_ONLY.match(value):
        shape.append('hex')
    elif BASE64ISH.match(value):
        shape.append('base64-ish')
        notes.append('not hex; OAuth 2.0 client secret?')
    else:
        shape.append('other characters')
        notes.append('unexpected characters')

    if len(set(value)) < 4:
        notes.append('very low character variety ("changeme"? empty template?)')

    return ', '.join(shape), notes


def main() -> int:
    print(load_env())
    print()

    values: dict[str, str] = {}
    concerns = 0

    for name, (label, expected) in FIELDS.items():
        value = os.environ.get(name, '')
        print(f'{name:<20} ({label})')
        if not value:
            print('    MISSING or empty')
            concerns += 3
            continue
        values[name] = value
        shape, notes = describe(value)
        print(f'    {shape}')
        if len(value) != expected:
            notes.append(f'unusual length (1.0a is typically {expected})')
        for note in notes:
            print(f'    ! {note}')
        concerns += len(notes)

    print()
    username = os.environ.get('MW_BOT_USERNAME', '(unset)')
    print(f'MW_BOT_USERNAME      {username}')

    if importlib.util.find_spec('mwoauth') is not None:
        print('mwoauth              installed (required by pywikibot for 1.0a)')
    else:
        print('mwoauth              MISSING; pywikibot cannot sign OAuth 1.0a')
        concerns += 1

    # Copy/paste mix-ups produce valid-looking values that cannot sign.
    pairs = [
        ('MW_CONSUMER_TOKEN', 'MW_ACCESS_TOKEN'),
        ('MW_CONSUMER_SECRET', 'MW_ACCESS_SECRET'),
        ('MW_CONSUMER_TOKEN', 'MW_CONSUMER_SECRET'),
        ('MW_ACCESS_TOKEN', 'MW_ACCESS_SECRET'),
    ]
    for left, right in pairs:
        if values.get(left) and values.get(left) == values.get(right):
            print(f'! {left} equals {right}; token and secret are different values')
            concerns += 2

    print()
    if concerns:
        print('Verdict: the credential values look wrong. Fix the flagged items.')
    else:
        print('Verdict: shape looks plausible, so the values themselves are')
        print('probably stale, regenerated, or from a different consumer.')

    print(
        '\nNext steps\n'
        '  1. Wikimedia OAuth 1.0a "owner-only" consumer:\n'
        '     https://meta.wikimedia.org/wiki/Special:AppManagement\n'
        '     - must be OAuth **1.0a** (not OAuth 2.0), owner-only\n'
        '       ("Allow consumer to be used as a bot")\n'
        '     - create it while logged in **as IngeniousBot**, otherwise the\n'
        '       edits are attributed to whoever owns the tokens\n'
        '     - re-copy consumer token + secret; regenerate the access token\n'
        '       and copy the new access secret (old ones stop working)\n'
        '  2. Re-run:  uv run pwb login -simulate\n'
        '     It must report "Logged in on no.wikipedia.org as IngeniousBot".\n'
        '  3. Simpler fallback if OAuth keeps fighting you: a bot password\n'
        '     from https://no.wikipedia.org/wiki/Special:BotPasswords (log in as\n'
        '     IngeniousBot), then in user-config.py:\n'
        '         password_file = "user-password.py"\n'
        '     with  ("IngeniousBot", BotPassword("suffix", "secret"))  in it.\n'
        '     Both files are gitignored.'
    )
    return 1 if concerns else 0


if __name__ == '__main__':
    sys.exit(main())

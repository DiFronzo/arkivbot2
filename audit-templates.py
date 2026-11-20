#!/usr/bin/env python3
"""Audit which pages archivebot would reject, and why (read-only).

`archivebot.py` refuses a page in two situations, both reported as template
errors:

  A  "Couldn't find the template in the header": after deciding to archive, it
     re-searches the pre-heading text with
     ``\\{\\{\\s*<template>\\s*?\\n.*?\\n\\}\\}``, i.e. the invocation must sit at the
     top of the page *and* be formatted multi-line.
  B  "Missing or malformed template": no template in the page matches the
     configured title. A page transcluding a *redirect* to the config template
     (e.g. a legacy `Bruker:ArkivBot/autoarkivering`) lands here, because the
     name on the page is compared literally.

Read-only: anonymous API requests, no credentials, no writes.

    ./audit-templates.py                        # no.wikipedia, Template:Autoarkivering
    ./audit-templates.py --wiki se --template Template:ArkivBot/config
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = ('arkivbot2-template-audit/1.0 '
      '(https://arkivbot2.toolforge.org; User talk:IngeniousBot) read-only')
WIKI_RE = re.compile(r'[a-z][a-z0-9-]{1,20}')


class ApiError(RuntimeError):
    """The API returned an error object or an unusable response."""


def api(api_url: str, **params: Any) -> dict[str, Any]:
    """GET the MediaWiki API, with basic retry/error handling."""
    params.setdefault('maxlag', '5')
    params.update(format='json', formatversion='2')
    url = api_url + '?' + urllib.parse.urlencode(params)

    for attempt in range(4):
        request = urllib.request.Request(url, headers={'User-Agent': UA})  # noqa: S310
        try:
            # URL is built from a validated wiki code + a fixed https base.
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise ApiError(f'HTTP {exc.code} from {api_url}') from exc
        except urllib.error.URLError as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise ApiError(f'cannot reach {api_url}: {exc.reason}') from exc

        if 'error' in data:
            raise ApiError(f'API error: {data["error"].get("info", data["error"])}')
        return dict(data)

    raise ApiError(f'giving up on {api_url}')


def template_namespaces(api_url: str) -> list[str]:
    """Return the local names of the template namespace and its aliases."""
    data = api(api_url, action='query', meta='siteinfo',
               siprop='namespaces|namespacealiases')
    names = [ns['name'] for ns in data['query']['namespaces'].values()
             if str(ns.get('id')) == '10']
    names += [alias['alias'] for alias in data['query'].get('namespacealiases', [])
              if str(alias.get('id')) == '10']
    return sorted({name for name in names if name})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--wiki', default='no',
                        help='wiki language code, e.g. no (default: no)')
    parser.add_argument('--template', default='Template:Autoarkivering',
                        help='config template as passed to archivebot '
                             '(default: Template:Autoarkivering)')
    args = parser.parse_args()

    if not WIKI_RE.fullmatch(args.wiki):
        raise SystemExit(f'invalid wiki code: {args.wiki!r}')

    api_url = f'https://{args.wiki}.wikipedia.org/w/api.php'

    title = args.template if ':' in args.template else f'Template:{args.template}'
    bare = title.split(':', 1)[1]
    name = re.escape(bare).replace(r'\ ', '[ _]')
    namespaces = template_namespaces(api_url)
    prefix = r'(?:(?i:' + '|'.join(re.escape(ns) for ns in namespaces) + r')[ _]*:[ _]*)?'
    present = re.compile(r'\{\{\s*' + prefix + name + r'\s*[|}\n]', re.IGNORECASE)
    header_ok = re.compile(r'\{\{\s*' + prefix + name + r'\s*?\n.*?\n\}\}', re.DOTALL)

    titles: list[str] = []
    cont: dict[str, Any] = {}
    while True:
        data = api(api_url, action='query', list='embeddedin',
                   eititle=title, eilimit='max', **cont)
        titles += [page['title'] for page in data['query']['embeddedin']]
        if 'continue' not in data:
            break
        cont = data['continue']

    print(f'{len(titles)} pages transclude {title} on {args.wiki}.wikipedia')
    print()

    not_in_header: list[str] = []
    single_line: list[str] = []
    wrong_name: list[str] = []

    for start in range(0, len(titles), 50):
        data = api(api_url, action='query', prop='revisions', rvprop='content',
                   rvslots='main', titles='|'.join(titles[start:start + 50]))
        for page in data['query']['pages']:
            if 'revisions' not in page:
                continue
            text = page['revisions'][0]['slots']['main']['content']
            # archivebot splits threads at level-2 headings only
            heading = re.search(r'(?m)^==[^=]', text)
            header = text[:heading.start()] if heading else text

            if not present.search(text):
                wrong_name.append(page['title'])
            elif not present.search(header):
                not_in_header.append(page['title'])
            elif not header_ok.search(header):
                single_line.append(page['title'])

    def report(title: str, items: list[str], hint: str) -> None:
        print(f'{title}: {len(items)}')
        for item in items:
            print(f'    {item}')
        if items:
            print(f'    -> {hint}')
        print()

    report('Template below the first heading', not_in_header,
           'move the template to the very top of the page (or drop it on archive pages)')
    report('Template present but written on one line', single_line,
           'reformat multi-line: "{{X\\n|param = value\\n}}"')
    report('No matching template name (legacy name or redirect)', wrong_name,
           'replace with the configured template name')

    broken = set(not_in_header) | set(single_line) | set(wrong_name)
    print(f'OK: {len(titles) - len(broken)} pages that archivebot can process')
    return 1 if broken else 0


if __name__ == '__main__':
    sys.exit(main())

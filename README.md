# ArkivBot2

Pywikibot's [`archivebot.py`](https://www.mediawiki.org/wiki/Manual:Pywikibot/archivebot.py)
for **Norwegian Bokmål** and **Northern Sami** Wikipedia, running as the
Wikimedia Toolforge tool [`arkivbot2`](https://arkivbot2.toolforge.org/).

Edits are made by the **[IngeniousBot](https://no.wikipedia.org/wiki/Bruker:IngeniousBot)**
account (the older `ArkivBot` account is retired). Live status:
<https://arkivbot2.toolforge.org/>, one HTML file and two text files served by
`serve.py`.

## What runs

All jobs live in [`jobs.yaml`](jobs.yaml).

| Job | Type | Purpose |
| --- | --- | --- |
| `arkivbot` | cron `0 5 * * *`, 25 min cap | [`run.sh`](run.sh): runs the pinned `archivebot.py` for no.wiki (and se.wiki when `SE_TEMPLATE` is set), writes `public_html/out` + `status`, exits non-zero on failure. |
| `arkivbot-web` | continuous, published, no file log | [`serve.py`](serve.py) serves `public_html/` at <https://arkivbot2.toolforge.org/>. Replaced the PHP `webservice` this tool used to run. |
| `logrotate-arkivbot` | cron `@daily` | [`logrotate.conf`](logrotate.conf): keeps the job logs from growing forever. |

Archive configuration is wiki-side, in templates:

- no.wiki: [`Mal:Autoarkivering`](https://no.wikipedia.org/wiki/Mal:Autoarkivering)
- se.wiki: `Málle:ArkivBot/config` (missing on-wiki, so that run is off by default)

`./audit-templates.py` lists pages archivebot would skip, and why.

The bot runs through `archivebot_wrapper.py`. Its only addition: when a wiki's
spam blacklist rejects an archive save (a thread citing `archive.today`, for
example), that URL is wrapped in `<nowiki>` and the save is retried, up to five
times for pages citing several blocked sites. The URL stays in the archive as
plain text, and the job log records `SANITIZED: wrapped ...`.

## Pinned versions

Everything production uses is pinned:

| Input | Where | Rule |
| --- | --- | --- |
| pywikibot | [`PYWIKIBOT_VERSION`](PYWIKIBOT_VERSION) | Git tag the checkout is pinned to; `run.sh` refuses any other revision |
| Python deps | [`requirements.txt`](requirements.txt) | Hashed, exported from `uv.lock`; installed with `--require-hashes` |
| Python | `.python-version`, `jobs.yaml` | 3.13 (the newest Toolforge image) |
| Local env | `pyproject.toml` + `uv.lock` | same pin as `PYWIKIBOT_VERSION` |

`archivebot.py` and its `i18n` submodule exist only in the git checkout (not in
the PyPI wheel), so the checkout is used via `PYTHONPATH` and never installed
into the venv.

## Requirements

- Python 3.13.
- **OAuth 1.0a secrets** for IngeniousBot, supplied through the environment:
  `PWB_USERNAME`, `PWB_CONSUMER_TOKEN`, `PWB_CONSUMER_SECRET`,
  `PWB_ACCESS_TOKEN`, `PWB_ACCESS_SECRET` (`MW_*` names also work). Locally that
  is a `.env` copied from [`.env.example`](.env.example); on Toolforge,
  `toolforge envvars`. [`user-config.py.example`](user-config.py.example) is the
  pywikibot side.
- Create the OAuth consumer while logged in **as IngeniousBot**. Owner-only
  1.0a credentials act as the account that created them.

Secrets are never committed: `.env`, `user-config.py`, `user-password.py` and
`passwordfile` are gitignored. Do not read `.env` (see `AGENTS.md`).

## Tests

```bash
./test-local.sh          # ~3 s: syntax, pins, run.sh exit codes, page contract,
                         #        status server, python syntax, shellcheck/ruff/mypy
./test-local.sh --live   # + archivebot -simulate on no.wikipedia (read-only)
SKIP_LINT=1 ./test-local.sh
```

The page/log contract is covered by `tests/status-page.test.mjs`, which runs the
real inline script from `index.html` against a DOM stub: log parsing (including
`date`'s space-padded day), run state, stall detection, 429 backoff, poll rates.
`test-local.sh` also checks that `serve.py` refuses traversal, dotfiles and
symlinks, that `filter-log.py` strips tracebacks and absolute paths, that a
second concurrent run backs off, and that no secret is tracked in git.

## Verifying without edits

Run them in this order. Step 1 is offline; steps 2 and 3 read from a wiki but cannot write.

1. `./test-local.sh`: offline, no network beyond `uv`.
2. `./test-local.sh --live` or `./run.sh --simulate`: pywikibot's `-simulate`
   blocks every write in the API layer, and `run.sh` leaves `public_html/`
   untouched.
3. On Toolforge, without disturbing the cron job:

   ```bash
   toolforge jobs run arkivbot-dry --command "./run.sh --simulate" --image python3.13 --wait 1800
   toolforge jobs logs arkivbot-dry
   ```

Compare the newest contribution before and after:

```bash
curl -s "https://no.wikipedia.org/w/api.php?action=query&list=usercontribs&ucuser=IngeniousBot&uclimit=1&format=json" | python3 -m json.tool
```

Credentials only: `uv run pwb login -simulate`, and
`uv run python check-oauth.py` for a secret-free shape check.

**The web interface, locally**, with synthetic run data:

```bash
./web-preview.py                  # animates a run, then goes idle
./web-preview.py --state idle     # the look before the first run
./web-preview.py --replay captured.log
```

## Deploying

First time: create the tool, then copy this repo into **its home root** (the job
commands are relative). On a bastion (`ssh login.toolforge.org`, then
`become arkivbot2`):

```bash
# one-time: OAuth secrets (PWB_CONSUMER_TOKEN, PWB_CONSUMER_SECRET,
#           PWB_ACCESS_TOKEN, PWB_ACCESS_SECRET, PWB_USERNAME)
toolforge envvars create PWB_CONSUMER_TOKEN

cp user-config.py.example user-config.py      # reads everything from the env
toolforge jobs run bootstrap-venv --command "./bootstrap-venv.sh" --image python3.13 --wait

# prove it works before anything is scheduled: reads the wikis, saves nothing
toolforge jobs run arkivbot-dry --command "./run.sh --simulate" --image python3.13 --wait 1800
toolforge jobs logs arkivbot-dry             # must report IngeniousBot and no ERROR lines

# only now install the jobs; this also starts the daily editing run
toolforge jobs load jobs.yaml                # arkivbot, arkivbot-web, logrotate
```

Full runbook, operational limits and gotchas: [`AGENTS.md`](AGENTS.md).

## Links

- Bot account: <https://no.wikipedia.org/wiki/Bruker:IngeniousBot>
- Contributions: <https://no.wikipedia.org/wiki/Spesial:Bidrag/IngeniousBot>
- Status page: <https://arkivbot2.toolforge.org/>
- Toolforge tool: <https://toolsadmin.wikimedia.org/tools/id/arkivbot2>
- Source: <https://github.com/DiFronzo/arkivbot2>

## License

Released into the public domain; see [LICENSE](LICENSE).

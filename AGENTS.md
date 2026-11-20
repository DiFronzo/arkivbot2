# AGENTS.md

Guidance for agents (and humans) working in this repo.

## Hard rules

- **Never read the `.env` file.** Do not `cat`, `grep`, `head`, `open`, diff,
  back up, upload or otherwise inspect it, and never start an editor on it. It
  holds live OAuth secrets for the bot account; no task in this repo needs it.
  Work from `.env.example` (placeholder names only) and envvar *names*, never
  values.
- If you believe you need a value from `.env`: stop and ask the operator, or
  have them set the variable in the environment. Do not derive it by reading the
  file, printing `os.environ`, dumping `config.authenticate`, or echoing tokens
  into logs, commits, PR text, or your own answer.
- Treat `.env.*`, `user-config.py`, `user-password.py` and `passwordfile` the
  same way: names and structure yes, secret contents no.
- **Any file scan must exclude them** and print file *names* only, e.g.
  `grep -rl --exclude='.env*' --exclude='user-*.py' PATTERN .`. A broad
  `grep -rn 'SECRET='` prints secrets into the transcript; that has happened
  once here and is why this rule exists.
- If secrets are ever exposed, the operator must rotate them (see
  § Secret hygiene). Do not try to "clean up" by rewriting logs.

## What this is

This repo is the code for the Wikimedia **Toolforge tool `arkivbot2`**: a
[Pywikibot](https://www.mediawiki.org/wiki/Manual:Pywikibot) `archivebot.py` job
that archives discussion pages on

- **no.wikipedia** (Norwegian Bokmål): config template `Mal:Autoarkivering`
- **se.wikipedia** (Northern Sami): config template `Málle:ArkivBot/config`,
  currently missing on-wiki, therefore disabled by default (`SE_TEMPLATE`)

plus a static status page at <https://arkivbot2.toolforge.org/>.

- Tool: **`arkivbot2`**, <https://arkivbot2.toolforge.org/>
  (jobs inside the tool are still named `arkivbot` / `arkivbot-web`, so log
  files are `arkivbot.out` / `arkivbot.err`)
- Bot account: **IngeniousBot**, <https://no.wikipedia.org/wiki/Bruker:IngeniousBot>
  (the legacy `ArkivBot` account is not accessible; the 2023 tool `arkivbot` is
  dead, it ran a PHP 7.4 webservice, and its home must not be reused)
- Source: <https://github.com/danmichaelo/arkivbot>

## Layout

| Path | Purpose |
| --- | --- |
| `run.sh` | Job entry point: pinned-checkout guard, `flock`, runs `archivebot.py` for `no` (and `se` when `SE_TEMPLATE` is set), writes `public_html/out` + `status`, exits non-zero on failure. |
| `jobs.yaml` | Three jobs: `arkivbot` (daily, 25 min cap per invocation), `arkivbot-web` (published static server), `logrotate-arkivbot` (daily log rotation). |
| `bootstrap-venv.sh` | Rebuilds `ENV` **inside a job container** from `requirements.txt` + `PYWIKIBOT_VERSION`. |
| `PYWIKIBOT_VERSION` | The pywikibot release tag everything is pinned to (wheel and checkout). |
| `requirements.txt` | Hashed, locked runtime requirements. Regenerate with `uv export`. |
| `pyproject.toml` / `uv.lock` / `.python-version` | Local dev env (uv), Python 3.13; dependency pins must match `PYWIKIBOT_VERSION`. |
| `serve.py` | Status-page server for the `arkivbot-web` job. Serves an allowlist only (`/`, `/index.html`, `/out`, `/status`); refuses traversal, dotfiles and symlinks. No request logging, `nosniff`, `text/plain` + `no-store` for the runtime files, `200 unknown` before the first run. |
| `filter-log.py` | Strips traceback frames and absolute paths from archivebot output before it reaches `public_html/out`. Full output stays in `logs/main.log` and the job log. |
| `health-check.sh` | Liveness probe for `arkivbot-web` (`health-check-script`), curl-free. |
| `logrotate.conf` | Rotation for the job logs (`logrotate-arkivbot`). |
| `public_html/index.html` | Status dashboard (no CDN, no PHP, no build step). |
| `test-local.sh` | Full local suite: syntax, pins, exit codes, page contract, server, linters (`--live` adds a read-only simulate run). |
| `tests/tracked-files.test.sh` | Fails if `.env`, `user-config.py`, a password file, `.DS_Store` or `*.pyc` reaches the git index. |
| `tests/status-page.test.mjs` | Node script that runs the inline script from `index.html` against a DOM stub. |
| `audit-templates.py` | Read-only audit of pages whose config template archivebot would reject. |
| `check-oauth.py` | Secret-free shape check of the OAuth values. |
| `web-preview.py` | Status page with synthetic (or `--replay`ed) run data, in memory. |
| `user-config.py.example` | pywikibot config template: account, per-wiki usernames, OAuth from env (`PWB_*` preferred, `MW_*` accepted). |
| `.env.example` | Envvar names for local runs. The real `.env` is gitignored and off limits. |
| `pywikibot/`, `ENV/`, `.venv/` | Not in the repo: checkout, Toolforge venv, local uv venv. |

## Deployment model

- **The repo must be the tool home root.** Job commands are `./run.sh` and
  `python3 serve.py`, which resolve relative to `$HOME`.
- **Only `bootstrap-venv.sh` changes the pywikibot checkout.** `run.sh` refuses
  to start if the checkout is not exactly at `PYWIKIBOT_VERSION`, and never
  pulls. A drifting checkout used to be a silent production risk.
- **Production deps are hashed.** `requirements.txt` is generated from
  `uv.lock`; bump deps by editing `pyproject.toml`, running `uv lock`, then
  `uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt`.
- **One run at a time** (`flock` on `.arkivbot.lock`); a second run exits 0 with
  a warning.
- **A run that fails now exits non-zero**, so `emails: onfailure` fires. The
  `ERROR:` line it writes is also highlighted on the status page.
- **The status page must never show a fake run.** `--simulate` writes only to
  stdout; use `web-preview.py` to see the live UI.
- **The public log is filtered, the private one is not.** `public_html/out` goes
  through `filter-log.py`; tracebacks and paths stay in pywikibot's
  `logs/main.log` and the job's own log. Do not bypass the filter.
- **`jobs load` starts the schedule.** Always run `arkivbot-dry` before it, so a
  broken credential cannot go live just because the deploy script succeeded.
- **Production requires `flock`, `timeout` and `stdbuf`.** `run.sh` exits 1 if
  any is missing (a dry run only warns), so a silently unlocked or uncapped run
  cannot happen.

## Bot account and credentials

The editing account is not in this repo. pywikibot reads it from
`user-config.py` (tool home, `~/.pywikibot/`, or repo root, whichever exists
first). `user-config.py.example` is the shape to copy:

```python
usernames['wikipedia']['no'] = BOT_USERNAME
authenticate['no.wikipedia.org'] = (consumer_token, consumer_secret,
                                    access_token, access_secret)
```

OAuth 1.0a (needs the `pywikibot[mwoauth]` extra, installed from
`requirements.txt`) with the four tokens from the environment: `.env` locally,
`toolforge envvars` on the tool. `PWB_*` names are preferred (they match
Toolforge's own pywikibot image); `MW_*` still works.

Account state (checked 2026-11): `IngeniousBot` has 4185 edits and the **bot**
flag on no.wiki; on se.wiki it has 0 edits and **no** bot flag, which is one
more reason se.wiki is opt-in.

Check the effective identity without editing anything:

```bash
./test-local.sh                 # prints "configured account" + OAuth sites
uv run pwb login -simulate
uv run python check-oauth.py    # secret-free shape check
```

### Secret hygiene

If a secret is ever exposed (transcript, log, screenshot): rotate it, don't
scrub. Rotating an OAuth 1.0a access token invalidates the old one, and the
consumer secret can be reset at
<https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/list>. Then
update the local `.env` and `toolforge envvars`, and re-check with
`./test-local.sh --live`.

## Day-to-day commands

```bash
./test-local.sh            # full offline suite (~5 s after the first run)
./test-local.sh --live     # + archivebot -simulate on no.wikipedia (read-only)
SKIP_LINT=1 ./test-local.sh
node tests/status-page.test.mjs        # page contract tests only
uvx ruff check . && uvx mypy --strict serve.py check-oauth.py web-preview.py audit-templates.py
./web-preview.py                       # status page with a synthetic run
./audit-templates.py                   # which pages archivebot would skip, and why
```

## Deploying to Toolforge

Getting the files into the tool home is out of band (git pull there, or
scp/rsync). Then on a bastion (`ssh login.toolforge.org`, then
`become arkivbot2`):

```bash
# 1. Confirm the images still exist (python3.13 newest; mariadb for logrotate)
toolforge jobs images | grep -E 'python3|mariadb'

# 1b. Confirm the image has the tools run.sh requires (it exits 1 without them)
toolforge jobs run probe --command "flock --version; timeout --version | head -1; stdbuf --version | head -1" \
    --image python3.13 --wait

# 2. Fresh tool: nothing to stop. If you inherited a webservice, retire it
#    first: it cannot start (PHP 7.4 is gone) and it would hold the route.
toolforge webservice stop || true

# 3. One-time: OAuth secrets for IngeniousBot (values from your consumer)
for v in PWB_CONSUMER_TOKEN PWB_CONSUMER_SECRET PWB_ACCESS_TOKEN PWB_ACCESS_SECRET; do
    toolforge envvars create "$v"     # prompts, hidden input
done
toolforge envvars create PWB_USERNAME   # IngeniousBot

# 4. user-config.py (reads everything from the env, no .env needed on the tool)
cp user-config.py.example user-config.py

# 5. Rebuild ENV: pinned checkout + hashed requirements, inside the image
toolforge jobs run bootstrap-venv --command "./bootstrap-venv.sh" --image python3.13 --wait

# 6. Prove the bot half without editing: reads the wikis, saves nothing.
#    `jobs load` has not run yet, so nothing is scheduled at this point.
toolforge jobs run arkivbot-dry --command "./run.sh --simulate" --image python3.13 --wait 1800
toolforge jobs logs arkivbot-dry        # must authenticate as IngeniousBot, no ERROR lines

# 7. Load the three jobs. This is the step that starts the daily editing run.
toolforge jobs load jobs.yaml
toolforge jobs list --output long        # arkivbot, arkivbot-web, logrotate

# 8. Verify the web half
toolforge jobs list --output long        # arkivbot-web should show the public URL
curl -si https://arkivbot2.toolforge.org/ | head -5
curl -s  https://arkivbot2.toolforge.org/status
curl -s -o /dev/null -w '%{http_code}\n' https://arkivbot2.toolforge.org/run.sh   # must be 404

# 9. Watch the first scheduled run
toolforge jobs restart arkivbot
toolforge jobs logs -f arkivbot
```

Bumping Python or pywikibot later: edit `.python-version`/`requires-python` and
`image:` values, or `PYWIKIBOT_VERSION`; then `uv lock`, `uv export …`,
`./test-local.sh --live`, deploy, and repeat steps 5–6.

## Operational limits

- `run.sh` caps each archivebot invocation at `ARCHIVEBOT_TIMEOUT` seconds
  (default 1500) using coreutils `timeout`, and traps `TERM`/`INT`/`HUP` so a
  cancelled run still resets `status`. The jobs framework's own timeout flag is
  not in the documented YAML schema, so it is deliberately not relied on.
- The page distrusts `status=true` older than 3 h (`Last-Modified`) and reports
  **Stalled?** instead of polling forever.
- `no-filelog: true` on `arkivbot-web`: without it, visitor polling grows
  `arkivbot-web.err` without bound. `serve.py` also does no per-request logging.
- `logrotate-arkivbot` rotates `*.out`/`*.err` daily, keeps 6 compressed copies.
- Toolforge rate-limits each IP to **100 requests/min across all of Toolforge**.
  The page polls every 5 s while a run is active, 15 s when idle, backs off to
  120 s on 429, and only refetches the larger `out` when something changed.

## Gotchas

- **The venv must be created inside the execution image.** `python3.13 -m venv`
  on a bastion produces a broken `ENV`. Always use a job
  (`toolforge jobs run … --image python3.13 --wait`).
- **`archivebot.py` is not in the PyPI wheel**; it lives in the pinned checkout,
  which `run.sh` puts on `PYTHONPATH` (this also shadows the wheel, so wheel and
  checkout can never disagree).
- **The i18n git submodule is required** (`scripts/i18n`); without it archivebot
  dies with `TranslationError: No translation available for key
  archivebot-archive-summary`.
- **`PYTHONPATH` must contain the checkout root *and* `scripts/`.** The root
  makes `scripts.i18n` importable; `scripts/` serves the script's own imports.
- **archivebot silently skips malformed pages**: the template must be in the
  pre-heading text *and* multi-line (`{{X\n|param = value\n}}`), and the name is
  compared literally, so a redirect (`{{Bruker:ArkivBot/autoarkivering}}`) fails
  with `MissingConfigError`. Run `./audit-templates.py` after template churn.
- **`toolforge jobs run --webservice --command …` appends** to the image's
  default server command, which is why `arkivbot-web` is a `continuous: true`
  job with `publish: "/"`.
- **Only one route can own `arkivbot2.toolforge.org`.** A published job and a
  `webservice` conflict.
- **`maxlag` was renamed to `write_maxlag` in pywikibot 11.8.**
- Never edit `service.manifest`/`service.template` by hand; this tool has no
  `webservice`, so neither file should exist.

## Conventions

- Shell is `bash` with `set -euo pipefail`; `run.sh` keeps `set -uo pipefail`
  (no `-e`) so it can record partial failures and still write the final status,
  but it must end with a non-zero exit when anything failed.
- Keep `pyproject.toml` pins, `PYWIKIBOT_VERSION` and `requirements.txt` in sync;
  `test-local.sh` fails if they drift.
- The status page stays dependency-free (no CDN, no framework, no build step),
  responsive from ~320 px, light + dark, `prefers-reduced-motion` aware, and
  must keep working when served by `python3 -m http.server`.
- Log lines the page parses (`"<date> : Bot started"`, `"Bot finished"`,
  `"Archiving N thread(s)."`) are a contract: change them together with
  `public_html/index.html` and `tests/status-page.test.mjs`.
- `.description` and `README` are Toolforge-facing; keep them accurate.

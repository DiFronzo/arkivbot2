#!/bin/bash
# Entry point for the `arkivbot` job of the `arkivbot2` Toolforge tool (see jobs.yaml).
#
# Runs archivebot.py twice: once for Norwegian Bokmål Wikipedia and once for
# Northern Sami Wikipedia (the latter only when SE_TEMPLATE is set). The trailing
# archivebot argument is the *config template* (namespace 10) it looks for
# transclusions of. It is NOT the bot account. The editing account comes from
# pywikibot's user-config.py (currently IngeniousBot); the Toolforge tool is
# `arkivbot2`, and the job names stay `arkivbot` / `arkivbot-web`.
#
# Usage:
#   ./run.sh             # real run: archives, writes public_html/out + status
#   ./run.sh --simulate  # dry run: pywikibot's -simulate, no edits, no status
#                        # file changes, output only on stdout (job log)
#
# Exit status is non-zero if either archivebot invocation failed, so that the
# job's `emails: onfailure` actually fires.
#
# Pywikibot logs to <config dir>/logs/main.log, i.e. $HOME/logs when a
# user-config.py sits in the tool home.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

SIMULATE=0
for arg in "$@"; do
    case "$arg" in
        --simulate | --dry-run | -n) SIMULATE=1 ;;
        *)
            echo "❌ Unknown argument: $arg (use --simulate for a dry run)" >&2
            exit 2
            ;;
    esac
done

VENV="${VENV:-$HERE/ENV}"
PYWIKI="${PYWIKI:-$HERE/pywikibot}"
PYTHON="${PYTHON:-$VENV/bin/python}"

STATUS=public_html/status

# A dry run must not clobber the log the status page serves.
if [[ "$SIMULATE" == 1 ]]; then
    LOG=/dev/null
else
    LOG=public_html/out
fi

# Config templates, not usernames (see the header comment).
NO_TEMPLATE="${NO_TEMPLATE:-Autoarkivering}"
# Northern Sami is opt-in: the template does not exist on se.wiki yet and the bot
# has no bot flag there, so a failure would turn every nightly run red.
SE_TEMPLATE="${SE_TEMPLATE:-}"

if [[ ! -x "$PYTHON" ]]; then
    echo "❌ No virtualenv at $VENV. Run ./bootstrap-venv.sh inside a python3.13 job first" >&2
    exit 1
fi

# Refuse to run on an unexpected pywikibot checkout: the copy is pinned, and a
# drifting checkout is how unrelated regressions get shipped at 05:00.
if [[ -f PYWIKIBOT_VERSION ]]; then
    REF="$(tr -d '[:space:]' < PYWIKIBOT_VERSION)"
    FOUND="$(git -C "$PYWIKI" describe --tags --exact-match 2>/dev/null || echo 'unknown')"
    if [[ "$FOUND" != "$REF" ]]; then
        echo "❌ pywikibot checkout is at '$FOUND', expected '$REF'. Rerun bootstrap-venv" >&2
        exit 1
    fi
fi
if [[ ! -d "$PYWIKI/scripts/i18n/archivebot" ]]; then
    echo "❌ pywikibot checkout has no i18n submodule. Rerun bootstrap-venv" >&2
    exit 1
fi

# One run at a time: cron, `jobs restart` and the dry-run job can otherwise
# overlap on the same checkout and venv.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$HERE/.arkivbot.lock"
    if ! flock -n 9; then
        echo "⚠️  another run is in progress, exiting"
        exit 0
    fi
fi

write_status() {
    [[ "$SIMULATE" == 1 ]] && return 0
    echo "$1" > "$STATUS"
}

mkdir -p public_html
: > "$LOG"
write_status true
# Flip the status back if we die, and also when the pod is asked to stop.
trap 'write_status false' EXIT
trap 'write_status false; exit 143' TERM INT HUP

echo "🏡 Working dir: $(pwd)"

# Set Pywikibot path. The checkout root is needed so that `scripts.i18n`
# (translations, a git submodule) is importable; archivebot aborts with
# TranslationError without it. It also shadows the PyPI wheel, so run.sh and
# requirements.txt always agree on the version.
export PYTHONPATH="$PYWIKI:$PYWIKI/scripts${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
PYVERSION="$("$PYTHON" -V 2>&1)"

# pywikibot's global -simulate switch: pages are read, nothing is saved.
ARCHIVEBOT_OPTS=()
if [[ "$SIMULATE" == 1 ]]; then
    ARCHIVEBOT_OPTS+=(-simulate)
    echo "🧪 SIMULATION MODE: no edits will be saved"
fi

{
    echo "------------------------------------------"
    if [[ "$SIMULATE" == 1 ]]; then
        echo "$(date) : SIMULATED run started"
    else
        echo "$(date) : Bot started"
    fi
    echo "------------------------------------------"
    echo "$PYVERSION"
} | tee -a "$LOG"

# Diagnostics for the job log only: this output contains local paths, so it is
# not mirrored to the public status page.
"$PYTHON" "$PYWIKI/pwb.py" version 2>&1 || echo "⚠️  pwb.py version failed"

FAILURES=0
# Wall-clock cap per invocation. The jobs framework has a timeout option, but it
# is not part of the documented YAML schema, so the limit lives here where it is
# testable. A hung run must not block tomorrow's job or leave status=true.
LIMIT="${ARCHIVEBOT_TIMEOUT:-1500}"
TIMEOUT_CMD=()
if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD=(timeout --signal=TERM "$LIMIT")
else
    echo "⚠️  no 'timeout' command available; running without a wall-clock cap"
fi

run_archivebot() {  # $1 = language code, $2 = config template
    local rc
    "${TIMEOUT_CMD[@]}" stdbuf -o 0 "$PYTHON" "$PYWIKI/scripts/archivebot.py" \
        "${ARCHIVEBOT_OPTS[@]}" -lang:"$1" -log:main.log "$2" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    if (( rc != 0 )); then
        echo "ERROR: archivebot -lang:$1 $2 exited with status $rc" | tee -a "$LOG"
        FAILURES=$((FAILURES + 1))
    fi
}

run_archivebot no "$NO_TEMPLATE"
if [[ -n "$SE_TEMPLATE" ]]; then
    run_archivebot se "$SE_TEMPLATE"
else
    echo "ℹ️  SE_TEMPLATE not set, skipping se.wikipedia"
fi

{
    echo "------------------------------------------"
    if [[ "$SIMULATE" == 1 ]]; then
        echo "$(date) : SIMULATED run finished (no edits saved)"
    else
        echo "$(date) : Bot finished"
    fi
    echo "------------------------------------------"
} | tee -a "$LOG"

if (( FAILURES > 0 )); then
    echo "❌ $FAILURES archivebot invocation(s) failed" >&2
    exit 1
fi
exit 0

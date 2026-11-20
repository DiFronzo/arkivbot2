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
# user-config.py sits in the tool home. That log keeps the full output,
# including tracebacks; public_html/out only gets what filter-log.py allows.

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

# The production run depends on these; a missing tool must not silently degrade
# into "no lock" or "no time limit". A dry run may proceed without them.
MISSING=()
for tool in flock timeout stdbuf; do
    command -v "$tool" >/dev/null 2>&1 || MISSING+=("$tool")
done
if (( ${#MISSING[@]} > 0 )); then
    if [[ "$SIMULATE" == 1 ]]; then
        echo "⚠️  missing ${MISSING[*]}; continuing because this is a dry run" >&2
    else
        echo "❌ missing required tools: ${MISSING[*]}. Run inside the python3.13 job image." >&2
        exit 1
    fi
fi

# Take the lock before any other check, so overlapping runs cannot both report
# success (or both start) while the other holds the checkout and venv.
if command -v flock >/dev/null 2>&1; then
    exec 9>"$HERE/.arkivbot.lock"
    if ! flock -n 9; then
        echo "⚠️  another run is in progress, exiting"
        exit 0
    fi
fi

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
for helper in filter-log.py archivebot_wrapper.py; do
    if [[ ! -f "$helper" ]]; then
        echo "❌ $helper is missing; refusing to run" >&2
        exit 1
    fi
done

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
FILTER_FAILURES=0
# Wall-clock cap per invocation, with a grace period if the bot ignores TERM.
# The jobs framework has a timeout option, but it is not part of the documented
# YAML schema, so the limit lives here where it is testable.
LIMIT="${ARCHIVEBOT_TIMEOUT:-1500}"

# bash 3.2 (macOS) errors on expanding an empty array under `set -u`, so the
# cap is applied by a wrapper rather than by an array that may be empty.
run_capped() {
    if command -v timeout >/dev/null 2>&1; then
        timeout --signal=TERM --kill-after=60 "$LIMIT" "$@"
    else
        "$@"
    fi
}

run_archivebot() {  # $1 = language code, $2 = config template
    local -a statuses
    run_capped stdbuf -o 0 "$PYTHON" "$HERE/archivebot_wrapper.py" \
        ${ARCHIVEBOT_OPTS[@]+"${ARCHIVEBOT_OPTS[@]}"} -lang:"$1" -log:main.log "$2" 2>&1 \
        | "$PYTHON" filter-log.py \
        | tee -a "$LOG"
    # read PIPESTATUS once: any command in between resets it
    statuses=(${PIPESTATUS[@]+"${PIPESTATUS[@]}"})
    local rc="${statuses[0]:-0}"
    local filter_rc="${statuses[1]:-0}"
    if (( rc != 0 )); then
        echo "ERROR: archivebot -lang:$1 $2 exited with status $rc" | tee -a "$LOG"
        FAILURES=$((FAILURES + 1))
    fi
    if (( filter_rc != 0 )); then
        echo "ERROR: filter-log.py exited with status $filter_rc (public log incomplete)" | tee -a "$LOG"
        FILTER_FAILURES=$((FILTER_FAILURES + 1))
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

# Per-page failures (spam filter, bad template, edit conflict) do not change
# archivebot's exit status, so count them explicitly. They are reported but do
# not fail the job, otherwise one unarchivable page would email every night.
if [[ "$SIMULATE" == 0 ]]; then
    PAGE_ERRORS="$(grep -cE '^ERROR: (Error occurred while processing page|Missing or malformed template in page)' "$LOG" 2>/dev/null || true)"
    if [[ "${PAGE_ERRORS:-0}" -gt 0 ]]; then
        echo "⚠️  $PAGE_ERRORS page(s) reported errors this run, see the log above" | tee -a "$LOG"
    fi
fi

if (( FAILURES > 0 || FILTER_FAILURES > 0 )); then
    echo "❌ $FAILURES archivebot invocation(s) and $FILTER_FAILURES filter failure(s)" >&2
    exit 1
fi
exit 0
